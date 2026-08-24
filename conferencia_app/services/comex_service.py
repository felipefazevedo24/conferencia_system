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
import secrets
from datetime import datetime, timedelta

from flask import current_app
from sqlalchemy.exc import IntegrityError

from ..compras.services import compras_service
from ..extensions import db
from ..models import ComexComentario, ComexCotacao, ComexCotacaoVolume, ComexDocumento, ComexPoItem, ComexProcesso
from . import expedicao_photo_storage as storage

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
]

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
}

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


def _proximo_modulo(atual: str) -> str | None:
    try:
        idx = MODULOS_SEQUENCIA.index(atual)
    except ValueError:
        return None
    if idx + 1 >= len(MODULOS_SEQUENCIA):
        return None
    return MODULOS_SEQUENCIA[idx + 1]


def _modulo_anterior(atual: str) -> str | None:
    try:
        idx = MODULOS_SEQUENCIA.index(atual)
    except ValueError:
        return None
    if idx == 0:
        return None
    return MODULOS_SEQUENCIA[idx - 1]


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
    ano = datetime.now().year
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
            "_oc_json": oc_json,
            "_fornecedor_json": fornecedor_json,
        })

    if termo_norm:
        termo_lower = termo_norm.lower()
        termo_digitos = termo_norm.replace(" ", "")

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
        raise ValueError(f"Esta OC ja foi importada como o processo {existente.id_op}.")

    oc_json = oc_header.get("_oc_json") or {}

    agora = datetime.now()
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

    processo.atualizado_em = datetime.now()
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


def _mesmo_fornecedor(processos: list[ComexProcesso]) -> bool:
    fornecedores = {(p.fornecedor or "").strip().lower() for p in processos}
    return len(fornecedores) <= 1


# Campos operacionais gerais (visiveis a partir da PO, editaveis ao longo de
# todo o processo) - texto livre (ate 20 caracteres, reforcado no front-end;
# a coluna tem folga ate 40 so como buffer) e data, mapeados pelo nome que
# chega no JSON do navegador para o atributo do model (o front manda "eta",
# que reaproveita a coluna ja existente `em_transito_eta`).
_CAMPOS_PO_TEXTO = {
    "ref_despachante": "ref_despachante",
    "bl_awb": "bl_awb",
    "invoice_numero": "invoice_numero",
    "entrega_real": "entrega_real",
    "nf_impo": "nf_impo",
    "nf_recebimento": "nf_recebimento",
}
_CAMPOS_PO_DATA = {
    "po_data": "po_data",
    "etd": "etd",
    "eta": "em_transito_eta",
    "previsao_entrega": "previsao_entrega",
}


def _parse_date(value):
    convertido = _parse_dt(value)
    return convertido.date() if convertido else None


