"""Servico do modulo Comex (gestao de processos de importacao/exportacao).

Workflow: OC -> PO -> Cotacao -> Instrucao e Documentacao -> Coleta ->
Em Transito -> Desembarque -> Desembaraco -> Transporte -> NF/Cambio.
Ver COMEX_ESPECIFICACAO.md na raiz do repo para a especificacao completa.

Nesta primeira leva so os modulos OC e PO tem UI/logica de negocio; os
demais campos ja existem no schema (ComexProcesso em models.py) para as
proximas levas.

Modulo 1 (OC): a importacao e SOB DEMANDA - o operador pesquisa a OC no ERP
e decide explicitamente importar aquela OC especifica. Nao ha sincronizacao
automatica/em massa, porque dentro das OCs do ERP existem compras locais
que nao sao de importacao/exportacao.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
from datetime import datetime, timedelta

from flask import current_app
from sqlalchemy.exc import IntegrityError

from ..compras.services import compras_service
from ..extensions import db
from ..models import ComexComentario, ComexCotacao, ComexCotacaoVolume, ComexDocumento, ComexPoItem, ComexProcesso
from ..models import ComexExportacaoAntecipada, ComexProcessoOrdemFat, ExpedicaoOrdemFat
from ..models import ComexCotacaoDestinatarios, ComexCotacaoModal, ComexCotacaoModalSolicitado, ComexFornecedor
from . import expedicao_photo_storage as storage
from ..tempo import agora_br

# Sequencia oficial do workflow (usada tanto para avancar quanto para a
# funcao "estornar" - que percorre a mesma lista na ordem inversa).
MODULOS_SEQUENCIA = [
    "OC",
    "PO",
    "Cotacao",
    "Instrucao",
    "Coleta",
    "EmTransito",
    "Desembarque",
    "Desembaraco",
    "Transporte",
    "NFCambio",
    "Concluido",
]

# Rotulo amigavel de cada modulo (o nome interno nao tem acento nem espaco).
LABELS_MODULO = {
    "OC": "OC",
    "PO": "PO",
    "Cotacao": "Cotação",
    "Instrucao": "Instrução",
    "Coleta": "Coleta",
    "EmTransito": "Em Trânsito",
    "Desembarque": "Desembarque",
    "Desembaraco": "Desembaraço",
    "Transporte": "Transporte",
    "NFCambio": "NF/Câmbio",
    "Concluido": "Concluído",
    "Cancelado": "Cancelado",
}

STATUS_SLUGS = {
    "OC": "oc",
    "PO": "po",
    "Cotacao": "cotacao",
    "Instrucao": "instrucao",
    "Coleta": "coleta",
    "EmTransito": "em_transito",
    "Desembarque": "desembarque",
    "Desembaraco": "desembaraco",
    "Transporte": "transporte",
    "NFCambio": "nf_cambio",
    "Concluido": "concluido",
    # Fora de MODULOS_SEQUENCIA de proposito: nao e' etapa do workflow (nao
    # avanca nem estorna) - so' sai dele por reativar(). Ver cancelar().
    "Cancelado": "cancelado",
}

STATUS_CANCELADO = "Cancelado"
MODULOS_CANCELAVEIS = ("OC", "PO")

TIPOS_OPERACAO = ("IM", "IA", "EM", "EA")  # IM/IA = Importacao Mar./Aer. | EM/EA = Exportacao Mar./Aer.
DIRECOES_OPERACAO = ("IMPO", "EXPO")
MODAIS_TRANSPORTE = ("Aéreo", "Marítimo")
PAGADORES_FRETE = ("Columbia", "Cliente-Fornecedor")

# Prefixo do ID OP a partir de (direcao_operacao, modal_transporte) - mantem
# o formato simples de 2 letras que ja existia (IM/IA), so estendido para
# tambem cobrir exportacao (EM/EA).
_MAPA_TIPO_OPERACAO = {
    ("IMPO", "Marítimo"): "IM",
    ("IMPO", "Aéreo"): "IA",
    ("EXPO", "Marítimo"): "EM",
    ("EXPO", "Aéreo"): "EA",
}


def _derivar_tipo_operacao(direcao_operacao: str, modal_transporte: str) -> str:
    return _MAPA_TIPO_OPERACAO.get((direcao_operacao, modal_transporte), "IM")


def status_slug(status_modulo: str) -> str:
    return STATUS_SLUGS.get(status_modulo, "oc")


def _parse_dt(value):
    """Converte um valor de data vindo do JSON do navegador (string ISO,
    ja que JSON nao tem tipo data nativo) ou ja como datetime/date (vindo
    direto do driver Postgres) para datetime. Retorna None se nao der pra
    interpretar."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    if hasattr(value, "isoformat") and not isinstance(value, str):
        # date (sem hora) -> datetime a meia-noite
        return datetime.combine(value, datetime.min.time())
    texto = str(value).strip()
    if not texto:
        return None
    try:
        return datetime.fromisoformat(texto.replace("Z", ""))
    except ValueError:
        for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
            try:
                return datetime.strptime(texto, fmt)
            except ValueError:
                continue
    return None


# Exportacao usa os mesmos status da importacao, pulando os que nao se
# aplicam: nao tem compra (OC/PO) nem desembaraco/NF de importacao. Nasce na
# Cotacao a partir da ordem de faturamento conferida (ver importar_ordem_fat).
MODULOS_EXPORTACAO = [
    "Cotacao",
    "Instrucao",
    "Coleta",
    "EmTransito",
    "Desembarque",
    "Transporte",
    "Concluido",
]


def exportacao_da_expedicao(processo: ComexProcesso) -> bool:
    """Fluxo curto so' pra quem nasceu da ordem de faturamento (ou
    antecipado pelo orcamento): processos antigos marcados EXPO na PO seguem
    o fluxo completo onde estao."""
    return processo.ordem_fat is not None or processo.exportacao_antecipada is not None


def aguardando_ordem_fat(processo: ComexProcesso) -> bool:
    """Exportacao antecipada pelo orcamento que ainda nao tem a ordem de
    faturamento vinculada - so' pode cotar."""
    return processo.exportacao_antecipada is not None and processo.ordem_fat is None


def _exigir_ordem_fat_para_sair_da_cotacao(processo: ComexProcesso) -> None:
    if processo.status_modulo == "Cotacao" and aguardando_ordem_fat(processo):
        raise ValueError("Exportação antecipada pelo orçamento: vincule a ordem de faturamento "
                         "conferida antes de seguir para a Instrução.")


def sequencia_do_processo(processo: ComexProcesso) -> list[str]:
    return MODULOS_EXPORTACAO if exportacao_da_expedicao(processo) else MODULOS_SEQUENCIA


def _proximo_modulo(atual: str, sequencia: list[str] = MODULOS_SEQUENCIA) -> str | None:
    try:
        idx = sequencia.index(atual)
    except ValueError:
        return None
    if idx + 1 >= len(sequencia):
        return None
    return sequencia[idx + 1]


def _modulo_anterior(atual: str, sequencia: list[str] = MODULOS_SEQUENCIA) -> str | None:
    try:
        idx = sequencia.index(atual)
    except ValueError:
        return None
    if idx == 0:
        return None
    return sequencia[idx - 1]


def gerar_id_op(tipo_operacao: str) -> str:
    """Gera o identificador unico do processo (ID OP), ex.: IM-2026-00001.
    Sequencial por tipo de operacao + ano. Nao confundir com `ref_ff`
    (Referencia Freight Forward), que e atribuida pelo freight forward mais
    adiante no workflow, nao gerada pelo sistema.

    Ordena pelo proprio `id_op` (nao pelo id/PK de insercao): como o numero
    final e zero-padded (%05d) e todos os candidatos compartilham o mesmo
    prefixo+ano, ordem alfabetica desc aqui equivale a ordem numerica desc.
    Isso importa porque o numero pode ser regenerado pra um processo
    existente a qualquer momento (troca de direcao/modal com a PO ainda em
    Rascunho - ver `salvar_po`), entao a ordem de insercao no banco nao
    reflete mais o maior numero ja usado; usar `id DESC` ali causava colisao
    (numero duplicado) quando processos eram renumerados fora de ordem."""
    tipo_operacao = tipo_operacao if tipo_operacao in TIPOS_OPERACAO else "IM"
    ano = agora_br().year
    prefixo = f"{tipo_operacao}-{ano}-"
    ultimo = (
        ComexProcesso.query
        .filter(ComexProcesso.id_op.like(f"{prefixo}%"))
        .order_by(ComexProcesso.id_op.desc())
        .first()
    )
    proximo_num = 1
    if ultimo and ultimo.id_op:
        try:
            proximo_num = int(ultimo.id_op.rsplit("-", 1)[-1]) + 1
        except ValueError:
            proximo_num = 1
    return f"{prefixo}{proximo_num:05d}"


# Campos de template RTF (cabecalho/rodape impresso da OC) e logs de texto
# livre que vem do ERP dentro de oc_json - nao usados pelo Comex, mas
# grandes (varios KB cada) o suficiente para pesar a resposta e a tela de
# busca. Removidos antes de mandar para o navegador/gravar no payload.
_CAMPOS_OC_IRRELEVANTES = {"cabecalho", "rodape", "log_autori_extorn", "log_rastreio"}


def _limpar_oc_json(oc_json: dict) -> dict:
    return {k: v for k, v in (oc_json or {}).items() if k not in _CAMPOS_OC_IRRELEVANTES}


def _bloqueio_importacao(linha: dict, oc_json: dict) -> str | None:
    """Busca por numero traz a OC em qualquer situacao: diz por que nao da
    pra importar, em vez de "nenhuma OC encontrada"."""
    if oc_json.get("cancelado"):
        return "OC cancelada no ERP."
    if not int(linha.get("qtd_itens") or 0):
        return "OC sem itens no ERP."
    if not int(linha.get("itens_com_saldo") or 0):
        return "OC sem saldo a receber (já recebida por completo)."
    return None


def buscar_ocs_para_importar(termo: str = "", cod_empresa: int | None = None, limite: int = 300) -> list[dict]:
    """Busca OCs em aberto (com saldo a receber) no ERP que combinem com o
    termo digitado - por numero da OC ou nome do fornecedor.

    Reaproveita compras_service.ordens_compra_cif_recentes (mesma consulta
    ja usada pela automacao de Solicitacoes CIF e pela Torre de Controle),
    que traz o cabecalho completo da OC + cadastro do fornecedor (incluindo
    e-mail) via tord_com/tfornece - fonte muito mais confiavel que o
    historico de compras (que so tem fornecedor/valor preenchidos quando ja
    existe lancamento em tcompras).

    Nao importa nada sozinho: so devolve candidatas para o operador revisar
    e escolher manualmente (ver `importar_oc`). Cada candidata vem marcada
    com `ja_importada` para o operador saber se aquela OC ja virou um
    processo Comex."""
    termo_norm = (termo or "").strip()
    termo_digitos = termo_norm.replace(" ", "")
    numero = int(termo_digitos) if termo_digitos.isdigit() else None

    filtrado_no_banco = True
    try:
        linhas = compras_service.buscar_ordens_compra_comex(
            cod_ordem_compra=numero,
            fornecedor=None if numero is not None else termo_norm,
            cod_empresa=cod_empresa,
            limite=limite,
        )
    except Exception:
        # Consulta nova falhou (ex.: coluna diferente no ERP): volta pra
        # busca antiga em vez de derrubar a tela. Fica no log pra corrigir.
        current_app.logger.exception("Comex: busca de OC filtrada no banco falhou - usando a busca antiga")
        filtrado_no_banco = False
        linhas = compras_service.ordens_compra_cif_recentes(
            cod_empresa=cod_empresa,
            janela_dias=3650,  # praticamente sem limite de atraso da previsao
            limite=max(1, min(int(limite or 300), 2000)),
        )

    candidatas = []
    for linha in linhas:
        oc_json = _limpar_oc_json(linha.get("oc_json") or {})
        fornecedor_json = _limpar_oc_json(linha.get("fornecedor_json") or {})
        try:
            cod_oc = int(linha.get("cod_ordem_compra") or oc_json.get("codigo") or 0)
        except (TypeError, ValueError):
            continue
        if not cod_oc:
            continue
        candidatas.append({
            "cod_empresa": oc_json.get("cod_empresa"),
            "cod_ordem_compra": cod_oc,
            "fornecedor": (oc_json.get("fornecedor") or fornecedor_json.get("razao_social")
                           or fornecedor_json.get("nome") or "(Sem fornecedor)"),
            "situacao_oc": oc_json.get("status") or ("Cancelada" if oc_json.get("cancelado") else "Aberta"),
            "total": oc_json.get("totalgeral"),
            "total_produtos": oc_json.get("subtotal"),
            "data": oc_json.get("data"),
            "previsao_entrega": linha.get("previsao_entrega"),
            "email_fornecedor": fornecedor_json.get("e_mail") or fornecedor_json.get("email_danfe"),
            "bloqueio": _bloqueio_importacao(linha, oc_json) if filtrado_no_banco else None,
            "_oc_json": oc_json,
            "_fornecedor_json": fornecedor_json,
        })

    if termo_norm and not filtrado_no_banco:
        termo_lower = termo_norm.lower()

        def _combina(c: dict) -> bool:
            if termo_digitos.isdigit() and str(c.get("cod_ordem_compra") or "") == termo_digitos:
                return True
            if termo_lower in str(c.get("fornecedor") or "").lower():
                return True
            return False

        candidatas = [c for c in candidatas if _combina(c)]

    ja_importadas = {
        (row.cod_empresa, row.cod_ordem_compra)
        for row in ComexProcesso.query.with_entities(
            ComexProcesso.cod_empresa, ComexProcesso.cod_ordem_compra
        ).all()
    }
    for c in candidatas:
        c["ja_importada"] = (c.get("cod_empresa"), c.get("cod_ordem_compra")) in ja_importadas

    return candidatas


