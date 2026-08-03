"""Camada de serviço: regras de negócio e orquestração de queries."""
from pathlib import Path
from typing import Optional
import unicodedata
import xml.etree.ElementTree as ET
import zipfile

from .. import db, queries
from ..config import get_settings


def _parse_os_list(n_os: Optional[str]) -> Optional[list[str]]:
    """Converte '9204,8714' (ou ' 9204 ; 8714 ') em ['9204','8714']. Vazio -> None."""
    if n_os is None:
        return None
    parts = [p.strip() for p in str(n_os).replace(";", ",").split(",")]
    parts = [p for p in parts if p]
    return parts or None


_CAMPOS_DATA_VALIDOS = {"dt_entrada", "dt_aprovacao", "dt_prevista"}

_CFOP_AP_FALLBACK = [
    "1101", "1102", "1116", "1117", "1120", "1124", "1126", "1252", "1352",
    "1401", "1403", "1406", "1407", "1551", "1933", "2102", "2126", "2352",
    "2401", "2403", "2406", "2407", "2501", "2556", "2933", "3101", "3102",
    "3551", "3556",
]


def _norm_header(v: str) -> str:
    s = unicodedata.normalize("NFKD", str(v or ""))
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return "".join(ch for ch in s.lower().strip() if ch.isalnum())


def _col_to_index(cell_ref: str) -> int:
    """Converte referência Excel (A1, BC12) no índice 0-based da coluna."""
    letters = "".join(ch for ch in str(cell_ref or "") if ch.isalpha()).upper()
    if not letters:
        return -1
    idx = 0
    for ch in letters:
        idx = idx * 26 + (ord(ch) - ord("A") + 1)
    return idx - 1


def _extract_cfop_tokens(v: str) -> list[str]:
    """Extrai CFOPs de um valor de célula (texto, número, lista separada por /,-,;)."""
    raw = str(v or "").strip()
    if not raw:
        return []

    # Normaliza separadores comuns para facilitar split.
    for sep in [";", "|", "/", "\\", "-"]:
        raw = raw.replace(sep, ",")

    tokens: list[str] = []
    for part in raw.split(","):
        digits = "".join(ch for ch in part if ch.isdigit())
        if not digits:
            continue
        # Se veio como número com decimal (ex.: 5102.0), mantém os 4 primeiros dígitos.
        if len(digits) >= 4:
            tokens.append(digits[:4])
    return tokens


