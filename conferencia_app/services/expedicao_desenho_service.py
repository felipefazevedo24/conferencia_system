"""Desenho da peca para a Conferencia de Expedicao, a partir do Nº OS do item.

O arquivo nao fica em pasta: o GRV guarda o PDF dentro do banco (coluna
`anexo` de tos_aux_desenhos / tos_aux_anexos). A localizacao reaproveita o
que o modulo de Producao ja' faz (inclusive o desenho herdado da estrutura de
origem) e as mesmas consultas ja' cadastradas na bridge do ERP - nao exige
atualizar a bridge.
"""

from . import producao_service


def _numero_os(n_os) -> str:
    return str(n_os or "").strip()


def _item_da_os(numero_os: str, ordem: dict) -> int | None:
    """A OS de um item de expedicao tem um unico item na estrutura (ex.:
    10954/001). Havendo mais de um, vale o primeiro: e' a peca que da' nome a
    OS, e abrir o desenho de um componente no lugar dela seria pior que nao
    abrir nenhum."""
    itens = producao_service._metadata_read(
        producao_service.fetch_all,
        producao_service.queries.SQL_PRODUCAO_ESTRUTURA_OS,
        {"cod_empresa": 1, "cod_os": ordem["codigo"]},
    )
    codigos = sorted(int(item["aux_code"]) for item in itens if item.get("aux_code") is not None)
    return codigos[0] if codigos else None


def localizar(n_os) -> dict | None:
    """Documento que serve de desenho da OS, ou None se ela nao tiver.
    Levanta excecao se o ERP estiver fora - quem chama decide o que mostrar."""
    numero_os = _numero_os(n_os)
    if not numero_os:
        return None
    try:
        ordem = producao_service._obter_ordem(numero_os)
        aux_code = _item_da_os(numero_os, ordem)
        if aux_code is None:
            return None
        documentos = producao_service.obter_documentos(numero_os, aux_code, ordem=ordem)
    except LookupError:
        return None
    escolhido = (
        next((d for d in documentos if d.get("is_primary")), None)
        or next((d for d in documentos if d.get("kind") == "drawing"), None)
        or next((d for d in documentos if str(d.get("filename") or "").lower().endswith(".pdf")), None)
    )
    if not escolhido:
        return None
    return {**escolhido, "numero_os": numero_os, "ordem": ordem}


def os_filhas(n_os) -> list[dict]:
    """OS que a OS do item precisa (aba Dependencias do GRV), da filha direta
    ate' a ultima neta, na ordem da arvore. Reaproveita o rastreio da Producao:
    ele parte do orcamento, entao OS sem orcamento vinculado volta sem filhas.
    Levanta excecao se o ERP estiver fora."""
    numero_os = _numero_os(n_os)
    if not numero_os:
        return []
    try:
        grafo = producao_service.obter_dependencias(numero_os)
    except LookupError:
        return []
    titulos = {no["number"]: no["title"] for no in grafo["nodes"]}
    filhas_de: dict[str, list[str]] = {}
    for ligacao in grafo["edges"]:
        filhas_de.setdefault(ligacao["dependent_order_number"], []).append(ligacao["prerequisite_order_number"])
    raiz = grafo["selected_order_number"] or numero_os
    resultado: list[dict] = []
    # A mesma OS pode atender dois pais; aparece uma vez so', no primeiro.
    vistas = {raiz}

    def descer(pai: str, nivel: int) -> None:
        for filha in sorted(filhas_de.get(pai, []), key=lambda n: (len(n), n)):
            if filha in vistas:
                continue
            vistas.add(filha)
            resultado.append({"numero": filha, "titulo": titulos.get(filha, ""), "nivel": nivel})
            descer(filha, nivel + 1)

    descer(raiz, 1)
    return resultado


def obter_arquivo(n_os) -> tuple[bytes, str]:
    documento = localizar(n_os)
    if not documento:
        raise LookupError("Esta OS não tem desenho anexado no ERP.")
    return producao_service.obter_arquivo(
        documento["numero_os"],
        documento["aux_code"],
        documento["source_kind"],
        documento["id"],
        ordem=documento["ordem"],
        source_cod_os=documento["source_cod_os"],
        source_aux_code=documento["source_aux_code"],
    )