# Nomes de campo candidatos por dado que precisamos, tentados em ordem
# (case-insensitive) contra o item_json cru vindo do ERP (tord_aux via
# to_jsonb). A primeira opcao de cada lista foi confirmada inspecionando o
# schema real em 2026-08-07 (ver SQL_COMEX_DIAG_TORD_AUX); as demais ficam
# como fallback caso o schema mude. O que nao for encontrado fica None e o
# operador preenche manualmente na tela (os campos da PO sao editaveis).
_CAMPOS_CANDIDATOS = {
    "codigo": ("cod_interno", "codigo_interno", "codigo", "cod_produto"),
    "descricao": ("descricao", "nome", "material", "produto"),
    "ncm": ("classificacao_fiscal", "ncm", "cod_ncm", "ncm_code", "codigo_ncm"),
    "pn": ("codigo_na_fabrica", "pn", "part_number", "cod_fabricante", "referencia_fabricante"),
    "quantidade": ("qtde", "quantidade", "qtd"),
    "valor_unitario": ("preco_unitario", "valor_unitario", "vl_unitario", "preco", "valor"),
    "valor_total": ("total", "valor_total", "vl_total"),
}


def _primeiro_valor(dicionario: dict, chaves: tuple[str, ...]):
    """Procura, no dicionario informado, a primeira chave que bater
    (case-insensitive) com alguma das `chaves` candidatas e tiver um valor
    nao vazio."""
    if not dicionario:
        return None
    mapa_lower = {str(k).lower(): v for k, v in dicionario.items()}
    for chave in chaves:
        valor = mapa_lower.get(chave.lower())
        if valor not in (None, ""):
            return valor
    return None


def _mapear_itens_erp(linhas: list[dict], oc_origem: str | None = None) -> list[dict]:
    itens = []
    for linha in linhas:
        item_json = linha.get("item_json") or {}
        quantidade = _primeiro_valor(item_json, _CAMPOS_CANDIDATOS["quantidade"])
        valor_unitario = _primeiro_valor(item_json, _CAMPOS_CANDIDATOS["valor_unitario"])
        valor_total = _primeiro_valor(item_json, _CAMPOS_CANDIDATOS["valor_total"])
        try:
            quantidade = float(quantidade) if quantidade is not None else None
            valor_unitario = float(valor_unitario) if valor_unitario is not None else None
            valor_total = float(valor_total) if valor_total is not None else None
        except (TypeError, ValueError):
            pass
        if valor_total is None and quantidade is not None and valor_unitario is not None:
            valor_total = round(quantidade * valor_unitario, 2)
        itens.append({
            "codigo": _primeiro_valor(item_json, _CAMPOS_CANDIDATOS["codigo"]),
            "descricao": _primeiro_valor(item_json, _CAMPOS_CANDIDATOS["descricao"]),
            "ncm": _primeiro_valor(item_json, _CAMPOS_CANDIDATOS["ncm"]),
            "pn": _primeiro_valor(item_json, _CAMPOS_CANDIDATOS["pn"]),
            "quantidade": quantidade,
            "valor_unitario": valor_unitario,
            "valor_total": valor_total,
            "oc_origem": str(oc_origem) if oc_origem is not None else None,
        })
    return itens


def buscar_itens_oc_no_erp(processo: ComexProcesso, outras_ocs_ids: list[int] | None = None) -> list[dict]:
    """Busca no ERP os itens (material) da OC do processo, ja tentando
    mapear codigo/NCM/PN/descricao/quantidade/valores a partir do que a
    consulta trouxer (tord_aux). So retorna sugestoes - o operador revisa e
    ajusta na tela antes de salvar (ver `salvar_itens_po`).

    Quando mais de uma OC do mesmo fornecedor vira uma unica PO/embarque
    (campo "Outras OCs do mesmo fornecedor" na tela), os itens de TODAS
    elas precisam entrar na lista, nao so os da OC principal. `outras_ocs_ids`
    traz o id (ComexProcesso.id) de cada OC extra marcada na tela agora; se
    nao vier nada mas a PO ja tiver sido salva combinando OCs antes, cai no
    fallback de `processo.po_ocs_vinculadas` (o que ja foi salvo)."""
    codigos = [processo.cod_ordem_compra] if processo.cod_ordem_compra else []

    if outras_ocs_ids:
        outras = ComexProcesso.query.filter(ComexProcesso.id.in_(outras_ocs_ids)).all()
        codigos += [o.cod_ordem_compra for o in outras if o.cod_ordem_compra]
    elif processo.po_ocs_vinculadas:
        try:
            salvos = json.loads(processo.po_ocs_vinculadas)
        except (TypeError, ValueError):
            salvos = []
        codigos += [c for c in salvos if c]

    codigos_unicos = list(dict.fromkeys(codigos))  # remove duplicado, preserva ordem
    if not codigos_unicos:
        return []

    itens = []
    for codigo_oc in codigos_unicos:
        linhas = compras_service.itens_ordem_compra(cod_ordem_compra=codigo_oc, cod_empresa=processo.cod_empresa)
        itens.extend(_mapear_itens_erp(linhas, oc_origem=codigo_oc))
    return itens


def importar_oc(oc_header: dict, usuario: str) -> ComexProcesso:
    """Cria um novo ComexProcesso (Modulo 1) a partir dos dados de UMA OC
    escolhida pelo operador na busca (`buscar_ocs_para_importar`). Os dados
    vem do proprio resultado da busca (mesma consulta, sem round-trip extra
    ao ERP) para evitar inconsistencia entre o que foi mostrado e o que foi
    importado.

    O tipo de operacao (IM/IA) NAO e perguntado aqui - na pratica so fica
    claro se e maritimo ou aereo mais perto da definicao do frete, entao a
    escolha acontece no Modulo 2 (PO), antes de criar a PO (ver `salvar_po`).
    O processo nasce com o prefixo provisorio "IM" no ID OP, que e trocado
    automaticamente se o operador escolher "IA" na PO."""
    cod_empresa = oc_header.get("cod_empresa")
    cod_ordem_compra = oc_header.get("cod_ordem_compra")
    if not cod_ordem_compra:
        raise ValueError("Ordem de compra invalida para importacao.")

    existente = ComexProcesso.query.filter_by(
        cod_empresa=cod_empresa, cod_ordem_compra=cod_ordem_compra
    ).first()
    if existente:
        if existente.status_modulo == STATUS_CANCELADO:
            raise ValueError(f"Esta OC já foi importada como o processo {existente.id_op}, que está cancelado - reative-o no card Cancelado.")
        raise ValueError(f"Esta OC ja foi importada como o processo {existente.id_op}.")

    if oc_header.get("bloqueio"):
        raise ValueError(f"Esta OC não pode ser importada: {oc_header['bloqueio']}")

    oc_json = oc_header.get("_oc_json") or {}

    agora = agora_br()
    processo = ComexProcesso(
        id_op=gerar_id_op("IM"),
        tipo_operacao="IM",
        direcao_operacao="IMPO",
        modal_transporte="Marítimo",
        status_modulo="OC",
        status_slug=status_slug("OC"),
        criado_por=usuario,
        atualizado_por=usuario,
        cod_empresa=cod_empresa,
        cod_ordem_compra=cod_ordem_compra,
        cod_compra=str(oc_json.get("codigo") or cod_ordem_compra or ""),
        fornecedor=oc_header.get("fornecedor"),
        comprador=oc_json.get("solicitante"),
        dt_lancamento_oc=_parse_dt(oc_header.get("data") or oc_json.get("data")),
        dt_recebimento_oc=None,
        total_produtos_oc=oc_header.get("total_produtos"),
        total_oc=oc_header.get("total"),
        situacao_oc=oc_header.get("situacao_oc"),
        oc_origem_payload=json.dumps(oc_header, default=str, ensure_ascii=False),
    )
    db.session.add(processo)
    db.session.commit()
    return processo


def criar_processo_manual(fornecedor: str, referencia: str, usuario: str) -> ComexProcesso:
    """Cria um ComexProcesso (Modulo 1) totalmente manual, sem OC vinculada
    do ERP - pra embarques que sao so acompanhamento de processo (ex.:
    amostra, reposicao em garantia), sem compra por tras. Segue o MESMO
    workflow de um processo importado por OC dai em diante (Criar PO,
    Cotacao, Instrucao, ...) - so nasce sem cod_ordem_compra/dados de OC, e
    os itens da PO sao preenchidos a mao (nao tem OC no ERP pra puxar).

    `cod_ordem_compra is None` e' o proprio sinal de "processo manual" -
    `importar_oc` sempre exige uma OC, entao nenhum processo vindo de la
    fica sem ela; nao precisa de uma coluna nova so pra marcar isso."""
    fornecedor = str(fornecedor or "").strip()
    if not fornecedor:
        raise ValueError("Informe o fornecedor.")

    agora = agora_br()
    processo = ComexProcesso(
        id_op=gerar_id_op("IM"),
        tipo_operacao="IM",
        direcao_operacao="IMPO",
        modal_transporte="Marítimo",
        status_modulo="OC",
        status_slug=status_slug("OC"),
        criado_por=usuario,
        atualizado_por=usuario,
        fornecedor=fornecedor,
    )
    db.session.add(processo)
    db.session.commit()

    referencia = str(referencia or "").strip()
    if referencia:
        adicionar_comentario(processo, f"Referência/motivo: {referencia}", usuario)

    return processo


