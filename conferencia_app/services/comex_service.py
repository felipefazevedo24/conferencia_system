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

import json
from datetime import datetime

from ..compras.services import compras_service
from ..extensions import db
from ..models import ComexPoItem, ComexProcesso

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

TIPOS_OPERACAO = ("IM", "IA")  # IM = Importacao Maritima | IA = Importacao Aerea
PAGADORES_FRETE = ("Columbia", "Cliente-Fornecedor")


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
    adiante no workflow, nao gerada pelo sistema."""
    tipo_operacao = tipo_operacao if tipo_operacao in TIPOS_OPERACAO else "IM"
    ano = datetime.now().year
    prefixo = f"{tipo_operacao}-{ano}-"
    ultimo = (
        ComexProcesso.query
        .filter(ComexProcesso.id_op.like(f"{prefixo}%"))
        .order_by(ComexProcesso.id.desc())
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
# (case-insensitive) contra o item_json e o produto_json crus vindos do ERP
# (SQL_COMEX_OC_ITENS usa to_jsonb(*), entao os nomes exatos das colunas
# dependem do schema real - ainda nao confirmado neste ambiente). O que nao
# for encontrado fica None e o operador preenche manualmente na tela (os
# campos da PO sao editaveis).
_CAMPOS_CANDIDATOS = {
    # codigo_interno primeiro: e o formato "legivel" (ex.: "20-03-01937")
    # usado no modelo de PO. cod_produto (ID numerico interno do ERP) so
    # entra como ultimo recurso, se nao houver codigo_interno cadastrado.
    "codigo": ("codigo_interno", "codigo", "cod_produto"),
    "descricao": ("nome", "descricao", "material", "produto"),
    "ncm": ("ncm", "cod_ncm", "ncm_code", "codigo_ncm"),
    "pn": ("pn", "part_number", "codigo_fabricante", "referencia_fabricante", "cod_fabricante"),
    "quantidade": ("qtde", "quantidade", "qtd"),
    "valor_unitario": ("preco_unitario", "valor_unitario", "vl_unitario", "preco", "valor"),
    "valor_total": ("valor_total", "vl_total", "total"),
}


def _primeiro_valor(*dicionarios, chaves: tuple[str, ...]):
    """Procura, nos dicionarios informados (na ordem), a primeira chave que
    bater (case-insensitive) com alguma das `chaves` candidatas e tiver um
    valor nao vazio."""
    for dic in dicionarios:
        if not dic:
            continue
        mapa_lower = {str(k).lower(): v for k, v in dic.items()}
        for chave in chaves:
            valor = mapa_lower.get(chave.lower())
            if valor not in (None, ""):
                return valor
    return None


def buscar_itens_oc_no_erp(processo: ComexProcesso) -> list[dict]:
    """Busca no ERP os itens (material + material estrangeiro) da OC do
    processo, ja tentando mapear codigo/NCM/PN/descricao/quantidade/valores
    a partir do que a consulta trouxer. So retorna sugestoes - o operador
    revisa e ajusta na tela antes de salvar (ver `salvar_itens_po`)."""
    if not processo.cod_ordem_compra:
        return []
    linhas = compras_service.itens_ordem_compra(
        cod_ordem_compra=processo.cod_ordem_compra, cod_empresa=processo.cod_empresa
    )
    itens = []
    for linha in linhas:
        item_json = linha.get("item_json") or {}
        produto_json = linha.get("produto_json") or {}
        quantidade = _primeiro_valor(item_json, chaves=_CAMPOS_CANDIDATOS["quantidade"])
        valor_unitario = _primeiro_valor(item_json, produto_json, chaves=_CAMPOS_CANDIDATOS["valor_unitario"])
        valor_total = _primeiro_valor(item_json, chaves=_CAMPOS_CANDIDATOS["valor_total"])
        try:
            quantidade = float(quantidade) if quantidade is not None else None
            valor_unitario = float(valor_unitario) if valor_unitario is not None else None
            valor_total = float(valor_total) if valor_total is not None else None
        except (TypeError, ValueError):
            pass
        if valor_total is None and quantidade is not None and valor_unitario is not None:
            valor_total = round(quantidade * valor_unitario, 2)
        itens.append({
            "codigo": _primeiro_valor(produto_json, item_json, chaves=_CAMPOS_CANDIDATOS["codigo"]),
            "descricao": _primeiro_valor(produto_json, item_json, chaves=_CAMPOS_CANDIDATOS["descricao"]),
            "ncm": _primeiro_valor(produto_json, item_json, chaves=_CAMPOS_CANDIDATOS["ncm"]),
            "pn": _primeiro_valor(produto_json, item_json, chaves=_CAMPOS_CANDIDATOS["pn"]),
            "quantidade": quantidade,
            "valor_unitario": valor_unitario,
            "valor_total": valor_total,
        })
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


def salvar_po(
    processo: ComexProcesso,
    *,
    pagador_frete: str,
    tipo_operacao: str,
    ocs_vinculadas_ids: list[int] | None,
    usuario: str,
    finalizar: bool = False,
) -> ComexProcesso:
    """Salva (rascunho) ou finaliza a PO do processo (Modulo 2). Quando mais
    de uma OC do mesmo fornecedor e vinculada, os totais da PO passam a
    refletir a soma delas - mas o processo "dono" da tela continua sendo
    este (`processo`); as demais OCs so ficam anotadas em
    `po_ocs_vinculadas` para referencia (nao criam processos adicionais).

    O tipo de operacao (IM/IA) e obrigatorio aqui - e o momento em que essa
    escolha precisa acontecer (nao na importacao da OC). Enquanto a PO
    ainda esta em Rascunho, trocar o tipo regenera o ID OP (e o numero da
    PO, que deriva dele) para refletir o prefixo correto; depois de
    Finalizada o ID OP fica fixo."""
    if pagador_frete not in PAGADORES_FRETE:
        raise ValueError("Pagador do frete invalido.")
    if tipo_operacao not in TIPOS_OPERACAO:
        raise ValueError("Selecione o tipo de operação (Marítima ou Aérea) antes de salvar a PO.")

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
    processo.pagador_frete = pagador_frete
    processo.frete_aplicavel = pagador_frete == "Columbia"
    processo.po_ocs_vinculadas = json.dumps(
        [processo.cod_ordem_compra, *[o.cod_ordem_compra for o in outros]], default=str
    )
    processo.po_status = "Finalizada" if finalizar else "Rascunho"
    processo.status_modulo = "PO"
    processo.status_slug = status_slug("PO")
    processo.atualizado_em = agora
    processo.atualizado_por = usuario
    db.session.commit()
    return processo


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
    processo.status_modulo = anterior
    processo.status_slug = status_slug(anterior)
    processo.atualizado_em = datetime.now()
    processo.atualizado_por = usuario
    db.session.commit()
    return processo


def listar_processos(status_modulo: str | None = None, busca: str = "") -> list[ComexProcesso]:
    query = ComexProcesso.query
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
    for processo in ComexProcesso.query.with_entities(ComexProcesso.status_slug).all():
        contagens[processo.status_slug] = contagens.get(processo.status_slug, 0) + 1
    return contagens