def _load_cfop_ap_list() -> list[str]:
    xlsx = Path(__file__).resolve().parents[3] / "cfop_classificacao_completa.xlsx"
    if not xlsx.exists():
        return _CFOP_AP_FALLBACK

    ns = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    rel_ns = {"r": "http://schemas.openxmlformats.org/package/2006/relationships"}

    try:
        with zipfile.ZipFile(xlsx) as zf:
            shared = []
            if "xl/sharedStrings.xml" in zf.namelist():
                root_ss = ET.fromstring(zf.read("xl/sharedStrings.xml"))
                for si in root_ss.findall("a:si", ns):
                    txt = "".join(t.text or "" for t in si.findall(".//a:t", ns))
                    shared.append(txt)

            wb = ET.fromstring(zf.read("xl/workbook.xml"))
            sheet = wb.findall(".//a:sheets/a:sheet", ns)[0]
            rel_id = sheet.attrib.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")

            rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
            target = None
            for rel in rels.findall("r:Relationship", rel_ns):
                if rel.attrib.get("Id") == rel_id:
                    target = rel.attrib.get("Target")
                    break
            if not target:
                return _CFOP_AP_FALLBACK

            ws = ET.fromstring(zf.read("xl/" + target))

            def _cell_value(c):
                t = c.attrib.get("t")
                v = c.find("a:v", ns)
                if v is None:
                    return ""
                raw = v.text or ""
                if t == "s":
                    try:
                        return shared[int(raw)]
                    except Exception:
                        return raw
                return raw

            rows: list[list[str]] = []
            for row in ws.findall(".//a:sheetData/a:row", ns):
                vals_map: dict[int, str] = {}
                max_col = -1
                for c in row.findall("a:c", ns):
                    idx = _col_to_index(c.attrib.get("r", ""))
                    if idx < 0:
                        continue
                    vals_map[idx] = _cell_value(c).strip()
                    if idx > max_col:
                        max_col = idx
                if max_col >= 0:
                    vals = [vals_map.get(i, "") for i in range(max_col + 1)]
                    rows.append(vals)

        if not rows:
            return _CFOP_AP_FALLBACK

        header = rows[0]
        hnorm = [_norm_header(h) for h in header]

        idx_cfop = next((i for i, h in enumerate(hnorm) if h == "cfop"), None)
        idx_tipo = next((i for i, h in enumerate(hnorm) if "tipooperacao" in h), None)
        idx_ap = next((i for i, h in enumerate(hnorm) if "geracontasapagar" in h or h == "geraap"), None)
        if idx_cfop is None or idx_tipo is None or idx_ap is None:
            return _CFOP_AP_FALLBACK

        cfops = set()
        for row in rows[1:]:
            if len(row) <= max(idx_cfop, idx_tipo, idx_ap):
                continue
            cfops_linha = _extract_cfop_tokens(row[idx_cfop])
            tipo = _norm_header(row[idx_tipo])
            gera_ap = _norm_header(row[idx_ap])
            if tipo == "entrada" and gera_ap in {"sim", "s", "1", "true"}:
                for cfop in cfops_linha:
                    if len(cfop) == 4:
                        cfops.add(cfop)

        return sorted(cfops) if cfops else _CFOP_AP_FALLBACK
    except Exception:
        return _CFOP_AP_FALLBACK


def _norm_data_campo(v: Optional[str]) -> str:
    """Sanitiza o nome do campo-data (default dt_entrada)."""
    if v and v in _CAMPOS_DATA_VALIDOS:
        return v
    return "dt_entrada"


def _norm_data(v) -> Optional[str]:
    """Normaliza data: '' -> None, datetime/date -> 'YYYY-MM-DD'."""
    if v is None or v == "":
        return None
    if hasattr(v, "isoformat"):
        return v.isoformat()[:10]
    return str(v)[:10]


def _only_digits(v: Optional[str]) -> Optional[str]:
    if v is None:
        return None
    s = ''.join(ch for ch in str(v) if ch.isdigit())
    return s or None


def _filtro_data(data_campo, data_de, data_ate) -> dict:
    return {
        "data_campo": _norm_data_campo(data_campo),
        "data_de":    _norm_data(data_de),
        "data_ate":   _norm_data(data_ate),
    }


def listar_materiais_por_os(
    n_os: Optional[str] = None,
    cod_empresa: Optional[int] = None,
    classificacao: Optional[str] = None,
    data_campo: Optional[str] = None,
    data_de=None,
    data_ate=None,
    limite: int = 5000,
) -> list[dict]:
    empresa = cod_empresa if cod_empresa is not None else get_settings().PG_COD_EMPRESA
    params = {
        "cod_empresa": empresa,
        "n_os_list": _parse_os_list(n_os),
        "classificacao": classificacao,
        "limite": max(1, min(int(limite or 5000), 20000)),
    }
    params.update(_filtro_data(data_campo, data_de, data_ate))
    return db.fetch_all(queries.SQL_MATERIAIS_POR_OS, params)


def indicadores_gap(
    metodo: Optional[int] = None,
    cod_empresa: Optional[int] = None,
    classificacao: Optional[str] = None,
    n_os: Optional[str] = None,
    data_campo: Optional[str] = None,
    data_de=None,
    data_ate=None,
) -> list[dict]:
    empresa = cod_empresa if cod_empresa is not None else get_settings().PG_COD_EMPRESA
    params = {
        "cod_empresa": empresa,
        "metodo": metodo,
        "classificacao": classificacao,
        "n_os_list": _parse_os_list(n_os),
    }
    params.update(_filtro_data(data_campo, data_de, data_ate))
    return db.fetch_all(queries.SQL_GAP_COMPRAS, params)