def buscar_ordens_fat_para_exportar(termo: str, vinculando: bool = False) -> list[dict]:
    """Ordens de faturamento da Conferencia de Expedicao pelo numero da
    ordem ou do orcamento - so' as ja' conferidas podem virar exportacao."""
    termo = str(termo or "").strip()
    if not termo:
        raise ValueError("Informe o número da ordem de faturamento ou do orçamento.")
    filtro = ExpedicaoOrdemFat.orcamento == termo
    if termo.isdigit():
        filtro = db.or_(filtro, ExpedicaoOrdemFat.cod_ordem_fat == int(termo))
    ordens = ExpedicaoOrdemFat.query.filter(filtro, ExpedicaoOrdemFat.excluido.is_(False)).order_by(
        ExpedicaoOrdemFat.cod_ordem_fat.desc()).limit(50).all()
    puxadas = {}
    if ordens:
        vinculos = ComexProcessoOrdemFat.query.filter(
            ComexProcessoOrdemFat.cod_ordem_fat.in_([o.cod_ordem_fat for o in ordens])).all()
        puxadas = {v.cod_ordem_fat: v.processo for v in vinculos}
    resultado = []
    for o in ordens:
        processo = puxadas.get(o.cod_ordem_fat)
        antecipada = _antecipada_aguardando(o.orcamento)
        bloqueio = None
        if processo:
            bloqueio = f"Já puxada no processo {processo.id_op}."
        elif antecipada and not vinculando:
            bloqueio = f"Orçamento com exportação antecipada {antecipada.id_op} - vincule por lá."
        elif not o.conferido_at:
            bloqueio = "Ainda não foi conferida na Conferência de Expedição."
        resultado.append({
            "cod_ordem_fat": o.cod_ordem_fat, "orcamento": o.orcamento, "pedido": o.pedido, "cliente": o.cliente,
            "status": o.status, "operacao_tipo": o.operacao_tipo, "numero_nf": o.numero_nf,
            "conferido_em": o.conferido_at.strftime("%d/%m/%Y %H:%M") if o.conferido_at else None,
            "os": _os_da_ordem_fat(o), "bloqueio": bloqueio,
        })
    return resultado


def _antecipada_aguardando(orcamento) -> ComexProcesso | None:
    """Exportacao antecipada ativa (sem ordem vinculada) desse orcamento."""
    orcamento = str(orcamento or "").strip()
    if not orcamento:
        return None
    for registro in ComexExportacaoAntecipada.query.filter_by(orcamento=orcamento).all():
        p = registro.processo
        if p.ordem_fat is None and p.status_modulo != STATUS_CANCELADO:
            return p
    return None


def criar_exportacao_antecipada(orcamento: str, cliente: str, modal_transporte: str, usuario: str) -> ComexProcesso:
    """Exportacao pelo ORCAMENTO, sem ordem de faturamento conferida - pra
    antecipar a cotacao do frete. Se a expedicao ja' tem ordem desse
    orcamento (mesmo pendente), cliente e OS vem de la'."""
    orcamento = str(orcamento or "").strip()[:80]
    if not orcamento:
        raise ValueError("Informe o número do orçamento.")
    if modal_transporte not in MODAIS_TRANSPORTE:
        raise ValueError("Selecione o modal de transporte (Aéreo ou Marítimo).")
    existente = _antecipada_aguardando(orcamento)
    if existente:
        raise ValueError(f"O orçamento {orcamento} já tem a exportação antecipada {existente.id_op}.")
    ordens = ExpedicaoOrdemFat.query.filter_by(orcamento=orcamento, excluido=False).all()
    cliente = str(cliente or "").strip()[:160] or next((o.cliente for o in ordens if o.cliente), "")
    if not cliente:
        raise ValueError("Informe o cliente (o orçamento ainda não tem ordem de faturamento na expedição).")
    os_texto = ", ".join(dict.fromkeys(x for o in ordens for x in _os_da_ordem_fat(o).split(", ") if x))

    tipo_operacao = _derivar_tipo_operacao("EXPO", modal_transporte)
    processo = ComexProcesso(
        id_op=gerar_id_op(tipo_operacao),
        tipo_operacao=tipo_operacao,
        direcao_operacao="EXPO",
        modal_transporte=modal_transporte,
        status_modulo=MODULOS_EXPORTACAO[0],
        status_slug=status_slug(MODULOS_EXPORTACAO[0]),
        criado_por=usuario,
        atualizado_por=usuario,
        fornecedor=cliente,
        numero_os=os_texto[:200] or None,
    )
    db.session.add(processo)
    db.session.flush()
    db.session.add(ComexExportacaoAntecipada(processo_id=processo.id, orcamento=orcamento, cliente=cliente))
    db.session.commit()
    return processo


def ordens_fat_para_vincular(processo: ComexProcesso, termo: str = "") -> list[dict]:
    """Ordens que podem ser ligadas a uma exportacao antecipada: por padrao
    as do orcamento dela; `termo` permite achar por outro numero (orcamento
    digitado diferente do da ordem)."""
    if not aguardando_ordem_fat(processo):
        raise ValueError("Este processo não está aguardando ordem de faturamento.")
    return buscar_ordens_fat_para_exportar(termo or processo.exportacao_antecipada.orcamento, vinculando=True)


def vincular_ordem_fat(processo: ComexProcesso, cod_ordem_fat, usuario: str) -> ComexProcesso:
    """Liga a ordem de faturamento (ja' conferida) a' exportacao antecipada -
    a cotacao feita antes fica no processo e o fluxo segue normal."""
    _exigir_nao_cancelado(processo)
    if not aguardando_ordem_fat(processo):
        raise ValueError("Este processo não está aguardando ordem de faturamento.")
    ordem = _ordem_fat_conferida_livre(cod_ordem_fat)
    db.session.add(ComexProcessoOrdemFat(processo_id=processo.id, cod_ordem_fat=ordem.cod_ordem_fat,
                                         orcamento=ordem.orcamento, pedido=ordem.pedido, cliente=ordem.cliente))
    processo.numero_os = _os_da_ordem_fat(ordem)[:200] or processo.numero_os
    processo.atualizado_em = agora_br()
    processo.atualizado_por = usuario
    texto = f"Ordem de faturamento {ordem.cod_ordem_fat} vinculada (orçamento {ordem.orcamento or '—'}, cliente {ordem.cliente or '—'})."
    db.session.add(ComexComentario(processo_id=processo.id, texto=texto, criado_por=usuario))
    db.session.commit()
    return processo


def _ordem_fat_conferida_livre(cod_ordem_fat) -> ExpedicaoOrdemFat:
    try:
        cod_ordem_fat = int(cod_ordem_fat)
    except (TypeError, ValueError):
        raise ValueError("Ordem de faturamento inválida.")
    ordem = ExpedicaoOrdemFat.query.filter_by(cod_ordem_fat=cod_ordem_fat, excluido=False).first()
    if not ordem:
        raise ValueError("Ordem de faturamento não encontrada na Conferência de Expedição.")
    if not ordem.conferido_at:
        raise ValueError("Esta ordem de faturamento ainda não foi conferida - confira na expedição antes de iniciar a exportação.")
    ja = ComexProcessoOrdemFat.query.filter_by(cod_ordem_fat=cod_ordem_fat).first()
    if ja:
        raise ValueError(f"Esta ordem de faturamento já foi puxada como o processo {ja.processo.id_op}.")
    return ordem


def _os_da_ordem_fat(ordem: ExpedicaoOrdemFat) -> str:
    return ", ".join(dict.fromkeys(str(i.n_os).strip() for i in ordem.itens if str(i.n_os or "").strip()))


def importar_ordem_fat(cod_ordem_fat, modal_transporte: str, usuario: str) -> ComexProcesso:
    """Cria um processo de EXPORTACAO a partir de uma ordem de faturamento
    ja' conferida. A direcao fica fixa em EXPO e o processo nasce na Cotacao
    (exportacao nao tem OC/PO - ver MODULOS_EXPORTACAO)."""
    if modal_transporte not in MODAIS_TRANSPORTE:
        raise ValueError("Selecione o modal de transporte (Aéreo ou Marítimo).")
    ordem = _ordem_fat_conferida_livre(cod_ordem_fat)
    cod_ordem_fat = ordem.cod_ordem_fat
    antecipada = _antecipada_aguardando(ordem.orcamento)
    if antecipada:
        raise ValueError(f"O orçamento {ordem.orcamento} tem a exportação antecipada {antecipada.id_op} - "
                         "vincule a ordem por lá para não duplicar o processo.")

    tipo_operacao = _derivar_tipo_operacao("EXPO", modal_transporte)
    processo = ComexProcesso(
        id_op=gerar_id_op(tipo_operacao),
        tipo_operacao=tipo_operacao,
        direcao_operacao="EXPO",
        modal_transporte=modal_transporte,
        status_modulo=MODULOS_EXPORTACAO[0],
        status_slug=status_slug(MODULOS_EXPORTACAO[0]),
        criado_por=usuario,
        atualizado_por=usuario,
        # "fornecedor" e' a contraparte do processo em todas as telas; na
        # exportacao e' o cliente.
        fornecedor=ordem.cliente,
        numero_os=_os_da_ordem_fat(ordem)[:200] or None,
    )
    db.session.add(processo)
    db.session.flush()
    db.session.add(ComexProcessoOrdemFat(processo_id=processo.id, cod_ordem_fat=cod_ordem_fat,
                                         orcamento=ordem.orcamento, pedido=ordem.pedido, cliente=ordem.cliente))
    db.session.commit()
    return processo


# Campos da OC (Modulo 1) que o operador pode corrigir manualmente depois da
# importacao - os dados vem do ERP, mas o requisito pede edicao total antes
# de virar PO.
_CAMPOS_OC_EDITAVEIS = (
    "fornecedor",
    "comprador",
    "cod_compra",
    "numero_os",
    "total_oc",
    "total_produtos_oc",
    "situacao_oc",
)


def editar_oc(processo: ComexProcesso, dados: dict, usuario: str) -> ComexProcesso:
    """Modulo 1 - Salvar/Editar: permite corrigir manualmente os campos da OC
    importada, enquanto o processo ainda nao avancou para a PO."""
    if processo.status_modulo != "OC":
        raise ValueError("Só é possível editar a OC enquanto o processo está no módulo OC.")

    for campo in _CAMPOS_OC_EDITAVEIS:
        if campo in dados:
            valor = dados.get(campo)
            if campo in ("total_oc", "total_produtos_oc"):
                try:
                    valor = float(valor) if valor not in (None, "") else None
                except (TypeError, ValueError):
                    raise ValueError(f"Valor inválido para o campo {campo}.")
            setattr(processo, campo, valor)

    dt_lancamento = dados.get("dt_lancamento_oc")
    if "dt_lancamento_oc" in dados:
        processo.dt_lancamento_oc = _parse_dt(dt_lancamento)

    processo.atualizado_em = agora_br()
    processo.atualizado_por = usuario
    db.session.commit()
    return processo


def apagar_oc(processo: ComexProcesso) -> None:
    """Modulo 1 - Apagar: remove o processo Comex criado a partir da OC,
    liberando essa OC (cod_empresa + cod_ordem_compra) para ser importada de
    novo mais tarde. So permitido enquanto ainda nao existe PO."""
    if processo.status_modulo != "OC":
        raise ValueError("Só é possível apagar a OC enquanto o processo está no módulo OC (antes da PO).")
    db.session.delete(processo)
    db.session.commit()


def _exigir_nao_cancelado(processo: ComexProcesso) -> None:
    if processo.status_modulo == STATUS_CANCELADO:
        raise ValueError("Processo cancelado - reative-o antes de continuar.")


def cancelar(processo: ComexProcesso, motivo: str, usuario: str) -> list[str]:
    """Cancela o processo (so' em OC ou PO): fica registrado como Cancelado,
    com o motivo no historico de comentarios - diferente de apagar_oc, nao
    some e a OC nao volta a ficar disponivel pra importar. As OCs combinadas
    nesta PO voltam a ser OCs soltas (como no estorno da PO);
    po_ocs_vinculadas fica guardado pra reativar() religar. Devolve as OCs
    liberadas."""
    if exportacao_da_expedicao(processo):
        if processo.status_modulo != MODULOS_EXPORTACAO[0]:
            raise ValueError("Só é possível cancelar exportação no status Cotação.")
    elif processo.status_modulo not in MODULOS_CANCELAVEIS:
        raise ValueError("Só é possível cancelar processos no status OC ou PO.")
    if processo.po_processo_principal_id:
        raise ValueError("Esta OC está combinada na PO de outro processo - cancele o processo principal.")
    motivo = str(motivo or "").strip()
    if not motivo:
        raise ValueError("Informe o motivo do cancelamento.")
    liberadas = ComexProcesso.query.filter_by(po_processo_principal_id=processo.id).all()
    for vinculada in liberadas:
        vinculada.po_processo_principal_id = None
    anterior = processo.status_modulo
    processo.status_modulo = STATUS_CANCELADO
    processo.status_slug = status_slug(STATUS_CANCELADO)
    processo.atualizado_em = agora_br()
    processo.atualizado_por = usuario
    ocs_liberadas = [str(v.cod_ordem_compra or v.id_op) for v in liberadas]
    texto = f"Processo CANCELADO (estava em {LABELS_MODULO.get(anterior, anterior)}). Motivo: {motivo}"
    if ocs_liberadas:
        texto += f" | OCs combinadas liberadas: {', '.join(ocs_liberadas)}."
    db.session.add(ComexComentario(processo_id=processo.id, texto=texto, criado_por=usuario))
    db.session.commit()
    return ocs_liberadas


