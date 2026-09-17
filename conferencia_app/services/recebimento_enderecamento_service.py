"""Endereçamento auditável após recebimento. Não movimenta saldos no GRV."""
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from flask import current_app, session
from itsdangerous import URLSafeTimedSerializer, BadSignature
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError

from ..extensions import db
from ..models import (LocalizacaoArmazem, RecebimentoEnderecamento as Tarefa,
                      RecebimentoEnderecamentoEvento as Evento,
                      RecebimentoEnderecamentoTrava as Trava)
from .erp_estoque_service import buscar_localizacao_produto_grv, atualizar_localizacao_estoque


def enderecos(valor):
    return list(dict.fromkeys(x.strip() for x in str(valor or "").split(";") if x.strip()))


def evento(tarefa, tipo, detalhes=None):
    db.session.add(Evento(tarefa_id=tarefa.id, tipo=tipo,
                         usuario=session.get("username", "sistema"), detalhes=detalhes))


def criar_pendencias(itens, contagens, conversoes, ids_conformes, usuario):
    tarefas = []
    for item in itens:
        if item.id not in ids_conformes:
            continue
        cfg = (conversoes.get(str(item.id)) or {}) if isinstance(conversoes, dict) else {}
        try:
            fator = float(str(cfg.get("fator") or 1).replace(",", "."))
        except (ValueError, TypeError, AttributeError):
            fator = 1
        if fator <= 0:
            fator = 1
        qtd = float(str(contagens.get(str(item.id), 0)).replace(",", ".")) * fator
        if not Decimal(str(qtd)).is_finite() or qtd <= 0:
            continue
        tarefa = Tarefa.query.filter_by(item_nota_id=item.id).first()
        if not tarefa:
            tarefa = Tarefa(item_nota_id=item.id, sku=str(item.codigo_grv or "").strip(),
                            quantidade=qtd, unidade=item.unidade_comercial, criado_por=usuario)
            db.session.add(tarefa)
            db.session.flush()
            evento(tarefa, "Criado", {"quantidade": qtd, "nota": item.numero_nota,
                                    "chave_acesso": item.chave_acesso, "sku": tarefa.sku})
        tarefas.append(tarefa.id)
    return tarefas


def assinador():
    return URLSafeTimedSerializer(current_app.secret_key, salt="recebimento-camera-v1")


def registrar_leitura(tarefa, tipo, codigo, manual=False):
    if tarefa.status != "Pendente":
        raise ValueError("Este item já foi confirmado. Atualize a lista.")
    validar_recebimento(tarefa)
    codigo = str(codigo or "").strip()
    if tipo == "sku":
        vinculado = str(tarefa.item.codigo_grv or "").strip()
        if vinculado != tarefa.sku:
            anterior = tarefa.sku
            tarefa.sku = vinculado
            tarefa.versao_leitura += 1
            evento(tarefa, "Vínculo SKU atualizado", {"antes": anterior, "sku": vinculado})
            db.session.commit()
        if not tarefa.sku:
            raise ValueError("Item sem SKU GRV vinculado. Corrija o vínculo do material no recebimento.")
        if codigo != tarefa.sku:
            raise ValueError("O SKU bipado não corresponde a este item do recebimento.")
        atuais = enderecos(buscar_localizacao_produto_grv(tarefa.sku))
    elif tipo == "local":
        if not codigo or ";" in codigo or len(codigo) > 80:
            raise ValueError("Bipe ou digite a etiqueta de um único endereço.")
        local = LocalizacaoArmazem.query.filter_by(codigo=codigo).first()
        if local and not local.ativo:
            raise ValueError("Endereço desativado. Solicite a regularização ao responsável.")
        if not local:
            # Sem cadastro prévio: a etiqueta física é a fonte da verdade.
            try:
                db.session.add(LocalizacaoArmazem(codigo=codigo, corredor="", prateleira="", posicao=""))
                evento(tarefa, "Endereço registrado pela etiqueta", {"endereco": codigo})
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
        atuais = None
    else:
        raise ValueError("Tipo de leitura inválido.")
    if manual:
        evento(tarefa, "Leitura digitada", {"tipo": tipo, "codigo": codigo})
        db.session.commit()
    dados = {"tarefa": tarefa.id, "usuario": session["username"], "tipo": tipo,
             "codigo": codigo, "atuais": atuais, "versao": tarefa.versao_leitura}
    return {"token": assinador().dumps(dados), "codigo": codigo, "enderecos": atuais}


