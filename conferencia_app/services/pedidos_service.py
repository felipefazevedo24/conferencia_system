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
import os
import re
import unicodedata
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
PEDIDOS_FONTE_GOOGLE_SHEETS = "GoogleSheets"
PEDIDOS_FONTE_CACHE_LOCAL = "CacheLocal"
PEDIDOS_FONTE_EXCEL_LOCAL = "ExcelLocal"
PEDIDOS_FONTE_ERP_POSTGRES = "ERPPostgres"

_PEDIDOS_HEADER_ALIASES = {
    "pedido_compra": {"oc", "ordemdecompra", "numerodaoc", "numeroordemdecompra", "pedidocompra", "pedido"},
    "codigo_material": {"codigomaterial", "codmaterial", "materialcodigo", "itemcodigo"},
    "descricao_material": {"descricaomaterial", "descricao", "materialdescricao", "itemdescricao"},
    "quantidade": {"quantidade", "qtd"},
    "valor_unit": {"valorunitario", "valorunit", "vlunit", "valor"},
    "fornecedor_codigo": {"codigofornecedor", "fornecedorcodigo", "codfornecedor"},
    "fornecedor_nome": {"fornecedor", "nomefornecedor", "razaosocialfornecedor", "fornecedorrazaosocial", "parceiro"},
    "fornecedor_cnpj": {"cnpjfornecedor", "fornecedorcnpj", "cnpj", "cnpjcpf", "documentofornecedor"},
    "contato": {"contato", "contatofornecedor", "responsavel", "responsavelfornecedor"},
    "telefone": {"telefone", "telefonefornecedor", "fone", "celular"},
    "email": {"email", "emailfornecedor"},
    "logradouro": {"endereco", "enderecofornecedor", "logradouro", "rua"},
    "numero": {"numero", "numeroendereco", "numerofornecedor"},
    "complemento": {"complemento", "complementoendereco"},
    "bairro": {"bairro", "bairrofornecedor"},
    "cidade": {"cidade", "cidadefornecedor"},
    "uf": {"uf", "estado", "uffornecedor"},
    "cep": {"cep", "cepfornecedor"},
    "observacoes": {"observacoes", "observacao", "obs", "janelaatendimento"},
}


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


