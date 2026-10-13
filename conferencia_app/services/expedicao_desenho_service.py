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
