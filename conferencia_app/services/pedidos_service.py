"""
Serviço de consulta ao Excel de pedidos de compra.

O arquivo pedidos.xlsx deve estar em instance/pedidos/pedidos.xlsx.
Estrutura esperada:
  - Coluna A: número do pedido
  - Coluna O: quantidade por linha de item
"""

from pathlib import Path

try:
    import openpyxl
    _OPENPYXL_OK = True
except ImportError:
    _OPENPYXL_OK = False

_BASE_DIR = Path(__file__).resolve().parent.parent.parent
PEDIDOS_EXCEL_PATH = _BASE_DIR / "instance" / "pedidos" / "pedidos.xlsx"


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
    return str(val).strip()


def _ler_qtd(row, col_index: int) -> float:
    val = row[col_index] if len(row) > col_index else None
    try:
        return float(val) if val not in (None, "") else 0.0
    except (TypeError, ValueError):
        return 0.0


def _carregar_wb():
    if not _OPENPYXL_OK:
        raise RuntimeError("openpyxl não está instalado. Execute: pip install openpyxl")
    if not PEDIDOS_EXCEL_PATH.exists():
        raise FileNotFoundError(f"Arquivo de pedidos não encontrado: {PEDIDOS_EXCEL_PATH}")
    return openpyxl.load_workbook(PEDIDOS_EXCEL_PATH, read_only=True, data_only=True)


def comparar_pedido_com_nf(numero_pedido: str, itens_nf: list) -> dict:
    """
    Compara as linhas do pedido (Excel) com as linhas da NF, uma a uma
    de forma posicional.

    itens_nf: lista de dicts com chaves 'codigo', 'descricao', 'qtd'

    Retorno:
        {
            "encontrado": bool,
            "pares": [
                {
                    "linha":      int,       # nº da linha (1-based)
                    "nf_codigo":  str,
                    "nf_descricao": str,
                    "nf_qtd":     float | None,
                    "po_qtd":     float | None,
                    "ok":         bool
                },
                ...
            ],
            "total_ok": bool   # True se todos os pares batem
        }
    """
    numero_pedido = _normalizar_numero(numero_pedido)
    if not numero_pedido:
        return {"encontrado": False, "pares": [], "total_ok": False}

    wb = _carregar_wb()
    ws = wb.active

    linhas_po = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        if _normalizar_numero(row[0] if row else None) == numero_pedido:
            linhas_po.append(_ler_qtd(row, 14))   # coluna O = índice 14

    wb.close()

    if not linhas_po:
        return {"encontrado": False, "pares": [], "total_ok": False}

    n = max(len(linhas_po), len(itens_nf))
    pares = []
    for i in range(n):
        nf = itens_nf[i] if i < len(itens_nf) else None
        po_qtd = linhas_po[i] if i < len(linhas_po) else None
        nf_qtd = float(nf["qtd"] or 0) if nf else None
        ok = (nf_qtd is not None and po_qtd is not None
              and abs(nf_qtd - po_qtd) < 0.0001)
        pares.append({
            "linha":        i + 1,
            "nf_codigo":    nf["codigo"] if nf else "---",
            "nf_descricao": nf["descricao"] if nf else "---",
            "nf_qtd":       nf_qtd,
            "po_qtd":       po_qtd,
            "ok":           ok,
        })

    total_ok = all(p["ok"] for p in pares)
    return {"encontrado": True, "pares": pares, "total_ok": total_ok}
