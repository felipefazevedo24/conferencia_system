"""Servico da Homologacao de Fornecedores (Compras) - F-COM-001-01.

Regras de negocio: pontuacao do formulario, workflow de aprovacao e
controle de validade. A definicao do formulario em si (secoes, perguntas
e pesos) fica em compras_homologacao_form.py.

Workflow:
    Rascunho ---enviar--> Em aprovacao ---homologar--> Homologado
                               |         \\--reprovar--> Reprovado
                               \\--devolver--> Rascunho
    (Homologado/Reprovado podem ser reabertos, voltando pra Rascunho.)

Formularios: as homologacoes novas nascem no F 066 rev. 04
(compras_homologacao_form_rev04.py); as anteriores continuam no
F-COM-001-01 (compras_homologacao_form.py), com a nota do jeito que foram
avaliadas. Ver formulario_da.

Self assessment (opcional, a partir do Rascunho):
    Rascunho ---enviar_para_fornecedor--> Com fornecedor
    Com fornecedor ---fornecedor envia pelo link--> Rascunho (comprador revisa)
    Com fornecedor ---cancelar_envio_fornecedor--> Rascunho
"""
from __future__ import annotations

import hashlib
import re
import secrets
from datetime import date, datetime, timedelta

from ..extensions import db
from ..models import (
    ComprasHomologacaoConvite,
    ComprasHomologacaoEvidencia,
    ComprasHomologacaoFornecedor as Homologacao,
    ComprasHomologacaoFoto,
    ComprasHomologacaoResponsavel,
    ComprasHomologacaoResposta,
)
from . import compras_homologacao_form as form
from . import compras_homologacao_form_rev04 as form_r04
from ..tempo import agora_br

# Quantos dias antes do vencimento a homologacao ja entra no alerta.
DIAS_ALERTA_VENCIMENTO = 30

MAX_FOTO_BYTES = 8 * 1024 * 1024
CONTENT_TYPES_FOTO = ("image/png", "image/jpeg", "image/jpg", "image/webp", "image/gif")

VALIDADE_LINK_FORNECEDOR_DIAS = 14
MAX_EVIDENCIA_BYTES = 10 * 1024 * 1024
# O content type vem da extensao (lista fechada), nao do que o navegador do
# fornecedor declarou - a evidencia e' servida de volta pro comprador.
TIPOS_EVIDENCIA = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}

# Campos que o fornecedor confirma/ajusta no link. Razao social, CNPJ e
# endereco vem do cartao CNPJ (so' leitura pra ele); auditoria, fotos e
# comentario final sao internos.
CAMPOS_FORNECEDOR = ("contato_principal", "telefone", "email", "website", "descricao_produto_servico")

# Formulario das homologacoes criadas daqui pra frente.
FORMULARIO_VIGENTE = form_r04.VERSAO

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _txt(valor) -> str:
    return str(valor if valor is not None else "").strip()


# ── Pontuacao ────────────────────────────────────────────────────────────
def calcular_nota(respostas_por_chave: dict[tuple[str, int], str]) -> tuple[float, str, dict]:
    """Aplica a regra de pontuacao do formulario.

    Cada secao distribui seu peso igualmente entre os itens; a resposta
    define quanto daquele item e' aproveitado (ver form.FATOR_RESPOSTA).
    Item sem resposta vale zero - o formulario so' deve ser enviado pra
    aprovacao com tudo respondido (ver validar_para_envio).

    Devolve (nota 0-1, classificacao, detalhe por secao).
    """
    nota = 0.0
    detalhe = {}
    for secao in form.SECOES:
        qtd = len(secao["itens"]) or 1
        peso_item = secao["peso"] / qtd
        obtido = 0.0
        respondidos = 0
        for i in range(1, len(secao["itens"]) + 1):
            resposta = _txt(respostas_por_chave.get((secao["chave"], i)))
            if not resposta:
                continue
            respondidos += 1
            obtido += peso_item * form.FATOR_RESPOSTA.get(resposta, 0.0)
        nota += obtido
        detalhe[secao["chave"]] = {
            "titulo": secao["titulo"],
            "peso": secao["peso"],
            "obtido": round(obtido, 6),
            # % da propria secao (quanto do peso dela foi aproveitado)
            "aproveitamento": round(obtido / secao["peso"], 4) if secao["peso"] else 0.0,
            "respondidos": respondidos,
            "total_itens": len(secao["itens"]),
        }

    nota = round(nota, 6)
    if nota >= form.NOTA_MINIMA_APROVADO:
        classificacao = form.CLASSIFICACAO_APROVADO
    elif nota >= form.NOTA_MINIMA_RESSALVAS:
        classificacao = form.CLASSIFICACAO_RESSALVAS
    else:
        classificacao = form.CLASSIFICACAO_REPROVADO
    return nota, classificacao, detalhe


