"""Consulta e preparo de agrupamentos de chapa, sem automacao do GRV."""
from __future__ import annotations

import re
import unicodedata
import logging
from collections import defaultdict
from typing import Any

from ..compras import queries
from ..compras.db import fetch_all

_logger = logging.getLogger(__name__)


def _texto(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _chave(value: Any) -> str:
    value = unicodedata.normalize("NFKD", _texto(value).upper())
    return " ".join("".join(c for c in value if not unicodedata.combining(c)).split())


def _espessura(material: str) -> str:
    match = re.search(r"\(([^)]*MM)\)", material.upper()) or re.search(r"(\d+(?:[,.]\d+)?\s*MM)", material.upper())
    return re.sub(r"\s+", "", match.group(1).replace(",", ".")) if match else ""


def _norma(material: str) -> str:
    value = _chave(material).removeprefix("CHAPA ").replace("–", "-").replace("—", "-")
    if " - " in value:
        return value.split(" - ", 1)[0].strip()
    if "-" in value:
        return value.split("-", 1)[0].strip()
    return re.sub(r"\d+(?:[,.]\d+)?\s*MM.*$", "", value).strip()


def _mm(value: str) -> float | None:
    value = _texto(value).upper().replace(" ", "").removesuffix("MM")
    return float(value.replace(",", ".")) if re.fullmatch(r"\d+(?:[,.]\d+)?", value) else None


def _polegada(value: str) -> float | None:
    value = value.replace(" ", "").replace('"', "")
    try:
        if "." in value and "/" in value:
            inteiro, fracao = value.split(".", 1)
            numerador, denominador = fracao.split("/", 1)
            return (float(inteiro) + float(numerador) / float(denominador)) * 25.4
        if "/" in value:
            numerador, denominador = value.split("/", 1)
            return float(numerador) / float(denominador) * 25.4
        return float(value) * 25.4
    except (ValueError, ZeroDivisionError):
        return None


def _normalizar_polegada(value: str) -> str:
    text = _chave(value).replace("”", '"').replace("“", '"').replace("″", '"')
    text = text.replace("''", '"').replace("'", '"')
    text = re.sub(r'(\d+)\s*"\s*(\d+\s*/\s*\d+)\s*"?', r'\1.\2"', text)
    text = re.sub(r"(?<![A-Z0-9])(\d{1,2})\s*-\s*(\d+\s*/\s*\d+)", r"\1.\2", text)
    text = re.sub(r"\b(\d+)\s+(\d+\s*/\s*\d+)\b", r"\1.\2", text)
    return re.sub(r"\s*/\s*", "/", text)


def _espessuras_observacao(obs: str) -> list[float]:
    obs = _normalizar_polegada(obs)
    values: list[float] = []
    patterns = (
        r"\bESP\s*#?\s*(\d+(?:\s*[.,]\s*\d+/\d+|\s*/\s*\d+|\s*[,.]\s*\d+)?)\s*(\"|MM)?",
        r"\bCHAPA\b[^,\r\n]*?\bDE\s+(\d+(?:\s*[.,]\s*\d+/\d+|\s*/\s*\d+|\s*[,.]\s*\d+)?)\s*(\"|MM)",
        r"\bCHAPA\s+(\d+(?:\s*[.,]\s*\d+/\d+|\s*/\s*\d+|\s*[,.]\s*\d+)?)\s*(\"|MM)\s*-",
        r"(?<![\d.])(\d+(?:\.\d+/\d+|/\d+)?)\s*(\")",
        r"(?<!\d)(\d+(?:[,.]\d+)?)\s*(MM)\b",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, obs, re.I):
            token = re.sub(r"\s+", "", match.group(1))
            unit = match.group(2) or ""
            amount = _polegada(token) if "/" in token or unit == '"' else _mm(token)
            if amount is not None:
                values.append(amount)
    return values


def _pontuacao(material: str, norma: str, espessura: str, obs: str) -> int:
    if not obs.strip():
        return 1
    thicknesses = _espessuras_observacao(obs)
    if thicknesses:
        material_mm = _mm(espessura)
        if material_mm is None:
            before_parenthesis = _normalizar_polegada(material.split("(", 1)[0])
            fraction = re.search(r"(?<!\d)(\d+(?:[.,]\d+/\d+|/\d+)?)\s*(?:\"|$)", before_parenthesis)
            material_mm = _polegada(fraction.group(1).replace(",", ".")) if fraction else None
        if material_mm is None or not any(abs(material_mm - value) <= 0.2 for value in thicknesses):
            return 0
    tokens = [token for token in re.split(r"[^A-Z0-9]+", norma) if token and token not in {"CHAPA", "SAE", "ASTM"}]
    norm_matches = bool(tokens) and all(token in _chave(obs) for token in tokens)
    return (10 if thicknesses else 0) + (5 if norm_matches else 0) or 1


def preparar_registros(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Seleciona uma unica melhor chapa por codigo de processo."""
    by_process: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        material = _texto(row.get("material"))
        codigo = _texto(row.get("codigo"))
        if not _chave(material).startswith("CHAPA") or not codigo:
            continue
        norma, espessura = _norma(material), _espessura(material)
        item = {
            "os": _texto(row.get("n_os")),
            "os_completa": _texto(row.get("cod_os_completo")),
            "codigo_processo": codigo,
            "classificacao": _texto(row.get("u_classificacao")),
            "descricao": _texto(row.get("descricao")),
            "material": material,
            "produto_chave": _chave(material),
            "norma": norma,
            "espessura": espessura,
            "largura_mm": row.get("largura_mm"),
            "altura_mm": row.get("altura_mm"),
            "comprimento_mm": row.get("comprimento_mm"),
            "quantidade_formato": row.get("qtde_formato"),
            "observacao": _texto(row.get("obs")),
            "cliente": _texto(row.get("cliente")),
            "status_os": _texto(row.get("status_os")),
            "tipo_servico": _texto(row.get("tipo_servico")),
            "processo_finalizado": bool(row.get("processo_finalizado")),
        }
        item["elegivel"] = (item["status_os"] == "ABERTA"
                            and not bool(row.get("cancelada"))
                            and not item["processo_finalizado"]
                            and _chave(item["tipo_servico"]).startswith("CORTE LASER"))
        item["pontuacao"] = _pontuacao(material, norma, espessura, item["observacao"])
        by_process[codigo].append(item)
    result = []
    for candidates in by_process.values():
        top_score = max(item["pontuacao"] for item in candidates)
        if top_score <= 0:
            continue
        item = next(candidate for candidate in candidates if candidate["pontuacao"] == top_score)
        item["elegivel"] = bool(item["elegivel"])
        item.pop("pontuacao")
        result.append(item)
    return result


def diagnosticar_registros(rows: list[dict[str, Any]], result: list[dict[str, Any]]) -> dict[str, int]:
    """Resume cada etapa usando o codigo do processo como unidade operacional."""
    def codigo(row: dict[str, Any]) -> str:
        return _texto(row.get("codigo"))

    def chapa_valida(row: dict[str, Any]) -> bool:
        return bool(codigo(row)) and _chave(row.get("material")).startswith("CHAPA")

    corte_laser = [
        row for row in rows
        if chapa_valida(row) and _chave(row.get("tipo_servico")).startswith("CORTE LASER")
    ]
    status_valido = [
        row for row in corte_laser
        if _texto(row.get("status_os")) == "ABERTA" and not bool(row.get("cancelada"))
    ]
    nao_finalizados = [row for row in status_valido if not bool(row.get("processo_finalizado"))]
    codigos_antes_deduplicacao = [codigo(row) for row in nao_finalizados]
    codigos_deduplicados = set(codigos_antes_deduplicacao)
    codigos_finais = {item["codigo_processo"] for item in result if item.get("elegivel")}
    return {
        "linhas_brutas": len(rows),
        "apos_corte_laser": len({codigo(row) for row in corte_laser}),
        "apos_status_valido": len({codigo(row) for row in status_valido}),
        "apos_nao_finalizados": len(codigos_deduplicados),
        "duplicados_removidos": len(codigos_antes_deduplicacao) - len(codigos_deduplicados),
        "apos_deduplicacao": len(codigos_deduplicados),
        "descartados_por_material": len(codigos_deduplicados) - len(codigos_finais),
        "total_final": len(codigos_finais),
    }


def consultar(busca: str = "", limite: int = 500) -> list[dict[str, Any]]:
    busca = _texto(busca)[:80]
    limite = max(1, min(int(limite), 1000))
    rows = fetch_all(queries.SQL_PRODUCAO_RPA_AGRUPAMENTO, {
        "cod_empresa": 1, "busca": busca, "busca_like": f"%{busca}%",
        # O JOIN com materiais pode gerar varias linhas para um processo. O
        # limite de exibicao e aplicado somente depois da deduplicacao.
        "limite": min(limite * 10, 10000),
    })
    prepared = preparar_registros(rows)
    eligible = [item for item in prepared if item["elegivel"]]
    diagnostic = diagnosticar_registros(rows, eligible)
    _logger.info(
        "rpa_agrupamento_consulta busca=%r linhas_brutas=%s apos_corte_laser=%s "
        "apos_status_valido=%s apos_nao_finalizados=%s duplicados_removidos=%s "
        "apos_deduplicacao=%s descartados_por_material=%s total_final=%s limite=%s",
        busca,
        diagnostic["linhas_brutas"],
        diagnostic["apos_corte_laser"],
        diagnostic["apos_status_valido"],
        diagnostic["apos_nao_finalizados"],
        diagnostic["duplicados_removidos"],
        diagnostic["apos_deduplicacao"],
        diagnostic["descartados_por_material"],
        diagnostic["total_final"],
        limite,
    )
    return eligible[:limite]


def montar_payload(selecionados: list[dict[str, Any]]) -> dict[str, Any]:
    if any(not item.get("elegivel") for item in selecionados):
        raise ValueError("A seleção contém processo que deixou de ser elegível.")
    unicos: dict[str, dict[str, Any]] = {}
    for item in selecionados:
        unicos.setdefault(item["codigo_processo"], item)
    if len(unicos) < 2:
        raise ValueError("Selecione pelo menos dois códigos de processo elegíveis.")
    items = sorted(unicos.values(), key=lambda item: (item["os"], item["os_completa"], item["codigo_processo"]))
    if len({item["produto_chave"] for item in items}) != 1:
        raise ValueError("Selecione processos da mesma chapa, espessura e norma.")
    first = items[0]
    return {
        "produto": first["material"], "produto_chave": first["produto_chave"],
        "norma_extraida": first["norma"], "espessura_extraida": first["espessura"],
        "quantidade_itens": len(items),
        "os_selecionadas": sorted({item["os"] for item in items}),
        "cod_os_completo": sorted({item["os_completa"] for item in items}),
        "codigos_destacados_para_agrupamento": sorted(unicos),
        "campos_destacados_grade_grv": items,
    }