def validar_leitura(token, tarefa, tipo):
    try:
        dados = assinador().loads(token, max_age=1800)
    except (BadSignature, TypeError):
        raise ValueError("Leitura expirada ou inválida. Bipe novamente.")
    if (dados.get("tarefa"), dados.get("usuario"), dados.get("tipo")) != (
            tarefa.id, session["username"], tipo):
        raise ValueError("Leitura não pertence a este item ou usuário.")
    if dados.get("versao") != tarefa.versao_leitura:
        raise ValueError("Leitura anterior à revisão. Bipe novamente o SKU e o endereço.")
    return dados


def impedimento(tarefa):
    if tarefa.item.status not in ("Concluído", "Lançado"):
        return "O recebimento não está concluído ou foi reaberto. Regularize a conferência antes de endereçar."
    if not str(tarefa.item.codigo_grv or '').strip():
        return "Item sem SKU GRV vinculado. Corrija o vínculo do material no recebimento."
    return ""


def validar_recebimento(tarefa):
    mensagem = impedimento(tarefa)
    if mensagem:
        raise ValueError(mensagem)


def confirmar(tarefa, dados, pode_alternar=False):
    if tarefa.status != "Pendente":
        return  # confirmação repetida não duplica alocações ou quantidades
    validar_recebimento(tarefa)
    if tarefa.sku != str(tarefa.item.codigo_grv or "").strip():
        raise ValueError("O vínculo do SKU mudou. Bipe o material novamente.")
    sku = validar_leitura(dados.get("sku_token"), tarefa, "sku")
    if sku["codigo"] != tarefa.sku:
        raise ValueError("SKU alterado. Bipe novamente.")
    motivo = dados.get("motivo", "normal")
    justificativa = str(dados.get("justificativa") or "").strip()
    if motivo not in ("normal", "superlotado", "alternativo"):
        raise ValueError("Motivo inválido.")
    if motivo == "alternativo" and (not pode_alternar or len(justificativa) < 5):
        raise ValueError("Destino alternativo exige permissão e justificativa de pelo menos 5 caracteres.")
    parcelas = dados.get("alocacoes")
    if not isinstance(parcelas, list) or not 1 <= len(parcelas) <= 50:
        raise ValueError("Bipe o endereço e informe a quantidade armazenada.")
    alocacoes, total = [], Decimal(0)
    for parcela in parcelas:
        if not isinstance(parcela, dict):
            raise ValueError("Parcela de endereçamento inválida.")
        local = validar_leitura(parcela.get("token"), tarefa, "local")["codigo"]
        if not LocalizacaoArmazem.query.filter_by(codigo=local, ativo=True).first():
            raise ValueError("Um endereço foi desativado. Revise as leituras.")
        try:
            qtd = Decimal(str(parcela.get("quantidade", "")).replace(",", "."))
        except InvalidOperation:
            raise ValueError("Quantidade inválida.")
        if not qtd.is_finite() or qtd <= 0:
            raise ValueError("Quantidade deve ser positiva e finita.")
        total += qtd
        alocacoes.append({"endereco": local, "quantidade": float(qtd),
                          "lote": str(parcela.get("lote") or "").strip()[:80]})
    if abs(total - Decimal(str(tarefa.quantidade))) > Decimal("0.000001"):
        raise ValueError("A soma por endereço deve ser igual à quantidade conferida.")
    atuais = enderecos(buscar_localizacao_produto_grv(tarefa.sku))
    if atuais != sku["atuais"]:
        raise ValueError("O endereço do GRV mudou durante a operação. Bipe o SKU novamente.")
    novos = [p["endereco"] for p in alocacoes]
    if atuais and motivo == "normal" and any(x not in atuais for x in novos):
        raise ValueError("Material já endereçado em " + ";".join(atuais) + ". Use esse local ou registre a exceção.")
    if motivo == "superlotado" and (not atuais or not any(x not in atuais for x in novos)):
        raise ValueError("Superlotação exige um endereço existente e a leitura de um endereço adicional.")
    alterados = Tarefa.query.filter_by(id=tarefa.id, status="Pendente").update({
        "status": "Aguardando sincronização", "alocacoes": alocacoes,
        "motivo": motivo, "justificativa": justificativa[:500],
        "confirmado_por": session["username"], "enderecos_antes": atuais,
    }, synchronize_session=False)
    if alterados:
        evento(tarefa, "Confirmado", {"alocacoes": alocacoes, "motivo": motivo,
                                    "justificativa": justificativa[:500], "antes": atuais})
    db.session.commit()
    db.session.expire_all()