def _respostas_por_chave(homologacao: Homologacao) -> dict[tuple[str, int], str]:
    return {(r.secao, r.item): r.resposta for r in homologacao.respostas}


# ── Versao do formulario ───────────────────────────────────────────────
def eh_rev04(homologacao: Homologacao) -> bool:
    return (homologacao.formulario_versao or "") == form_r04.VERSAO


def formulario_da(homologacao: Homologacao):
    """Modulo do formulario em que a homologacao foi preenchida (NULL = o
    antigo F-COM-001-01)."""
    return form_r04 if eh_rev04(homologacao) else form


def formulario_vigente():
    return form_r04 if FORMULARIO_VIGENTE == form_r04.VERSAO else form


def _com_evidencia(homologacao: Homologacao) -> set:
    return {(e.secao, e.item) for e in homologacao.evidencias}


def iso_valido(homologacao: Homologacao) -> bool:
    """Rev. 04: ISO 9001 declarado E certificado anexado - so' assim o
    questionario deixa de ser exigido."""
    return (
        eh_rev04(homologacao)
        and bool(homologacao.iso9001_certificado)
        and (form_r04.SECAO_ISO, 1) in _com_evidencia(homologacao)
    )


def itens_da(homologacao: Homologacao):
    """(secao, item, texto) que precisam de resposta nesta homologacao."""
    if eh_rev04(homologacao):
        return form_r04.itens_do_formulario(iso_valido(homologacao))
    return form.itens_do_formulario()


def calcular_nota_da(homologacao: Homologacao) -> tuple[float, str, dict]:
    if eh_rev04(homologacao):
        return form_r04.calcular_nota(
            _respostas_por_chave(homologacao), _com_evidencia(homologacao), iso_valido(homologacao),
        )
    return calcular_nota(_respostas_por_chave(homologacao))


def recalcular(homologacao: Homologacao) -> Homologacao:
    nota, classificacao, _ = calcular_nota_da(homologacao)
    homologacao.nota = nota
    homologacao.classificacao = classificacao
    return homologacao


# ── CRUD ────────────────────────────────────────────────────────────────
_CAMPOS_CABECALHO = (
    "razao_social", "cnpj", "nome_fantasia", "inscricao_estadual", "endereco",
    "cidade_estado", "contato_principal", "telefone", "email", "website",
    "categoria_compra", "descricao_produto_servico", "resultado_auditoria",
    "obs_conformidade_legal", "comentario",
    "amostra_obs", "visita_obs",
)
_SIM_NAO = ("Sim", "Não")


def criar(dados: dict, usuario: str) -> Homologacao:
    razao = _txt(dados.get("razao_social"))
    if not razao:
        raise ValueError("Informe a razão social do fornecedor.")
    homologacao = Homologacao(
        status=Homologacao.STATUS_RASCUNHO, criado_por=usuario, formulario_versao=FORMULARIO_VIGENTE,
    )
    _aplicar_cabecalho(homologacao, dados)
    db.session.add(homologacao)
    db.session.flush()
    salvar_respostas(homologacao, dados.get("respostas") or [], commit=False)
    if "responsaveis" in dados:
        salvar_responsaveis(homologacao, dados.get("responsaveis"))
    recalcular(homologacao)
    db.session.commit()
    return homologacao


def _aplicar_cabecalho(homologacao: Homologacao, dados: dict) -> None:
    for campo in _CAMPOS_CABECALHO:
        if campo in dados:
            setattr(homologacao, campo, _txt(dados.get(campo)) or None)
    if "validade_meses" in dados:
        try:
            meses = int(dados.get("validade_meses") or 12)
        except (TypeError, ValueError):
            meses = 12
        homologacao.validade_meses = max(1, min(meses, 120))
    for campo in ("amostra_necessaria", "visita_necessaria"):
        if campo in dados:
            valor = _txt(dados.get(campo))
            if valor and valor not in _SIM_NAO:
                raise ValueError("Responda Sim ou Não em amostra / visita técnica.")
            setattr(homologacao, campo, valor or None)
    _aplicar_iso(homologacao, dados)