def _aplicar_campos_operacionais(processo: ComexProcesso, dados: dict) -> None:
    for chave_json, atributo in _CAMPOS_PO_TEXTO.items():
        if chave_json in dados:
            valor = str(dados.get(chave_json) or "").strip()
            setattr(processo, atributo, (valor[:40] or None) if valor else None)
    for chave_json, atributo in _CAMPOS_PO_DATA.items():
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
    define o prefixo do ID OP. Enquanto a PO ainda esta em Rascunho, trocar
    direcao ou modal regenera o ID OP (e o numero da PO, que deriva dele)
    para refletir o prefixo correto; depois de Finalizada o ID OP fica fixo.

    `dados_operacionais` traz os demais campos gerais do processo (Data PO,
    Ref Despachante, BL/AWB, Invoice, ETD, ETA, Previsao Entrega, Entrega
    Real, NF impo, NF recebimento) - todos opcionais e editaveis a qualquer
    momento, inclusive depois da PO Finalizada."""
    if pagador_frete not in PAGADORES_FRETE:
        raise ValueError("Pagador do frete invalido.")
    if direcao_operacao not in DIRECOES_OPERACAO:
        raise ValueError("Selecione a direção da operação (Importação ou Exportação) antes de salvar a PO.")
    if modal_transporte not in MODAIS_TRANSPORTE:
        raise ValueError("Selecione o modal de transporte (Aéreo ou Marítimo) antes de salvar a PO.")
    tipo_operacao = _derivar_tipo_operacao(direcao_operacao, modal_transporte)

    outros = []
    if ocs_vinculadas_ids:
        outros = ComexProcesso.query.filter(ComexProcesso.id.in_(ocs_vinculadas_ids)).all()
        if not _mesmo_fornecedor([processo, *outros]):
            raise ValueError("Só é possível combinar OCs do mesmo fornecedor na mesma PO.")

    agora = datetime.now()
    ainda_editavel = processo.po_status != "Finalizada"
    if ainda_editavel and tipo_operacao != processo.tipo_operacao:
        processo.tipo_operacao = tipo_operacao
        processo.id_op = gerar_id_op(tipo_operacao)
    if ainda_editavel or not processo.po_numero:
        processo.po_numero = f"PO-{processo.id_op}"
    processo.direcao_operacao = direcao_operacao
    processo.modal_transporte = modal_transporte
    processo.pagador_frete = pagador_frete
    processo.frete_aplicavel = pagador_frete == "Columbia"
    processo.po_ocs_vinculadas = json.dumps(
        [processo.cod_ordem_compra, *[o.cod_ordem_compra for o in outros]], default=str
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
    processo.po_status = "Finalizada" if finalizar else "Rascunho"
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
    agora = datetime.now()
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
    agora = datetime.now()
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
    ... -> PO -> OC)."""
    anterior = _modulo_anterior(processo.status_modulo)
    if anterior is None:
        raise ValueError("Este processo já está no primeiro módulo (OC) - não há como estornar mais.")
    agora = datetime.now()

    # Estornar a PO de volta pra OC desfaz o agrupamento: as OCs combinadas
    # (que tinham sumido da lista principal - ver salvar_po) voltam a
    # valer como OCs independentes, senao ficariam vinculadas pra sempre a
    # uma PO que nao existe mais.
    if anterior == "OC":
        for vinculada in ComexProcesso.query.filter_by(po_processo_principal_id=processo.id).all():
            vinculada.po_processo_principal_id = None
            vinculada.atualizado_em = agora
            vinculada.atualizado_por = usuario
        processo.po_ocs_vinculadas = None

    processo.status_modulo = anterior
    processo.status_slug = status_slug(anterior)
    processo.atualizado_em = agora
    processo.atualizado_por = usuario
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
    proximo = _proximo_modulo(processo.status_modulo)
    if proximo is None:
        raise ValueError("Este processo já está no último módulo do workflow (NF/Câmbio) - não há como avançar mais.")
    processo.status_modulo = proximo
    processo.status_slug = status_slug(proximo)
    processo.atualizado_em = datetime.now()
    processo.atualizado_por = usuario
    db.session.commit()
    return processo


# Campos minimos de embarque exigidos pra avancar normalmente (nao via
# "Pular Status") de Coleta pra Em Transito.
_CAMPOS_OBRIGATORIOS_EM_TRANSITO = {
    "ref_despachante": "Ref. Despachante",
    "bl_awb": "BL/AWB",
    "etd": "ETD",
    "em_transito_eta": "ETA",
}