def reativar(processo: ComexProcesso, usuario: str) -> dict:
    """Desfaz o cancelamento: volta pra PO se a PO existia, senao pra OC.
    Religa as OCs que estavam combinadas na PO, se ainda estiverem soltas
    no status OC; as que ja seguiram outro caminho ficam de fora (e saem de
    po_ocs_vinculadas)."""
    if processo.status_modulo != STATUS_CANCELADO:
        raise ValueError("Este processo não está cancelado.")
    if exportacao_da_expedicao(processo):
        destino = MODULOS_EXPORTACAO[0]
    else:
        destino = "PO" if processo.po_numero else "OC"
    religadas, nao_religadas = [], []
    if destino == "PO" and processo.po_ocs_vinculadas:
        try:
            codigos = json.loads(processo.po_ocs_vinculadas) or []
        except (TypeError, ValueError):
            codigos = []
        mantidas = []
        for codigo in codigos:
            if str(codigo) == str(processo.cod_ordem_compra):
                mantidas.append(codigo)
                continue
            outra = ComexProcesso.query.filter(
                ComexProcesso.cod_ordem_compra == codigo,
                ComexProcesso.fornecedor == processo.fornecedor,
                ComexProcesso.id != processo.id,
            ).first()
            if outra and outra.status_modulo == "OC" and outra.po_processo_principal_id is None:
                outra.po_processo_principal_id = processo.id
                mantidas.append(codigo)
                religadas.append(str(codigo))
            else:
                nao_religadas.append(str(codigo))
        processo.po_ocs_vinculadas = json.dumps(mantidas, default=str) if mantidas else None
    processo.status_modulo = destino
    processo.status_slug = status_slug(destino)
    processo.atualizado_em = agora_br()
    processo.atualizado_por = usuario
    texto = f"Processo REATIVADO (voltou para {LABELS_MODULO[destino]})."
    if religadas:
        texto += f" OCs combinadas de volta: {', '.join(religadas)}."
    if nao_religadas:
        texto += f" OCs que não voltaram para a PO (já seguiram outro caminho): {', '.join(nao_religadas)}."
    db.session.add(ComexComentario(processo_id=processo.id, texto=texto, criado_por=usuario))
    db.session.commit()
    return {"destino": destino, "religadas": religadas, "nao_religadas": nao_religadas}


def _mesmo_fornecedor(processos: list[ComexProcesso]) -> bool:
    fornecedores = {(p.fornecedor or "").strip().lower() for p in processos}
    return len(fornecedores) <= 1


# Campos operacionais gerais (visiveis a partir da PO, editaveis ao longo de
# todo o processo) - texto livre (ate 20 caracteres, reforcado no front-end;
# a coluna tem folga ate 40 so como buffer) e data, mapeados pelo nome que
# chega no JSON do navegador para o atributo do model (o front manda "eta",
# que reaproveita a coluna ja existente `em_transito_eta`).
# Campos operacionais com a etapa a partir da qual cada um fica EDITAVEL.
#
# Regra definida pelo Comex: o campo so' abre no modulo indicado e
# permanece aberto nas etapas seguintes; a exigencia de estar preenchido
# acontece SO' na conclusao do processo (nenhuma transicao intermediaria
# trava por campo vazio - ver avancar_status).
#
# (chave_json, atributo_no_model, label, modulo_minimo, tipo)
CAMPOS_OPERACIONAIS = (
    ("ref_ff", "ref_ff", "REF Agente de Carga", "EmTransito", "texto"),
    ("ref_despachante", "ref_despachante", "REF. Despachante", "EmTransito", "texto"),
    ("invoice_numero", "invoice_numero", "Invoice", "EmTransito", "texto"),
    ("bl_awb", "bl_awb", "BL/AWB", "EmTransito", "texto"),
    ("etd", "etd", "ETD", "EmTransito", "data"),
    ("eta", "em_transito_eta", "ETA", "EmTransito", "data"),
    ("numerario_numero", "numerario_numero", "Nº do Numerário", "Desembarque", "texto"),
    ("numerario_valor", "numerario_valor", "Valor do Numerário", "Desembarque", "valor"),
    ("numerario_data_pagamento", "numerario_data_pagamento", "Data de pagamento", "Desembarque", "data"),
    ("entrega_real", "entrega_real", "Entrega Real", "Desembaraco", "texto"),
    ("nf_impo", "nf_impo", "NF IMPO", "Desembaraco", "texto"),
    ("di_numero", "di_numero", "Número da DI", "Desembaraco", "texto"),
    ("di_data", "di_data", "Data da DI", "Desembaraco", "data"),
    # Nasce preenchida ao concluir e segue editavel no Concluido - por isso
    # NAO entra na lista de exigidos pra concluir (seria impossivel).
    ("data_fechamento", "data_fechamento", "Data de fechamento", "Concluido", "data"),
)

# Campos gerais que nao seguem a regra por modulo (continuam editaveis
# desde a PO, como sempre foram).
_CAMPOS_GERAIS_DATA = {
    "po_data": "po_data",
    "previsao_entrega": "previsao_entrega",
}

# Preenchido automaticamente na conclusao, logo nao pode ser exigido antes.
_CAMPO_AUTO_NA_CONCLUSAO = "data_fechamento"

# A partir de qual modulo o formulario "Editar dados de embarque" (com
# TODOS os campos operacionais) fica disponivel. Dali pra frente aparece
# em TODO status, inclusive no Concluido - processo fechado antes da regra
# nova (ou via "Pular Status") precisa poder ser completado depois.
MODULO_MINIMO_EDICAO_DADOS = "Instrucao"


def pode_editar_dados(processo: ComexProcesso) -> bool:
    return campo_liberado(processo.status_modulo, MODULO_MINIMO_EDICAO_DADOS)


def _indice_modulo(modulo: str) -> int:
    try:
        return MODULOS_SEQUENCIA.index(modulo)
    except ValueError:
        return -1


def campo_liberado(status_modulo: str, modulo_minimo: str) -> bool:
    """O campo abre no modulo indicado e continua aberto dali pra frente."""
    return _indice_modulo(status_modulo) >= _indice_modulo(modulo_minimo)


def _campos_operacionais(processo: ComexProcesso):
    """Exportacao pula o Desembaraco: os campos dele (DI, NF IMPO, Entrega
    Real) nao aparecem nem travam a conclusao."""
    sequencia = sequencia_do_processo(processo)
    return [c for c in CAMPOS_OPERACIONAIS if c[3] in sequencia]


def campos_operacionais_do_processo(processo: ComexProcesso) -> list[dict]:
    """Metadados dos campos pro front-end: rotulo, etapa em que abre, se ja
    esta liberado e o valor atual."""
    campos = []
    for chave, atributo, label, modulo_minimo, tipo in _campos_operacionais(processo):
        valor = getattr(processo, atributo, None)
        if hasattr(valor, "isoformat"):
            valor = valor.isoformat()
        campos.append({
            "campo": chave,
            "label": label,
            "modulo_minimo": modulo_minimo,
            "modulo_minimo_label": LABELS_MODULO.get(modulo_minimo, modulo_minimo),
            "liberado": campo_liberado(processo.status_modulo, modulo_minimo),
            "tipo": tipo,
            "valor": valor,
        })
    return campos


def campos_faltando_para_concluir(processo: ComexProcesso) -> list[str]:
    """Rotulos dos campos ainda vazios que travam a conclusao."""
    faltando = []
    for chave, atributo, label, _modulo, _tipo in _campos_operacionais(processo):
        if chave == _CAMPO_AUTO_NA_CONCLUSAO:
            continue
        if not getattr(processo, atributo, None):
            faltando.append(label)
    return faltando


def _parse_date(value):
    convertido = _parse_dt(value)
    return convertido.date() if convertido else None


def _parse_valor(value):
    if value in (None, ""):
        return None
    texto = str(value).strip()
    if not texto:
        return None
    # Aceita "1.234,56" (pt-BR) e "1234.56".
    if "," in texto:
        texto = texto.replace(".", "").replace(",", ".")
    try:
        return float(texto)
    except (TypeError, ValueError):
        return None


def _aplicar_campos_operacionais(processo: ComexProcesso, dados: dict) -> None:
    """Grava os campos operacionais enviados pelo front.

    Campo cuja etapa ainda nao chegou e' IGNORADO (a trava nao e' so'
    visual - o back-end tambem recusa, senao bastaria chamar a API direto
    pra furar a regra)."""
    for chave_json, atributo, _label, modulo_minimo, tipo in CAMPOS_OPERACIONAIS:
        if chave_json not in dados:
            continue
        if not campo_liberado(processo.status_modulo, modulo_minimo):
            continue
        if tipo == "data":
            setattr(processo, atributo, _parse_date(dados.get(chave_json)))
        elif tipo == "valor":
            setattr(processo, atributo, _parse_valor(dados.get(chave_json)))
        else:
            valor = str(dados.get(chave_json) or "").strip()
            setattr(processo, atributo, (valor[:40] or None) if valor else None)

    for chave_json, atributo in _CAMPOS_GERAIS_DATA.items():
        if chave_json in dados:
            setattr(processo, atributo, _parse_date(dados.get(chave_json)))