def _data_iso(valor) -> date | None:
    texto = _txt(valor)[:10]
    for formato in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(texto, formato).date()
        except ValueError:
            continue
    if texto:
        raise ValueError("Data de validade do certificado ISO 9001 inválida.")
    return None


def _aplicar_iso(homologacao: Homologacao, dados: dict) -> None:
    if "iso9001_certificado" in dados:
        valor = dados.get("iso9001_certificado")
        if isinstance(valor, str):
            valor = {"sim": True, "true": True, "nao": False, "não": False, "false": False}.get(valor.strip().lower())
        homologacao.iso9001_certificado = None if valor is None else bool(valor)
    if "iso9001_validade" in dados:
        homologacao.iso9001_validade = _data_iso(dados.get("iso9001_validade"))


def salvar_responsaveis(homologacao: Homologacao, lista) -> None:
    """Upsert por setor (Diretoria, Vendas, Financeiro, Qualidade)."""
    existentes = {r.setor: r for r in homologacao.responsaveis}
    for entrada in lista or []:
        if not isinstance(entrada, dict):
            continue
        setor = _txt(entrada.get("setor"))
        if setor not in form_r04.SETORES_RESPONSAVEIS:
            continue
        registro = existentes.get(setor)
        if registro is None:
            registro = ComprasHomologacaoResponsavel(setor=setor)
            homologacao.responsaveis.append(registro)
            existentes[setor] = registro
        for campo, limite in (("nome", 120), ("cargo", 80), ("telefone", 40), ("email", 120)):
            if campo in entrada:
                setattr(registro, campo, _txt(entrada.get(campo))[:limite] or None)


def atualizar(homologacao: Homologacao, dados: dict) -> Homologacao:
    if homologacao.status != Homologacao.STATUS_RASCUNHO:
        raise ValueError(
            f"Homologação '{homologacao.status}' não pode ser editada - reabra para rascunho antes."
        )
    if "razao_social" in dados and not _txt(dados.get("razao_social")):
        raise ValueError("Informe a razão social do fornecedor.")
    _aplicar_cabecalho(homologacao, dados)
    if "respostas" in dados:
        salvar_respostas(homologacao, dados.get("respostas") or [], commit=False)
    if "responsaveis" in dados:
        salvar_responsaveis(homologacao, dados.get("responsaveis"))
    recalcular(homologacao)
    db.session.commit()
    return homologacao


def salvar_respostas(homologacao: Homologacao, respostas: list, commit: bool = True) -> Homologacao:
    """Upsert das respostas por (secao, item). Só aceita secao/item que
    existam no formulario da homologacao e resposta dentro da escala da secao."""
    formulario = formulario_da(homologacao)
    existentes = {(r.secao, r.item): r for r in homologacao.respostas}
    for entrada in respostas or []:
        if not isinstance(entrada, dict):
            continue
        secao_chave = _txt(entrada.get("secao"))
        secao = formulario.secao_por_chave(secao_chave)
        if not secao:
            continue
        try:
            item = int(entrada.get("item"))
        except (TypeError, ValueError):
            continue
        if not (1 <= item <= len(secao["itens"])):
            continue
        resposta = _txt(entrada.get("resposta"))
        if resposta and resposta not in secao["escala"]:
            raise ValueError(f"Resposta inválida para '{secao['titulo']}': {resposta}.")

        registro = existentes.get((secao_chave, item))
        if registro is None:
            registro = ComprasHomologacaoResposta(secao=secao_chave, item=item)
            # Anexa pela RELACAO (nao pela FK solta): assim a resposta ja
            # entra em homologacao.respostas na mesma sessao e o recalculo
            # logo abaixo enxerga ela - com FK solta a nota saia zerada.
            homologacao.respostas.append(registro)
            existentes[(secao_chave, item)] = registro
        registro.resposta = resposta or None
        if "comentario" in entrada:
            registro.comentario = _txt(entrada.get("comentario"))[:2000] or None

    if commit:
        recalcular(homologacao)
        db.session.commit()
    return homologacao


def excluir(homologacao: Homologacao) -> None:
    if homologacao.status == Homologacao.STATUS_HOMOLOGADO:
        raise ValueError("Homologação aprovada não pode ser excluída - reabra e reprove, se for o caso.")
    db.session.delete(homologacao)
    db.session.commit()