def listar_classificacoes(cod_empresa: Optional[int] = None) -> list[dict]:
    empresa = cod_empresa if cod_empresa is not None else get_settings().PG_COD_EMPRESA
    return db.fetch_all(queries.SQL_CLASSIFICACOES, {"cod_empresa": empresa})


def painel_os(
    cod_empresa: Optional[int] = None,
    classificacao: Optional[str] = None,
    n_os: Optional[str] = None,
    somente_abertas: bool = True,
    limite: int = 2000,
    data_campo: Optional[str] = None,
    data_de=None,
    data_ate=None,
) -> list[dict]:
    empresa = cod_empresa if cod_empresa is not None else get_settings().PG_COD_EMPRESA
    params = {
        "cod_empresa": empresa,
        "classificacao": classificacao,
        "n_os_list": _parse_os_list(n_os),
        "somente_abertas": 1 if somente_abertas else 0,
        "limite": max(1, min(limite, 10000)),
    }
    params.update(_filtro_data(data_campo, data_de, data_ate))
    return db.fetch_all(queries.SQL_OS_PAINEL, params)


def historico_ordens_compra(
    cod_empresa: Optional[int] = None,
    classificacao: Optional[str] = None,
    n_os: Optional[str] = None,
    situacao: Optional[str] = None,
    data_campo: Optional[str] = None,
    data_de=None,
    data_ate=None,
    limite: int = 5000,
) -> dict:
    empresa = cod_empresa if cod_empresa is not None else get_settings().PG_COD_EMPRESA
    sit = (situacao or "todas").strip().lower()
    if sit not in {"todas", "abertas", "encerradas"}:
        sit = "todas"
    params = {
        "cod_empresa": empresa,
        "classificacao": classificacao,
        "n_os_list": _parse_os_list(n_os),
        "situacao": sit,
        "limite": max(1, min(int(limite or 5000), 20000)),
    }
    params.update(_filtro_data(data_campo, data_de, data_ate))

    headers = db.fetch_all(queries.SQL_HIST_OC_HEADER, params)
    itens = db.fetch_all(queries.SQL_HIST_OC_ITENS, params)

    abertas = sum(1 for h in headers if str(h.get("situacao_oc") or "").upper() == "ABERTA")
    encerradas = sum(1 for h in headers if str(h.get("situacao_oc") or "").upper() == "ENCERRADA")
    qtd_itens = len(itens)
    qtd_linhas = sum(int(h.get("qtd_linhas") or 0) for h in headers)
    qtd_os = len({str(it.get("n_os") or "") for it in itens if it.get("n_os")})
    qtd_produtos = len({int(it.get("cod_produto")) for it in itens if it.get("cod_produto") is not None})
    volume_qtd = round(sum(float(it.get("qtde_liquida") or 0) for it in itens), 2)

    return {
        "resumo": {
            "qtd_oc": len(headers),
            "qtd_oc_abertas": abertas,
            "qtd_oc_encerradas": encerradas,
            "qtd_itens": qtd_itens,
            "qtd_linhas": qtd_linhas,
            "qtd_os": qtd_os,
            "qtd_produtos": qtd_produtos,
            "volume_qtd": volume_qtd,
        },
        "headers": headers,
        "itens": itens,
    }