def avancar_status(processo: ComexProcesso, usuario: str) -> ComexProcesso:
    """Funcao "Avançar" - avanco NORMAL (validado) pro proximo modulo,
    disponivel pra qualquer operador com acesso ao Comex (diferente de
    "Pular Status", que ignora toda validacao e exige permissao extra de
    gerencia). Cada transicao pode ter sua propria regra de validacao; hoje
    so Coleta -> Em Transito exige algo (dados minimos de embarque ja
    preenchidos - ver `_CAMPOS_OBRIGATORIOS_EM_TRANSITO`)."""
    proximo = _proximo_modulo(processo.status_modulo)
    if proximo is None:
        raise ValueError("Este processo já está no último módulo do workflow (NF/Câmbio) - não há como avançar mais.")

    if processo.status_modulo == "Coleta" and proximo == "EmTransito":
        faltando = [
            label for campo, label in _CAMPOS_OBRIGATORIOS_EM_TRANSITO.items()
            if not getattr(processo, campo, None)
        ]
        if faltando:
            raise ValueError(
                "Preencha os dados de embarque antes de avançar pra Em Trânsito: " + ", ".join(faltando) + "."
            )

    processo.status_modulo = proximo
    processo.status_slug = status_slug(proximo)
    processo.atualizado_em = datetime.now()
    processo.atualizado_por = usuario
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
    processos = query.order_by(ComexProcesso.criado_em.desc()).all()
    if busca:
        termo = busca.strip().lower()
        processos = [
            p for p in processos
            if termo in (p.id_op or "").lower()
            or termo in (p.ref_ff or "").lower()
            or termo in (p.fornecedor or "").lower()
            or termo in (p.po_numero or "").lower()
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
# funcao de anexar documento) - mesmo padrao de storage das fotos de
# expedicao (Drive ou disco local, conforme EXPEDICAO_FOTOS_STORAGE).
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
    fiscal, BL/AWB, invoice, packing list, comprovante etc.)."""
    if modulo not in MODULOS_SEQUENCIA:
        raise ValueError("Módulo inválido para anexar documento.")
    if not dados:
        raise ValueError("Arquivo vazio.")
    if not file_name:
        raise ValueError("Nome de arquivo inválido.")

    nome_no_storage = f"{processo.id_op}_{modulo}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}_{file_name}"
    if storage.using_drive():
        enviado = storage.upload_bytes_to_drive(dados, nome_no_storage, mimetype)
        caminho = enviado.file_path
    else:
        pasta = _documentos_dir()
        os.makedirs(pasta, exist_ok=True)
        caminho = os.path.join(pasta, nome_no_storage)
        with open(caminho, "wb") as f:
            f.write(dados)

    documento = ComexDocumento(
        processo_id=processo.id,
        modulo=modulo,
        titulo=(titulo or "").strip() or None,
        file_name=file_name,
        file_path=caminho,
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


def criar_link_cotacao(
    processo: ComexProcesso,
    *,
    tipo_frete: str,
    usuario: str,
    email_instrucao_embarque: str | None = None,
    validade_dias: int = 14,
    pre_preenchido: dict | None = None,
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
    if processo.po_status != "Finalizada":
        raise ValueError("Finalize a PO antes de gerar uma cotação.")

    token = secrets.token_urlsafe(32)
    agora = datetime.now()
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
        email_instrucao_embarque=(email_instrucao_embarque or "").strip() or None,
        link_gerado_em=agora,
        criado_por=usuario,
    )
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


def obter_cotacao_por_token(token: str) -> ComexCotacao | None:
    if not token:
        return None
    return ComexCotacao.query.filter_by(token_publico_hash=_hash_token_cotacao(token)).first()


def link_cotacao_valido(cotacao: ComexCotacao) -> bool:
    if not cotacao or cotacao.status != "Pendente":
        return False
    if cotacao.token_publico_expira_em and cotacao.token_publico_expira_em < datetime.now():
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
    else:
        cotacao.transit_time = str(dados.get("transit_time") or "").strip()[:60] or None
        cotacao.validade = _parse_date(dados.get("validade"))
        cotacao.ptax = _to_float(dados.get("ptax"))
        ComexCotacaoVolume.query.filter_by(cotacao_id=cotacao.id).delete()
        _adicionar_volumes(cotacao, dados.get("volumes"))

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

    agora = datetime.now()
    cotacao.termos_aceitos = True
    cotacao.termos_aceitos_em = agora
    cotacao.status = "Recebida"
    cotacao.recebida_em = agora
    db.session.commit()

    _recalcular_sugerida(cotacao.processo_id)
    db.session.commit()
    return cotacao


def _recalcular_sugerida(processo_id: int) -> None:
    """Marca como `is_sugerida_pelo_sistema` a cotacao Recebida de menor
    custo total em USD para o processo - recalculado a cada nova cotacao
    recebida (a sugestao pode mudar de "dona" com o tempo)."""
    recebidas = (
        ComexCotacao.query
        .filter_by(processo_id=processo_id, status="Recebida")
        .order_by(ComexCotacao.custo_total_usd.asc())
        .all()
    )
    for idx, c in enumerate(recebidas):
        c.is_sugerida_pelo_sistema = (idx == 0)


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
    processo.atualizado_em = datetime.now()
    processo.atualizado_por = usuario
    db.session.commit()
    return processo


def custo_total_consolidado_brl(cotacao: ComexCotacao) -> float | None:
    """Custo total da cotacao convertido pra um unico valor em BRL, usando a
    taxa de cambio de referencia do processo (a mesma pra todos os
    fornecedores comparados) - (custo_total_usd * taxa) + custo_total_brl.
    None se faltar a taxa ou nao houver nenhum custo lancado ainda."""
    taxa = cotacao.processo.taxa_cambio_referencia if cotacao.processo else None
    if not taxa:
        return None
    if cotacao.custo_total_usd is None and cotacao.custo_total_brl is None:
        return None
    total = (float(cotacao.custo_total_usd or 0) * taxa) + float(cotacao.custo_total_brl or 0)
    return round(total, 2)


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
        raise ValueError("Informe a justificativa para escolher uma cotação diferente da sugerida pelo sistema (menor custo).")
    saida_escolhida = (saida_escolhida or "").strip()
    if not saida_escolhida:
        raise ValueError("Informe a saída de embarque escolhida com o prestador antes de confirmar.")

    ComexCotacao.query.filter_by(processo_id=cotacao.processo_id).update({"is_escolhida": False})
    cotacao.is_escolhida = True
    cotacao.saida_escolhida = saida_escolhida

    processo = cotacao.processo
    processo.cotacao_vencedora_id = cotacao.id
    processo.cotacao_justificativa = justificativa or None
    processo.atualizado_em = datetime.now()
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
    if processo.status_modulo not in ("Cotacao", "Instrucao", "Coleta", "EmTransito"):
        raise ValueError("A instrução de embarque só pode ser enviada depois da PO finalizada.")

    agora = datetime.now()
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