# ── Workflow ────────────────────────────────────────────────────────────
def itens_faltando(homologacao: Homologacao) -> list[dict]:
    """Itens do formulario ainda sem resposta - o que trava o envio."""
    respondidas = {
        (r.secao, r.item) for r in homologacao.respostas if _txt(r.resposta)
    }
    formulario = formulario_da(homologacao)
    faltando = []
    for secao_chave, item, texto in itens_da(homologacao):
        if (secao_chave, item) not in respondidas:
            secao = formulario.secao_por_chave(secao_chave)
            faltando.append({
                "secao": secao_chave,
                "secao_titulo": secao["titulo"] if secao else secao_chave,
                "item": item,
                "texto": texto,
            })
    return faltando


def validar_para_envio(homologacao: Homologacao) -> None:
    if not _txt(homologacao.razao_social):
        raise ValueError("Informe a razão social do fornecedor.")
    _validar_iso_rev04(homologacao)
    faltando = itens_faltando(homologacao)
    if faltando:
        primeira = faltando[0]
        raise ValueError(
            f"Responda todos os {sum(1 for _ in itens_da(homologacao))} itens antes de enviar para aprovação - "
            f"faltam {len(faltando)} (ex.: {primeira['secao_titulo']}, item {primeira['item']})."
        )
    if eh_rev04(homologacao):
        _validar_rev04(homologacao)


def pendencias_justificativa(homologacao: Homologacao) -> list[dict]:
    """Rev. 04: documento marcado Nao Aplicavel precisa de justificativa."""
    if not eh_rev04(homologacao):
        return []
    pendentes = []
    for r in homologacao.respostas:
        secao = form_r04.secao_por_chave(r.secao)
        if secao and secao["justificar_na"] and r.resposta == form_r04.RESPOSTA_NA and not _txt(r.comentario):
            pendentes.append({"secao": r.secao, "secao_titulo": secao["titulo"], "item": r.item})
    return sorted(pendentes, key=lambda p: p["item"])


def _validar_iso_rev04(homologacao: Homologacao) -> None:
    """Vem antes da checagem de itens em branco: quem declarou ISO precisa
    saber que falta o certificado, nao que faltam as 15 questoes."""
    if not eh_rev04(homologacao) or not homologacao.iso9001_certificado:
        return
    if (form_r04.SECAO_ISO, 1) not in _com_evidencia(homologacao):
        raise ValueError("Anexe o certificado ISO 9001 - sem ele o questionário precisa ser respondido.")
    if not homologacao.iso9001_validade:
        raise ValueError("Informe a validade do certificado ISO 9001.")


def _validar_rev04(homologacao: Homologacao) -> None:
    """Regras do F 066 rev. 04 que valem pro comprador e pro fornecedor."""
    _validar_iso_rev04(homologacao)
    sem_evidencia = pendencias_evidencia(homologacao)
    if sem_evidencia:
        primeira = sem_evidencia[0]
        raise ValueError(
            f"Documento marcado como Atende precisa da cópia anexada - faltam {len(sem_evidencia)} "
            f"(ex.: item {primeira['item']}: {primeira['texto']})."
        )
    sem_justificativa = pendencias_justificativa(homologacao)
    if sem_justificativa:
        raise ValueError(
            f"Justifique os documentos marcados como Não Aplicável - faltam {len(sem_justificativa)} "
            f"(ex.: item {sem_justificativa[0]['item']})."
        )


def enviar_para_aprovacao(homologacao: Homologacao, usuario: str) -> Homologacao:
    if homologacao.status != Homologacao.STATUS_RASCUNHO:
        raise ValueError("Só um rascunho pode ser enviado para aprovação.")
    validar_para_envio(homologacao)
    recalcular(homologacao)
    homologacao.status = Homologacao.STATUS_EM_APROVACAO
    homologacao.enviado_em = agora_br()
    homologacao.enviado_por = usuario
    db.session.commit()
    return homologacao


def devolver_para_rascunho(homologacao: Homologacao, usuario: str, motivo: str = "") -> Homologacao:
    if homologacao.status != Homologacao.STATUS_EM_APROVACAO:
        raise ValueError("Só uma homologação em aprovação pode ser devolvida.")
    homologacao.status = Homologacao.STATUS_RASCUNHO
    homologacao.enviado_em = None
    homologacao.enviado_por = None
    homologacao.justificativa_decisao = _txt(motivo)[:2000] or None
    db.session.commit()
    return homologacao


def _aplicar_validade(homologacao: Homologacao, referencia: datetime) -> None:
    meses = homologacao.validade_meses or 12
    # Aproximacao por dias (30 dias/mes) - suficiente pro alerta de
    # revalidacao e evita depender de lib de calendario.
    homologacao.valido_ate = (referencia + timedelta(days=30 * meses)).date()


