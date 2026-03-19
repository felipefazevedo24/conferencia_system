"""
Serviço de consulta ao Google Sheets de pedidos de compra.

Planilha (pública):
    - Coluna A: número da ordem de compra
    - Coluna D: código do material
    - Coluna E: descrição do material
    - Coluna F: quantidade por linha de item
    - Coluna G: valor unitário por linha de item
"""

import csv
import json
import re
from io import StringIO
from pathlib import Path
from datetime import datetime

import requests

try:
    import openpyxl
except Exception:
    openpyxl = None

PEDIDOS_SHEETS_URL = (
        "https://docs.google.com/spreadsheets/d/1mo0Vb8mvVl_XyPdRENVqVF_UiPY4DNarhM5xB_wCtx8/edit?usp=sharing"
)
PEDIDOS_LOCAL_EXCEL_PATH = Path(__file__).resolve().parent.parent.parent / "instance" / "pedidos" / "pedidos.xlsx"
PEDIDOS_CACHE_PATH = Path(__file__).resolve().parent.parent.parent / "instance" / "pedidos" / "pedidos_cache.json"


def _normalizar_numero(val) -> str:
    """Converte célula do Excel para string de número de pedido.
    Trata floats como '2000039571.0' → '2000039571'.
    """
    if val is None:
        return ""
    if isinstance(val, float):
        if val == int(val):
            return str(int(val)).strip()
        return str(val).strip()
    txt = str(val).strip()
    if not txt:
        return ""

    # Normaliza strings numericas como '0011049' ou '11049.0' para '11049'.
    if re.fullmatch(r"\d+(?:\.0+)?", txt):
        try:
            return str(int(float(txt)))
        except Exception:
            return txt

    return txt


def _ler_float(row, col_index: int) -> float:
    val = row[col_index] if len(row) > col_index else None
    try:
        txt = val
        if isinstance(val, str):
            txt = val.strip()
            if "." in txt and "," in txt:
                # Formato pt-BR com milhar: 1.234,56
                txt = txt.replace(".", "").replace(",", ".")
            elif "," in txt:
                # Formato decimal com vírgula: 1234,56
                txt = txt.replace(",", ".")
        return float(txt) if txt not in (None, "") else 0.0
    except (TypeError, ValueError):
        return 0.0


def _normalizar_codigo_material(val) -> str:
    if val is None:
        return ""
    digits = re.sub(r"\D", "", str(val).strip())
    if len(digits) == 8:
        # Aceita codigo vindo sem zero na ultima parte (XXXX) e normaliza para (0XXXX)
        digits = digits[:4] + "0" + digits[4:]
    return digits


def _formatar_codigo_material_padrao(val) -> str:
    """Formata codigo para o padrao 00-00-00000 quando houver 8 ou 9 digitos."""
    if val is None:
        return ""

    txt = str(val).strip()
    if not txt:
        return ""

    # Alguns exports podem trazer valor numerico com .0
    if txt.endswith(".0") and txt[:-2].isdigit():
        txt = txt[:-2]

    digits = re.sub(r"\D", "", txt)
    if len(digits) == 8:
        digits = digits[:4] + "0" + digits[4:]
    if len(digits) == 9:
        bloco_final = digits[4:]

        # Regra operacional: quando bloco final vier com prefixo incorreto 19/20,
        # apenas os 2 ultimos digitos sao confiaveis.
        if (
            bloco_final.startswith("19")
            or bloco_final.startswith("20")
            or bloco_final[1:3] == "19"
            or bloco_final[1:3] == "20"
        ):
            bloco_final = f"000{bloco_final[-2:]}"

        return f"{digits[:2]}-{digits[2:4]}-{bloco_final}"

    return txt


def formatar_codigo_material_padrao(val) -> str:
    """API publica para padronizar codigo interno no formato operacional."""
    return _formatar_codigo_material_padrao(val)


def _sheets_to_csv_url(url: str) -> str:
    if "/edit" in url:
        return url.split("/edit", 1)[0] + "/export?format=csv"
    if "format=csv" in url:
        return url
    return url + ("&" if "?" in url else "?") + "format=csv"


def _parse_lista_pedidos(numero_pedido: str) -> list[str]:
    raw = str(numero_pedido or "").strip()
    if not raw:
        return []

    pedidos = []
    vistos = set()
    for parte in re.split(r"[;,\n]+", raw):
        pedido = _normalizar_numero(parte)
        if not pedido or pedido in vistos:
            continue
        vistos.add(pedido)
        pedidos.append(pedido)

    return sorted(pedidos)