def ordens_compra_entregas(
    cod_empresa: Optional[int] = None,
    limite: int = 2000,
) -> list[dict]:
    """OCs em aberto (com SALDO a receber) e sua previsao de entrega (da OC).

    Uma OC por linha, com fornecedor e previsao. OCs ja recebidas (saldo 0)
    nao entram. O painel separa atrasadas e a chegar na semana.
    """
    empresa = cod_empresa if cod_empresa is not None else get_settings().PG_COD_EMPRESA
    params = {
        "cod_empresa": empresa,
        "limite": max(1, min(int(limite or 2000), 5000)),
    }
    return db.fetch_all(queries.SQL_OC_ENTREGAS, params)


def ordens_compra_cif_recentes(
    cod_empresa: Optional[int] = None,
    janela_dias: int = 60,
    limite: int = 2000,
) -> list[dict]:
    """OCs em aberto com previsao de entrega recente + cabecalho completo (JSON).

    Retorna, por OC em aberto, a previsao de entrega, o cabecalho da OC
    (oc_json) e o cadastro do fornecedor (fornecedor_json) para a automacao de
    Solicitacoes Logisticas CIF classificar a modalidade de frete e herdar o
    endereco de coleta.
    """
    empresa = cod_empresa if cod_empresa is not None else get_settings().PG_COD_EMPRESA
    params = {
        "cod_empresa": empresa,
        "janela_dias": max(0, int(janela_dias or 0)),
        "limite": max(1, min(int(limite or 2000), 5000)),
    }
    return db.fetch_all(queries.SQL_OC_CIF_RECENTES, params)


def visibility_compras(
    cod_empresa: Optional[int] = None,
    classificacao: Optional[str] = None,
    n_os: Optional[str] = None,
    somente_sc_sem_oc: bool = False,
    data_campo: Optional[str] = None,
    data_de=None,
    data_ate=None,
    limite: int = 8000,
) -> dict:
    """Visão de visibilidade de compras.

    Header: solicitações e ordens de compra com status do fluxo.
    Detalhada: itens pendentes no estado "A COMPRAR".
    """
    empresa = cod_empresa if cod_empresa is not None else get_settings().PG_COD_EMPRESA
    params = {
        "cod_empresa": empresa,
        "classificacao": classificacao,
        "n_os_list": _parse_os_list(n_os),
        "somente_sc_sem_oc": 1 if somente_sc_sem_oc else 0,
        "limite": max(1, min(int(limite or 8000), 30000)),
    }
    params.update(_filtro_data(data_campo, data_de, data_ate))

    headers = db.fetch_all(queries.SQL_VISIBILITY_HEADER, params)
    itens = db.fetch_all(queries.SQL_VISIBILITY_DETALHADA, params)

    qtd_sc_total = len({int(h.get("cod_solicitacao")) for h in headers if h.get("cod_solicitacao") is not None})
    qtd_sc_abertas = len({
        int(h.get("cod_solicitacao"))
        for h in headers
        if h.get("cod_solicitacao") is not None and int(h.get("solicitacao_aberta") or 0) == 1
    })
    qtd_sc_encerradas = len({
        int(h.get("cod_solicitacao"))
        for h in headers
        if h.get("cod_solicitacao") is not None and int(h.get("solicitacao_aberta") or 0) == 0
    })
    qtd_oc = len({int(h.get("cod_ordem_compra")) for h in headers if h.get("cod_ordem_compra") is not None})
    qtd_os = len({str(it.get("n_os") or "") for it in itens if it.get("n_os")})
    qtd_produtos = len({int(it.get("cod_produto")) for it in itens if it.get("cod_produto") is not None})
    volume_pendente = round(sum(float(it.get("qtde_pendente") or 0) for it in itens), 2)

    return {
        "resumo": {
            "qtd_sc": qtd_sc_abertas,
            "qtd_sc_abertas": qtd_sc_abertas,
            "qtd_sc_encerradas": qtd_sc_encerradas,
            "qtd_sc_total": qtd_sc_total,
            "qtd_oc": qtd_oc,
            "qtd_headers": len(headers),
            "qtd_itens_acomprar": len(itens),
            "qtd_os": qtd_os,
            "qtd_produtos": qtd_produtos,
            "volume_pendente": volume_pendente,
        },
        "headers": headers,
        "itens": itens,
    }