def homologar(homologacao: Homologacao, usuario: str, justificativa: str = "") -> Homologacao:
    if homologacao.status != Homologacao.STATUS_EM_APROVACAO:
        raise ValueError("Só uma homologação em aprovação pode ser homologada.")
    recalcular(homologacao)
    agora = agora_br()
    homologacao.status = Homologacao.STATUS_HOMOLOGADO
    homologacao.decidido_em = agora
    homologacao.decidido_por = usuario
    homologacao.justificativa_decisao = _txt(justificativa)[:2000] or None
    _aplicar_validade(homologacao, agora)
    # Rev. 04: itens que nao pontuaram cheio viram plano de acao (paralelo).
    from . import compras_homologacao_plano_service as plano_svc
    plano_svc.criar_se_pendente(homologacao, usuario)
    db.session.commit()
    return homologacao


def reprovar(homologacao: Homologacao, usuario: str, justificativa: str) -> Homologacao:
    if homologacao.status != Homologacao.STATUS_EM_APROVACAO:
        raise ValueError("Só uma homologação em aprovação pode ser reprovada.")
    justificativa = _txt(justificativa)
    if not justificativa:
        raise ValueError("Informe o motivo da reprovação.")
    recalcular(homologacao)
    homologacao.status = Homologacao.STATUS_REPROVADO
    homologacao.decidido_em = agora_br()
    homologacao.decidido_por = usuario
    homologacao.justificativa_decisao = justificativa[:2000]
    homologacao.valido_ate = None
    db.session.commit()
    return homologacao


def reabrir(homologacao: Homologacao, usuario: str) -> Homologacao:
    if homologacao.status not in (Homologacao.STATUS_HOMOLOGADO, Homologacao.STATUS_REPROVADO):
        raise ValueError("Só uma homologação já decidida pode ser reaberta.")
    homologacao.status = Homologacao.STATUS_RASCUNHO
    homologacao.decidido_em = None
    homologacao.decidido_por = None
    homologacao.enviado_em = None
    homologacao.enviado_por = None
    homologacao.valido_ate = None
    from . import compras_homologacao_plano_service as plano_svc
    plano_svc.cancelar_aberto(homologacao)
    db.session.commit()
    return homologacao


# ── Validade ────────────────────────────────────────────────────────────
def situacao_validade(homologacao: Homologacao, hoje: date | None = None) -> dict:
    """vigente | a_vencer | vencido | None (quando nao se aplica)."""
    if homologacao.status != Homologacao.STATUS_HOMOLOGADO or not homologacao.valido_ate:
        return {"situacao": None, "dias_restantes": None}
    hoje = hoje or date.today()
    dias = (homologacao.valido_ate - hoje).days
    if dias < 0:
        situacao = "vencido"
    elif dias <= DIAS_ALERTA_VENCIMENTO:
        situacao = "a_vencer"
    else:
        situacao = "vigente"
    return {"situacao": situacao, "dias_restantes": dias}


# ── Consulta ────────────────────────────────────────────────────────────
def listar(status: str = "", busca: str = "", validade: str = "") -> list[Homologacao]:
    query = Homologacao.query
    status = _txt(status)
    if status:
        query = query.filter_by(status=status)
    busca = _txt(busca)
    if busca:
        termo = f"%{busca}%"
        query = query.filter(
            db.or_(
                Homologacao.razao_social.ilike(termo),
                Homologacao.nome_fantasia.ilike(termo),
                Homologacao.cnpj.ilike(termo),
                Homologacao.categoria_compra.ilike(termo),
            )
        )
    registros = query.order_by(Homologacao.criado_em.desc()).all()

    validade = _txt(validade)
    if validade:
        registros = [
            r for r in registros if situacao_validade(r)["situacao"] == validade
        ]
    return registros


def metricas(registros: list[Homologacao]) -> dict:
    contagem = {
        "total": len(registros),
        Homologacao.STATUS_RASCUNHO: 0,
        Homologacao.STATUS_EM_APROVACAO: 0,
        Homologacao.STATUS_HOMOLOGADO: 0,
        Homologacao.STATUS_REPROVADO: 0,
        "a_vencer": 0,
        "vencido": 0,
    }
    for registro in registros:
        contagem[registro.status] = contagem.get(registro.status, 0) + 1
        situacao = situacao_validade(registro)["situacao"]
        if situacao in ("a_vencer", "vencido"):
            contagem[situacao] += 1
    return contagem