def _carregar_rows_google_sheets() -> list:
    csv_url = _sheets_to_csv_url(PEDIDOS_SHEETS_URL)
    try:
        resp = requests.get(csv_url, timeout=20)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise RuntimeError(f"Falha ao consultar Google Sheets: {exc}") from exc

    reader = csv.reader(StringIO(resp.text))
    return list(reader)


def _carregar_rows_excel_local() -> list:
    """Fallback para pedidos antigos quando nao existem mais no Google Sheets."""
    if openpyxl is None:
        return []
    if not PEDIDOS_LOCAL_EXCEL_PATH.exists():
        return []

    wb = openpyxl.load_workbook(PEDIDOS_LOCAL_EXCEL_PATH, read_only=True, data_only=True)
    try:
        ws = wb.active
        rows = []
        for row in ws.iter_rows(values_only=True):
            rows.append(list(row) if row is not None else [])
        return rows
    finally:
        wb.close()


def _load_pedidos_cache() -> dict:
    if not PEDIDOS_CACHE_PATH.exists():
        return {}
    try:
        return json.loads(PEDIDOS_CACHE_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _save_pedidos_cache(cache: dict) -> None:
    try:
        PEDIDOS_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        PEDIDOS_CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=True, indent=2), encoding="utf-8")
    except Exception:
        # Cache nunca deve quebrar o fluxo operacional.
        pass


def _cache_get_linhas_pedido(numero_pedido: str) -> list:
    cache = _load_pedidos_cache()
    linhas = cache.get(str(numero_pedido), {}).get("linhas") if isinstance(cache, dict) else None
    return list(linhas or [])


def _cache_set_linhas_pedido(numero_pedido: str, linhas: list) -> None:
    if not numero_pedido or not linhas:
        return
    cache = _load_pedidos_cache()
    cache[str(numero_pedido)] = {
        "atualizado_em": datetime.now().isoformat(timespec="seconds"),
        "linhas": linhas,
    }
    _save_pedidos_cache(cache)


def buscar_linhas_pedido(numero_pedido: str) -> list:
    """Retorna as linhas de um pedido com quantidade, valor unitário, código e descrição do material."""
    pedidos = _parse_lista_pedidos(numero_pedido)
    if not pedidos:
        return []

    try:
        rows = _carregar_rows_google_sheets()
    except Exception:
        rows = []

    linhas_po = []
    pedidos_encontrados = set()
    linhas_por_pedido = {}

    def _append_linha(row, pedido_row):
        codigo_material_raw = row[3].strip() if len(row) > 3 and isinstance(row[3], str) else str(row[3]).strip() if len(row) > 3 and row[3] is not None else ""
        codigo_material = _formatar_codigo_material_padrao(codigo_material_raw)
        descricao_material = row[4].strip() if len(row) > 4 and isinstance(row[4], str) else str(row[4]).strip() if len(row) > 4 and row[4] is not None else ""
        linha = {
            "pedido_compra": pedido_row,
            "qtd": _ler_float(row, 5),
            "valor_unit": _ler_float(row, 6),
            "codigo_material": codigo_material,
            "descricao_material": descricao_material,
        }
        linhas_po.append(linha)
        linhas_por_pedido.setdefault(pedido_row, []).append(linha)

    for idx, row in enumerate(rows):
        if idx == 0:
            continue
        pedido_row = _normalizar_numero(row[0] if row else None)
        if pedido_row in pedidos:
            pedidos_encontrados.add(pedido_row)
            _append_linha(row, pedido_row)

    faltantes = [p for p in pedidos if p not in pedidos_encontrados]
    if faltantes:
        # Fallback 1: cache local persistente (pedido pode ter sumido do Sheets apos fechamento no ERP)
        ainda_faltantes = []
        for pedido in faltantes:
            linhas_cache = _cache_get_linhas_pedido(pedido)
            if linhas_cache:
                for linha in linhas_cache:
                    linhas_po.append(
                        {
                            "pedido_compra": str(linha.get("pedido_compra") or pedido),
                            "qtd": float(linha.get("qtd") or 0.0),
                            "valor_unit": float(linha.get("valor_unit") or 0.0),
                            "codigo_material": _formatar_codigo_material_padrao(linha.get("codigo_material")),
                            "descricao_material": str(linha.get("descricao_material") or "").strip(),
                        }
                    )
            else:
                ainda_faltantes.append(pedido)

        faltantes = ainda_faltantes

    if faltantes:
        rows_local = _carregar_rows_excel_local()
        for idx, row in enumerate(rows_local):
            if idx == 0:
                continue
            pedido_row = _normalizar_numero(row[0] if row else None)
            if pedido_row in faltantes:
                _append_linha(row, pedido_row)

    # Atualiza cache com o que veio do Sheets/Excel para uso futuro.
    for pedido, linhas in linhas_por_pedido.items():
        _cache_set_linhas_pedido(pedido, linhas)

    return linhas_po