def _parse_date_like(v):
    if v is None or v == "":
        return None
    if hasattr(v, "date") and hasattr(v, "year"):
        try:
            return v.date()
        except Exception:
            pass
    if hasattr(v, "year") and hasattr(v, "month") and hasattr(v, "day"):
        return v
    try:
        return _datetime.fromisoformat(str(v)[:10]).date()
    except Exception:
        return None


def _month_periods(rows: list[dict], data_de=None, data_ate=None) -> list[str]:
    dt_de = _parse_date_like(data_de)
    dt_ate = _parse_date_like(data_ate)
    if dt_de and dt_ate:
        atual = dt_de.replace(day=1)
        fim = dt_ate.replace(day=1)
        meses = []
        while atual <= fim:
            meses.append(f"{atual.year:04d}-{atual.month:02d}")
            if atual.month == 12:
                atual = atual.replace(year=atual.year + 1, month=1)
            else:
                atual = atual.replace(month=atual.month + 1)
        return meses
    return sorted({str(r.get("periodo_mes")) for r in rows if r.get("periodo_mes")})


def _join_distinct(values: set[str], fallback: str) -> str:
    cleaned = sorted(v for v in values if v)
    return ", ".join(cleaned) if cleaned else fallback


def spend_baseline(
    cod_empresa: Optional[int] = None,
    tipo_item: Optional[str] = None,
    classificacao_item: Optional[str] = None,
    destinatario_cnpj: Optional[str] = None,
    data_de=None,
    data_ate=None,
    limite: int = 10000,
) -> list[dict]:
    empresa = cod_empresa if cod_empresa is not None else get_settings().PG_COD_EMPRESA
    tipo = (tipo_item or "").strip().upper() or None
    if tipo not in {None, "SERVICO", "PRODUTO"}:
        tipo = None
    params = {
        "cod_empresa": empresa,
        "tipo_item": tipo,
        "classificacao_item": classificacao_item,
        "destinatario_cnpj": _only_digits(destinatario_cnpj),
        "data_de": _norm_data(data_de),
        "data_ate": _norm_data(data_ate),
        "limite": max(1, min(int(limite or 10000), 30000)),
    }
    rows = db.fetch_all(queries.SQL_SPEND_BASELINE, params)
    meses = _month_periods(rows, data_de=data_de, data_ate=data_ate)
    month_keys = {mes: f"mes_{mes.replace('-', '_')}" for mes in meses}

    agrupado: dict[tuple, dict] = {}
    meta: dict[tuple, dict] = {}

    for row in rows:
        key = (
            row.get("cod_empresa"),
            str(row.get("cnpj") or ""),
            str(row.get("fornecedor") or ""),
        )
        if key not in agrupado:
            item = {
                "cod_empresa": row.get("cod_empresa"),
                "cnpj": row.get("cnpj"),
                "fornecedor": row.get("fornecedor"),
                "tipo_item": "",
                "classificacao_item": "",
                "cfop_entrada": "",
                "cfop_nf": "",
                "natureza_operacao": "",
                "total_periodo": 0.0,
                "qtd_itens_total": 0,
                "qtd_nf_total": 0,
            }
            for month_key in month_keys.values():
                item[month_key] = 0.0
            agrupado[key] = item
            meta[key] = {
                "tipo_item": set(),
                "classificacao_item": set(),
                "cfop_entrada": set(),
                "cfop_nf": set(),
                "natureza_operacao": set(),
            }

        item = agrupado[key]
        info = meta[key]
        periodo = str(row.get("periodo_mes") or "")
        valor = float(row.get("valor_mensal") or 0)
        if periodo in month_keys:
            item[month_keys[periodo]] += valor
        item["total_periodo"] += valor
        item["qtd_itens_total"] += int(row.get("qtd_itens") or 0)
        item["qtd_nf_total"] += int(row.get("qtd_nf") or 0)

        for campo in info.keys():
            valor_campo = str(row.get(campo) or "").strip()
            if valor_campo and not valor_campo.startswith("(Sem "):
                info[campo].add(valor_campo)

    result = []
    for key, item in agrupado.items():
        info = meta[key]
        item["tipo_item"] = _join_distinct(info["tipo_item"], "PRODUTO")
        item["classificacao_item"] = _join_distinct(info["classificacao_item"], "(Sem classificação)")
        item["cfop_entrada"] = _join_distinct(info["cfop_entrada"], "(Sem CFOP entrada)")
        item["cfop_nf"] = _join_distinct(info["cfop_nf"], "(Sem CFOP NF)")
        item["natureza_operacao"] = _join_distinct(info["natureza_operacao"], "(Sem natureza)")
        item["total_periodo"] = round(item["total_periodo"], 2)
        for month_key in month_keys.values():
            item[month_key] = round(float(item[month_key] or 0), 2)
        result.append(item)

    result.sort(key=lambda r: (-float(r.get("total_periodo") or 0), str(r.get("fornecedor") or "")))
    return result