# ── Fotos (secao 8 do formulario) ───────────────────────────────────────
def anexar_foto(homologacao: Homologacao, arquivo, usuario: str, legenda: str = "") -> ComprasHomologacaoFoto:
    if homologacao.status != Homologacao.STATUS_RASCUNHO:
        raise ValueError("Só é possível anexar fotos enquanto a homologação está em rascunho.")
    if not arquivo or not getattr(arquivo, "filename", ""):
        raise ValueError("Nenhum arquivo recebido.")
    conteudo = arquivo.read()
    if not conteudo:
        raise ValueError("Arquivo vazio.")
    if len(conteudo) > MAX_FOTO_BYTES:
        raise ValueError("Foto muito grande (máximo 8 MB).")
    content_type = (getattr(arquivo, "content_type", "") or "").lower()
    if content_type and content_type not in CONTENT_TYPES_FOTO:
        raise ValueError("Formato não suportado - envie uma imagem (PNG, JPG, WEBP ou GIF).")

    foto = ComprasHomologacaoFoto(
        homologacao_id=homologacao.id,
        nome_arquivo=_txt(arquivo.filename)[:260],
        content_type=content_type or "image/jpeg",
        tamanho_bytes=len(conteudo),
        dados=conteudo,
        legenda=_txt(legenda)[:250] or None,
        enviado_por=usuario,
    )
    db.session.add(foto)
    db.session.commit()
    return foto


def remover_foto(foto: ComprasHomologacaoFoto) -> None:
    if foto.homologacao.status != Homologacao.STATUS_RASCUNHO:
        raise ValueError("Só é possível remover fotos enquanto a homologação está em rascunho.")
    db.session.delete(foto)
    db.session.commit()


# ── Self assessment (link publico do fornecedor) ────────────────────────
def _hash_token(token: str) -> str:
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def convite_ativo(homologacao: Homologacao) -> ComprasHomologacaoConvite | None:
    """O convite ainda nao respondido nem cancelado (pode estar expirado)."""
    for convite in reversed(homologacao.convites):
        if not convite.respondido_em and not convite.cancelado_em:
            return convite
    return None


def ultimo_convite(homologacao: Homologacao) -> ComprasHomologacaoConvite | None:
    return homologacao.convites[-1] if homologacao.convites else None


def situacao_convite(convite: ComprasHomologacaoConvite, agora: datetime | None = None) -> str:
    if convite.respondido_em:
        return "respondido"
    if convite.cancelado_em:
        return "cancelado"
    if convite.expira_em < (agora or agora_br()):
        return "expirado"
    return "pendente"


def _cancelar_convites_abertos(homologacao: Homologacao, agora: datetime) -> None:
    for convite in homologacao.convites:
        if not convite.respondido_em and not convite.cancelado_em:
            convite.cancelado_em = agora


def enviar_para_fornecedor(homologacao: Homologacao, email: str, usuario: str) -> tuple[ComprasHomologacaoConvite, str]:
    """Gera o link de self assessment. Serve tambem pro reenvio: o convite
    anterior e' cancelado (o link velho para de funcionar) e sai um novo,
    com prazo renovado. O token bruto so' existe neste retorno - so' o
    hash fica salvo (mesmo padrao da cotacao do Comex)."""
    if homologacao.status not in (Homologacao.STATUS_RASCUNHO, Homologacao.STATUS_COM_FORNECEDOR):
        raise ValueError("Só um rascunho pode ser enviado ao fornecedor.")
    if not _txt(homologacao.razao_social):
        raise ValueError("Informe a razão social do fornecedor.")
    if not _txt(homologacao.cnpj):
        raise ValueError("Informe o CNPJ do fornecedor antes de enviar o link.")
    email = _txt(email).lower()
    if not _EMAIL_RE.match(email):
        raise ValueError("Informe um e-mail válido para o fornecedor.")

    agora = agora_br()
    _cancelar_convites_abertos(homologacao, agora)
    token = secrets.token_urlsafe(32)
    convite = ComprasHomologacaoConvite(
        email=email[:120],
        token_hash=_hash_token(token),
        expira_em=agora + timedelta(days=VALIDADE_LINK_FORNECEDOR_DIAS),
        enviado_em=agora,
        enviado_por=usuario,
    )
    homologacao.convites.append(convite)
    if not _txt(homologacao.email):
        homologacao.email = email[:120]
    homologacao.status = Homologacao.STATUS_COM_FORNECEDOR
    db.session.commit()
    return convite, token