def _normalizar_header(valor) -> str:
    texto = str(valor or "").strip().lower()
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(ch for ch in texto if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", "", texto)


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


def obter_fonte_pedidos_google_sheets() -> dict:
    return {
        "tipo": PEDIDOS_FONTE_GOOGLE_SHEETS,
        "label": "Google Sheets",
        "url": PEDIDOS_SHEETS_URL,
        "csv_url": _sheets_to_csv_url(PEDIDOS_SHEETS_URL),
    }


def obter_fonte_pedidos_erp_postgres() -> dict:
    return {
        "tipo": PEDIDOS_FONTE_ERP_POSTGRES,
        "label": "ERP Postgres",
        "url": "",
        "csv_url": "",
    }


def label_fonte_pedidos(fonte: str | None) -> str:
    mapping = {
        PEDIDOS_FONTE_ERP_POSTGRES: "ERP Postgres",
        PEDIDOS_FONTE_GOOGLE_SHEETS: "Google Sheets",
        PEDIDOS_FONTE_CACHE_LOCAL: "Cache local",
        PEDIDOS_FONTE_EXCEL_LOCAL: "Excel local",
    }
    return mapping.get(str(fonte or "").strip(), str(fonte or "").strip())


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


def _carregar_config_postgres_pedidos() -> dict:
    """Resolve credenciais de leitura do ERP sem depender de contexto Flask."""
    config_path = Path(__file__).resolve().parent.parent.parent / "instance" / "erp_lancamento_config.json"
    arquivo = {}
    try:
        if config_path.exists():
            arquivo = json.loads(config_path.read_text(encoding="utf-8")) or {}
            if not isinstance(arquivo, dict):
                arquivo = {}
    except Exception:
        arquivo = {}

    return {
        "host": str(
            os.environ.get("PEDIDOS_ERP_PG_HOST")
            or os.environ.get("ERP_LANCAMENTO_PG_HOST")
            or arquivo.get("host")
            or ""
        ).strip(),
        "port": int(
            os.environ.get("PEDIDOS_ERP_PG_PORT")
            or os.environ.get("ERP_LANCAMENTO_PG_PORT")
            or arquivo.get("port")
            or 5432
        ),
        "database": str(
            os.environ.get("PEDIDOS_ERP_PG_DB")
            or os.environ.get("ERP_LANCAMENTO_PG_DB")
            or arquivo.get("database")
            or ""
        ).strip(),
        "user": str(
            os.environ.get("PEDIDOS_ERP_PG_USER")
            or os.environ.get("ERP_LANCAMENTO_PG_USER")
            or arquivo.get("user")
            or ""
        ).strip(),
        "password": str(
            os.environ.get("PEDIDOS_ERP_PG_PASSWORD")
            or os.environ.get("ERP_LANCAMENTO_PG_PASSWORD")
            or arquivo.get("password")
            or ""
        ),
        "connect_timeout": int(os.environ.get("PEDIDOS_ERP_CONNECT_TIMEOUT", "5") or 5),
        "api_url": str(
            os.environ.get("PEDIDOS_ERP_API_URL")
            or os.environ.get("ERP_LANCAMENTO_API_URL")
            or arquivo.get("api_url")
            or ""
        ).strip().rstrip("/"),
        "api_token": str(
            os.environ.get("PEDIDOS_ERP_API_TOKEN")
            or os.environ.get("ERP_LANCAMENTO_API_TOKEN")
            or arquivo.get("api_token")
            or ""
        ),
        "api_timeout": int(
            os.environ.get("PEDIDOS_ERP_API_TIMEOUT")
            or os.environ.get("ERP_LANCAMENTO_API_TIMEOUT")
            or arquivo.get("api_timeout")
            or 30
        ),
    }


def _conectar_postgres_pedidos(cfg: dict):
    import psycopg2  # type: ignore

    return psycopg2.connect(
        host=cfg["host"],
        port=cfg["port"],
        dbname=cfg["database"],
        user=cfg["user"],
        password=cfg["password"],
        connect_timeout=cfg.get("connect_timeout") or 5,
    )


def _linha_postgres_to_pedido(row: dict) -> dict:
    ordem_compra = _normalizar_numero(row.get("ordem_compra"))
    cod_interno = str(row.get("cod_interno") or "").strip()
    descricao = str(row.get("descricao") or "").strip()
    pendente = float(row.get("pendente") or 0.0)
    preco_unitario = float(row.get("preco_unitario") or 0.0)
    vl_pendente = float(row.get("vl_pendente") or 0.0)
    total_item = float(row.get("total_item") or 0.0)
    fornecedor_cnpj = re.sub(r"\D", "", str(row.get("fornecedor_cnpj") or "").strip())
    cep = re.sub(r"\D", "", str(row.get("cep") or "").strip())
    linha = {
        "ordem_compra": ordem_compra,
        "cod_fornecedor": str(row.get("cod_fornecedor") or "").strip(),
        "fornecedor": str(row.get("fornecedor") or "").strip(),
        "cod_interno": cod_interno,
        "descricao": descricao,
        "pendente": pendente,
        "preco_unitario": preco_unitario,
        "vl_pendente": vl_pendente,
        "total_item": total_item,
        # Compatibilidade com o contrato antigo usado pelo auditor/agendamento.
        "pedido_compra": ordem_compra,
        "qtd": pendente,
        "valor_unit": preco_unitario,
        "codigo_material": cod_interno,
        "descricao_material": descricao,
        "fonte_dados": PEDIDOS_FONTE_ERP_POSTGRES,
    }

    for campo in (
        "tipo_pedido",
        "classificacao_item",
        "cfop_entrada_esperado",
        "cfop_envio_origem",
        "cod_os",
        "cod_os_aux",
        "cod_os_completo",
        "grupo_serv_ter",
        "sub_grupo_serv_ter",
    ):
        valor = row.get(campo)
        if valor not in (None, ""):
            linha[campo] = str(valor).strip()

    for campo in (
        "data_pedido",
        "cond_pagamento",
        "forma_pgto",
        "prazo_entrega",
        "solicitante",
        "tipo_movimento",
    ):
        valor = row.get(campo)
        if valor not in (None, ""):
            linha[campo] = valor.isoformat() if hasattr(valor, "isoformat") else str(valor).strip()

    for campo in ("subtotal", "totalgeral", "vl_frete", "vl_desconto", "ipi_total", "icms_total"):
        valor = row.get(campo)
        if valor not in (None, ""):
            try:
                linha[campo] = float(valor or 0)
            except (TypeError, ValueError):
                linha[campo] = 0.0

    extras = {
        "fornecedor_codigo": linha["cod_fornecedor"],
        "fornecedor_nome": linha["fornecedor"],
        "fornecedor_cnpj": fornecedor_cnpj,
        "contato": str(row.get("contato") or "").strip(),
        "telefone": str(row.get("telefone") or "").strip(),
        "email": str(row.get("email") or "").strip(),
        "logradouro": str(row.get("logradouro") or "").strip(),
        "numero": str(row.get("numero") or "").strip(),
        "complemento": str(row.get("complemento") or "").strip(),
        "bairro": str(row.get("bairro") or "").strip(),
        "cidade": str(row.get("cidade") or "").strip(),
        "uf": str(row.get("uf") or "").strip()[:2].upper(),
        "cep": cep,
        "observacoes": str(row.get("observacoes") or "").strip(),
    }
    linha.update({chave: valor for chave, valor in extras.items() if valor})
    return linha


def _buscar_linhas_pedido_postgres(pedidos: list[str]) -> list:
    if not pedidos:
        return []

    cfg = _carregar_config_postgres_pedidos()
    if cfg.get("api_url"):
        return _buscar_linhas_pedido_api(cfg, pedidos)

    if not cfg["host"] or not cfg["database"] or not cfg["user"]:
        return []

    sql = """
        with pedidos(numero) as (
            select unnest(%s::text[])
        ),
        oc_base as (
            select
                oc.cod_empresa,
                oc.codigo,
                oc.cod_fornecedor,
                coalesce(nullif(oc.fornecedor, ''), f.nome, f.razao_social) as fornecedor,
                coalesce(nullif(oc.fornecedor_cnpj, ''), f.cgc) as fornecedor_cnpj,
                coalesce(nullif(oc.contato_fornecedor, ''), f.contato, f.nome_vendedor) as contato,
                coalesce(nullif(oc.fornecedor_telefone, ''), f.fone1, f.fone2) as telefone,
                f.e_mail as email,
                coalesce(nullif(oc.fornecedor_endeferco, ''), f.endereco) as logradouro,
                coalesce(nullif(oc.fornecedor_numero, ''), f.numero_imovel) as numero,
                coalesce(nullif(oc.fornecedor_complento, ''), f.endereco_complemento) as complemento,
                coalesce(nullif(oc.fornecedor_bairro, ''), f.bairro) as bairro,
                coalesce(nullif(oc.fornecedor_cidade, ''), f.cidade) as cidade,
                coalesce(nullif(oc.fornecedor_uf, ''), f.uf) as uf,
                coalesce(nullif(oc.fornecedor_cep, ''), f.cep) as cep,
                oc.prazo_entrega as observacoes,
                oc.data as data_pedido,
                oc.cond_pagamento,
                oc.forma_pgto,
                oc.prazo_entrega,
                oc.solicitante,
                oc.tipo_movimento,
                coalesce(oc.subtotal, 0) as subtotal,
                coalesce(oc.totalgeral, 0) as totalgeral,
                coalesce(oc.vl_frete, 0) as vl_frete,
                coalesce(oc.vl_desconto, 0) as vl_desconto,
                coalesce(oc.ipi, 0) as ipi_total,
                coalesce(oc.icms, 0) as icms_total
            from public.tord_com oc
            left join public.tfornece f
              on f.cod_empresa = oc.cod_empresa
             and f.codigo = oc.cod_fornecedor
            join pedidos p on p.numero = oc.codigo::text
        )
        select
            oc.codigo::text as ordem_compra,
            oc.cod_fornecedor,
            oc.fornecedor,
            item.cod_interno,
            item.descricao,
            greatest(
                coalesce(item.qtde_compra, item.qtde, 0) - coalesce(item.qtde_entregue, 0),
                0
            ) as pendente,
            coalesce(item.preco_unitario, 0) as preco_unitario,
            greatest(
                coalesce(item.qtde_compra, item.qtde, 0) - coalesce(item.qtde_entregue, 0),
                0
            ) * coalesce(item.preco_unitario, 0) as vl_pendente,
            coalesce(
                item.total,
                coalesce(item.qtde_compra, item.qtde, 0) * coalesce(item.preco_unitario, 0)
            ) as total_item,
            oc.fornecedor_cnpj,
            oc.contato,
            oc.telefone,
            oc.email,
            oc.logradouro,
            oc.numero,
            oc.complemento,
            oc.bairro,
            oc.cidade,
            oc.uf,
            oc.cep,
            oc.observacoes,
            oc.data_pedido,
            oc.cond_pagamento,
            oc.forma_pgto,
            oc.prazo_entrega,
            oc.solicitante,
            oc.tipo_movimento,
            oc.subtotal,
            oc.totalgeral,
            oc.vl_frete,
            oc.vl_desconto,
            oc.ipi_total,
            oc.icms_total,
            'CompraFornecedor' as tipo_pedido,
            'MaterialCompra' as classificacao_item,
            null::text as cfop_entrada_esperado,
            null::text as cfop_envio_origem,
            null::text as cod_os,
            null::text as cod_os_aux,
            null::text as cod_os_completo,
            null::text as grupo_serv_ter,
            null::text as sub_grupo_serv_ter,
            10 as ordem_tipo,
            item.item as ordem_item
        from oc_base oc
        join public.tord_aux item
          on item.cod_empresa = oc.cod_empresa
         and item.cod_ord_compra = oc.codigo

        union all

        select
            oc.codigo::text as ordem_compra,
            oc.cod_fornecedor,
            oc.fornecedor,
            mat.cod_interno,
            mat.produto as descricao,
            coalesce(mat.qtde, 0) as pendente,
            0::double precision as preco_unitario,
            0::double precision as vl_pendente,
            0::double precision as total_item,
            oc.fornecedor_cnpj,
            oc.contato,
            oc.telefone,
            oc.email,
            oc.logradouro,
            oc.numero,
            oc.complemento,
            oc.bairro,
            oc.cidade,
            oc.uf,
            oc.cep,
            oc.observacoes,
            oc.data_pedido,
            oc.cond_pagamento,
            oc.forma_pgto,
            oc.prazo_entrega,
            oc.solicitante,
            oc.tipo_movimento,
            oc.subtotal,
            oc.totalgeral,
            oc.vl_frete,
            oc.vl_desconto,
            oc.ipi_total,
            oc.icms_total,
            'ServicoTerceiros' as tipo_pedido,
            'ProducaoPropria' as classificacao_item,
            '5902' as cfop_entrada_esperado,
            coalesce(aux.cfop_envio_nf, '5901') as cfop_envio_origem,
            serv.cod_os::text as cod_os,
            serv.cod_os_aux::text as cod_os_aux,
            serv.cod_os_completo,
            serv.grupo_serv_ter,
            serv.sub_grupo_serv_ter,
            20 as ordem_tipo,
            0 as ordem_item
        from oc_base oc
        join public.tord_serv serv
          on serv.cod_empresa = oc.cod_empresa
         and serv.cod_ordem_compra = oc.codigo
        join public.tserv_te_mat mat
          on mat.cod_empresa = serv.cod_empresa
         and mat.cod_os = serv.cod_os
         and mat.cod_os_aux = serv.cod_os_aux
        left join public.tcom_aux_os aux
          on aux.cod_empresa = serv.cod_empresa
         and aux.cod_os = serv.cod_os
         and aux.cod_os_aux = serv.cod_os_aux
         and aux.cod_produto = mat.cod_produto
         and coalesce(aux.cfop_envio_nf, '') <> ''
        where coalesce(mat.cod_interno, '') <> ''

        union all

        select
            oc.codigo::text as ordem_compra,
            oc.cod_fornecedor,
            oc.fornecedor,
            retorno.cod_interno_retorno_indu as cod_interno,
            retorno.produto_retorno_indu as descricao,
            coalesce(retorno.qtde, serv.qtde, 0) as pendente,
            coalesce(serv.vl_unitario, 0) as preco_unitario,
            coalesce(retorno.qtde, serv.qtde, 0) * coalesce(serv.vl_unitario, 0) as vl_pendente,
            coalesce(serv.vl_total, coalesce(retorno.qtde, serv.qtde, 0) * coalesce(serv.vl_unitario, 0)) as total_item,
            oc.fornecedor_cnpj,
            oc.contato,
            oc.telefone,
            oc.email,
            oc.logradouro,
            oc.numero,
            oc.complemento,
            oc.bairro,
            oc.cidade,
            oc.uf,
            oc.cep,
            oc.observacoes,
            oc.data_pedido,
            oc.cond_pagamento,
            oc.forma_pgto,
            oc.prazo_entrega,
            oc.solicitante,
            oc.tipo_movimento,
            oc.subtotal,
            oc.totalgeral,
            oc.vl_frete,
            oc.vl_desconto,
            oc.ipi_total,
            oc.icms_total,
            'ServicoTerceiros' as tipo_pedido,
            'ProducaoTerceiros' as classificacao_item,
            '5124' as cfop_entrada_esperado,
            null::text as cfop_envio_origem,
            serv.cod_os::text as cod_os,
            serv.cod_os_aux::text as cod_os_aux,
            serv.cod_os_completo,
            serv.grupo_serv_ter,
            serv.sub_grupo_serv_ter,
            30 as ordem_tipo,
            0 as ordem_item
        from oc_base oc
        join public.tord_serv serv
          on serv.cod_empresa = oc.cod_empresa
         and serv.cod_ordem_compra = oc.codigo
        join public.tserv_te retorno
          on retorno.cod_empresa = serv.cod_empresa
         and retorno.cod_os = serv.cod_os
         and retorno.cod_os_aux = serv.cod_os_aux
        where coalesce(retorno.cod_interno_retorno_indu, '') <> ''
        order by ordem_compra, ordem_tipo, cod_os, cod_os_aux, ordem_item, descricao
    """

    with _conectar_postgres_pedidos(cfg) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (pedidos,))
            cols = [desc[0] for desc in cur.description]
            return [_linha_postgres_to_pedido(dict(zip(cols, row))) for row in cur.fetchall()]