def spend_baseline_composicao(
    cod_empresa: Optional[int] = None,
    cnpj: Optional[str] = None,
    fornecedor: Optional[str] = None,
    tipo_item: Optional[str] = None,
    data_de=None,
    data_ate=None,
    limite: int = 30000,
) -> list[dict]:
    empresa = cod_empresa if cod_empresa is not None else get_settings().PG_COD_EMPRESA
    tipo = (tipo_item or "").strip().upper() or None
    if tipo not in {None, "SERVICO", "PRODUTO"}:
        tipo = None
    params = {
        "cod_empresa": empresa,
        "cnpj": _only_digits(cnpj),
        "fornecedor": (fornecedor or "").strip() or None,
        "tipo_item": tipo,
        "data_de": _norm_data(data_de),
        "data_ate": _norm_data(data_ate),
        "limite": max(1, min(int(limite or 30000), 50000)),
    }
    return db.fetch_all(queries.SQL_SPEND_BASELINE_COMPOSICAO, params)


def healthcheck_db() -> bool:
    row = db.fetch_one(queries.SQL_HEALTHCHECK)
    return bool(row and row.get("ok") == 1)


# -------------------------------------------------------------------
# Dashboard: performance por departamento (engenharia / compras / produ\u00e7\u00e3o)
# Buckets: Expired, On going, Planned, Pipeline next week.
# -------------------------------------------------------------------
from datetime import date as _date, datetime as _datetime, timedelta as _timedelta


_DEPARTAMENTOS = [
    ("Engenharia", "engenharia_inicio", "engenharia_prazo"),
    ("Compras",    "compras_inicio",    "compras_prazo"),
    ("Produção",   "producao_inicio",   "producao_prazo"),
]
_BUCKETS = ["Expired", "On going", "Planned", "Pipeline next week", "Sem prazo"]


def _to_date(v):
    if v is None:
        return None
    if isinstance(v, _datetime):
        return v.date()
    if isinstance(v, _date):
        return v
    try:
        return _datetime.fromisoformat(str(v)[:10]).date()
    except Exception:
        return None


def _bucket_da_fase(inicio, prazo, hoje, prox_semana_fim, concluido: bool):
    if concluido:
        return None
    if inicio is None or prazo is None:
        return "Sem prazo"
    if prazo < hoje:
        return "Expired"
    if inicio <= hoje <= prazo:
        return "On going"
    # inicio > hoje (futuro)
    if inicio <= prox_semana_fim:
        return "Pipeline next week"
    return "Planned"