def cancelar_envio_fornecedor(homologacao: Homologacao, usuario: str) -> Homologacao:
    if homologacao.status != Homologacao.STATUS_COM_FORNECEDOR:
        raise ValueError("Esta homologação não está com o fornecedor.")
    _cancelar_convites_abertos(homologacao, agora_br())
    homologacao.status = Homologacao.STATUS_RASCUNHO
    db.session.commit()
    return homologacao


def obter_convite_por_token(token: str) -> ComprasHomologacaoConvite | None:
    if not token:
        return None
    return ComprasHomologacaoConvite.query.filter_by(token_hash=_hash_token(token)).first()


def convite_aceita_edicao(convite: ComprasHomologacaoConvite) -> bool:
    return (
        situacao_convite(convite) == "pendente"
        and convite.homologacao.status == Homologacao.STATUS_COM_FORNECEDOR
    )


def _exigir_convite_aberto(convite: ComprasHomologacaoConvite) -> Homologacao:
    if not convite_aceita_edicao(convite):
        situacao = situacao_convite(convite)
        if situacao == "respondido":
            raise ValueError("Este questionário já foi enviado. Obrigado!")
        if situacao == "expirado":
            raise ValueError("Este link expirou. Solicite um novo link ao comprador da Columbia.")
        raise ValueError("Este link não está mais ativo. Solicite um novo link ao comprador da Columbia.")
    return convite.homologacao


def salvar_self_assessment(convite: ComprasHomologacaoConvite, dados: dict) -> Homologacao:
    """Rascunho do fornecedor: grava so' os campos liberados pra ele e as
    respostas/comentarios. Pode salvar e voltar pelo mesmo link."""
    homologacao = _exigir_convite_aberto(convite)
    for campo in CAMPOS_FORNECEDOR:
        if campo in dados:
            setattr(homologacao, campo, _txt(dados.get(campo)) or None)
    if "respostas" in dados:
        salvar_respostas(homologacao, dados.get("respostas") or [], commit=False)
    if "responsaveis" in dados:
        salvar_responsaveis(homologacao, dados.get("responsaveis"))
    if eh_rev04(homologacao):
        _aplicar_iso(homologacao, dados)
    recalcular(homologacao)
    db.session.commit()
    return homologacao


def respostas_exigem_evidencia(homologacao: Homologacao, secao_chave: str) -> tuple:
    """Respostas que so' valem com anexo naquela secao. Rev. 04: so' o
    "Atende" dos documentos - no questionario o anexo so' limita a nota."""
    if eh_rev04(homologacao):
        secao = form_r04.secao_por_chave(secao_chave)
        return (form_r04.RESPOSTA_ATENDE,) if secao and secao["evidencia"] == "obrigatoria" else ()
    return form.RESPOSTAS_EXIGEM_EVIDENCIA


def pendencias_evidencia(homologacao: Homologacao) -> list[dict]:
    """Itens cuja resposta exige evidencia e estao sem nenhuma, na ordem do
    formulario."""
    formulario = formulario_da(homologacao)
    respostas = {(r.secao, r.item): r.resposta for r in homologacao.respostas}
    com_evidencia = _com_evidencia(homologacao)
    pendentes = []
    for secao_chave, item, texto in itens_da(homologacao):
        if respostas.get((secao_chave, item)) in respostas_exigem_evidencia(homologacao, secao_chave) \
                and (secao_chave, item) not in com_evidencia:
            pendentes.append({
                "secao": secao_chave,
                "secao_titulo": formulario.secao_por_chave(secao_chave)["titulo"],
                "item": item,
                "texto": texto,
            })
    return pendentes


def concluir_self_assessment(convite: ComprasHomologacaoConvite, dados: dict) -> Homologacao:
    """Envio final do fornecedor: tudo respondido + evidencia onde exigida.
    Volta pra Rascunho pro comprador revisar e mandar pra aprovacao."""
    homologacao = salvar_self_assessment(convite, dados)
    _validar_iso_rev04(homologacao)
    faltando = itens_faltando(homologacao)
    if faltando:
        primeira = faltando[0]
        raise ValueError(
            f"Responda todos os itens antes de enviar - faltam {len(faltando)} "
            f"(ex.: {primeira['secao_titulo']}, item {primeira['item']})."
        )
    if eh_rev04(homologacao):
        _validar_rev04(homologacao)
    sem_evidencia = pendencias_evidencia(homologacao)
    if sem_evidencia:
        primeira = sem_evidencia[0]
        raise ValueError(
            f"Anexe evidência nos itens respondidos como Sim, Parcial ou Conforme - faltam "
            f"{len(sem_evidencia)} (ex.: {primeira['secao_titulo']}, item {primeira['item']})."
        )
    convite.respondido_em = agora_br()
    homologacao.status = Homologacao.STATUS_RASCUNHO
    db.session.commit()
    return homologacao