def _buscar_linhas_pedido_api(cfg: dict, pedidos: list[str]) -> list:
    url = f"{cfg['api_url']}/api/erp/pedidos"
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "ngrok-skip-browser-warning": "true",
        "User-Agent": "ColumbiaSync/ERP-Pedidos",
    }
    if cfg.get("api_token"):
        headers["Authorization"] = f"Bearer {cfg['api_token']}"

    resp = requests.post(url, headers=headers, json={"pedidos": pedidos}, timeout=cfg.get("api_timeout") or 30)
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, dict) or not data.get("sucesso"):
        raise RuntimeError(str((data or {}).get("erro") or "Resposta invalida da API ERP Pedidos"))

    linhas_raw = data.get("linhas") or []
    if not isinstance(linhas_raw, list):
        return []
    return [_linha_postgres_to_pedido(linha) for linha in linhas_raw if isinstance(linha, dict)]


def _resolver_header_map_pedidos(rows: list) -> dict[str, int]:
    if not rows:
        return {}
    header_row = rows[0] or []
    normalized = {_normalizar_header(valor): idx for idx, valor in enumerate(header_row)}
    mapping = {}
    for campo, aliases in _PEDIDOS_HEADER_ALIASES.items():
        for alias in aliases:
            idx = normalized.get(alias)
            if idx is not None:
                mapping[campo] = idx
                break
    return mapping