def salvar_po(
    processo: ComexProcesso,
    *,
    pagador_frete: str,
    direcao_operacao: str,
    modal_transporte: str,
    ocs_vinculadas_ids: list[int] | None,
    usuario: str,
    finalizar: bool = False,
    dados_operacionais: dict | None = None,
) -> ComexProcesso:
    """Salva (rascunho) ou finaliza a PO do processo (Modulo 2). Quando mais
    de uma OC do mesmo fornecedor e vinculada, os totais da PO passam a
    refletir a soma delas - mas o processo "dono" da tela continua sendo
    este (`processo`); as demais OCs so ficam anotadas em
    `po_ocs_vinculadas` para referencia (nao criam processos adicionais).

    A direcao da operacao (Importacao/Exportacao) e o modal de transporte
    (Aereo/Maritimo) sao obrigatorios aqui - e o momento em que essa escolha
    precisa acontecer (nao na importacao da OC). Juntos eles derivam o tipo
    de operacao de 2 letras (IM/IA/EM/EA - ver `_derivar_tipo_operacao`) que
    define o prefixo do ID OP. Trocar direcao ou modal regenera o ID OP (e
    o numero da PO, que deriva dele) pra refletir o prefixo correto -
    inclusive depois de Finalizada, porque a estrategia inicial as vezes
    muda de verdade mais adiante (ex.: era Maritimo, precisou virar Aereo).
    Documentos/e-mails ja enviados com o numero antigo nao sao atualizados
    retroativamente.

    `dados_operacionais` traz os demais campos gerais do processo (Data PO,
    Ref Despachante, BL/AWB, Invoice, ETD, ETA, Previsao Entrega, Entrega
    Real, NF impo, NF recebimento) - todos opcionais e editaveis a qualquer
    momento, inclusive depois da PO Finalizada."""
    _exigir_nao_cancelado(processo)
    if pagador_frete not in PAGADORES_FRETE:
        raise ValueError("Pagador do frete invalido.")
    # A direcao vem da origem: OC = importacao. Exportacao nasce da ordem de
    # faturamento (importar_ordem_fat) e nao passa pela PO.
    direcao_operacao = processo.direcao_operacao or "IMPO"
    if direcao_operacao not in DIRECOES_OPERACAO:
        raise ValueError("Direção da operação inválida.")
    if modal_transporte not in MODAIS_TRANSPORTE:
        raise ValueError("Selecione o modal de transporte (Aéreo ou Marítimo) antes de salvar a PO.")
    tipo_operacao = _derivar_tipo_operacao(direcao_operacao, modal_transporte)

    outros = []
    if ocs_vinculadas_ids:
        outros = ComexProcesso.query.filter(ComexProcesso.id.in_(ocs_vinculadas_ids)).all()
        if not _mesmo_fornecedor([processo, *outros]):
            raise ValueError("Só é possível combinar OCs do mesmo fornecedor na mesma PO.")

    agora = agora_br()
    ainda_editavel = processo.po_status != "Finalizada"
    estrategia_mudou = tipo_operacao != processo.tipo_operacao
    if estrategia_mudou:
        # Direcao/modal mudaram - regenera ID OP e numero da PO mesmo com a
        # PO ja Finalizada: a estrategia inicial (ex.: Maritimo) as vezes
        # muda de verdade mais adiante (ex.: precisou virar Aereo) e o ID OP
        # tem que refletir isso, nao ficar preso ao prefixo antigo. PDFs/
        # e-mails ja enviados com o numero antigo nao sao atualizados
        # retroativamente - e' um numero novo a partir daqui.
        processo.tipo_operacao = tipo_operacao
        processo.id_op = gerar_id_op(tipo_operacao)
        processo.po_numero = f"PO-{processo.id_op}"
    elif ainda_editavel or not processo.po_numero:
        processo.po_numero = f"PO-{processo.id_op}"
    processo.direcao_operacao = direcao_operacao
    processo.modal_transporte = modal_transporte
    processo.pagador_frete = pagador_frete
    processo.frete_aplicavel = pagador_frete == "Columbia"
    # Filtra None: processo manual (sem OC, ver criar_processo_manual) nao
    # tem cod_ordem_compra - sem isso a lista ficava com um "null" solto.
    processo.po_ocs_vinculadas = json.dumps(
        [c for c in [processo.cod_ordem_compra, *[o.cod_ordem_compra for o in outros]] if c],
        default=str,
    )
    # Marca as OCs combinadas como vinculadas a este processo (a dona da
    # PO/itens) - assim elas param de aparecer soltas com status "OC"
    # parado na lista principal. Se uma OC que estava vinculada antes saiu
    # da selecao atual, desvincula ela de volta (volta a valer como OC
    # independente, pode ganhar sua propria PO).
    ids_atuais = {o.id for o in outros}
    for antiga in ComexProcesso.query.filter_by(po_processo_principal_id=processo.id).all():
        if antiga.id not in ids_atuais:
            antiga.po_processo_principal_id = None
    for outro in outros:
        outro.po_processo_principal_id = processo.id
        outro.atualizado_em = agora
        outro.atualizado_por = usuario
    # Uma vez Finalizada, so uma acao explicita de reverter (apagar_po,
    # estornar) volta pra Rascunho - salvar de novo sem marcar "Finalizar"
    # (ex.: so pra corrigir modal/direcao, ver requisito de 03/09) NAO pode
    # derrubar a PO de volta pra Rascunho silenciosamente.
    processo.po_status = "Finalizada" if (finalizar or processo.po_status == "Finalizada") else "Rascunho"
    if finalizar:
        processo.po_finalizada_sem_envio = True
        avancar_modulo_apos_po_finalizada(processo)
    elif MODULOS_SEQUENCIA.index(processo.status_modulo) <= MODULOS_SEQUENCIA.index("PO"):
        processo.status_modulo = "PO"
        processo.status_slug = status_slug("PO")
    if dados_operacionais:
        _aplicar_campos_operacionais(processo, dados_operacionais)
    processo.atualizado_em = agora
    processo.atualizado_por = usuario
    db.session.commit()
    return processo


def avancar_modulo_apos_po_finalizada(processo: ComexProcesso) -> None:
    """Ao finalizar a PO - seja pelo botão "Finalizar sem enviar e-mail" ou
    pelo envio de e-mail da PO - avanca o processo pro proximo modulo certo:
    se a Columbia paga o frete, ela mesma precisa cotar (Modulo 3); se o
    pagador e o cliente/fornecedor, a cotacao nao e responsabilidade da
    Columbia e o processo pula direto pra Instrucao de Embarque (Modulo 4).
    So avanca se o processo ainda estiver na PO ou antes dela - nunca
    retrocede quem ja passou desse ponto (ex.: reenviar o e-mail depois)."""
    if MODULOS_SEQUENCIA.index(processo.status_modulo) > MODULOS_SEQUENCIA.index("PO"):
        return
    proximo_modulo = "Cotacao" if processo.pagador_frete == "Columbia" else "Instrucao"
    processo.status_modulo = proximo_modulo
    processo.status_slug = status_slug(proximo_modulo)


def salvar_itens_po(processo: ComexProcesso, itens: list[dict]) -> list[ComexPoItem]:
    """Substitui a lista de itens de linha da PO (codigo, NCM, PN, descricao,
    quantidade, valor unitario, valor total). Preenchidos manualmente pelo
    operador por enquanto - a ideia e que no futuro venham pre-preenchidos
    da OC assim que a bridge do ERP expuser preco unitario/NCM por item.

    Substitui a lista inteira a cada chamada (apaga e recria) - mais simples
    e suficiente para o volume tipico de itens de uma PO."""
    agora = agora_br()
    _exigir_nao_cancelado(processo)
    ComexPoItem.query.filter_by(processo_id=processo.id).delete()

    novos = []
    for idx, item in enumerate(itens or []):
        quantidade = _to_float(item.get("quantidade"))
        valor_unitario = _to_float(item.get("valor_unitario"))
        valor_total = _to_float(item.get("valor_total"))
        if valor_total is None and quantidade is not None and valor_unitario is not None:
            valor_total = round(quantidade * valor_unitario, 2)
        novos.append(ComexPoItem(
            processo_id=processo.id,
            order_index=idx,
            oc_origem=str(item.get("oc_origem") or "").strip() or None,
            codigo=str(item.get("codigo") or "").strip() or None,
            ncm=str(item.get("ncm") or "").strip() or None,
            pn=str(item.get("pn") or "").strip() or None,
            descricao=str(item.get("descricao") or "").strip() or None,
            quantidade=quantidade,
            valor_unitario=valor_unitario,
            valor_total=valor_total,
            criado_em=agora,
            atualizado_em=agora,
        ))
    db.session.add_all(novos)

    processo.atualizado_em = agora
    db.session.commit()
    return ComexPoItem.query.filter_by(processo_id=processo.id).order_by(ComexPoItem.order_index).all()


def listar_itens_po(processo: ComexProcesso) -> list[ComexPoItem]:
    return (
        ComexPoItem.query
        .filter_by(processo_id=processo.id)
        .order_by(ComexPoItem.order_index)
        .all()
    )


def subtotal_itens_po(processo: ComexProcesso) -> float | None:
    """Soma do valor_total dos itens da PO (USD) - usado como sugestao de
    "Valor da mercadoria" ao gerar uma cotacao de frete (o prestador precisa
    disso pra calcular o seguro). None se ainda nao ha itens."""
    total = sum(float(it.valor_total or 0) for it in listar_itens_po(processo))
    return round(total, 2) if total else None


def _to_float(valor):
    if valor in (None, ""):
        return None
    try:
        return float(valor)
    except (TypeError, ValueError):
        return None


def apagar_po(processo: ComexProcesso, usuario: str) -> ComexProcesso:
    """Apaga a PO criada, devolvendo o processo para o estagio OC."""
    agora = agora_br()
    processo.po_numero = None
    processo.po_ocs_vinculadas = None
    processo.pagador_frete = None
    processo.frete_aplicavel = None
    processo.po_status = None
    processo.po_pdf_file_name = None
    processo.po_pdf_file_path = None
    processo.po_enviada_em = None
    processo.po_enviada_por = None
    processo.po_destinatarios_email = None
    processo.po_finalizada_sem_envio = False
    processo.status_modulo = "OC"
    processo.status_slug = status_slug("OC")
    processo.atualizado_em = agora
    processo.atualizado_por = usuario
    db.session.commit()
    return processo


def estornar(processo: ComexProcesso, usuario: str) -> ComexProcesso:
    """Funcao "estornar" (requisito geral do Comex): volta o processo para o
    modulo anterior do workflow (ordem inversa: NF/Cambio -> Transporte ->
    ... -> PO -> OC).

    NAO atualiza `atualizado_em`/`atualizado_por` (do processo nem das OCs
    desvinculadas) de proposito: esse campo e' usado como "ultima interacao"
    em telas de acompanhamento (ex.: Torre de Controle) e um estorno e' um
    retrocesso, nao um avanco/interacao real - contar como interacao
    mascararia processos parados que so foram estornados."""
    _exigir_nao_cancelado(processo)
    sequencia = sequencia_do_processo(processo)
    anterior = _modulo_anterior(processo.status_modulo, sequencia)
    if anterior is None:
        raise ValueError(f"Este processo já está no primeiro módulo ({LABELS_MODULO[sequencia[0]]}) - não há como estornar mais.")

    # Estornar a PO de volta pra OC desfaz o agrupamento: as OCs combinadas
    # (que tinham sumido da lista principal - ver salvar_po) voltam a
    # valer como OCs independentes, senao ficariam vinculadas pra sempre a
    # uma PO que nao existe mais.
    if anterior == "OC":
        for vinculada in ComexProcesso.query.filter_by(po_processo_principal_id=processo.id).all():
            vinculada.po_processo_principal_id = None
        processo.po_ocs_vinculadas = None

    if processo.status_modulo == "Concluido":
        processo.processo_concluido_em = None

    processo.status_modulo = anterior
    processo.status_slug = status_slug(anterior)
    db.session.commit()
    return processo


def pular_status(processo: ComexProcesso, usuario: str) -> ComexProcesso:
    """Funcao "Pular Status" (requisito geral do Comex, espelho do
    "estornar"): avanca o processo manualmente pro proximo modulo do
    workflow, IGNORANDO as validacoes normais de cada modulo (PO
    finalizada, cotacao escolhida, etc.). Existe pra dois casos: processos
    que comecaram fora do sistema (ja estao mais adiantados no mundo real
    do que o cadastro reflete) e simples acompanhamento/follow-up de status
    de embarque sem precisar repetir cada acao do fluxo normal."""
    _exigir_nao_cancelado(processo)
    _exigir_ordem_fat_para_sair_da_cotacao(processo)
    proximo = _proximo_modulo(processo.status_modulo, sequencia_do_processo(processo))
    if proximo is None:
        raise ValueError("Este processo já está Concluído - não há como avançar mais.")
    processo.status_modulo = proximo
    processo.status_slug = status_slug(proximo)
    processo.atualizado_em = agora_br()
    processo.atualizado_por = usuario
    if proximo == "Concluido":
        agora = agora_br()
        processo.processo_concluido_em = agora
        # Nasce com a data da conclusao e segue editavel no Concluido (o
        # fechamento contabil pode ter sido em outra data). Por isso a
        # "Data de fechamento" nao entra na lista de exigidos pra concluir.
        if not processo.data_fechamento:
            processo.data_fechamento = agora.date()
    db.session.commit()
    return processo


def avancar_status(processo: ComexProcesso, usuario: str) -> ComexProcesso:
    """Funcao "Avançar" - avanco NORMAL (validado) pro proximo modulo,
    disponivel pra qualquer operador com acesso ao Comex (diferente de
    "Pular Status", que ignora toda validacao e exige permissao extra de
    gerencia).

    Regra do Comex: as etapas intermediarias andam livres - campo vazio nao
    trava nada no meio do caminho. A unica transicao validada e' a ULTIMA
    (NF/Cambio -> Concluido), que exige todos os campos operacionais
    preenchidos (ver CAMPOS_OPERACIONAIS / campos_faltando_para_concluir)."""
    _exigir_nao_cancelado(processo)
    _exigir_ordem_fat_para_sair_da_cotacao(processo)
    proximo = _proximo_modulo(processo.status_modulo, sequencia_do_processo(processo))
    if proximo is None:
        raise ValueError("Este processo já está Concluído - não há como avançar mais.")

    if proximo == "Concluido":
        faltando = campos_faltando_para_concluir(processo)
        if faltando:
            raise ValueError(
                "Preencha todos os campos do processo antes de concluir. "
                f"Faltam {len(faltando)}: " + ", ".join(faltando) + "."
            )

    processo.status_modulo = proximo
    processo.status_slug = status_slug(proximo)
    processo.atualizado_em = agora_br()
    processo.atualizado_por = usuario
    if proximo == "Concluido":
        agora = agora_br()
        processo.processo_concluido_em = agora
        # Nasce com a data da conclusao e segue editavel no Concluido (o
        # fechamento contabil pode ter sido em outra data). Por isso a
        # "Data de fechamento" nao entra na lista de exigidos pra concluir.
        if not processo.data_fechamento:
            processo.data_fechamento = agora.date()
    db.session.commit()
    return processo