def anexar_evidencia(convite: ComprasHomologacaoConvite, secao_chave: str, item, arquivo) -> ComprasHomologacaoEvidencia:
    homologacao = _exigir_convite_aberto(convite)
    return _anexar_evidencia(homologacao, secao_chave, item, arquivo, f"Fornecedor ({convite.email})")


def anexar_evidencia_interna(homologacao: Homologacao, secao_chave: str, item, arquivo, usuario: str) -> ComprasHomologacaoEvidencia:
    """Comprador anexa a evidencia (ex.: documento recebido por e-mail) -
    no rev. 04 os documentos so' valem como Atende com a copia anexada."""
    if homologacao.status != Homologacao.STATUS_RASCUNHO:
        raise ValueError("Só é possível anexar evidências enquanto a homologação está em rascunho.")
    return _anexar_evidencia(homologacao, secao_chave, item, arquivo, usuario)


def remover_evidencia_interna(evidencia: ComprasHomologacaoEvidencia) -> None:
    homologacao = evidencia.homologacao
    if homologacao.status != Homologacao.STATUS_RASCUNHO:
        raise ValueError("Só é possível remover evidências enquanto a homologação está em rascunho.")
    homologacao.evidencias.remove(evidencia)
    recalcular(homologacao)
    db.session.commit()


def _item_valido(homologacao: Homologacao, secao_chave: str, item: int) -> str | None:
    """Chave da secao se (secao, item) existe no formulario da homologacao."""
    if eh_rev04(homologacao) and secao_chave == form_r04.SECAO_ISO:
        return form_r04.SECAO_ISO if item == 1 else None
    secao = formulario_da(homologacao).secao_por_chave(secao_chave)
    return secao["chave"] if secao and 1 <= item <= len(secao["itens"]) else None


def _anexar_evidencia(homologacao: Homologacao, secao_chave: str, item, arquivo, enviado_por: str) -> ComprasHomologacaoEvidencia:
    try:
        item = int(item)
    except (TypeError, ValueError):
        item = 0
    chave = _item_valido(homologacao, _txt(secao_chave), item)
    if not chave:
        raise ValueError("Item do formulário inválido.")
    if not arquivo or not getattr(arquivo, "filename", ""):
        raise ValueError("Nenhum arquivo recebido.")
    nome = _txt(arquivo.filename)
    extensao = ("." + nome.rsplit(".", 1)[-1].lower()) if "." in nome else ""
    content_type = TIPOS_EVIDENCIA.get(extensao)
    if not content_type:
        raise ValueError("Formato não suportado - envie PDF, JPG ou PNG.")
    conteudo = arquivo.read(MAX_EVIDENCIA_BYTES + 1)
    if not conteudo:
        raise ValueError("Arquivo vazio.")
    if len(conteudo) > MAX_EVIDENCIA_BYTES:
        raise ValueError("Arquivo muito grande (máximo 10 MB).")

    evidencia = ComprasHomologacaoEvidencia(
        secao=chave,
        item=item,
        nome_arquivo=nome[:260],
        content_type=content_type,
        tamanho_bytes=len(conteudo),
        dados=conteudo,
        enviado_por=_txt(enviado_por)[:100],
    )
    homologacao.evidencias.append(evidencia)
    # No rev. 04 a evidencia mexe na nota (questionario / ISO).
    recalcular(homologacao)
    db.session.commit()
    return evidencia


def evidencia_do_convite(convite: ComprasHomologacaoConvite, evidencia_id) -> ComprasHomologacaoEvidencia | None:
    """So' enxerga evidencia da propria homologacao do link."""
    return next((e for e in convite.homologacao.evidencias if str(e.id) == str(evidencia_id)), None)


def remover_evidencia(convite: ComprasHomologacaoConvite, evidencia: ComprasHomologacaoEvidencia) -> None:
    homologacao = _exigir_convite_aberto(convite)
    homologacao.evidencias.remove(evidencia)
    recalcular(homologacao)
    db.session.commit()