def _valor_celula_row(row, idx: int | None):
    if idx is None or idx < 0 or idx >= len(row):
        return None
    return row[idx]


def _texto_row(row, idx: int | None) -> str:
    valor = _valor_celula_row(row, idx)
    return str(valor).strip() if valor not in (None, "") else ""


def _extrair_metadados_pedido(row, header_map: dict[str, int]) -> dict:
    extras = {}
    for campo in (
        "fornecedor_codigo",
        "fornecedor_nome",
        "fornecedor_cnpj",
        "contato",
        "telefone",
        "email",
        "logradouro",
        "numero",
        "complemento",
        "bairro",
        "cidade",
        "uf",
        "cep",
        "observacoes",
    ):
        valor = _texto_row(row, header_map.get(campo))
        if campo in {"fornecedor_cnpj", "cep"}:
            valor = re.sub(r"\D", "", valor)
        if campo == "uf":
            valor = valor[:2].upper()
        if valor:
            extras[campo] = valor
    return extras


def _append_linha_pedido(
    destino: list,
    linhas_por_pedido: dict,
    row,
    pedido_row: str,
    *,
    header_map: dict[str, int] | None = None,
    fonte_dados: str = PEDIDOS_FONTE_GOOGLE_SHEETS,
) -> None:
    header_map = header_map or {}
    codigo_idx = header_map.get("codigo_material", 3)
    descricao_idx = header_map.get("descricao_material", 4)
    qtd_idx = header_map.get("quantidade", 5)
    valor_idx = header_map.get("valor_unit", 6)

    codigo_material_raw = _texto_row(row, codigo_idx)
    descricao_material = _texto_row(row, descricao_idx)
    linha = {
        "pedido_compra": pedido_row,
        "qtd": _ler_float(row, qtd_idx),
        "valor_unit": _ler_float(row, valor_idx),
        "codigo_material": _formatar_codigo_material_padrao(codigo_material_raw),
        "descricao_material": descricao_material,
        "fonte_dados": fonte_dados,
    }
    linha.update(_extrair_metadados_pedido(row, header_map))
    destino.append(linha)
    linhas_por_pedido.setdefault(pedido_row, []).append(linha)