def listar_processos(status_modulo: str | None = None, busca: str = "") -> list[ComexProcesso]:
    # OCs combinadas na PO de outro processo (po_processo_principal_id
    # preenchido) somem da lista principal - passam a existir so "dentro"
    # do processo consolidado (ver salvar_po e o checklist de itens, que
    # mostra a origem por OC). Continuam no banco, so nao aparecem soltas.
    query = ComexProcesso.query.filter(ComexProcesso.po_processo_principal_id.is_(None))
    if status_modulo:
        query = query.filter_by(status_modulo=status_modulo)
    else:
        # Cancelado so' aparece no card proprio (filtro "Cancelado").
        query = query.filter(ComexProcesso.status_modulo != STATUS_CANCELADO)
    processos = query.order_by(ComexProcesso.criado_em.desc()).all()
    if busca:
        termo = busca.strip().lower()

        def _ocs(p: ComexProcesso) -> list[str]:
            # A OC propria e as combinadas nesta PO (estas somem da lista e
            # so existem "dentro" do processo principal - buscar pelo numero
            # delas tem que achar o principal).
            ocs = [str(p.cod_ordem_compra or ""), p.cod_compra or ""]
            if p.ordem_fat:  # exportacao: acha pela ordem de faturamento ou orcamento
                ocs += [str(p.ordem_fat.cod_ordem_fat), p.ordem_fat.orcamento or ""]
            if p.exportacao_antecipada:
                ocs.append(p.exportacao_antecipada.orcamento or "")
            try:
                ocs += [str(c) for c in json.loads(p.po_ocs_vinculadas or "[]") or []]
            except (TypeError, ValueError):
                pass
            return ocs

        processos = [
            p for p in processos
            if termo in (p.id_op or "").lower()
            or termo in (p.ref_ff or "").lower()
            or termo in (p.fornecedor or "").lower()
            or termo in (p.po_numero or "").lower()
            or any(termo in oc.lower() for oc in _ocs(p) if oc)
        ]
    return processos


def metricas_por_modulo() -> dict:
    contagens = {slug: 0 for slug in STATUS_SLUGS.values()}
    query = (
        ComexProcesso.query
        .filter(ComexProcesso.po_processo_principal_id.is_(None))
        .with_entities(ComexProcesso.status_slug)
    )
    for processo in query.all():
        contagens[processo.status_slug] = contagens.get(processo.status_slug, 0) + 1
    return contagens


# ── Anexo de documentos (requisito geral: todo modulo precisa ter uma ─────
# funcao de anexar documento) - guardado direto no banco (coluna `dados`),
# sem depender de Google Drive nem de disco persistente no servidor (ver
# conversa de 03/09: service account sem cota propria quebrava o upload
# pro Drive). `_documentos_dir()`/storage local so seguem existindo pra
# reabrir documentos antigos, anexados antes dessa mudanca.
TAMANHO_MAXIMO_DOCUMENTO = 25 * 1024 * 1024  # 25 MB


def _documentos_dir() -> str:
    return current_app.config.get("COMEX_DOCUMENTOS_DIR", "") or os.path.join(
        current_app.instance_path, "comex_documentos"
    )


def anexar_documento(
    processo: ComexProcesso,
    *,
    modulo: str,
    dados: bytes,
    file_name: str,
    usuario: str,
    titulo: str | None = None,
    mimetype: str | None = None,
) -> ComexDocumento:
    """Anexa um documento ao processo, em qualquer modulo do workflow (nota
    fiscal, BL/AWB, invoice, packing list, comprovante etc.) - o conteudo
    vai direto pra coluna `dados` do banco, sem passar por Google Drive ou
    disco do servidor."""
    if modulo not in MODULOS_SEQUENCIA:
        raise ValueError("Módulo inválido para anexar documento.")
    if not dados:
        raise ValueError("Arquivo vazio.")
    if not file_name:
        raise ValueError("Nome de arquivo inválido.")
    if len(dados) > TAMANHO_MAXIMO_DOCUMENTO:
        raise ValueError(
            f"Arquivo muito grande ({len(dados) / 1024 / 1024:.1f} MB) - o limite é "
            f"{TAMANHO_MAXIMO_DOCUMENTO // 1024 // 1024} MB."
        )

    documento = ComexDocumento(
        processo_id=processo.id,
        modulo=modulo,
        titulo=(titulo or "").strip() or None,
        file_name=file_name,
        file_path="",
        dados=dados,
        mimetype=(mimetype or "").strip() or "application/octet-stream",
        uploaded_by=usuario,
    )
    db.session.add(documento)
    db.session.commit()
    return documento


def listar_documentos(processo: ComexProcesso, modulo: str | None = None) -> list[ComexDocumento]:
    query = ComexDocumento.query.filter_by(processo_id=processo.id)
    if modulo:
        query = query.filter_by(modulo=modulo)
    return query.order_by(ComexDocumento.id.desc()).all()


# ── Cotacao (Modulo 3) - formulario publico, no formato do "modelo de ─────
# cotação.xlsx" usado hoje pela empresa (2 variantes: FCL e LCL/Aereo).
TIPOS_FRETE = ("FCL", "LCL_AEREO")

# Etapas de custo, na mesma ordem do modelo (linhas 14-20 / 22-28 do Excel) -
# usado tanto para somar o custo total quanto para montar a tabela na tela.
ETAPAS_CUSTO_COTACAO = (
    ("pick_up", "Pick-up"),
    ("origem_charges", "Origem charges"),
    ("frete_internacional", "International Freight"),
    ("seguro", "Ensurance"),
    ("destination_charges", "Destination Charges"),
    ("docs_release", "Docs release"),
    ("delivery", "Delivery"),
)

# Termos de consentimento (modelo de cotação.xlsx, linhas 37-49) - aceite
# obrigatorio no formulario publico antes de enviar.
TERMOS_COTACAO = {
    "paragrafos": [
        "A cotação deverá contemplar todos os custos necessários para a execução "
        "integral da operação, incluindo frete, despesas operacionais, "
        "administrativas, documentais e quaisquer outras taxas normalmente "
        "aplicáveis à origem, transporte internacional e destino.",
        "Os valores informados em Origin Charges e Destination Charges deverão "
        "representar o custo total dessas etapas, não sendo aceitas cobranças "
        "complementares ou taxas adicionais após a aprovação da cotação.",
        "Serão aceitas exclusivamente as seguintes exceções:",
    ],
    "excecoes": [
        "Movimentações extraordinárias entre recintos alfandegados não previstas "
        "na operação original, desde que devidamente justificadas;",
        "Taxas, tributos, emolumentos ou despesas exigidas por órgãos "
        "governamentais, aduaneiros ou reguladores, mediante apresentação da "
        "respectiva documentação comprobatória (fatura, guia de recolhimento ou "
        "documento equivalente);",
        "Alterações de escopo solicitadas pelo embarcador ou importador após a "
        "aprovação da cotação.",
    ],
    "final": (
        "Custos não informados, omitidos ou incorretamente avaliados pelo "
        "prestador de serviço no momento da cotação serão considerados de sua "
        "exclusiva responsabilidade e não poderão ser cobrados posteriormente."
    ),
}


def _hash_token_cotacao(token: str) -> str:
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


# Campos que o operador pode pre-preencher ao gerar o link (Columbia ja sabe
# essa informacao - origem/destino/carga - antes de pedir a cotacao; o
# prestador de frete recebe pre-preenchido e so confirma/ajusta se precisar,
# em vez de ter que descobrir/digitar tudo do zero). Tamanho maximo de cada
# um bate com a coluna correspondente em ComexCotacao.
_CAMPOS_COTACAO_PRE_PREENCHIDO = {
    "origem": 120,
    "destino": 120,
    "incoterm": 20,
    "imo_classe": 20,
    "un_numero": 20,
}


def _adicionar_volumes(cotacao: ComexCotacao, volumes_dados: list[dict] | None) -> None:
    """Cria as linhas de ComexCotacaoVolume a partir de uma lista de dicts
    {comprimento, largura, altura, peso} - ignora linhas totalmente vazias.
    Usado tanto ao gerar o link (operador ja sabe as dimensoes) quanto na
    submissao do prestador (que pode ajustar)."""
    for idx, vol in enumerate(volumes_dados or [], start=1):
        if not any(vol.get(k) for k in ("comprimento", "largura", "altura", "peso")):
            continue
        db.session.add(ComexCotacaoVolume(
            cotacao=cotacao,
            numero=idx,
            comprimento=_to_float(vol.get("comprimento")),
            largura=_to_float(vol.get("largura")),
            altura=_to_float(vol.get("altura")),
            peso=_to_float(vol.get("peso")),
        ))


_TAMANHO_EMAIL_COTACAO = 255  # ComexCotacao.email_instrucao_embarque


def _gravar_emails_cotacao(cotacao: ComexCotacao, emails) -> None:
    """A coluna fica com os enderecos inteiros que cabem em 255; a lista
    completa, se passar disso, vai pra ComexCotacaoDestinatarios."""
    partes = [e.strip() for e in re.split(r"[;,]", str(emails or "")) if e.strip()]
    completo = "; ".join(partes)
    cabe = []
    for e in partes:
        if len("; ".join(cabe + [e])) > _TAMANHO_EMAIL_COTACAO:
            break
        cabe.append(e)
    cotacao.email_instrucao_embarque = "; ".join(cabe) or None
    if len(completo) > _TAMANHO_EMAIL_COTACAO:
        db.session.add(ComexCotacaoDestinatarios(cotacao=cotacao, emails=completo))


def emails_da_cotacao(cotacao: ComexCotacao | None) -> str | None:
    if not cotacao:
        return None
    if cotacao.destinatarios_completos:
        return cotacao.destinatarios_completos.emails
    return cotacao.email_instrucao_embarque


def _normalizar_modal_solicitado(tipo_frete: str, modal) -> str | None:
    if tipo_frete == "FCL":
        return None
    modal = str(modal or "").strip().upper()
    if modal and modal not in MODAIS_COTADOS:
        raise ValueError("Modal a cotar inválido (use LCL, Aéreo ou os dois).")
    return modal or None


def _gravar_modal_solicitado(cotacao: ComexCotacao, modal: str | None) -> None:
    atual = cotacao.modal_solicitado
    if not modal:
        if atual:
            db.session.delete(atual)
    elif atual:
        atual.modal = modal
    else:
        db.session.add(ComexCotacaoModalSolicitado(cotacao=cotacao, modal=modal))