def adquirir_trava(tarefa):
    # Uma trava persistente por SKU serializa tarefas e retries entre workers.
    if not db.session.get(Trava, tarefa.sku):
        try:
            db.session.add(Trava(sku=tarefa.sku))
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
    token = str(uuid4())
    agora = datetime.now()
    acquired = Trava.query.filter(Trava.sku == tarefa.sku, or_(
        Trava.expira_em.is_(None), Trava.expira_em < agora)).update({
            "token": token, "expira_em": agora + timedelta(minutes=10)}, synchronize_session=False)
    db.session.commit()
    if not acquired:
        raise ValueError("Este SKU está sendo sincronizado. Aguarde e atualize a lista.")
    return token


def reabrir(tarefa, justificativa):
    justificativa = str(justificativa or "").strip()
    if len(justificativa) < 5:
        raise ValueError("Informe a justificativa da revisão, com pelo menos 5 caracteres.")
    token = adquirir_trava(tarefa)
    try:
        db.session.refresh(tarefa)
        from ..models import EnderecoMovimento
        from .enderecamento_service import chave_recebimento
        if EnderecoMovimento.query.filter_by(chave=chave_recebimento(tarefa)).first():
            raise ValueError("Este material já entrou no saldo por endereço. Use Movimentar material no módulo Endereçamento para corrigir, preservando o histórico.")
        if tarefa.status not in ("Aguardando sincronização", "Concluído"):
            raise ValueError("Somente endereçamentos aguardando sincronização ou concluídos podem ser revisados/estornados.")
        tipo = "Estornado" if tarefa.status == "Concluído" else "Reaberto para nova leitura"
        evento(tarefa, tipo, {"justificativa": justificativa[:500],
                              "alocacoes": tarefa.alocacoes})
        tarefa.status = "Pendente"
        tarefa.versao_leitura += 1
        tarefa.alocacoes = None
        tarefa.erro = None
        tarefa.concluido_em = None
    finally:
        Trava.query.filter_by(sku=tarefa.sku, token=token).update({"token": None, "expira_em": None})
        db.session.commit()


def sincronizar(tarefa):
    if tarefa.status == "Concluído":
        return
    if tarefa.status != "Aguardando sincronização":
        raise ValueError("Confirme as leituras antes de sincronizar.")
    token = adquirir_trava(tarefa)
    agora = datetime.now()
    try:
        db.session.refresh(tarefa)
        if tarefa.status == "Concluído":
            return
        if tarefa.status != "Aguardando sincronização":
            return
        validar_recebimento(tarefa)
        tarefa.executando_em = agora
        evento(tarefa, "Sincronização iniciada")
        db.session.commit()
        atuais = enderecos(buscar_localizacao_produto_grv(tarefa.sku))
        novos = list(dict.fromkeys(p["endereco"] for p in tarefa.alocacoes))
        for local in novos:
            if not LocalizacaoArmazem.query.filter_by(codigo=local, ativo=True).first():
                raise ValueError("Endereço desativado após confirmação. Regularize o cadastro antes de tentar novamente.")
        # Não remove endereços adicionados por inventário/GRV entre tentativas.
        if tarefa.motivo == "normal" and atuais and any(x not in atuais for x in novos):
            raise ValueError("Endereço do GRV mudou. Regularize o destino antes de sincronizar.")
        enviados = ";".join(dict.fromkeys(atuais + novos))
        tarefa.enderecos_enviados = enviados
        evento(tarefa, "Envio GRV", {"antes": atuais, "enviado": enviados})
        db.session.commit()
        resposta = atualizar_localizacao_estoque(tarefa.sku, enviados)
        if isinstance(resposta, dict) and (resposta.get("sucesso") is False or resposta.get("success") is False):
            raise RuntimeError("GRV recusou a atualização do endereço.")
        from .enderecamento_service import creditar_recebimento
        creditar_recebimento(tarefa, tarefa.enderecos_antes or [])
        tarefa.status = "Concluído"
        tarefa.concluido_em = datetime.now()
        tarefa.erro = None
        evento(tarefa, "Sincronizado", {"enviado": enviados})
    except Exception as exc:
        db.session.rollback()
        current_app.logger.exception("Falha no endereçamento %s", tarefa.id)
        tarefa.erro = str(exc)[:1000] if isinstance(exc, ValueError) else "Falha na comunicação com o GRV. Tente sincronizar novamente."
        evento(tarefa, "Falha de sincronização", {"erro": tarefa.erro})
    finally:
        tarefa.executando_em = None
        Trava.query.filter_by(sku=tarefa.sku, token=token).update({"token": None, "expira_em": None})
        db.session.commit()