def _normalizar_linha_cache(linha: dict, pedido: str) -> dict:
    normalizada = {
        "pedido_compra": str(linha.get("pedido_compra") or pedido),
        "qtd": float(linha.get("qtd") or 0.0),
        "valor_unit": float(linha.get("valor_unit") or 0.0),
        "codigo_material": str(linha.get("codigo_material") or "").strip(),
        "descricao_material": str(linha.get("descricao_material") or "").strip(),
        "fonte_dados": str(linha.get("fonte_dados") or PEDIDOS_FONTE_CACHE_LOCAL),
    }
    for campo in (
        "fornecedor_codigo",
        "fornecedor_nome",
        "fornecedor_cnpj",
        "contato",
        "telefone",
        "email",
        "logradouro",
        "numero",
        "complemento",
        "bairro",
        "cidade",
        "uf",
        "cep",
        "observacoes",
    ):
        valor = str(linha.get(campo) or "").strip()
        if campo in {"fornecedor_cnpj", "cep"}:
            valor = re.sub(r"\D", "", valor)
        if campo == "uf":
            valor = valor[:2].upper()
        if valor:
            normalizada[campo] = valor
    return normalizada


def _carregar_rows_excel_local() -> list:
    """Fallback para pedidos antigos quando não existem mais no Google Sheets."""
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
        return _buscar_linhas_pedido_postgres(pedidos)
    except Exception:
        return []