def criar_link_cotacao(
    processo: ComexProcesso,
    *,
    tipo_frete: str,
    usuario: str,
    email_instrucao_embarque: str | None = None,
    validade_dias: int = 14,
    pre_preenchido: dict | None = None,
    modal_solicitado: str | None = None,
) -> tuple[ComexCotacao, str]:
    """Gera um novo link publico de cotacao (Modulo 3) para um prestador de
    frete preencher sem login. O token bruto so existe neste retorno - so o
    hash fica salvo (mesmo padrao do convite de usuario). Move o processo
    para o modulo Cotacao na primeira cotacao gerada.

    `pre_preenchido` traz os campos que o operador ja sabe e preenche antes
    de mandar o link (origem, destino, incoterm, IMO/UN e, se FCL,
    quantidade de equipamento) - o prestador recebe isso pronto no
    formulario publico e so confirma ou ajusta."""
    if tipo_frete not in TIPOS_FRETE:
        raise ValueError("Tipo de frete inválido (use FCL ou LCL_AEREO).")
    if not exportacao_da_expedicao(processo) and processo.po_status != "Finalizada":
        raise ValueError("Finalize a PO antes de gerar uma cotação.")
    modal_solicitado = _normalizar_modal_solicitado(tipo_frete, modal_solicitado)

    token = secrets.token_urlsafe(32)
    agora = agora_br()
    cotacao = ComexCotacao(
        processo_id=processo.id,
        tipo_frete=tipo_frete,
        status="Pendente",
        # Vazio (nao None) de proposito: bancos ja provisionados antes do
        # redesenho deste modulo ainda tem "fornecedor_frete" como NOT NULL
        # (sync_missing_columns.py so adiciona coluna que falta, nao ajusta
        # restricao de coluna que ja existia) - inserir None quebraria o
        # INSERT nesses bancos. O prestador substitui isso ao submeter.
        fornecedor_frete="",
        token_publico_hash=_hash_token_cotacao(token),
        token_publico_expira_em=agora + timedelta(days=max(1, validade_dias)),
        link_gerado_em=agora,
        criado_por=usuario,
    )
    _gravar_emails_cotacao(cotacao, email_instrucao_embarque)
    _gravar_modal_solicitado(cotacao, modal_solicitado)
    pre_preenchido = pre_preenchido or {}
    for campo, tamanho in _CAMPOS_COTACAO_PRE_PREENCHIDO.items():
        valor = str(pre_preenchido.get(campo) or "").strip()
        setattr(cotacao, campo, valor[:tamanho] or None)
    if tipo_frete == "FCL":
        cotacao.qtd_40hc = _to_int(pre_preenchido.get("qtd_40hc"))
        cotacao.qtd_20dry = _to_int(pre_preenchido.get("qtd_20dry"))
    else:
        _adicionar_volumes(cotacao, pre_preenchido.get("volumes"))
    # Valor da mercadoria - essencial pro prestador calcular o seguro. Se o
    # operador nao informou, sugere o subtotal dos itens da PO.
    cotacao.valor_mercadoria_usd = _to_float(pre_preenchido.get("valor_mercadoria_usd")) or subtotal_itens_po(processo)
    db.session.add(cotacao)

    if processo.status_modulo == "PO":
        processo.status_modulo = "Cotacao"
        processo.status_slug = status_slug("Cotacao")
        processo.atualizado_em = agora
        processo.atualizado_por = usuario

    db.session.commit()
    return cotacao, token


def editar_cotacao_pendente(cotacao: ComexCotacao, *, tipo_frete: str, dados: dict, usuario: str) -> ComexCotacao:
    """Edita uma cotacao que ainda esta Pendente (o prestador de frete
    ainda nao respondeu) - reaproveita o MESMO link/token ja gerado
    (eventualmente ja enviado por e-mail), so corrige os dados que a
    Columbia pre-preencheu (tipo de frete FCL/LCL, origem/destino,
    equipamento ou volumes, IMO/UN, valor da mercadoria) antes do prestador
    abrir o formulario publico. Cobre a situacao de a estrategia inicial
    mudar (ex.: pensava em FCL, saldo nao fechou container e virou LCL).

    Depois que o prestador ja respondeu (status != 'Pendente'), os numeros
    submetidos ja dependem do tipo original que foi pedido - nao da mais
    pra editar essa cotacao, so gerar um link novo (ver criar_link_cotacao)."""
    if cotacao.status != "Pendente":
        raise ValueError("Só é possível editar uma cotação que ainda está aguardando resposta do prestador.")
    if tipo_frete not in TIPOS_FRETE:
        raise ValueError("Tipo de frete inválido (use FCL ou LCL_AEREO).")

    dados = dados or {}
    _gravar_modal_solicitado(cotacao, _normalizar_modal_solicitado(tipo_frete, dados.get("modal_solicitado")))
    cotacao.tipo_frete = tipo_frete
    for campo, tamanho in _CAMPOS_COTACAO_PRE_PREENCHIDO.items():
        valor = str(dados.get(campo) or "").strip()
        setattr(cotacao, campo, valor[:tamanho] or None)

    # Limpa equipamento/volumes do tipo anterior antes de aplicar os do
    # tipo novo - senao trocar FCL -> LCL deixava qtd_40hc/20dry velhos
    # pendurados (e vice-versa, volumes orfaos de uma troca LCL -> FCL).
    ComexCotacaoVolume.query.filter_by(cotacao_id=cotacao.id).delete()
    if tipo_frete == "FCL":
        cotacao.qtd_40hc = _to_int(dados.get("qtd_40hc"))
        cotacao.qtd_20dry = _to_int(dados.get("qtd_20dry"))
    else:
        cotacao.qtd_40hc = None
        cotacao.qtd_20dry = None
        _adicionar_volumes(cotacao, dados.get("volumes"))

    valor_mercadoria = _to_float(dados.get("valor_mercadoria_usd"))
    if valor_mercadoria is not None:
        cotacao.valor_mercadoria_usd = valor_mercadoria

    db.session.commit()
    return cotacao


def obter_cotacao_por_token(token: str) -> ComexCotacao | None:
    if not token:
        return None
    return ComexCotacao.query.filter_by(token_publico_hash=_hash_token_cotacao(token)).first()


def link_cotacao_valido(cotacao: ComexCotacao) -> bool:
    if not cotacao or cotacao.status != "Pendente":
        return False
    if cotacao.token_publico_expira_em and cotacao.token_publico_expira_em < agora_br():
        return False
    return True


# Tamanho maximo de cada campo de texto - bate com a coluna correspondente
# em ComexCotacao (truncar sem isso pode estourar o limite da coluna e
# quebrar o INSERT/UPDATE no MySQL de producao, que roda em modo estrito).
_CAMPOS_COTACAO_TEXTO = {
    "fornecedor_frete": 200,
    "origem": 120,
    "destino": 120,
    "incoterm": 20,
    "imo_classe": 20,
    "un_numero": 20,
    "transit_time": 60,
    "rota": 200,
}
_CAMPOS_COTACAO_CUSTO = tuple(f"{prefixo}_{moeda}" for prefixo, _ in ETAPAS_CUSTO_COTACAO for moeda in ("usd", "brl"))

# Link LCL/Aereo: o prestador diz o que cotou - um modal ou os dois.
MODAIS_COTADOS = {"LCL": "LCL", "AEREO": "Aéreo", "AMBOS": "LCL e Aéreo"}


def _preencher_logistica_e_custos(cotacao: ComexCotacao, dados: dict) -> None:
    """Parte da cotacao LCL/Aereo que muda por modal: logistica e custos."""
    cotacao.transit_time = str(dados.get("transit_time") or "").strip()[:60] or None
    cotacao.rota = str(dados.get("rota") or "").strip()[:200] or None
    cotacao.validade = _parse_date(dados.get("validade"))
    cotacao.ptax = _to_float(dados.get("ptax"))
    _preencher_custos(cotacao, dados)


def _preencher_custos(cotacao: ComexCotacao, dados: dict) -> None:
    total_usd = 0.0
    total_brl = 0.0
    for campo in _CAMPOS_COTACAO_CUSTO:
        valor = _to_float(dados.get(campo))
        setattr(cotacao, campo, valor)
        if valor:
            if campo.endswith("_usd"):
                total_usd += valor
            else:
                total_brl += valor
    cotacao.custo_total_usd = round(total_usd, 2)
    cotacao.custo_total_brl = round(total_brl, 2)


def _cotacao_aereo_irma(cotacao: ComexCotacao, dados_aereo: dict, volumes: list | None) -> ComexCotacao:
    """Segunda cotacao (Aereo) do mesmo prestador, quando ele cota os dois
    modais num link so'. Copia o que e' comum (prestador, origem/destino,
    carga, volumes, saidas) e fica com logistica/custos proprios. Nao tem
    link proprio (token vazio) - ja' nasce Recebida."""
    irma = ComexCotacao(
        processo_id=cotacao.processo_id, tipo_frete=cotacao.tipo_frete, status="Recebida",
        fornecedor_frete=cotacao.fornecedor_frete, origem=cotacao.origem, destino=cotacao.destino,
        incoterm=cotacao.incoterm, imo_classe=cotacao.imo_classe, un_numero=cotacao.un_numero,
        valor_mercadoria_usd=cotacao.valor_mercadoria_usd, proximas_saidas=cotacao.proximas_saidas,
        criado_por=cotacao.criado_por,
        termos_aceitos=True, termos_aceitos_em=cotacao.termos_aceitos_em, recebida_em=cotacao.recebida_em,
    )
    db.session.add(irma)
    _gravar_emails_cotacao(irma, emails_da_cotacao(cotacao))
    _preencher_logistica_e_custos(irma, dados_aereo)
    _adicionar_volumes(irma, volumes)
    db.session.add(ComexCotacaoModal(cotacao=irma, modal="AEREO"))
    return irma


def submeter_cotacao_publica(cotacao: ComexCotacao, dados: dict) -> ComexCotacao:
    """Recebe o formulario publico preenchido pelo prestador de frete e
    fecha a cotacao (Modulo 3). So aceita uma vez - o link fica invalido
    depois (`link_cotacao_valido`)."""
    if not link_cotacao_valido(cotacao):
        raise ValueError("Este link de cotação já foi usado ou expirou.")
    if not dados.get("termos_aceitos"):
        raise ValueError("É necessário aceitar os termos de consentimento para enviar a cotação.")
    if not str(dados.get("fornecedor_frete") or "").strip():
        raise ValueError("Informe o nome do prestador de frete.")
    modal_cotado = None
    if cotacao.tipo_frete != "FCL":
        modal_cotado = str(dados.get("modal_cotado") or "").strip().upper()
        if modal_cotado not in MODAIS_COTADOS:
            raise ValueError("Informe o frete cotado: LCL, Aéreo ou os dois.")
        # Aereo em branco viraria uma cotacao de custo zero - e a sugerida do modal.
        aereo = dados.get("aereo") or {}
        if modal_cotado == "AMBOS" and not any(_to_float(aereo.get(c)) for c in _CAMPOS_COTACAO_CUSTO):
            raise ValueError("Informe os custos do frete Aéreo ou marque que está cotando só LCL.")

    for campo, tamanho in _CAMPOS_COTACAO_TEXTO.items():
        if campo in dados:
            valor = str(dados.get(campo) or "").strip()
            setattr(cotacao, campo, valor[:tamanho] or None)

    # Valor da mercadoria ja vem sugerido (subtotal da PO ou informado pelo
    # operador ao gerar o link) - o prestador so ajusta se o valor mudou.
    if "valor_mercadoria_usd" in dados:
        cotacao.valor_mercadoria_usd = _to_float(dados.get("valor_mercadoria_usd")) or cotacao.valor_mercadoria_usd

    if "proximas_saidas" in dados:
        cotacao.proximas_saidas = str(dados.get("proximas_saidas") or "").strip() or None

    if cotacao.tipo_frete == "FCL":
        cotacao.qtd_40hc = _to_int(dados.get("qtd_40hc"))
        cotacao.qtd_20dry = _to_int(dados.get("qtd_20dry"))
        _preencher_custos(cotacao, dados)
    else:
        _preencher_logistica_e_custos(cotacao, dados)
        ComexCotacaoVolume.query.filter_by(cotacao_id=cotacao.id).delete()
        _adicionar_volumes(cotacao, dados.get("volumes"))
        # "Os dois": a cotacao do link fica LCL e o Aereo vira outra cotacao.
        db.session.add(ComexCotacaoModal(cotacao=cotacao, modal="LCL" if modal_cotado == "AMBOS" else modal_cotado))

    agora = agora_br()
    cotacao.termos_aceitos = True
    cotacao.termos_aceitos_em = agora
    cotacao.status = "Recebida"
    cotacao.recebida_em = agora
    if modal_cotado == "AMBOS":
        _cotacao_aereo_irma(cotacao, dados.get("aereo") or {}, dados.get("volumes"))
    db.session.commit()

    from . import comex_custo_service
    ptax = comex_custo_service.ptax_do_dia()
    recalcular_sugerida(cotacao.processo_id, ptax["cotacao"] if ptax else None)
    db.session.commit()
    return cotacao