def _calcula_buckets(rows: list[dict], hoje, prox_semana_fim, concl_fn) -> list[dict]:
    """Monta o array de departamentos (Engenharia/Compras/Producao) com
    contagem de Expired/On going/Planned/Pipeline next week.
    `concl_fn(dep_nome, row)` -> bool indica se a fase ja esta concluida
    naquela linha (excluida da contagem)."""
    departamentos = []
    for dep_nome, k_ini, k_fim in _DEPARTAMENTOS:
        counts = {b: 0 for b in _BUCKETS}
        total = 0
        for r in rows:
            concl = concl_fn(dep_nome, r)
            b = _bucket_da_fase(
                _to_date(r.get(k_ini)),
                _to_date(r.get(k_fim)),
                hoje, prox_semana_fim, concl,
            )
            if b is None:
                continue
            counts[b] += 1
            total += 1
        departamentos.append({
            "departamento": dep_nome,
            "total": total,
            "buckets": [
                {
                    "label": b,
                    "qtd":  counts[b],
                    "perc": round((counts[b] / total) * 100, 2) if total else 0.0,
                }
                for b in _BUCKETS
            ],
        })
    return departamentos


def _concl_os(dep_nome: str, r: dict) -> bool:
    """Regra de 'concluido por fase' no nivel OS-mae (consolidado)."""
    if dep_nome == "Engenharia":
        return str(r.get("status_lis_material") or "") == "Com lista"
    if dep_nome == "Compras":
        soc = str(r.get("status_ordem_compra") or "").upper()
        sdisp = str(r.get("status_disponibilidade") or "").upper()
        return (soc == "EMITIDA") or (soc == "SEM OC" and sdisp == "DISPONIVEL")
    # Producao
    pf = str(r.get("producao_finalizada") or "").lower()
    return pf in ("sim", "sem processos")


# status_material por item considerados "fora da fila de compras"
_COMPRAS_CONCL_ITEM = {"ENTREGUE", "OC EMITIDA", "COBERTO ESTOQUE", "RESERVADO", "SOLICITACAO"}