def comparar_pedido_com_nf(numero_pedido: str, itens_nf: list) -> dict:
    """
    Compara as linhas do pedido no ERP com as linhas da NF, uma a uma
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

    def _normalizar_fator(valor) -> float:
        try:
            f = float(valor or 1.0)
        except Exception:
            f = 1.0
        return f if f > 0 else 1.0

    def _fatores_candidatos(nf_item: dict, po_item: dict) -> list[float]:
        fator_manual = _normalizar_fator(nf_item.get("conversao_fator"))
        if bool(nf_item.get("conversao_manual")):
            return [fator_manual]

        candidatos = {
            1.0,
            10.0,
            12.0,
            100.0,
            1000.0,
            0.1,
            1.0 / 12.0,
            0.01,
            0.001,
        }

        nf_qtd_base = float(nf_item.get("qtd_original") or nf_item.get("qtd") or 0)
        po_qtd = float(po_item.get("qtd") or 0) if po_item else 0
        if nf_qtd_base > 0 and po_qtd > 0:
            ratio = po_qtd / nf_qtd_base
            if 0.0001 <= ratio <= 10000:
                candidatos.add(round(ratio, 6))

        return sorted(candidatos)

    def _metricas_match(nf_item: dict, po_item: dict) -> dict:
        po_qtd = po_item["qtd"] if po_item else None
        po_valor_unit = po_item["valor_unit"] if po_item else None
        po_pedido = po_item.get("pedido_compra") if po_item else ""
        po_codigo = po_item.get("codigo_material") if po_item else ""
        po_descricao = po_item.get("descricao_material") if po_item else ""

        nf_qtd_base = float(nf_item.get("qtd_original") or nf_item.get("qtd") or 0)
        nf_valor_total = float(nf_item.get("valor_total_linha") or 0)
        nf_codigo = _normalizar_codigo_material(nf_item.get("codigo"))
        po_codigo_norm = _normalizar_codigo_material(po_codigo)
        codigo_ok = bool(nf_codigo and po_codigo_norm and nf_codigo == po_codigo_norm)

        melhor = None
        fatores = _fatores_candidatos(nf_item, po_item)
        for fator in fatores:
            nf_qtd = nf_qtd_base * fator
            nf_valor_unit = (nf_valor_total / nf_qtd) if nf_qtd > 0 else 0.0

            qtd_diff = abs((po_qtd or 0) - nf_qtd) if po_qtd is not None else float("inf")
            qtd_ok = po_qtd is not None and qtd_diff < 0.0001
            unit_diff = abs((po_valor_unit or 0) - nf_valor_unit) if po_valor_unit is not None else float("inf")
            unit_ok = po_valor_unit is not None and unit_diff <= 0.01

            total_fallback_ok = False
            total_diff = float("inf")
            if po_qtd is not None and po_valor_unit is not None:
                total_po = po_qtd * po_valor_unit
                total_diff = abs(total_po - nf_valor_total)
                if not unit_ok:
                    total_fallback_ok = total_diff <= 0.02

            valor_ok = unit_ok or total_fallback_ok
            ok = qtd_ok and valor_ok

            score = 0
            if codigo_ok:
                score += 200
            if qtd_ok:
                score += 140
            if unit_ok:
                score += 120
            elif total_fallback_ok:
                score += 80

            if not qtd_ok and qtd_diff != float("inf"):
                score -= min(40, qtd_diff)
            if not unit_ok and unit_diff != float("inf"):
                score -= min(20, unit_diff * 10)

            candidato = {
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
                "fator_aplicado": fator,
                "qtd_diff": qtd_diff,
                "unit_diff": unit_diff,
                "total_diff": total_diff,
            }

            if melhor is None:
                melhor = candidato
                continue

            atual_rank = (candidato["score"], candidato["ok"], candidato["qtd_ok"], candidato["valor_ok"], -candidato["qtd_diff"], -candidato["unit_diff"], -candidato["total_diff"])
            melhor_rank = (melhor["score"], melhor["ok"], melhor["qtd_ok"], melhor["valor_ok"], -melhor["qtd_diff"], -melhor["unit_diff"], -melhor["total_diff"])
            if atual_rank > melhor_rank:
                melhor = candidato

        if melhor is None:
            melhor = {
                "po_qtd": po_qtd,
                "po_valor_unit": po_valor_unit,
                "po_pedido": po_pedido,
                "po_codigo_material": po_codigo,
                "po_descricao_material": po_descricao,
                "nf_qtd": nf_qtd_base,
                "nf_valor_unit": float(nf_item.get("valor_unit") or 0),
                "nf_valor_total": nf_valor_total,
                "qtd_ok": False,
                "valor_ok": False,
                "valor_via_total": False,
                "codigo_ok": codigo_ok,
                "ok": False,
                "score": 0,
                "fator_aplicado": _normalizar_fator(nf_item.get("conversao_fator")),
                "qtd_diff": float("inf"),
                "unit_diff": float("inf"),
                "total_diff": float("inf"),
            }

        return melhor

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
                "conversao_fator": float(met.get("fator_aplicado") or nf.get("conversao_fator") or 1.0),
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