def comparar_pedido_com_nf(numero_pedido: str, itens_nf: list) -> dict:
    """
    Compara as linhas do pedido (Google Sheets) com as linhas da NF, uma a uma
    de forma posicional.

    itens_nf: lista de dicts com chaves 'codigo', 'descricao', 'qtd', 'valor_unit'

    Retorno:
        {
            "encontrado": bool,
            "pares": [
                {
                    "linha":      int,       # nº da linha (1-based)
                    "nf_codigo":  str,
                    "nf_descricao": str,
                    "nf_qtd":     float | None,
                    "po_qtd":        float | None,
                    "nf_valor_unit": float | None,
                    "po_valor_unit": float | None,
                    "qtd_ok":        bool,
                    "valor_ok":      bool,
                    "ok":            bool
                },
                ...
            ],
            "total_ok": bool   # True se todos os pares batem
        }
    """
    pedidos = _parse_lista_pedidos(numero_pedido)
    if not pedidos:
        return {"encontrado": False, "pares": [], "total_ok": False}

    linhas_po = buscar_linhas_pedido(numero_pedido)

    if not linhas_po:
        return {"encontrado": False, "pares": [], "total_ok": False}

    def _metricas_match(nf_item: dict, po_item: dict) -> dict:
        po_qtd = po_item["qtd"] if po_item else None
        po_valor_unit = po_item["valor_unit"] if po_item else None
        po_pedido = po_item.get("pedido_compra") if po_item else ""
        po_codigo = po_item.get("codigo_material") if po_item else ""
        po_descricao = po_item.get("descricao_material") if po_item else ""

        nf_qtd = float(nf_item.get("qtd") or 0)
        nf_valor_unit = float(nf_item.get("valor_unit") or 0)
        nf_valor_total = float(nf_item.get("valor_total_linha") or 0)
        nf_codigo = _normalizar_codigo_material(nf_item.get("codigo"))

        qtd_ok = po_qtd is not None and abs(nf_qtd - po_qtd) < 0.0001
        unit_ok = po_valor_unit is not None and abs(nf_valor_unit - po_valor_unit) <= 0.01

        total_fallback_ok = False
        if not unit_ok and po_qtd is not None and po_valor_unit is not None:
            total_po = po_qtd * po_valor_unit
            total_fallback_ok = abs(total_po - nf_valor_total) <= 0.02

        po_codigo_norm = _normalizar_codigo_material(po_codigo)
        codigo_ok = bool(nf_codigo and po_codigo_norm and nf_codigo == po_codigo_norm)

        valor_ok = unit_ok or total_fallback_ok
        ok = qtd_ok and valor_ok

        score = 0
        if codigo_ok:
            score += 200
        if qtd_ok:
            score += 120
        if unit_ok:
            score += 100
        elif total_fallback_ok:
            score += 60

        return {
            "po_qtd": po_qtd,
            "po_valor_unit": po_valor_unit,
            "po_pedido": po_pedido,
            "po_codigo_material": po_codigo,
            "po_descricao_material": po_descricao,
            "nf_qtd": nf_qtd,
            "nf_valor_unit": nf_valor_unit,
            "nf_valor_total": nf_valor_total,
            "qtd_ok": qtd_ok,
            "valor_ok": valor_ok,
            "valor_via_total": total_fallback_ok and not unit_ok,
            "codigo_ok": codigo_ok,
            "ok": ok,
            "score": score,
        }

    total_nf = len(itens_nf)
    atribuicoes = [None] * total_nf
    po_usadas = set()

    # 1) Respeita vínculo manual quando válido e ainda não utilizado.
    for i, nf in enumerate(itens_nf):
        linha_po_vinculada = nf.get("linha_po_vinculada")
        if not isinstance(linha_po_vinculada, int) or linha_po_vinculada < 0:
            continue
        if linha_po_vinculada >= len(linhas_po):
            continue
        if linha_po_vinculada in po_usadas:
            continue
        atribuicoes[i] = linha_po_vinculada
        po_usadas.add(linha_po_vinculada)

    # 2) Match automático 1-para-1 pelo melhor score (sem duplicar linha PO).
    candidatos = []
    for i, nf in enumerate(itens_nf):
        if atribuicoes[i] is not None:
            continue
        for j, po in enumerate(linhas_po):
            if j in po_usadas:
                continue
            met = _metricas_match(nf, po)
            candidatos.append((met["score"], met["ok"], met["qtd_ok"], met["valor_ok"], i, j))

    candidatos.sort(key=lambda x: (x[0], x[1], x[2], x[3]), reverse=True)

    for score, _ok, _qtd, _valor, i, j in candidatos:
        if atribuicoes[i] is not None or j in po_usadas:
            continue
        # Só aplica automaticamente quando há indício mínimo de match (score > 0).
        if score <= 0:
            continue
        atribuicoes[i] = j
        po_usadas.add(j)

    # 3) Fallback posicional para tentar cobrir tudo sem repetir linha PO.
    for i, _nf in enumerate(itens_nf):
        if atribuicoes[i] is not None:
            continue
        if i < len(linhas_po) and i not in po_usadas:
            atribuicoes[i] = i
            po_usadas.add(i)

    pares = []
    for i, nf in enumerate(itens_nf):
        po_index = atribuicoes[i]
        po_row = linhas_po[po_index] if isinstance(po_index, int) and po_index < len(linhas_po) else None
        linha_po_vinculada = nf.get("linha_po_vinculada")
        usa_vinculo_manual = isinstance(linha_po_vinculada, int) and linha_po_vinculada >= 0
        met = _metricas_match(nf, po_row) if po_row else {
            "po_qtd": None,
            "po_valor_unit": None,
            "po_pedido": "",
            "po_codigo_material": "",
            "po_descricao_material": "",
            "nf_qtd": float(nf.get("qtd") or 0),
            "nf_valor_unit": float(nf.get("valor_unit") or 0),
            "nf_valor_total": float(nf.get("valor_total_linha") or 0),
            "qtd_ok": False,
            "valor_ok": False,
            "valor_via_total": False,
            "codigo_ok": False,
            "ok": False,
            "score": 0,
        }

        pares.append(
            {
                "linha": i + 1,
                "item_id": nf.get("item_id"),
                "linha_po_vinculada": linha_po_vinculada,
                "po_linha": (po_index + 1) if isinstance(po_index, int) else None,
                "po_index": po_index,
                "vinculo_manual": bool(usa_vinculo_manual),
                "nf_codigo": nf.get("codigo") or "---",
                "nf_descricao": nf.get("descricao") or "---",
                "nf_qtd": met["nf_qtd"],
                "nf_qtd_original": float(nf.get("qtd_original") or met["nf_qtd"] or 0),
                "nf_unidade": nf.get("unidade_comercial") or "UN",
                "conversao_fator": float(nf.get("conversao_fator") or 1.0),
                "conversao_unidade": nf.get("conversao_unidade") or (nf.get("unidade_comercial") or "UN"),
                "po_qtd": met["po_qtd"],
                "nf_valor_unit": met["nf_valor_unit"],
                "po_valor_unit": met["po_valor_unit"],
                "po_pedido": met["po_pedido"],
                "nf_valor_total": met["nf_valor_total"],
                "po_codigo_material": met["po_codigo_material"],
                "po_descricao_material": met["po_descricao_material"],
                "codigo_ok": met["codigo_ok"],
                "qtd_ok": met["qtd_ok"],
                "valor_ok": met["valor_ok"],
                "valor_via_total": met["valor_via_total"],
                "ok": met["ok"],
            }
        )

    total_ok = all(p["ok"] for p in pares)
    return {
        "encontrado": True,
        "pares": pares,
        "total_ok": total_ok,
        "linhas_po": [
            {
                "linha": idx + 1,
                "pedido_compra": po.get("pedido_compra") or "",
                "qtd": po.get("qtd"),
                "valor_unit": po.get("valor_unit"),
                "codigo_material": po.get("codigo_material") or "",
                "descricao_material": po.get("descricao_material") or "",
            }
            for idx, po in enumerate(linhas_po)
        ],
        "linhas_po_total": len(linhas_po),
        "linhas_po_utilizadas": len([p for p in pares if p.get("po_index") is not None]),
    }