def modal_da_cotacao(cotacao: ComexCotacao) -> str:
    """FCL, LCL ou AEREO - grupo em que a cotacao concorre. Link LCL/Aereo
    antigo, sem o modal informado, fica no grupo LCL_AEREO."""
    if cotacao.tipo_frete == "FCL":
        return "FCL"
    return cotacao.modal_cotado.modal if cotacao.modal_cotado else "LCL_AEREO"


def recalcular_sugerida(processo_id: int, ptax: float | None = None) -> bool:
    """Marca como `is_sugerida_pelo_sistema` a cotacao Recebida de menor
    custo de CADA modal (FCL, LCL, Aereo) - aereo e maritimo nao concorrem
    entre si, a decisao entre eles e' do operador. O custo comparado e' o
    total em R$ com o markup de cada forwarder (um forwarder mais barato em
    US$ pode sair mais caro em R$); sem taxa nenhuma, compara em US$.
    Devolve se alguma marcacao mudou."""
    recebidas = ComexCotacao.query.filter_by(processo_id=processo_id, status="Recebida").order_by(ComexCotacao.id).all()
    mapa = forwarders_por_email()

    def custo(c):
        total = cambio_da_cotacao(c, ptax, mapa)["custo_total_consolidado_brl"]
        if total is not None:
            return (0, total)
        return (1, c.custo_total_usd if c.custo_total_usd is not None else float("inf"))

    mudou = False
    vistos = set()
    for c in sorted(recebidas, key=custo):
        modal = modal_da_cotacao(c)
        sugerida = modal not in vistos
        vistos.add(modal)
        if bool(c.is_sugerida_pelo_sistema) != sugerida:
            c.is_sugerida_pelo_sistema = sugerida
            mudou = True
    return mudou


def listar_cotacoes(processo: ComexProcesso) -> list[ComexCotacao]:
    return (
        ComexCotacao.query
        .filter_by(processo_id=processo.id)
        .order_by(ComexCotacao.link_gerado_em.desc())
        .all()
    )


def definir_taxa_cambio(processo: ComexProcesso, taxa: float | str | None, usuario: str) -> ComexProcesso:
    """Define a taxa de cambio de referencia do processo (ex.: PTAX do dia),
    usada pra converter o custo total de TODAS as cotacoes desse processo
    pra um total consolidado em BRL - a mesma taxa vale pra qualquer
    fornecedor comparado, em vez de cada um informar a sua."""
    valor = _to_float(taxa)
    if not valor or valor <= 0:
        raise ValueError("Informe uma taxa de câmbio válida (maior que zero).")
    processo.taxa_cambio_referencia = valor
    processo.atualizado_em = agora_br()
    processo.atualizado_por = usuario
    db.session.commit()
    return processo


def _lista_emails(texto) -> list[str]:
    return [e.strip().lower() for e in re.split(r"[;,]", str(texto or "")) if e.strip()]


def forwarders_por_email() -> dict:
    """{e-mail: Freight Forwarder} do cadastro - a cotacao nao guarda o
    fornecedor, so' os e-mails pra onde o link foi; e' por eles que se acha
    o markup (vale tambem pras cotacoes antigas)."""
    mapa = {}
    for f in ComexFornecedor.query.filter_by(tipo_fornecedor="Freight Forwarder").all():
        for e in _lista_emails(f.email):
            mapa.setdefault(e, f)
    return mapa


def forwarder_da_cotacao(cotacao: ComexCotacao, mapa: dict | None = None) -> ComexFornecedor | None:
    mapa = forwarders_por_email() if mapa is None else mapa
    for e in _lista_emails(emails_da_cotacao(cotacao)):
        if e in mapa:
            return mapa[e]
    return None


def cambio_da_cotacao(cotacao: ComexCotacao, ptax: float | None, mapa: dict | None = None) -> dict:
    """Total da cotacao em R$: US$ x PTAX do dia x (1 + markup do forwarder)
    + R$. O markup e' so' do frete (decisao de 09/11/2026); impostos usam a
    PTAX pura. Sem PTAX (BCB fora do ar) cai na taxa manual do processo;
    forwarder sem markup cadastrado converte pela PTAX pura, com aviso."""
    manual = cotacao.processo.taxa_cambio_referencia if cotacao.processo else None
    base = ptax or manual
    forwarder = forwarder_da_cotacao(cotacao, mapa)
    markup = forwarder.markup.markup_pct if forwarder and forwarder.markup else None
    taxa = round(base * (1 + (markup or 0) / 100), 4) if base else None
    total = None
    if taxa and (cotacao.custo_total_usd is not None or cotacao.custo_total_brl is not None):
        total = round(float(cotacao.custo_total_usd or 0) * taxa + float(cotacao.custo_total_brl or 0), 2)
    return {
        "taxa_base": base,
        "taxa_base_fonte": "PTAX" if ptax else ("manual" if manual else None),
        "forwarder_cadastro": (forwarder.nome_fantasia or forwarder.razao_social) if forwarder else None,
        "markup_pct": markup,
        "taxa_agente": taxa,
        "custo_total_consolidado_brl": total,
    }


COTACAO_SUBSTATUS_NAO_INICIADO = "Não iniciado"
COTACAO_SUBSTATUS_EM_COTACAO = "Em Cotação"
COTACAO_SUBSTATUS_FECHADO = "Fechado"


def cotacao_substatus(processo: ComexProcesso) -> str | None:
    """Sub-status do Modulo 3: Nao iniciado (nenhum link gerado ainda) ->
    Em Cotacao (pelo menos um link gerado, aguardando/comparando propostas)
    -> Fechado (uma cotacao ja foi escolhida - so a partir daqui a acao
    "Enviar Instrução de Embarque" fica disponivel). "Fechado" continua
    valendo mesmo depois que o processo avanca pra Instrucao (o pagador do
    frete e a Columbia, entao passou por Cotacao antes) - so retorna None
    quando o processo nem chegou a Cotacao ainda, ou pulou ela de vez
    (pagador do frete e o cliente/fornecedor)."""
    if processo.cotacao_vencedora_id:
        return COTACAO_SUBSTATUS_FECHADO
    if processo.status_modulo != "Cotacao":
        return None
    tem_cotacoes = db.session.query(
        ComexCotacao.query.filter_by(processo_id=processo.id).exists()
    ).scalar()
    return COTACAO_SUBSTATUS_EM_COTACAO if tem_cotacoes else COTACAO_SUBSTATUS_NAO_INICIADO


def escolher_cotacao(
    cotacao: ComexCotacao, *, usuario: str, justificativa: str | None = None, saida_escolhida: str | None = None
) -> ComexProcesso:
    """Modulo 3 - o operador escolhe a cotacao vencedora entre as recebidas.
    Se nao for a sugerida pelo sistema (menor custo), exige justificativa.
    Tambem exige a saida/embarque confirmada com o prestador (o operador ve
    as "proximas saidas" que ele informou e confirma qual foi combinada) -
    e o que vai no e-mail de selecao enviado ao prestador escolhido."""
    if cotacao.status != "Recebida":
        raise ValueError("Só é possível escolher uma cotação que já foi recebida do prestador.")
    justificativa = (justificativa or "").strip()
    if not cotacao.is_sugerida_pelo_sistema and not justificativa:
        raise ValueError("Informe a justificativa para escolher uma cotação diferente da sugerida pelo sistema (menor custo do modal).")
    saida_escolhida = (saida_escolhida or "").strip()
    if not saida_escolhida:
        raise ValueError("Informe a saída de embarque escolhida com o prestador antes de confirmar.")

    ComexCotacao.query.filter_by(processo_id=cotacao.processo_id).update({"is_escolhida": False})
    cotacao.is_escolhida = True
    cotacao.saida_escolhida = saida_escolhida

    processo = cotacao.processo
    processo.cotacao_vencedora_id = cotacao.id
    processo.cotacao_justificativa = justificativa or None
    processo.atualizado_em = agora_br()
    processo.atualizado_por = usuario
    db.session.commit()
    return processo


# ── Instrucao de Embarque (Modulo 4, versao minima) ────────────────────
def enviar_instrucao(processo: ComexProcesso, dados: dict, usuario: str) -> ComexProcesso:
    """Libera os dados gerais de operacao (Ref. Despachante, BL/AWB,
    Invoice, ETD, ETA, Previsao Entrega, Entrega Real, NF Impo, NF
    Recebimento) para edicao. So chega aqui de duas formas: (a) o pagador
    do frete e a Columbia - precisa de uma cotacao escolhida primeiro
    (Modulo 3); ou (b) o pagador e o cliente/fornecedor - a cotacao nao e
    responsabilidade da Columbia, entao `salvar_po` ja avanca direto pra
    "Instrucao" sem passar por "Cotacao". Reaproveita
    `_aplicar_campos_operacionais` (mesmos campos usados em `salvar_po`).
    Pode ser chamada de novo depois (os campos continuam editaveis) - a
    deteccao de "primeira vez" usa `instrucao_enviada_em`, nao a transicao
    de modulo, porque o caminho (b) ja chega em "Instrucao" direto.

    Os dados de embarque continuam editaveis nos modulos seguintes tambem
    (Coleta, Em Transito) - o BL/AWB, ETD/ETA etc. podem precisar de ajuste
    depois que o processo ja avancou, entao isso NAO regride o modulo atual
    (so avanca Cotacao -> Instrucao na primeira vez; dali pra frente so
    atualiza os campos, sem mexer no status_modulo)."""
    if processo.status_modulo == "Cotacao" and not processo.cotacao_vencedora_id:
        raise ValueError("Escolha uma cotação de frete antes de enviar a instrução de embarque.")
    _exigir_ordem_fat_para_sair_da_cotacao(processo)
    # Limite so' por baixo (a PO precisa estar finalizada). Nao ha limite
    # por cima: campo como Numerario (Desembarque), DI (Desembaraco) e Data
    # de fechamento (Concluido) so' existe DEPOIS daqui. Quais campos podem
    # de fato ser gravados em cada etapa e' decidido campo a campo em
    # _aplicar_campos_operacionais.
    if _indice_modulo(processo.status_modulo) < _indice_modulo("Cotacao"):
        raise ValueError("A instrução de embarque só pode ser enviada depois da PO finalizada.")

    agora = agora_br()
    primeira_vez = processo.instrucao_enviada_em is None
    _aplicar_campos_operacionais(processo, dados)
    if processo.status_modulo == "Cotacao":
        processo.status_modulo = "Instrucao"
        processo.status_slug = status_slug("Instrucao")
    if primeira_vez:
        processo.instrucao_enviada_em = agora
        processo.instrucao_enviada_por = usuario
    processo.atualizado_em = agora
    processo.atualizado_por = usuario
    db.session.commit()
    return processo


def _to_int(valor):
    if valor in (None, ""):
        return None
    try:
        return int(float(valor))
    except (TypeError, ValueError):
        return None


def apagar_documento(documento: ComexDocumento) -> None:
    if storage.is_external_url(documento.file_path):
        try:
            storage.delete_drive_url(documento.file_path)
        except Exception:
            current_app.logger.exception("Falha ao apagar documento Comex do Drive (id=%s)", documento.id)
    else:
        try:
            if documento.file_path and os.path.exists(documento.file_path):
                os.remove(documento.file_path)
        except OSError:
            current_app.logger.exception("Falha ao apagar documento Comex do disco (id=%s)", documento.id)
    db.session.delete(documento)
    db.session.commit()


# ── Comentarios (requisito geral: mesmo campo/historico em qualquer ────────
# modulo do workflow, nao muda de acordo com a etapa atual do processo) ────
def listar_comentarios(processo: ComexProcesso) -> list[ComexComentario]:
    return (
        ComexComentario.query
        .filter_by(processo_id=processo.id)
        .order_by(ComexComentario.criado_em.desc())
        .all()
    )


def adicionar_comentario(processo: ComexProcesso, texto: str, usuario: str) -> ComexComentario:
    texto = str(texto or "").strip()
    if not texto:
        raise ValueError("Informe o comentário.")
    comentario = ComexComentario(processo_id=processo.id, texto=texto, criado_por=usuario)
    db.session.add(comentario)
    db.session.commit()
    return comentario