def dashboard_performance(
    cod_empresa: Optional[int] = None,
    classificacao: Optional[str] = None,
    n_os: Optional[str] = None,
    somente_abertas: bool = True,
    data_campo: Optional[str] = None,
    data_de=None,
    data_ate=None,
    limite: int = 10000,
) -> dict:
    rows = painel_os(
        cod_empresa=cod_empresa,
        classificacao=classificacao,
        n_os=n_os,
        somente_abertas=somente_abertas,
        limite=limite,
        data_campo=data_campo,
        data_de=data_de,
        data_ate=data_ate,
    )
    hoje = _date.today()
    prox_semana_fim = hoje + _timedelta(days=7)

    # -------- Consolidado (1 unidade por OS-mae) --------
    consolidado = _calcula_buckets(rows, hoje, prox_semana_fim, _concl_os)

    # -------- Detalhado (1 unidade por item de tlis_mat) --------
    # Engenharia/Producao usam o status da OS-mae (lookup por cod_os);
    # Compras usa o status_material proprio do item.
    os_lookup = {
        (r.get("cod_empresa"), r.get("cod_os")): r for r in rows
    }
    materiais = listar_materiais_por_os(
        n_os=n_os,
        cod_empresa=cod_empresa,
        classificacao=classificacao,
        data_campo=data_campo,
        data_de=data_de,
        data_ate=data_ate,
        limite=20000,
    )
    def _concl_item(dep_nome: str, it: dict) -> bool:
        # Só entram em Compras os itens com metodo_reposicao==0 (Compras)
        if dep_nome == "Compras":
            if it.get("metodo_reposicao", 0) != 0:
                return True  # Não é de compras, considera concluído para Compras
            return str(it.get("status_material") or "").upper() in _COMPRAS_CONCL_ITEM
        # Produção recebe tudo que não é compras
        if dep_nome == "Produção":
            if it.get("metodo_reposicao", 0) != 0:
                # Só considera "não concluído" se status_material não for atendido
                return str(it.get("status_material") or "").upper() in {"ENTREGUE", "COBERTO ESTOQUE"}
        os_row = os_lookup.get((it.get("cod_empresa"), it.get("cod_os"))) or {}
        return _concl_os(dep_nome, os_row)

    # Adiciona coluna 'pendente_compras' para cada item
    for it in materiais:
        metodo = it.get("metodo_reposicao", 0)
        status = str(it.get("status_material") or "").upper() or "(sem status)"
        it["pendente_compras"] = (
            (metodo in (0, 3)) and status == "A COMPRAR" and not _concl_item("Compras", it)
        )

    # Garante que o bucket 'A COMPRAR' em detalhado['Compras'] seja igual ao do status_materiais
    detalhado = _calcula_buckets(materiais, hoje, prox_semana_fim, _concl_item)
    # Corrige o bucket 'A COMPRAR' em detalhado['Compras'] para usar a mesma contagem do status_materiais
    # Unifica a lógica de contagem do bucket 'A COMPRAR' nos dois gráficos detalhados
    _ORDEM_STATUS = ["ENTREGUE", "OC EMITIDA", "SOLICITACAO",
                     "COBERTO ESTOQUE", "RESERVADO", "A COMPRAR"]
    cont_status: dict[str, int] = {}
    a_comprar_count = 0
    for it in materiais:
        metodo = it.get("metodo_reposicao", 0)
        status = str(it.get("status_material") or "").upper() or "(sem status)"
        # método 2 sempre vira 'PRODUÇÃO', nunca entra como 'A COMPRAR' ou 'RESERVADO'
        if metodo == 2:
            cont_status["PRODUÇÃO"] = cont_status.get("PRODUÇÃO", 0) + 1
            continue
        # Só conta como 'A COMPRAR' se não estiver concluído para Compras
        if status == "A COMPRAR" and metodo in (0, 3) and not _concl_item("Compras", it):
            a_comprar_count += 1
            cont_status["A COMPRAR"] = cont_status.get("A COMPRAR", 0) + 1
            continue
        # método 3 só pode ser 'A COMPRAR' (já tratado acima), ignora outros status
        if metodo == 3:
            continue
        # método 0: só conta 'A COMPRAR' se não estiver concluído para Compras
        if metodo == 0:
            if status == "A COMPRAR":
                if not _concl_item("Compras", it):
                    cont_status[status] = cont_status.get(status, 0) + 1
                # se estiver concluído, não conta
            else:
                cont_status[status] = cont_status.get(status, 0) + 1
            continue
        # outros métodos não entram
    total_status = sum(cont_status.values())
    labels_ordenados = [s for s in _ORDEM_STATUS if s in cont_status] + \
        [s for s in cont_status if s not in _ORDEM_STATUS]
    status_materiais = {
        "departamento": "Status itens",
        "total": total_status,
        "buckets": [
            {
                "label": s,
                "qtd": cont_status[s],
                "perc": round((cont_status[s] / total_status) * 100, 2) if total_status else 0.0,
            }
            for s in labels_ordenados
        ],
    }
    # Garante que o bucket 'A COMPRAR' em detalhado['Compras'] seja igual ao do status_materiais
    for dep in detalhado:
        if dep["departamento"] == "Compras":
            for b in dep["buckets"]:
                if b["label"] == "A COMPRAR":
                    b["qtd"] = a_comprar_count
                    b["perc"] = round((b["qtd"] / dep["total"])*100, 2) if dep["total"] else 0.0

    return {
        "hoje": hoje.isoformat(),
        "pipeline_ate": prox_semana_fim.isoformat(),
        "total_os": len(rows),
        "total_itens": len(materiais),
        # Mantido para compat com clientes antigos (== consolidado).
        "departamentos": consolidado,
        "consolidado": consolidado,
        "detalhado": detalhado,
        "status_materiais": status_materiais,
    }
