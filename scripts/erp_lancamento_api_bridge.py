"""API bridge para o ERP Lancamento rodar em uma VM com acesso ao Postgres.

Uso local/VM:
    set ERP_BRIDGE_TOKEN=troque-por-um-token-forte
    set ERP_LANCAMENTO_PG_HOST=10.250.100.251
    set ERP_LANCAMENTO_PG_DB=CPS
    set ERP_LANCAMENTO_PG_USER=DevLeitura
    set ERP_LANCAMENTO_PG_PASSWORD=...
    python scripts/erp_lancamento_api_bridge.py

Endpoint:
    POST /api/erp/lancamentos
    Authorization: Bearer <ERP_BRIDGE_TOKEN>
"""
from __future__ import annotations

import base64
import os
import re
import sys
import time
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from xml.etree import ElementTree as et

# Quando a bridge e executada direto por:
#   python .\scripts\erp_lancamento_api_bridge.py
# o Python coloca apenas a pasta scripts/ no sys.path. O bloco abaixo procura
# a raiz real do projeto e injeta antes dos imports de conferencia_app.
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, os.pardir))
if not os.path.isdir(os.path.join(ROOT_DIR, "conferencia_app")):
    ROOT_DIR = os.getcwd()
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from flask import Blueprint, Flask, g, jsonify, request, session
import psycopg2  # type: ignore

from conferencia_app.bootstrap import initialize_database
from conferencia_app.config import Config
from conferencia_app.error_handlers import register_error_handlers
from conferencia_app.extensions import db, migrate
from conferencia_app.routes.auth_routes import auth_bp
from conferencia_app.routes.facilities_routes import facilities_bp


def _env(name: str, default: str = "") -> str:
    return str(os.environ.get(name, default) or "").strip()


def _env_int(name: str, default: str) -> int:
    try:
        return int(_env(name, default))
    except ValueError:
        return int(default)


def _config() -> dict[str, Any]:
    return {
        "host": _env("ERP_LANCAMENTO_PG_HOST"),
        "port": _env_int("ERP_LANCAMENTO_PG_PORT", "5432"),
        "database": _env("ERP_LANCAMENTO_PG_DB"),
        "user": _env("ERP_LANCAMENTO_PG_USER"),
        "password": os.environ.get("ERP_LANCAMENTO_PG_PASSWORD", ""),
        "table": _env("ERP_LANCAMENTO_PG_TABLE", "tcompras"),
        "connect_timeout": _env_int("ERP_BRIDGE_CONNECT_TIMEOUT", "15"),
        "token": os.environ.get("ERP_BRIDGE_TOKEN", ""),
    }


def _validar_table(table: str) -> str:
    table = str(table or "").strip()
    if not table.replace("_", "").isalnum():
        raise ValueError(f"Nome de tabela invalido: {table}")
    return table


def _conectar(cfg: dict[str, Any]):
    return psycopg2.connect(
        host=cfg["host"],
        port=cfg["port"],
        dbname=cfg["database"],
        user=cfg["user"],
        password=cfg["password"],
        connect_timeout=cfg["connect_timeout"],
    )


def _parse_data(valor: Any) -> date | None:
    if not valor:
        return None
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    texto = str(valor).strip()
    if not texto:
        return None
    return datetime.fromisoformat(texto[:10]).date()


def _iso_dt(valor: Any) -> str | None:
    if valor is None:
        return None
    if hasattr(valor, "isoformat"):
        return valor.isoformat()
    return str(valor)


def _normalizar_linhas_lancamento(rows: list[tuple[Any, ...]]) -> tuple[str, Any, str, str, str] | None:
    linhas_validas = [
        (
            str(row[0]).strip(),
            row[1],
            str(row[2] or "").strip(),
            str(row[3] or "").strip() if len(row) > 3 else "",
            str(row[4] or "").strip() if len(row) > 4 else "",
        )
        for row in rows
        if row and row[0] is not None and str(row[0]).strip()
    ]
    if not linhas_validas:
        return None

    chaves = {chv_nfe for _codigo, _dt_nf, chv_nfe, _cnpj, _nome in linhas_validas if chv_nfe}
    if len(chaves) == 1:
        return linhas_validas[0]

    # Fallback para bases antigas/linhas sem chave: so aceita se codigo e data tambem forem identicos.
    assinaturas = {(codigo, _iso_dt(dt_nf)) for codigo, dt_nf, _chv_nfe, _cnpj, _nome in linhas_validas}
    if not chaves and len(assinaturas) == 1:
        return linhas_validas[0]
    return None


PEDIDOS_SQL = """
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
            oc.prazo_entrega as observacoes
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
        greatest(coalesce(item.qtde_compra, item.qtde, 0) - coalesce(item.qtde_entregue, 0), 0) as pendente,
        coalesce(item.preco_unitario, 0) as preco_unitario,
        greatest(coalesce(item.qtde_compra, item.qtde, 0) - coalesce(item.qtde_entregue, 0), 0) * coalesce(item.preco_unitario, 0) as vl_pendente,
        coalesce(item.total, coalesce(item.qtde_compra, item.qtde, 0) * coalesce(item.preco_unitario, 0)) as total_item,
        oc.fornecedor_cnpj, oc.contato, oc.telefone, oc.email, oc.logradouro, oc.numero, oc.complemento,
        oc.bairro, oc.cidade, oc.uf, oc.cep, oc.observacoes,
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
        oc.fornecedor_cnpj, oc.contato, oc.telefone, oc.email, oc.logradouro, oc.numero, oc.complemento,
        oc.bairro, oc.cidade, oc.uf, oc.cep, oc.observacoes,
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
        oc.fornecedor_cnpj, oc.contato, oc.telefone, oc.email, oc.logradouro, oc.numero, oc.complemento,
        oc.bairro, oc.cidade, oc.uf, oc.cep, oc.observacoes,
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


NFE_EMAIL_DATA_MINIMA = date(2026, 5, 13)


def _date_to_api(valor: Any) -> str | None:
    if valor is None:
        return None
    if isinstance(valor, datetime):
        return valor.isoformat()
    if isinstance(valor, date):
        return valor.isoformat()
    return str(valor)


def _b64(valor: Any) -> str:
    if not valor:
        return ""
    if isinstance(valor, memoryview):
        valor = valor.tobytes()
    if isinstance(valor, bytearray):
        valor = bytes(valor)
    if isinstance(valor, str):
        valor = valor.encode("utf-8")
    if not isinstance(valor, bytes):
        return ""
    return base64.b64encode(valor).decode("ascii")


def _xml_bytes(valor: Any) -> bytes:
    if not valor:
        return b""
    if isinstance(valor, memoryview):
        return valor.tobytes()
    if isinstance(valor, bytearray):
        return bytes(valor)
    if isinstance(valor, bytes):
        return valor
    if isinstance(valor, str):
        return valor.encode("utf-8", errors="ignore")
    return b""


def _extrair_refs_nfe_xml(valor: Any) -> list[str]:
    dados = _xml_bytes(valor)
    if not dados:
        return []
    try:
        root = et.fromstring(dados)
    except Exception:
        return []
    ns = {"nfe": "http://www.portalfiscal.inf.br/nfe"}
    refs = []
    for ref in root.findall(".//nfe:NFref/nfe:refNFe", ns):
        texto = "".join(ch for ch in str(ref.text or "") if ch.isdigit())
        if len(texto) == 44:
            refs.append(texto)
    return refs


def _extrair_numeros_ref_texto(*valores: Any) -> list[str]:
    texto = " ".join(str(v or "") for v in valores)
    candidatos = re.findall(r"(?<!\d)(\d{4,6})(?!\d)", texto)
    vistos: set[str] = set()
    refs = []
    for candidato in candidatos:
        normalizado = candidato.lstrip("0") or "0"
        if normalizado not in vistos:
            vistos.add(normalizado)
            refs.append(normalizado)
    return refs


def _aplicar_retornos_conserto(remessas: list[dict[str, Any]], retornos: list[dict[str, Any]]) -> None:
    por_chave_produto: dict[tuple[str, str], dict[str, Any]] = {}
    por_numero_produto: dict[tuple[str, str], dict[str, Any]] = {}
    for remessa in remessas:
        remessa["quantidade_retornada"] = 0.0
        remessa["retornos"] = []
        produto = str(remessa.get("produto_codigo") or "").strip()
        chave = str(remessa.get("chave_nf_remessa") or "").strip()
        numero = str(remessa.get("numero_nf_remessa") or "").strip().lstrip("0")
        if chave and produto:
            por_chave_produto[(chave, produto)] = remessa
        if numero and produto:
            por_numero_produto[(numero, produto)] = remessa

    for retorno in retornos:
        produto = str(retorno.get("produto_codigo") or "").strip()
        if not produto:
            continue
        refs_chave = set()
        rastreio_chave = "".join(ch for ch in str(retorno.get("chave_nf_remessa_rastreio") or "") if ch.isdigit())
        if len(rastreio_chave) == 44:
            refs_chave.add(rastreio_chave)
        refs_chave.update(_extrair_refs_nfe_xml(retorno.pop("nfe_arquivo_xml", None)))
        chave_ref = "".join(ch for ch in str(retorno.get("chave_nf_remessa_ref") or "") if ch.isdigit())
        if len(chave_ref) == 44:
            refs_chave.add(chave_ref)

        refs_numero = set()
        numero_rastreio = str(retorno.get("numero_nf_remessa_rastreio") or "").strip().lstrip("0")
        if numero_rastreio:
            refs_numero.add(numero_rastreio)
        refs_numero.update(_extrair_numeros_ref_texto(retorno.get("referencia"), retorno.get("infadprod_v01")))
        numero_ref = str(retorno.get("numero_nf_remessa_ref") or "").strip().lstrip("0")
        if numero_ref and numero_ref != "None":
            refs_numero.add(numero_ref)

        remessa = None
        if rastreio_chave:
            remessa = por_chave_produto.get((rastreio_chave, produto))
        if not remessa and numero_rastreio:
            remessa = por_numero_produto.get((numero_rastreio, produto))
        if not remessa:
            remessa = next((por_chave_produto.get((ref, produto)) for ref in refs_chave if por_chave_produto.get((ref, produto))), None)
        if not remessa:
            remessa = next((por_numero_produto.get((ref, produto)) for ref in refs_numero if por_numero_produto.get((ref, produto))), None)
        if not remessa:
            continue

        quantidade = float(retorno.get("quantidade_rastreio") or retorno.get("quantidade_retornada") or 0)
        remessa["quantidade_retornada"] = float(remessa.get("quantidade_retornada") or 0) + quantidade
        remessa["retornos"].append({
            "codigo_entrada": retorno.get("codigo_entrada"),
            "numero_nf_retorno": retorno.get("numero_nf_retorno"),
            "chave_nf_retorno": retorno.get("chave_nf_retorno"),
            "data_nf_retorno": _date_to_api(retorno.get("data_nf_retorno")),
            "quantidade": quantidade,
            "cfop_retorno": retorno.get("cfop_retorno"),
            "origem_vinculo": "rastreio_servico" if (rastreio_chave or numero_rastreio) else "nf_entrada_referenciada",
        })


def _parse_data_minima(valor: Any) -> date:
    data = _parse_data(valor) or NFE_EMAIL_DATA_MINIMA
    return data if data >= NFE_EMAIL_DATA_MINIMA else NFE_EMAIL_DATA_MINIMA


NFE_EMITIDAS_SQL = """
    select
        numero::text as numero,
        chv_nfe as chave,
        dt_emissao as emitido_em,
        cod_cliente,
        coalesce(nullif(cliente, ''), nullif(razao_social, '')) as dest_nome,
        cgc_cpf as dest_cnpj,
        vl_total_nf as valor,
        modelo,
        serie,
        sub_serie,
        nfe_cod_status,
        nfe_desc_status,
        email_danfe,
        case when nfe_arquivo_xml is null then 0 else octet_length(nfe_arquivo_xml) end as xml_len,
        case when pdf_danfe is null then 0 else octet_length(pdf_danfe) end as pdf_len
    from public.tnota_fiscal
    where dt_emissao::date >= %s
      and coalesce(nfe, 0) = 1
      and coalesce(modelo, '') = '55'
    order by dt_emissao desc, numero desc
    limit %s
"""


NFE_EMITIDA_SQL = """
    select
        numero::text as numero,
        chv_nfe as chave,
        dt_emissao as emitido_em,
        cod_cliente,
        coalesce(nullif(cliente, ''), nullif(razao_social, '')) as dest_nome,
        cgc_cpf as dest_cnpj,
        vl_total_nf as valor,
        modelo,
        serie,
        sub_serie,
        nfe_cod_status,
        nfe_desc_status,
        email_danfe,
        nfe_arquivo_xml,
        pdf_danfe
    from public.tnota_fiscal
    where dt_emissao::date >= %s
      and coalesce(nfe, 0) = 1
      and coalesce(modelo, '') = '55'
      and (
        (%s <> '' and numero::text = %s)
        or (%s <> '' and regexp_replace(coalesce(chv_nfe, ''), '\\D', '', 'g') = %s)
      )
    order by dt_emissao desc, numero desc
    limit 1
"""


CADASTRO_EMAIL_POR_CNPJ_SQL = """
    with cliente as (
        select
            'cliente'::text as origem,
            coalesce(to_jsonb(c)->>'codigo', '') as codigo,
            coalesce(
                nullif(to_jsonb(c)->>'razao_social', ''),
                nullif(to_jsonb(c)->>'nome', ''),
                nullif(to_jsonb(c)->>'fantasia', ''),
                ''
            ) as nome,
            regexp_replace(
                coalesce(
                    to_jsonb(c)->>'rg_cgc',
                    to_jsonb(c)->>'cgc',
                    to_jsonb(c)->>'cnpj_cpf',
                    ''
                ),
                '\\D',
                '',
                'g'
            ) as documento,
            coalesce(
                nullif(to_jsonb(c)->>'e_mail', ''),
                nullif(to_jsonb(c)->>'email', ''),
                nullif(to_jsonb(c)->>'email_danfe', ''),
                ''
            ) as email
        from public.tcliente c
        where regexp_replace(
            coalesce(
                to_jsonb(c)->>'rg_cgc',
                to_jsonb(c)->>'cgc',
                to_jsonb(c)->>'cnpj_cpf',
                ''
            ),
            '\\D',
            '',
            'g'
        ) = %s
    ),
    fornecedor as (
        select
            'fornecedor'::text as origem,
            coalesce(to_jsonb(f)->>'codigo', '') as codigo,
            coalesce(
                nullif(to_jsonb(f)->>'razao_social', ''),
                nullif(to_jsonb(f)->>'nome', ''),
                nullif(to_jsonb(f)->>'fantasia', ''),
                ''
            ) as nome,
            regexp_replace(
                coalesce(
                    to_jsonb(f)->>'cgc',
                    to_jsonb(f)->>'rg_cgc',
                    to_jsonb(f)->>'cnpj_cpf',
                    ''
                ),
                '\\D',
                '',
                'g'
            ) as documento,
            coalesce(
                nullif(to_jsonb(f)->>'e_mail', ''),
                nullif(to_jsonb(f)->>'email', ''),
                nullif(to_jsonb(f)->>'email_danfe', ''),
                ''
            ) as email
        from public.tfornece f
        where regexp_replace(
            coalesce(
                to_jsonb(f)->>'cgc',
                to_jsonb(f)->>'rg_cgc',
                to_jsonb(f)->>'cnpj_cpf',
                ''
            ),
            '\\D',
            '',
            'g'
        ) = %s
    ),
    candidatos as (
        select * from cliente
        union all
        select * from fornecedor
    )
    select origem, codigo, nome, documento, email
    from candidatos
    where coalesce(email, '') <> ''
    order by case origem when 'cliente' then 1 else 2 end, codigo
    limit 1
"""


CONTAS_RECEBER_ABERTO_SQL = """
    select
        r.codigo::text as codigo,
        r.cod_cliente,
        coalesce(nullif(r.cliente, ''), nullif(c.razao_social, ''), nullif(c.nome, ''), nullif(nf.razao_social, ''), nullif(nf.cliente, '')) as nome_pagador,
        coalesce(nullif(c.rg_cgc, ''), nullif(nf.cgc_cpf, '')) as cpf_cnpj_pagador,
        r.n_nf::text as numero_nota,
        coalesce(nullif(r.documento, ''), r.n_nf::text, r.codigo::text) as documento,
        coalesce(r.n_orcamento::text, r.cod_orcamento::text, '') as orcamento,
        r.dt_vencimento as vencimento_raw,
        coalesce(r.valor, 0) as valor,
        coalesce(r.valor_pago, 0) as valor_pago,
        coalesce(r.pago, 0) as pago,
        r.dt_pagamento as data_pagamento,
        r.parcela,
        r.n_parcelas,
        coalesce(r.nossonumero, '') as nossonumero,
        coalesce(r.boleto_gerado, 0) as boleto_gerado,
        coalesce(r.boleto_situacao, '') as boleto_situacao,
        coalesce(nf.chv_nfe, '') as chave_acesso
    from public.treceber r
    left join public.tcliente c
      on c.cod_empresa = r.cod_empresa
     and c.codigo = r.cod_cliente
    left join public.tnota_fiscal nf
      on nf.cod_empresa = r.cod_empresa
     and nf.numero = r.n_nf
     and nf.cod_cliente = r.cod_cliente
        where (
                %s = true
                or (coalesce(r.pago, 0) = 0 and r.dt_pagamento is null and coalesce(r.valor, 0) > coalesce(r.valor_pago, 0))
            )
      and (
        (%s <> '' and regexp_replace(coalesce(c.rg_cgc, nf.cgc_cpf, ''), '\\D', '', 'g') = %s)
        or (%s <> '' and (r.n_nf::text = %s or coalesce(r.documento, '') ilike %s))
        or (%s <> '' and (r.n_orcamento::text = %s or r.cod_orcamento::text = %s))
      )
    order by r.dt_vencimento asc nulls last, r.codigo asc
    limit %s
"""


CONTAS_RECEBER_BOLETO_PDF_SQL = """
    select
        r.codigo::text as codigo,
        r.n_nf::text as numero_nota,
        coalesce(r.nossonumero, '') as nossonumero,
        coalesce(r.boleto_situacao, '') as boleto_situacao,
        boleto_pdf
    from public.treceber r
    where r.codigo::text = %s
    limit 1
"""


ENTRADA_CHAPA_SQL = """
    select
        c.codigo::text as codigo_lancamento,
        coalesce(nullif(c.romaneio, ''), nullif(lot.descricao, ''), '') as numero_ar,
        c.n_nf::text as numero_nota,
        c.dt_nf,
        c.dt_recebimento,
        c.dt_lancamento,
        coalesce(nullif(c.chv_nfe, ''), '') as chave_acesso,
        coalesce(nullif(f.razao_social, ''), nullif(f.nome, ''), nullif(c.fornecedor, '')) as fornecedor_nome,
        coalesce(nullif(cli.razao_social, ''), nullif(cli.nome, ''), nullif(c.cliente, '')) as cliente_nome,
        case
            when left(coalesce(nullif(a.cfop, ''), nullif(c.cfop, ''), ''), 4) = '1924'
                then coalesce(nullif(cli.razao_social, ''), nullif(cli.nome, ''), nullif(c.cliente, ''), nullif(f.razao_social, ''), nullif(f.nome, ''), nullif(c.fornecedor, ''))
            else coalesce(nullif(f.razao_social, ''), nullif(f.nome, ''), nullif(c.fornecedor, ''), nullif(cli.razao_social, ''), nullif(cli.nome, ''), nullif(c.cliente, ''))
        end as parceiro_nome,
        case
            when left(coalesce(nullif(a.cfop, ''), nullif(c.cfop, ''), ''), 4) = '1924'
                then coalesce(nullif(cli.rg_cgc, ''), nullif(f.cgc, ''))
            else coalesce(nullif(f.cgc, ''), nullif(cli.rg_cgc, ''))
        end as parceiro_documento,
        coalesce(nullif(c.cfop, ''), '') as cfop_cabecalho,
        a.numero_item,
        coalesce(nullif(a.cfop, ''), nullif(c.cfop, '')) as cfop_item,
        coalesce(nullif(a.descricao_cfop, ''), nullif(c.tipo_movimento, ''), nullif(c.codigo_movimentacao, '')) as natureza_operacao,
        coalesce(nullif(a.cod_interno, ''), nullif(p.codigo_interno, '')) as cod_interno,
        coalesce(nullif(a.produto, ''), nullif(p.nome, '')) as descricao,
        coalesce(a.qtde, 0) as quantidade,
        coalesce(nullif(a.unidade, ''), nullif(p.unidade, ''), nullif(p.unidade_compra, '')) as unidade,
        coalesce(fam.nome, '') as familia,
        coalesce(p.cod_grupo::text, '') as grupo,
        coalesce(nullif(p.localizacao_estoque, ''), '') as localizacao_estoque,
        coalesce(a.tipo_controle, p.tipo_controle, 0) as tipo_controle,
        coalesce(p.controle_lote_serie, 0) as controle_lote_serie,
        coalesce(nullif(a.lote, ''), nullif(lot.descricao, '')) as lote,
        a.guid_linha
    from public.tcompras c
    left join public.tfornece f
      on f.cod_empresa = c.cod_empresa
     and f.codigo = c.cod_fornecedor
    left join public.tcliente cli
      on cli.cod_empresa = c.cod_empresa
     and cli.codigo = c.cod_cliente
    left join public.tcom_aux a
      on a.cod_empresa = c.cod_empresa
     and a.realciona_auto = c.codigo
    left join public.tproduto p
      on p.cod_empresa = a.cod_empresa
     and p.codigo = a.cod_produto
    left join public.tfamilia fam
      on fam.cod_empresa = p.cod_empresa
     and fam.codigo = p.cod_familia
    left join public.tcom_aux_loteserie lot
      on lot.cod_empresa = a.cod_empresa
     and lot.guid_pai = a.guid_linha
    where (
        (%s <> '' and c.codigo::text = %s)
        or (%s = '' and %s <> '' and c.n_nf::text = %s)
        or (%s = '' and %s = '' and %s <> '' and regexp_replace(coalesce(c.chv_nfe, ''), '\\D', '', 'g') = %s)
    )
    order by c.dt_lancamento desc nulls last, c.dt_nf desc nulls last, c.codigo desc, a.numero_item, a.guid_linha
    limit 200
"""


ENTRADAS_CHAPA_DESDE_SQL = """
    select
        c.codigo::text as codigo_lancamento,
        coalesce(nullif(c.romaneio, ''), nullif(lot.descricao, ''), '') as numero_ar,
        c.n_nf::text as numero_nota,
        c.dt_nf,
        c.dt_recebimento,
        c.dt_lancamento,
        coalesce(nullif(c.chv_nfe, ''), '') as chave_acesso,
        coalesce(nullif(f.razao_social, ''), nullif(f.nome, ''), nullif(c.fornecedor, '')) as fornecedor_nome,
        coalesce(nullif(cli.razao_social, ''), nullif(cli.nome, ''), nullif(c.cliente, '')) as cliente_nome,
        case
            when left(coalesce(nullif(a.cfop, ''), nullif(c.cfop, ''), ''), 4) = '1924'
                then coalesce(nullif(cli.razao_social, ''), nullif(cli.nome, ''), nullif(c.cliente, ''), nullif(f.razao_social, ''), nullif(f.nome, ''), nullif(c.fornecedor, ''))
            else coalesce(nullif(f.razao_social, ''), nullif(f.nome, ''), nullif(c.fornecedor, ''), nullif(cli.razao_social, ''), nullif(cli.nome, ''), nullif(c.cliente, ''))
        end as parceiro_nome,
        case
            when left(coalesce(nullif(a.cfop, ''), nullif(c.cfop, ''), ''), 4) = '1924'
                then coalesce(nullif(cli.rg_cgc, ''), nullif(f.cgc, ''))
            else coalesce(nullif(f.cgc, ''), nullif(cli.rg_cgc, ''))
        end as parceiro_documento,
        coalesce(nullif(c.cfop, ''), '') as cfop_cabecalho,
        a.numero_item,
        coalesce(nullif(a.cfop, ''), nullif(c.cfop, '')) as cfop_item,
        coalesce(nullif(a.descricao_cfop, ''), nullif(c.tipo_movimento, ''), nullif(c.codigo_movimentacao, '')) as natureza_operacao,
        coalesce(nullif(a.cod_interno, ''), nullif(p.codigo_interno, '')) as cod_interno,
        coalesce(nullif(a.produto, ''), nullif(p.nome, '')) as descricao,
        coalesce(a.qtde, 0) as quantidade,
        coalesce(nullif(a.unidade, ''), nullif(p.unidade, ''), nullif(p.unidade_compra, '')) as unidade,
        coalesce(fam.nome, '') as familia,
        coalesce(p.cod_grupo::text, '') as grupo,
        coalesce(nullif(p.localizacao_estoque, ''), '') as localizacao_estoque,
        coalesce(a.tipo_controle, p.tipo_controle, 0) as tipo_controle,
        coalesce(p.controle_lote_serie, 0) as controle_lote_serie,
        coalesce(nullif(a.lote, ''), nullif(lot.descricao, '')) as lote,
        a.guid_linha
    from public.tcompras c
    left join public.tfornece f
      on f.cod_empresa = c.cod_empresa
     and f.codigo = c.cod_fornecedor
    left join public.tcliente cli
      on cli.cod_empresa = c.cod_empresa
     and cli.codigo = c.cod_cliente
    left join public.tcom_aux a
      on a.cod_empresa = c.cod_empresa
     and a.realciona_auto = c.codigo
    left join public.tproduto p
      on p.cod_empresa = a.cod_empresa
     and p.codigo = a.cod_produto
    left join public.tfamilia fam
      on fam.cod_empresa = p.cod_empresa
     and fam.codigo = p.cod_familia
    left join public.tcom_aux_loteserie lot
      on lot.cod_empresa = a.cod_empresa
     and lot.guid_pai = a.guid_linha
    where c.dt_recebimento::date >= %s
      and (
        coalesce(p.controle_lote_serie, 0)::text = any(%s)
        or coalesce(a.tipo_controle, 0)::text = any(%s)
      )
    order by c.dt_recebimento desc nulls last, c.codigo desc, a.numero_item, a.guid_linha
    limit %s
"""


ESTOQUE_SQL = """
    with estoque_deposito as (
        select
            cod_empresa,
            cod_produto,
            sum(coalesce(qtde_total, 0)) as qtde_total,
            sum(coalesce(qtde_reservada, 0)) as qtde_reservada,
            sum(coalesce(qtde_disponivel, 0)) as qtde_disponivel
        from public.tproduto_deposito
        group by cod_empresa, cod_produto
    )
    select
        p.codigo_interno,
        p.nome as item,
        coalesce(nullif(p.unidade, ''), nullif(p.unidade_compra, ''), 'UN') as unidade,
        -- Fonte principal: saldo por deposito (tproduto_deposito), que e o
        -- saldo efetivo usado no GRV. Fallback para campos legados de
        -- tproduto quando nao houver linha em tproduto_deposito.
        coalesce(
            d.qtde_total,
            (
                case
                    when p.estoque is not null then coalesce(p.estoque, 0) + coalesce(p.estoque_reservado, 0)
                    when p.estoque_disponivel_uso is not null then coalesce(p.estoque_disponivel_uso, 0) + coalesce(p.estoque_reservado, 0)
                    else null
                end
            ),
            0
        ) as qtde_total,
        coalesce(d.qtde_reservada, p.estoque_reservado, 0) as qtde_reservada,
        coalesce(d.qtde_disponivel, p.estoque, coalesce(p.estoque_disponivel_uso, 0) - coalesce(p.estoque_reservado, 0), 0) as qtde_disponivel,
        p.localizacao_estoque,
        coalesce(f.nome, '') as familia,
        coalesce(p.cod_grupo::text, '') as grupo
    from public.tproduto p
    left join estoque_deposito d
      on d.cod_empresa = p.cod_empresa
     and d.cod_produto = p.codigo
    left join public.tfamilia f
      on f.cod_empresa = p.cod_empresa
     and f.codigo = p.cod_familia
    where p.cod_empresa = %s
      and coalesce(nullif(trim(p.codigo_interno), ''), '') <> ''
      and coalesce(p.inativo, 0) = 0
        order by coalesce(p.localizacao_estoque, ''), p.codigo_interno
"""


ESTOQUE_CODIGOS_ATIVOS_SQL = """
    select distinct p.codigo_interno
    from public.tproduto p
    where p.cod_empresa = %s
      and coalesce(nullif(trim(p.codigo_interno), ''), '') <> ''
      and coalesce(p.inativo, 0) = 0
"""


CONSERTO_REMESSAS_SQL = """
    select
        nf.numero::text as numero_nf_remessa,
        nf.dt_emissao as data_emissao,
        nf.chv_nfe as chave_nf_remessa,
        regexp_replace(coalesce(nf.cgc_cpf, ''), '[^0-9]', '', 'g') as fornecedor_cnpj,
        coalesce(nullif(nf.cliente, ''), nullif(nf.razao_social, ''), 'Fornecedor nao informado') as fornecedor_nome,
        coalesce(nullif(i.nitem_vc03, ''), '0') as numero_item_remessa,
        i.codigo as produto_codigo,
        i.produto as produto_descricao,
        coalesce(i.qtde, 0) as quantidade_enviada,
        i.cfop as cfop_remessa,
        0::double precision as quantidade_retornada,
        'Conserto' as tipo_operacao
    from public.tnota_fiscal nf
    join public.tnota_fiscal_item i
      on i.cod_empresa = nf.cod_empresa
     and i.modelo = nf.modelo
     and i.serie = nf.serie
     and i.sub_serie = nf.sub_serie
     and i.numero_nf = nf.numero
    where nf.cod_empresa = %s
      and coalesce(nf.cancelada, 0) = 0
      and nf.dt_emissao::date >= %s
      and i.cfop in ('5915', '6915')
      and coalesce(nullif(trim(i.codigo), ''), '') <> ''
    order by nf.dt_emissao desc nulls last, nf.numero desc, coalesce(nullif(i.nitem_vc03, ''), '0')
    limit %s
"""


CONSERTO_RETORNOS_SQL = """
    select
        c.codigo::text as codigo_entrada,
        c.n_nf::text as numero_nf_retorno,
        c.dt_nf as data_nf_retorno,
        c.dt_recebimento,
        c.chv_nfe as chave_nf_retorno,
        regexp_replace(coalesce(f.cgc, ''), '[^0-9]', '', 'g') as fornecedor_cnpj,
        coalesce(nullif(c.fornecedor, ''), nullif(f.razao_social, ''), nullif(f.nome, ''), 'Fornecedor nao informado') as fornecedor_nome,
        coalesce(ref.entraref_numero_nf, c.num_doc_ref::text) as numero_nf_remessa_ref,
        coalesce(ref.entraref_chave_nf, c.hash_doc_ref) as chave_nf_remessa_ref,
        a.cod_interno as produto_codigo,
        a.produto as produto_descricao,
        coalesce(a.qtde, 0) as quantidade_retornada,
        a.cfop as cfop_retorno,
        a.referencia,
        a.infadprod_v01,
        os.numero_nf_envio::text as numero_nf_remessa_rastreio,
        os.chave_envio_nf as chave_nf_remessa_rastreio,
        os.numero_item_envio_nf as numero_item_remessa_rastreio,
        os.qtde as quantidade_rastreio,
        os.cfop_envio_nf as cfop_remessa_rastreio,
        c.nfe_arquivo_xml
    from public.tcompras c
    join public.tcom_aux a
      on a.cod_empresa = c.cod_empresa
     and a.realciona_auto = c.codigo
    left join public.tcom_aux_os os
      on os.cod_empresa = a.cod_empresa
     and os.guid_pai = a.guid_linha
     and coalesce(os.cancelado, 0) = 0
    left join public.tnota_fiscal_entraref ref
      on ref.cod_empresa = c.cod_empresa
     and ref.cod_compra = c.codigo
     and coalesce(nullif(ref.entraref_numero_nf, ''), nullif(ref.entraref_chave_nf, '')) is not null
    left join public.tfornece f
      on f.cod_empresa = c.cod_empresa
     and f.codigo = c.cod_fornecedor
    where c.cod_empresa = %s
      and c.dt_nf::date >= %s
      and a.cfop in ('1916', '2916')
      and coalesce(nullif(trim(a.cod_interno), ''), '') <> ''
    order by c.dt_nf desc nulls last, c.codigo desc
    limit %s
"""


def _montar_entrada_chapa(rows: list[dict[str, Any]], numero_ar: str = "", numero_nota: str = "", chave: str = "") -> dict[str, Any] | None:
    if not rows:
        return None
    cab = rows[0]
    row_1924 = next(
        (
            r for r in rows
            if str(r.get("cfop_item") or r.get("cfop_cabecalho") or "").strip()[:4] == "1924"
            and (r.get("cliente_nome") or r.get("parceiro_nome"))
        ),
        None,
    )
    parceiro_nome = (row_1924.get("cliente_nome") or row_1924.get("parceiro_nome")) if row_1924 else cab.get("parceiro_nome")
    parceiro_documento = row_1924.get("parceiro_documento") if row_1924 else cab.get("parceiro_documento")
    itens = []
    for row in rows:
        if not row.get("cod_interno") and not row.get("descricao"):
            continue
        itens.append({
            "numero_item": row.get("numero_item"),
            "cfop": row.get("cfop_item") or row.get("cfop_cabecalho") or "",
            "natureza_operacao": row.get("natureza_operacao") or "",
            "cod_interno": row.get("cod_interno") or "",
            "descricao": row.get("descricao") or "",
            "quantidade": row.get("quantidade") or 0,
            "unidade": row.get("unidade") or "",
            "familia": row.get("familia") or "",
            "grupo": row.get("grupo") or "",
            "localizacao_estoque": row.get("localizacao_estoque") or "",
            "valor_unitario": row.get("valor_unitario") or 0,
            "valor_total_linha": row.get("valor_total_linha") or 0,
            "icms_base_calculo": row.get("icms_base_calculo") or 0,
            "icms_aliquota": row.get("icms_aliquota") or 0,
            "icms_cst": row.get("icms_cst") or "",
            "icms_valor": row.get("icms_valor") or 0,
            "pis_base_calculo": row.get("pis_base_calculo") or 0,
            "pis_aliquota": row.get("pis_aliquota") or 0,
            "pis_cst": row.get("pis_cst") or "",
            "pis_valor_credito": row.get("pis_valor_credito") or 0,
            "cofins_base_calculo": row.get("cofins_base_calculo") or 0,
            "cofins_aliquota": row.get("cofins_aliquota") or 0,
            "cofins_cst": row.get("cofins_cst") or "",
            "cofins_valor_credito": row.get("cofins_valor_credito") or 0,
            "tipo_controle": row.get("tipo_controle") or 0,
            "controle_lote_serie": row.get("controle_lote_serie") or 0,
            "lote": row.get("lote") or "",
        })
    return {
        "codigo_lancamento": cab.get("codigo_lancamento") or numero_ar,
        "numero_ar": next((r.get("numero_ar") for r in rows if r.get("numero_ar")), ""),
        "numero_nota": cab.get("numero_nota") or numero_nota,
        "dt_nf": _date_to_api(cab.get("dt_nf")),
        "dt_recebimento": _date_to_api(cab.get("dt_recebimento")),
        "dt_lancamento": _date_to_api(cab.get("dt_lancamento")),
        "chave_acesso": cab.get("chave_acesso") or chave,
        "parceiro_nome": parceiro_nome or "",
        "parceiro_documento": parceiro_documento or "",
        "cfop_cabecalho": cab.get("cfop_cabecalho") or "",
        "itens": itens,
    }


def _enriquecer_valores_entrada(cur, rows: list[dict[str, Any]]) -> None:
    guids = [str(row.get("guid_linha") or "").strip() for row in rows if str(row.get("guid_linha") or "").strip()]
    if not guids:
        return
    cur.execute("select column_name from information_schema.columns where table_schema = 'public' and table_name = 'tcom_aux'")
    cols_aux = {str(row[0]).lower() for row in cur.fetchall()}

    def col(alias: str, *candidates: str) -> str:
        existentes = [name for name in candidates if name.lower() in cols_aux]
        if not existentes:
            return f"0 as {alias}"
        exprs = [f"nullif({name}, 0)" for name in existentes]
        return f"coalesce({', '.join(exprs)}, 0) as {alias}"

    valor_unitario = col("valor_unitario", "valor_unitario", "vl_unitario", "vlr_unitario", "preco_unitario", "preco", "unitario")
    valor_total = col("valor_total_linha", "valor_total", "vl_total", "vlr_total", "total", "valor_produto", "vprod", "vl_item", "vlr_item")
    cur.execute(
        f"""
        select guid_linha::text as guid_linha, {valor_unitario}, {valor_total}
        from public.tcom_aux
        where guid_linha::text = any(%s)
        """,
        (guids,),
    )
    por_guid = {str(row[0]): {"valor_unitario": row[1] or 0, "valor_total_linha": row[2] or 0} for row in cur.fetchall()}
    for row in rows:
        valores = por_guid.get(str(row.get("guid_linha") or ""))
        if valores:
            row.update(valores)


def _enriquecer_tributos_entrada(cur, rows: list[dict[str, Any]]) -> None:
    guids = [str(row.get("guid_linha") or "").strip() for row in rows if str(row.get("guid_linha") or "").strip()]
    if not guids:
        return
    cur.execute("select column_name from information_schema.columns where table_schema = 'public' and table_name = 'tcom_aux'")
    cols_aux = {str(row[0]).lower() for row in cur.fetchall()}

    def col(alias: str, *candidates: str, default: str = "null") -> str:
        existentes = [name for name in candidates if name.lower() in cols_aux]
        if not existentes:
            return f"{default} as {alias}"
        if default == "0":
            exprs = [f"nullif(a.{name}, 0)" for name in existentes]
            return f"coalesce({', '.join(exprs)}, 0) as {alias}"
        exprs = [f"nullif(a.{name}::text, '')" for name in existentes]
        return f"coalesce({', '.join(exprs)}, {default}) as {alias}"

    tax_select = ",\n            ".join([
        col("icms_base_calculo", "icms_base_calculo", "base_icms", "base_calculo_icms", "vl_base_calc_icms", "vl_base_icms", "vlr_base_icms", "vbc_icms", "bc_icms", "bcicms", default="0"),
        col("icms_aliquota", "icms_aliquota", "aliquota_icms", "aliq_icms", "percentual_icms", "perc_icms", "per_icms", "p_icms", default="0"),
        col("icms_cst", "icms_cst", "cst_icms", "cst_n12", "sit_trib_icms", "situacao_tributaria_icms", "cst", "sit_trib", default="''"),
        col("icms_valor", "icms_valor", "valor_icms", "vl_icms", "vlr_icms", "v_icms", "icms", default="0"),
        col("pis_base_calculo", "pis_base_calculo", "base_pis", "base_calculo_pis", "vl_base_calc_pis", "vl_base_pis", "vlr_base_pis", "vbc_pis", "vbc_q07", "bc_pis", default="0"),
        col("pis_aliquota", "pis_aliquota", "aliquota_pis", "aliq_pis", "aliq_perc_pis", "perc_pis", "per_pis", "ppis_q08", "ppis_r03", "p_pis", default="0"),
        col("pis_cst", "pis_cst", "cst_pis", "cst_q06", "sit_trib_pis", "situacao_tributaria_pis", default="''"),
        col("pis_valor_credito", "pis_valor_credito", "valor_pis", "vl_pis", "vlr_pis", "vl_reais_pis", "vpis_q09", "vpis_r06", "v_pis", "pis", default="0"),
        col("cofins_base_calculo", "cofins_base_calculo", "base_cofins", "base_calculo_cofins", "vl_base_calc_cofins", "vl_base_cofins", "vlr_base_cofins", "vbc_cofins", "vbc_s07", "bc_cofins", default="0"),
        col("cofins_aliquota", "cofins_aliquota", "aliquota_cofins", "aliq_cofins", "aliq_perc_cofins", "perc_cofins", "per_cofins", "pcofins_s08", "pcofins_t03", "p_cofins", default="0"),
        col("cofins_cst", "cofins_cst", "cst_cofins", "cst_s06", "sit_trib_cofins", "situacao_tributaria_cofins", default="''"),
        col("cofins_valor_credito", "cofins_valor_credito", "valor_cofins", "vl_cofins", "vlr_cofins", "vl_reais_cofins", "vl_reias_cofins", "vcofins_s11", "vcofins_t06", "v_cofins", "cofins", default="0"),
    ])
    cur.execute(
        f"""
        select a.guid_linha::text as guid_linha, {tax_select}
        from public.tcom_aux a
        where a.guid_linha::text = any(%s)
        """,
        (guids,),
    )
    cols = [desc[0] for desc in cur.description]
    por_guid = {str(row[0]): dict(zip(cols, row)) for row in cur.fetchall()}
    for row in rows:
        guid = str(row.get("guid_linha") or "")
        row.update(por_guid.get(guid, {}))


def _authorized(cfg: dict[str, Any]) -> bool:
    token = str(cfg.get("token") or "")
    if not token:
        return False
    auth = request.headers.get("Authorization", "")
    return auth == f"Bearer {token}"


def _local_request() -> bool:
    host = (request.remote_addr or "").strip()
    return host in {"127.0.0.1", "::1", "localhost"}


def _json_safe(value: Any) -> Any:
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, Decimal):
        return float(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _safe_ident(name: str) -> str:
    ident = str(name or "").strip()
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", ident):
        raise ValueError(f"Identificador inválido: {name}")
    return ident


def _detectar_fonte_kardex(cur) -> dict[str, Any] | None:
    candidatos = [
        "tkardex",
        "t_kardex",
        "tkardex_produto",
        "tmov_estoque",
        "tmovimento_estoque",
        "tproduto_movimento",
        "tproduto_mov",
        "tmov_produto",
    ]
    code_cols = ["codigo_interno", "cod_interno", "codigo", "cod_produto", "produto"]
    date_cols = ["data_movimento", "dt_movimento", "data", "dt_lancamento", "dt_emissao", "dt"]
    qty_cols = ["qtde", "quantidade", "qtd", "qtde_movimentada", "volume"]
    tipo_cols = ["tipo_movimento", "tp_movimento", "movimento", "operacao", "natureza"]
    saldo_cols = ["saldo", "qtde_saldo", "saldo_atual"]
    empresa_cols = ["cod_empresa", "empresa"]

    for tabela in candidatos:
        cur.execute(
            """
            select column_name
            from information_schema.columns
            where table_schema = 'public' and table_name = %s
            """,
            (tabela,),
        )
        cols = {str(row[0]).strip().lower() for row in cur.fetchall() if row and row[0]}
        if not cols:
            continue

        code_col = next((c for c in code_cols if c in cols), None)
        date_col = next((c for c in date_cols if c in cols), None)
        qty_col = next((c for c in qty_cols if c in cols), None)
        if not code_col or not date_col or not qty_col:
            continue

        tipo_col = next((c for c in tipo_cols if c in cols), None)
        saldo_col = next((c for c in saldo_cols if c in cols), None)
        empresa_col = next((c for c in empresa_cols if c in cols), None)

        select_tipo = f"{_safe_ident(tipo_col)}::text" if tipo_col else "''::text"
        select_saldo = f"{_safe_ident(saldo_col)}::double precision" if saldo_col else "null::double precision"

        filtros = [f"{_safe_ident(date_col)}::date between %s and %s"]
        if empresa_col:
            filtros.append(f"{_safe_ident(empresa_col)} = %s")
        filtros.append(f"upper(trim({_safe_ident(code_col)}::text)) = any(%s::text[])")

        sql = f"""
            select
                upper(trim({_safe_ident(code_col)}::text)) as codigo_interno,
                {_safe_ident(date_col)}::date as data_movimento,
                coalesce({_safe_ident(qty_col)}, 0)::double precision as quantidade,
                {select_tipo} as tipo_movimento,
                {select_saldo} as saldo
            from public.{_safe_ident(tabela)}
            where {' and '.join(filtros)}
        """
        return {
            "tabela": f"public.{tabela}",
            "sql": sql,
            "tem_empresa": bool(empresa_col),
        }
    return None


def _compras_query_catalog() -> dict[str, str]:
    from conferencia_app.compras import queries as compras_queries

    return {
        name: value
        for name, value in vars(compras_queries).items()
        if name.startswith("SQL_") and isinstance(value, str)
    }


def _solicitacao_nf_query_catalog() -> dict[str, str]:
    from conferencia_app.services import solicitacao_nf_service

    return {
        name: value
        for name, value in vars(solicitacao_nf_service).items()
        if name.startswith("SQL_") and isinstance(value, str)
    }


def _registrar_facilities_na_bridge(app: Flask) -> None:
    app.config.from_object(Config)
    os.makedirs(app.instance_path, exist_ok=True)
    db.init_app(app)
    migrate.init_app(app, db)

    pages_bp = Blueprint("pages", __name__)

    @pages_bp.route("/")
    def home():
        return jsonify({"ok": True, "service": "erp-lancamento-api-bridge", "facilities": True})

    app.register_blueprint(auth_bp)
    app.register_blueprint(pages_bp)
    app.register_blueprint(facilities_bp)
    register_error_handlers(app)

    @app.before_request
    def before_request_logging():
        g.start_time = time.perf_counter()
        if "username" in session:
            now = datetime.now()
            last_activity_raw = session.get("last_activity")
            if last_activity_raw:
                last_activity = datetime.fromisoformat(last_activity_raw)
                timeout = timedelta(minutes=app.config.get("SESSION_TIMEOUT_MINUTES", 30))
                if now - last_activity > timeout:
                    session.clear()
            session["last_activity"] = now.isoformat()
            session.permanent = True

    @app.after_request
    def after_request_logging(response):
        elapsed_ms = (time.perf_counter() - g.start_time) * 1000
        app.logger.info("%s %s %s %.2fms", request.method, request.path, response.status_code, elapsed_ms)
        ctype = response.headers.get("Content-Type", "")
        if ctype.startswith("text/html") and "charset" not in ctype.lower():
            response.headers["Content-Type"] = "text/html; charset=utf-8"
        elif ctype.startswith("application/json") and "charset" not in ctype.lower():
            response.headers["Content-Type"] = "application/json; charset=utf-8"
        return response

    @app.context_processor
    def inject_access_control_helpers():
        from conferencia_app.auth import get_effective_permissions, has_permission

        perms = get_effective_permissions() if "username" in session else {}
        return {
            "can_access": lambda key: has_permission(key),
            "access_permissions": perms,
        }

    @app.context_processor
    def inject_asset_version():
        try:
            css_dir = os.path.join(app.static_folder or "", "css")
            mtimes = []
            for root_, _dirs, files in os.walk(css_dir):
                for fn in files:
                    if fn.endswith(".css"):
                        mtimes.append(os.path.getmtime(os.path.join(root_, fn)))
            asset_version = str(int(max(mtimes))) if mtimes else str(int(time.time()))
        except Exception:
            asset_version = str(int(time.time()))
        return {"asset_version": asset_version}

    # A bridge (VM ERP + ngrok) usa Postgres direto para ERP/Facilities e NAO
    # depende do banco da aplicacao (MySQL). Se esse banco estiver ausente ou
    # com schema desatualizado, o bootstrap nao deve derrubar a inicializacao.
    try:
        initialize_database(app)
    except Exception as exc:
        app.logger.warning(
            "initialize_database ignorado na bridge (%s: %s). "
            "A bridge ERP/Facilities usa Postgres direto e continua ativa.",
            type(exc).__name__,
            str(exc).splitlines()[0] if str(exc) else "sem detalhe",
        )


def create_app() -> Flask:
    app = Flask(
        __name__,
        template_folder=os.path.join(ROOT_DIR, "templates"),
        static_folder=os.path.join(ROOT_DIR, "static"),
        instance_path=os.path.join(ROOT_DIR, "instance"),
        instance_relative_config=True,
    )
    _registrar_facilities_na_bridge(app)

    @app.get("/health")
    def health():
        return jsonify({"ok": True, "service": "erp-lancamento-api-bridge", "facilities": True})

    @app.get("/api/erp/facilities-diagnostico")
    def facilities_diagnostico():
        cfg = _config()
        if not _authorized(cfg) and not _local_request():
            return jsonify({"erro": "nao_autorizado"}), 401
        try:
            from conferencia_app.services.facilities_grv_service import FacilitiesGRVService

            if hasattr(FacilitiesGRVService, "_listar_funcionarios_postgres"):
                funcionarios = FacilitiesGRVService._listar_funcionarios_postgres(ativos=True)
            else:
                funcionarios = FacilitiesGRVService.listar_funcionarios(ativos=True)
            if hasattr(FacilitiesGRVService, "_listar_materiais_epi_uniforme_postgres"):
                materiais = FacilitiesGRVService._listar_materiais_epi_uniforme_postgres(com_saldo=True)
            else:
                materiais = FacilitiesGRVService.listar_materiais_epi_uniforme(com_saldo=True)
            epi = next((m for m in materiais if m.get("tipo") == "epi"), None)
            uniforme = next((m for m in materiais if m.get("tipo") == "uniforme"), None)
            funcionario = next((f for f in funcionarios if str(f.get("codigo")) != "240"), None)
            return jsonify({
                "sucesso": True,
                "instance_path": app.instance_path,
                "postgres_configurado": bool(cfg.get("host") and cfg.get("database") and cfg.get("user")),
                "postgres_host": cfg.get("host") or "",
                "postgres_database": cfg.get("database") or "",
                "funcionarios_total": len(funcionarios),
                "materiais_total": len(materiais),
                "funcionario_exemplo": funcionario,
                "epi_exemplo": epi,
                "uniforme_exemplo": uniforme,
            })
        except Exception as exc:
            app.logger.exception("Falha no diagnostico Facilities GRV")
            return jsonify({"sucesso": False, "erro": str(exc), "instance_path": app.instance_path}), 500

    @app.post("/api/erp/facilities/funcionarios")
    def facilities_funcionarios_grv():
        cfg = _config()
        if not _authorized(cfg):
            return jsonify({"erro": "nao_autorizado"}), 401
        if not cfg["host"] or not cfg["database"] or not cfg["user"]:
            return jsonify({"erro": "postgres_nao_configurado"}), 500
        try:
            payload = request.get_json(silent=True) or {}
            ativos = bool(payload.get("ativos", True))
            from conferencia_app.services.facilities_grv_service import FacilitiesGRVService

            app.logger.info("Facilities GRV: consultando funcionarios ativos=%s", ativos)
            if hasattr(FacilitiesGRVService, "_listar_funcionarios_postgres"):
                funcionarios = FacilitiesGRVService._listar_funcionarios_postgres(ativos=ativos)
            else:
                funcionarios = FacilitiesGRVService.listar_funcionarios(ativos=ativos)
            return jsonify({"sucesso": True, "funcionarios": funcionarios})
        except Exception as exc:
            app.logger.exception("Falha ao consultar funcionarios Facilities no GRV")
            return jsonify({"sucesso": False, "erro": str(exc)}), 500

    @app.post("/api/erp/facilities/materiais")
    def facilities_materiais_grv():
        cfg = _config()
        if not _authorized(cfg):
            return jsonify({"erro": "nao_autorizado"}), 401
        if not cfg["host"] or not cfg["database"] or not cfg["user"]:
            return jsonify({"erro": "postgres_nao_configurado"}), 500
        try:
            payload = request.get_json(silent=True) or {}
            com_saldo = bool(payload.get("com_saldo", True))
            from conferencia_app.services.facilities_grv_service import FacilitiesGRVService

            app.logger.info("Facilities GRV: consultando materiais com_saldo=%s", com_saldo)
            if hasattr(FacilitiesGRVService, "_listar_materiais_epi_uniforme_postgres"):
                materiais = FacilitiesGRVService._listar_materiais_epi_uniforme_postgres(com_saldo=com_saldo)
            else:
                materiais = FacilitiesGRVService.listar_materiais_epi_uniforme(com_saldo=com_saldo)
            return jsonify({"sucesso": True, "materiais": materiais})
        except Exception as exc:
            app.logger.exception("Falha ao consultar materiais Facilities no GRV")
            return jsonify({"sucesso": False, "erro": str(exc)}), 500

    @app.post("/api/erp/facilities/saldo")
    def facilities_saldo_grv():
        cfg = _config()
        if not _authorized(cfg):
            return jsonify({"erro": "nao_autorizado"}), 401
        if not cfg["host"] or not cfg["database"] or not cfg["user"]:
            return jsonify({"erro": "postgres_nao_configurado"}), 500
        try:
            payload = request.get_json(silent=True) or {}
            codigo = str(payload.get("codigo_interno") or "").strip()
            if not codigo:
                return jsonify({"sucesso": False, "erro": "codigo_interno_obrigatorio"}), 400
            from conferencia_app.services.facilities_grv_service import FacilitiesGRVService

            app.logger.info("Facilities GRV: consultando saldo codigo_interno=%s", codigo)
            if hasattr(FacilitiesGRVService, "_saldo_material_postgres"):
                saldo = FacilitiesGRVService._saldo_material_postgres(codigo)
            else:
                saldo = FacilitiesGRVService.saldo_material(codigo)
            return jsonify({"sucesso": True, "codigo_interno": codigo, "saldo": saldo})
        except Exception as exc:
            app.logger.exception("Falha ao consultar saldo Facilities no GRV")
            return jsonify({"sucesso": False, "erro": str(exc)}), 500

    @app.post("/api/erp/cadastro-workflow/materiais/buscar")
    def cadastro_workflow_buscar_materiais_grv():
        cfg = _config()
        if not _authorized(cfg):
            return jsonify({"erro": "nao_autorizado"}), 401
        if not cfg["host"] or not cfg["database"] or not cfg["user"]:
            return jsonify({"erro": "postgres_nao_configurado"}), 500
        try:
            payload = request.get_json(silent=True) or {}
            codigo = str(payload.get("codigo") or "").strip().lower()
            descricao = str(payload.get("descricao") or "").strip().lower()
            limite = max(1, min(int(payload.get("limite") or 12), 30))
            if not codigo and not descricao:
                return jsonify({"sucesso": True, "materiais": []})

            filtros = []
            params: list[Any] = [1]
            if codigo:
                filtros.append("lower(coalesce(nullif(trim(p.codigo_interno), ''), p.codigo::text, '')) like %s")
                params.append(f"%{codigo}%")
            if descricao:
                filtros.append("lower(coalesce(nullif(trim(p.nome), ''), '')) like %s")
                params.append(f"%{descricao}%")
            params.append(limite)

            sql = f"""
                select
                    p.codigo::text as codigo_grv,
                    coalesce(nullif(trim(p.codigo_interno), ''), p.codigo::text) as codigo_material,
                    coalesce(nullif(trim(p.nome), ''), '') as descricao,
                    coalesce(nullif(trim(p.unidade), ''), nullif(trim(p.unidade_compra), ''), '') as unidade,
                    coalesce(f.nome, '') as familia,
                    coalesce(p.cod_grupo::text, '') as grupo,
                    coalesce(nullif(trim(p.localizacao_estoque), ''), '') as localizacao_estoque,
                    coalesce(p.inativo, 0) as inativo
                from public.tproduto p
                left join public.tfamilia f
                  on f.cod_empresa = p.cod_empresa
                 and f.codigo = p.cod_familia
                where p.cod_empresa = %s
                  and coalesce(nullif(trim(p.nome), ''), '') <> ''
                  and ({' or '.join(filtros)})
                order by coalesce(p.inativo, 0), p.codigo desc
                limit %s
            """

            with _conectar(cfg) as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, tuple(params))
                    cols = [desc[0] for desc in cur.description]
                    rows = [dict(zip(cols, row)) for row in cur.fetchall()]

            return jsonify({"sucesso": True, "materiais": rows})
        except Exception as exc:
            app.logger.exception("Falha ao consultar materiais do cadastro workflow no GRV")
            return jsonify({"sucesso": False, "erro": str(exc)}), 500

    @app.post("/api/erp/lancamentos")
    def consultar_lancamentos():
        cfg = _config()
        if not _authorized(cfg):
            return jsonify({"erro": "nao_autorizado"}), 401
        if not cfg["host"] or not cfg["database"] or not cfg["user"]:
            return jsonify({"erro": "postgres_nao_configurado"}), 500

        try:
            table = _validar_table(cfg["table"])
            payload = request.get_json(silent=True) or {}
            chaves = payload.get("chaves") or []
            if not isinstance(chaves, list):
                return jsonify({"erro": "chaves_deve_ser_lista"}), 400

            resultados: dict[str, dict[str, Any]] = {}
            status: dict[str, str] = {}
            sql_com_data = f"""
                SELECT c.codigo, c.dt_nf, c.chv_nfe,
                       regexp_replace(coalesce(f.cgc, ''), '\\D', '', 'g') as fornecedor_cnpj,
                       coalesce(nullif(c.fornecedor, ''), f.razao_social, f.nome) as fornecedor_nome
                FROM {table} c
                LEFT JOIN public.tfornece f
                  ON f.cod_empresa = c.cod_empresa AND f.codigo = c.cod_fornecedor
                WHERE c.n_nf = %s AND c.dt_nf::date = %s LIMIT 50
            """
            sql_sem_data = f"""
                SELECT c.codigo, c.dt_nf, c.chv_nfe,
                       regexp_replace(coalesce(f.cgc, ''), '\\D', '', 'g') as fornecedor_cnpj,
                       coalesce(nullif(c.fornecedor, ''), f.razao_social, f.nome) as fornecedor_nome
                FROM {table} c
                LEFT JOIN public.tfornece f
                  ON f.cod_empresa = c.cod_empresa AND f.codigo = c.cod_fornecedor
                WHERE c.n_nf = %s LIMIT 50
            """

            def _linha_para_resultado(row: tuple[str, Any, str, str, str]) -> dict[str, Any]:
                return {
                    "codigo": row[0],
                    "dt_nf": _iso_dt(row[1]),
                    "chv_nfe": row[2],
                    "fornecedor_cnpj": row[3],
                    "fornecedor_nome": row[4],
                }

            with _conectar(cfg) as conn:
                with conn.cursor() as cur:
                    for item in chaves:
                        if not isinstance(item, dict):
                            continue
                        n_nf = str(item.get("n_nf") or "").strip()
                        if not n_nf:
                            continue
                        data_emissao = _parse_data(item.get("data_emissao"))
                        if data_emissao:
                            cur.execute(sql_com_data, (n_nf, data_emissao))
                            row = _normalizar_linhas_lancamento(cur.fetchall())
                            if row:
                                resultados[n_nf] = _linha_para_resultado(row)
                            else:
                                # Fallback seguro: se a dt_nf do ERP nao for a emissao
                                # do XML, tenta por numero e aceita apenas retorno unico.
                                cur.execute(sql_sem_data, (n_nf,))
                                rows_sem_data = cur.fetchall()
                                row_sem_data = _normalizar_linhas_lancamento(rows_sem_data)
                                if row_sem_data:
                                    resultados[n_nf] = _linha_para_resultado(row_sem_data)
                                elif rows_sem_data:
                                    status[n_nf] = "ERP encontrou o numero, mas com multiplas chaves/datas - confirme manualmente"
                                else:
                                    status[n_nf] = "Aguardando lancamento no ERP"
                        else:
                            cur.execute(sql_sem_data, (n_nf,))
                            rows = cur.fetchall()
                            row = _normalizar_linhas_lancamento(rows)
                            if row:
                                resultados[n_nf] = _linha_para_resultado(row)
                            elif rows:
                                status[n_nf] = "Multiplas chaves de acesso para esse numero no ERP - vincule manualmente"
                            else:
                                status[n_nf] = "Aguardando lancamento no ERP"

            return jsonify({"sucesso": True, "resultados": resultados, "status": status})
        except Exception as exc:
            app.logger.exception("Falha ao consultar lancamentos no ERP")
            return jsonify({"sucesso": False, "erro": str(exc)}), 500

    @app.post("/api/erp/compras/query")
    def compras_query():
        cfg = _config()
        if not _authorized(cfg):
            return jsonify({"erro": "nao_autorizado"}), 401
        if not cfg["host"] or not cfg["database"] or not cfg["user"]:
            return jsonify({"erro": "postgres_nao_configurado"}), 500

        try:
            payload = request.get_json(silent=True) or {}
            query_name = str(payload.get("query") or "").strip()
            params = payload.get("params") or {}
            one = bool(payload.get("one"))
            catalog = _compras_query_catalog()
            sql = catalog.get(query_name)
            if not sql:
                return jsonify({"sucesso": False, "erro": "query_nao_permitida"}), 400
            if not isinstance(params, dict):
                return jsonify({"sucesso": False, "erro": "params_deve_ser_objeto"}), 400

            with _conectar(cfg) as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, params)
                    cols = [desc[0] for desc in (cur.description or [])]
                    if one:
                        row = cur.fetchone()
                        return jsonify({"sucesso": True, "row": _json_safe(dict(zip(cols, row)) if row else None)})
                    rows = [dict(zip(cols, row)) for row in cur.fetchall()]
                    return jsonify({"sucesso": True, "rows": _json_safe(rows)})
        except Exception as exc:
            app.logger.exception("Falha ao executar query de Compras na bridge")
            return jsonify({"sucesso": False, "erro": str(exc)}), 500

    @app.post("/api/erp/solicitacao-nf/query")
    def solicitacao_nf_query():
        cfg = _config()
        if not _authorized(cfg):
            return jsonify({"erro": "nao_autorizado"}), 401
        if not cfg["host"] or not cfg["database"] or not cfg["user"]:
            return jsonify({"erro": "postgres_nao_configurado"}), 500

        try:
            payload = request.get_json(silent=True) or {}
            query_name = str(payload.get("query") or "").strip()
            params = payload.get("params") or {}
            catalog = _solicitacao_nf_query_catalog()
            sql = catalog.get(query_name)
            if not sql:
                return jsonify({"sucesso": False, "erro": "query_nao_permitida"}), 400
            if not isinstance(params, dict):
                return jsonify({"sucesso": False, "erro": "params_deve_ser_objeto"}), 400

            with _conectar(cfg) as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, params)
                    cols = [desc[0] for desc in (cur.description or [])]
                    rows = [dict(zip(cols, row)) for row in cur.fetchall()]
                    return jsonify({"sucesso": True, "rows": _json_safe(rows)})
        except Exception as exc:
            app.logger.exception("Falha ao executar query da Solicitacao de NF na bridge")
            return jsonify({"sucesso": False, "erro": str(exc)}), 500

    @app.post("/api/erp/lancamentos-periodo")
    def consultar_lancamentos_periodo():
        cfg = _config()
        if not _authorized(cfg):
            return jsonify({"erro": "nao_autorizado"}), 401
        if not cfg["host"] or not cfg["database"] or not cfg["user"]:
            return jsonify({"erro": "postgres_nao_configurado"}), 500

        try:
            table = _validar_table(cfg["table"])
            payload = request.get_json(silent=True) or {}
            inicio = _parse_data(payload.get("inicio"))
            fim = _parse_data(payload.get("fim")) or inicio
            if not inicio or not fim:
                return jsonify({"sucesso": False, "erro": "inicio_e_fim_obrigatorios"}), 400
            try:
                limite = int(payload.get("limite") or 2000)
            except (TypeError, ValueError):
                limite = 2000
            limite = max(1, min(limite, 5000))

            sql = f"""
                select
                    c.codigo::text as codigo,
                    c.n_nf::text as numero_nota,
                    c.dt_nf,
                    c.dt_lancamento,
                    coalesce(nullif(c.chv_nfe, ''), '') as chave_acesso,
                    regexp_replace(coalesce(f.cgc, ''), '\\D', '', 'g') as fornecedor_cnpj,
                    coalesce(nullif(c.fornecedor, ''), f.razao_social, f.nome) as fornecedor_nome
                from {table} c
                left join public.tfornece f
                  on f.cod_empresa = c.cod_empresa and f.codigo = c.cod_fornecedor
                where c.dt_lancamento::date >= %s
                  and c.dt_lancamento::date <= %s
                  and coalesce(c.codigo::text, '') <> ''
                  and coalesce(c.n_nf::text, '') <> ''
                order by c.dt_lancamento desc nulls last, c.codigo desc
                limit %s
            """
            with _conectar(cfg) as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, (inicio, fim, limite))
                    cols = [desc[0] for desc in cur.description]
                    rows = []
                    for row in cur.fetchall():
                        item = dict(zip(cols, row))
                        item["dt_nf"] = _iso_dt(item.get("dt_nf"))
                        item["dt_lancamento"] = _iso_dt(item.get("dt_lancamento"))
                        rows.append(item)

            return jsonify({"sucesso": True, "lancamentos": rows})
        except Exception as exc:
            app.logger.exception("Falha ao consultar lancamentos por periodo no ERP")
            return jsonify({"sucesso": False, "erro": str(exc)}), 500

    @app.post("/api/erp/pedidos")
    def consultar_pedidos():
        cfg = _config()
        if not _authorized(cfg):
            return jsonify({"erro": "nao_autorizado"}), 401
        if not cfg["host"] or not cfg["database"] or not cfg["user"]:
            return jsonify({"erro": "postgres_nao_configurado"}), 500

        try:
            payload = request.get_json(silent=True) or {}
            pedidos_raw = payload.get("pedidos") or []
            if not isinstance(pedidos_raw, list):
                return jsonify({"erro": "pedidos_deve_ser_lista"}), 400

            pedidos = []
            vistos = set()
            for pedido in pedidos_raw:
                numero = str(pedido or "").strip()
                if numero and numero not in vistos:
                    vistos.add(numero)
                    pedidos.append(numero)

            if not pedidos:
                return jsonify({"sucesso": True, "linhas": []})

            with _conectar(cfg) as conn:
                with conn.cursor() as cur:
                    cur.execute(PEDIDOS_SQL, (pedidos,))
                    cols = [desc[0] for desc in cur.description]
                    linhas = [dict(zip(cols, row)) for row in cur.fetchall()]

            return jsonify({"sucesso": True, "linhas": linhas})
        except Exception as exc:
            app.logger.exception("Falha ao consultar pedidos no ERP")
            return jsonify({"sucesso": False, "erro": str(exc)}), 500

    @app.post("/api/erp/estoque")
    def consultar_estoque():
        cfg = _config()
        if not _authorized(cfg):
            return jsonify({"erro": "nao_autorizado"}), 401
        if not cfg["host"] or not cfg["database"] or not cfg["user"]:
            return jsonify({"erro": "postgres_nao_configurado"}), 500

        try:
            payload = request.get_json(silent=True) or {}
            try:
                empresa = int(payload.get("empresa") or 1)
            except (TypeError, ValueError):
                empresa = 1

            with _conectar(cfg) as conn:
                with conn.cursor() as cur:
                    cur.execute(ESTOQUE_SQL, (empresa,))
                    cols = [desc[0] for desc in cur.description]
                    itens = [dict(zip(cols, row)) for row in cur.fetchall()]
                    cur.execute(ESTOQUE_CODIGOS_ATIVOS_SQL, (empresa,))
                    codigos_ativos = [str(row[0]).strip() for row in cur.fetchall() if row and row[0]]

            return jsonify({"sucesso": True, "empresa": empresa, "itens": itens, "codigos_ativos": codigos_ativos})
        except Exception as exc:
            app.logger.exception("Falha ao consultar estoque no ERP")
            return jsonify({"sucesso": False, "erro": str(exc)}), 500

    @app.post("/api/erp/estoque/kardex-consumo")
    def consultar_estoque_kardex_consumo():
        cfg = _config()
        if not _authorized(cfg):
            return jsonify({"erro": "nao_autorizado"}), 401
        if not cfg["host"] or not cfg["database"] or not cfg["user"]:
            return jsonify({"erro": "postgres_nao_configurado"}), 500

        try:
            payload = request.get_json(silent=True) or {}
            try:
                empresa = int(payload.get("empresa") or 1)
            except (TypeError, ValueError):
                empresa = 1
            try:
                janela_dias = int(payload.get("janela_dias") or 30)
            except (TypeError, ValueError):
                janela_dias = 30
            janela_dias = max(7, min(janela_dias, 180))

            codigos_raw = payload.get("codigos") or []
            if not isinstance(codigos_raw, list):
                return jsonify({"sucesso": False, "erro": "codigos_deve_ser_lista"}), 400

            codigos = []
            vistos = set()
            for codigo in codigos_raw:
                chave = re.sub(r"[^A-Z0-9]", "", str(codigo or "").strip().upper())
                if chave and chave not in vistos:
                    vistos.add(chave)
                    codigos.append(chave)
            if not codigos:
                return jsonify({"sucesso": True, "consumos": {}, "janela_dias": janela_dias, "fonte": "sem_codigos"})

            data_fim = date.today()
            data_inicio = data_fim - timedelta(days=janela_dias - 1)

            with _conectar(cfg) as conn:
                with conn.cursor() as cur:
                    fonte = _detectar_fonte_kardex(cur)
                    if not fonte:
                        return jsonify(
                            {
                                "sucesso": True,
                                "consumos": {},
                                "janela_dias": janela_dias,
                                "fonte": "indisponivel",
                                "mensagem": "Nenhuma tabela de kardex/movimentação compatível foi encontrada.",
                            }
                        )

                    params = [data_inicio, data_fim]
                    if fonte["tem_empresa"]:
                        params.append(empresa)
                    params.append(codigos)
                    cur.execute(fonte["sql"], tuple(params))
                    rows = cur.fetchall()

            agregados: dict[str, dict[str, Any]] = {}
            for row in rows:
                codigo = re.sub(r"[^A-Z0-9]", "", str(row[0] or "").strip().upper())
                if not codigo:
                    continue
                data_mov = row[1]
                quantidade = float(row[2] or 0)
                tipo_mov = str(row[3] or "").strip().upper()
                saldo = row[4]

                slot = agregados.setdefault(codigo, {"saida_total": 0.0, "dias_saida": set(), "saldos": []})
                tokens_saida = (
                    "SAIDA",
                    "CONSUMO",
                    "BAIXA",
                    "EXPED",
                    "VENDA",
                    "REQUIS",
                    "REMESSA",
                    "TRANSFERENCIA SAIDA",
                )
                tokens_entrada = (
                    "ENTRADA",
                    "COMPRA",
                    "RETORNO",
                    "AJUSTE ENTRADA",
                    "TRANSFERENCIA ENTRADA",
                    "DEVOLUCAO FORNECEDOR",
                )
                texto_mov = re.sub(r"\s+", " ", tipo_mov)
                is_saida = quantidade < 0
                if not is_saida and texto_mov:
                    if any(tok in texto_mov for tok in tokens_entrada):
                        is_saida = False
                    elif any(tok in texto_mov for tok in tokens_saida):
                        is_saida = True
                if is_saida and quantidade != 0:
                    slot["saida_total"] += abs(quantidade)
                    if data_mov is not None:
                        slot["dias_saida"].add(str(data_mov))

                if saldo is not None:
                    try:
                        slot["saldos"].append(float(saldo))
                    except (TypeError, ValueError):
                        pass

            consumos: dict[str, dict[str, Any]] = {}
            for codigo in codigos:
                slot = agregados.get(codigo) or {"saida_total": 0.0, "dias_saida": set(), "saldos": []}
                saida_total = float(slot.get("saida_total") or 0)
                dias_saida = len(slot.get("dias_saida") or set())
                saldos = [float(v) for v in (slot.get("saldos") or [])]
                estoque_medio = (sum(saldos) / len(saldos)) if saldos else 0.0
                consumos[codigo] = {
                    "consumo_medio_diario": round(saida_total / float(janela_dias), 6),
                    "saida_total_periodo": round(saida_total, 6),
                    "dias_com_saida": dias_saida,
                    "estoque_medio_periodo": round(estoque_medio, 6),
                }

            return jsonify(
                {
                    "sucesso": True,
                    "empresa": empresa,
                    "janela_dias": janela_dias,
                    "inicio": data_inicio.isoformat(),
                    "fim": data_fim.isoformat(),
                    "fonte": fonte["tabela"],
                    "consumos": consumos,
                }
            )
        except Exception as exc:
            app.logger.exception("Falha ao consultar consumo do kardex no ERP")
            return jsonify({"sucesso": False, "erro": str(exc)}), 500

    @app.post("/api/erp/conserto")
    def consultar_conserto():
        cfg = _config()
        if not _authorized(cfg):
            return jsonify({"erro": "nao_autorizado"}), 401
        if not cfg["host"] or not cfg["database"] or not cfg["user"]:
            return jsonify({"erro": "postgres_nao_configurado"}), 500

        try:
            payload = request.get_json(silent=True) or {}
            try:
                empresa = int(payload.get("empresa") or 1)
            except (TypeError, ValueError):
                empresa = 1

            data_inicial = _parse_data(payload.get("data_inicial")) or date(datetime.now().year, 3, 1)
            try:
                limite = int(payload.get("limite") or 5000)
            except (TypeError, ValueError):
                limite = 5000
            limite = max(1, min(limite, 20000))

            with _conectar(cfg) as conn:
                with conn.cursor() as cur:
                    cur.execute(CONSERTO_REMESSAS_SQL, (empresa, data_inicial, limite))
                    cols = [desc[0] for desc in cur.description]
                    remessas = []
                    for row in cur.fetchall():
                        item = dict(zip(cols, row))
                        item["data_emissao"] = _date_to_api(item.get("data_emissao"))
                        item["data_nf_retorno"] = _date_to_api(item.get("data_nf_retorno"))
                        remessas.append(item)
                    cur.execute(CONSERTO_RETORNOS_SQL, (empresa, data_inicial, limite))
                    cols = [desc[0] for desc in cur.description]
                    retornos = [dict(zip(cols, row)) for row in cur.fetchall()]

            _aplicar_retornos_conserto(remessas, retornos)

            return jsonify({
                "sucesso": True,
                "empresa": empresa,
                "data_inicial": data_inicial.isoformat(),
                "remessas": remessas,
                "retornos_lidos": len(retornos),
            })
        except Exception as exc:
            app.logger.exception("Falha ao consultar conserto no ERP")
            return jsonify({"sucesso": False, "erro": str(exc)}), 500

    @app.post("/api/erp/nfe-emitidas")
    def consultar_nfe_emitidas():
        cfg = _config()
        if not _authorized(cfg):
            return jsonify({"erro": "nao_autorizado"}), 401
        if not cfg["host"] or not cfg["database"] or not cfg["user"]:
            return jsonify({"erro": "postgres_nao_configurado"}), 500

        try:
            payload = request.get_json(silent=True) or {}
            data_inicial = _parse_data_minima(payload.get("data_inicial"))
            try:
                limite = int(payload.get("limite") or 300)
            except (TypeError, ValueError):
                limite = 300
            limite = max(1, min(limite, 1000))

            with _conectar(cfg) as conn:
                with conn.cursor() as cur:
                    cur.execute(NFE_EMITIDAS_SQL, (data_inicial, limite))
                    cols = [desc[0] for desc in cur.description]
                    notas = []
                    for row in cur.fetchall():
                        item = dict(zip(cols, row))
                        item["emitido_em"] = _date_to_api(item.get("emitido_em"))
                        item["autorizada"] = str(item.get("nfe_cod_status") or "").strip() == "100"
                        notas.append(item)

            return jsonify({
                "sucesso": True,
                "data_inicial": data_inicial.isoformat(),
                "notas": notas,
            })
        except Exception as exc:
            app.logger.exception("Falha ao consultar NF-e emitidas no ERP")
            return jsonify({"sucesso": False, "erro": str(exc)}), 500

    @app.post("/api/erp/nfe-emitida")
    def consultar_nfe_emitida():
        cfg = _config()
        if not _authorized(cfg):
            return jsonify({"erro": "nao_autorizado"}), 401
        if not cfg["host"] or not cfg["database"] or not cfg["user"]:
            return jsonify({"erro": "postgres_nao_configurado"}), 500

        try:
            payload = request.get_json(silent=True) or {}
            data_minima = _parse_data_minima(payload.get("data_minima"))
            numero = str(payload.get("numero") or "").strip()
            chave = "".join(ch for ch in str(payload.get("chave") or "") if ch.isdigit())
            if not numero and not chave:
                return jsonify({"sucesso": False, "erro": "numero_ou_chave_obrigatorio"}), 400

            with _conectar(cfg) as conn:
                with conn.cursor() as cur:
                    cur.execute(NFE_EMITIDA_SQL, (data_minima, numero, numero, chave, chave))
                    row = cur.fetchone()
                    if not row:
                        return jsonify({"sucesso": True, "nota": None})
                    cols = [desc[0] for desc in cur.description]
                    nota = dict(zip(cols, row))

            xml = nota.pop("nfe_arquivo_xml", None)
            pdf = nota.pop("pdf_danfe", None)
            nota["emitido_em"] = _date_to_api(nota.get("emitido_em"))
            nota["autorizada"] = str(nota.get("nfe_cod_status") or "").strip() == "100"
            nota["xml_base64"] = _b64(xml)
            nota["pdf_base64"] = _b64(pdf)
            return jsonify({"sucesso": True, "nota": nota})
        except Exception as exc:
            app.logger.exception("Falha ao consultar NF-e emitida no ERP")
            return jsonify({"sucesso": False, "erro": str(exc)}), 500

    @app.post("/api/erp/cadastro-email-por-cnpj")
    def consultar_cadastro_email_por_cnpj():
        cfg = _config()
        if not _authorized(cfg):
            return jsonify({"erro": "nao_autorizado"}), 401
        if not cfg["host"] or not cfg["database"] or not cfg["user"]:
            return jsonify({"erro": "postgres_nao_configurado"}), 500

        try:
            payload = request.get_json(silent=True) or {}
            cnpj = "".join(ch for ch in str(payload.get("cnpj") or payload.get("cpf_cnpj") or "") if ch.isdigit())
            if not cnpj:
                return jsonify({"sucesso": False, "erro": "cnpj_obrigatorio"}), 400

            with _conectar(cfg) as conn:
                with conn.cursor() as cur:
                    cur.execute(CADASTRO_EMAIL_POR_CNPJ_SQL, (cnpj, cnpj))
                    row = cur.fetchone()
                    if not row:
                        return jsonify({"sucesso": True, "encontrado": False, "email": ""})

                    cols = [desc[0] for desc in cur.description]
                    item = dict(zip(cols, row))
                    return jsonify({
                        "sucesso": True,
                        "encontrado": True,
                        "cnpj": cnpj,
                        **item,
                    })
        except Exception as exc:
            app.logger.exception("Falha ao consultar e-mail de cadastro no ERP")
            return jsonify({"sucesso": False, "erro": str(exc)}), 500

    @app.post("/api/erp/contas-receber-aberto")
    def consultar_contas_receber_aberto():
        cfg = _config()
        if not _authorized(cfg):
            return jsonify({"erro": "nao_autorizado"}), 401
        if not cfg["host"] or not cfg["database"] or not cfg["user"]:
            return jsonify({"erro": "postgres_nao_configurado"}), 500

        try:
            payload = request.get_json(silent=True) or {}
            cpf_cnpj = "".join(ch for ch in str(payload.get("cpf_cnpj") or "") if ch.isdigit())
            numero_nota = str(payload.get("numero_nota") or "").strip()
            orcamento = str(payload.get("orcamento") or "").strip()
            incluir_pagos = bool(payload.get("incluir_pagos"))
            if not cpf_cnpj and not numero_nota and not orcamento:
                return jsonify({"sucesso": False, "erro": "cpf_cnpj_numero_nota_ou_orcamento_obrigatorio"}), 400
            try:
                limite = int(payload.get("limite") or 200)
            except (TypeError, ValueError):
                limite = 200
            limite = max(1, min(limite, 500))

            with _conectar(cfg) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        CONTAS_RECEBER_ABERTO_SQL,
                        (
                            incluir_pagos,
                            cpf_cnpj,
                            cpf_cnpj,
                            numero_nota,
                            numero_nota,
                            f"%{numero_nota}%",
                            orcamento,
                            orcamento,
                            orcamento,
                            limite,
                        ),
                    )
                    cols = [desc[0] for desc in cur.description]
                    titulos = []
                    for row in cur.fetchall():
                        item = dict(zip(cols, row))
                        item["vencimento_raw"] = _date_to_api(item.get("vencimento_raw"))
                        titulos.append(item)

            return jsonify({"sucesso": True, "titulos": titulos})
        except Exception as exc:
            app.logger.exception("Falha ao consultar contas a receber em aberto no ERP")
            return jsonify({"sucesso": False, "erro": str(exc)}), 500

    @app.post("/api/erp/contas-receber-boleto-pdf")
    def consultar_contas_receber_boleto_pdf():
        cfg = _config()
        if not _authorized(cfg):
            return jsonify({"erro": "nao_autorizado"}), 401
        if not cfg["host"] or not cfg["database"] or not cfg["user"]:
            return jsonify({"erro": "postgres_nao_configurado"}), 500

        try:
            payload = request.get_json(silent=True) or {}
            codigo = str(payload.get("codigo") or "").strip()
            if not codigo:
                return jsonify({"sucesso": False, "erro": "codigo_obrigatorio"}), 400

            with _conectar(cfg) as conn:
                with conn.cursor() as cur:
                    cur.execute(CONTAS_RECEBER_BOLETO_PDF_SQL, (codigo,))
                    row = cur.fetchone()
                    if not row:
                        return jsonify({"sucesso": True, "encontrado": False, "pdf_base64": None})

                    cols = [desc[0] for desc in cur.description]
                    item = dict(zip(cols, row))
                    pdf = item.pop("boleto_pdf", None)
                    item["encontrado"] = True
                    item["pdf_base64"] = _b64(pdf)
                    item["pdf_len"] = len(bytes(pdf)) if pdf is not None else 0
                    return jsonify({"sucesso": True, **item})
        except Exception as exc:
            app.logger.exception("Falha ao consultar boleto PDF no contas a receber")
            return jsonify({"sucesso": False, "erro": str(exc)}), 500

    @app.post("/api/erp/entrada-chapa")
    def consultar_entrada_chapa():
        cfg = _config()
        if not _authorized(cfg):
            return jsonify({"erro": "nao_autorizado"}), 401
        if not cfg["host"] or not cfg["database"] or not cfg["user"]:
            return jsonify({"erro": "postgres_nao_configurado"}), 500

        try:
            payload = request.get_json(silent=True) or {}
            numero_ar = str(payload.get("numero_ar") or payload.get("codigo_lancamento") or "").strip()
            numero_nota = str(payload.get("numero_nota") or "").strip()
            chave = "".join(ch for ch in str(payload.get("chave") or "") if ch.isdigit())
            if not numero_ar and not numero_nota and not chave:
                return jsonify({"sucesso": False, "erro": "numero_ar_numero_nota_ou_chave_obrigatorio"}), 400

            with _conectar(cfg) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        ENTRADA_CHAPA_SQL,
                        (
                            numero_ar,
                            numero_ar,
                            numero_ar,
                            numero_nota,
                            numero_nota,
                            numero_ar,
                            numero_nota,
                            chave,
                            chave,
                        ),
                    )
                    cols = [desc[0] for desc in cur.description]
                    rows = [dict(zip(cols, row)) for row in cur.fetchall()]
                    _enriquecer_valores_entrada(cur, rows)
                    _enriquecer_tributos_entrada(cur, rows)

            entrada = _montar_entrada_chapa(rows, numero_ar, numero_nota, chave)
            return jsonify({"sucesso": True, "entrada": entrada})
        except Exception as exc:
            app.logger.exception("Falha ao consultar entrada de chapa no ERP")
            return jsonify({"sucesso": False, "erro": str(exc)}), 500

    @app.post("/api/erp/entradas-chapa-lote")
    def consultar_entradas_chapa_lote():
        cfg = _config()
        if not _authorized(cfg):
            return jsonify({"erro": "nao_autorizado"}), 401
        if not cfg["host"] or not cfg["database"] or not cfg["user"]:
            return jsonify({"erro": "postgres_nao_configurado"}), 500

        try:
            payload = request.get_json(silent=True) or {}
            entradas_payload = payload.get("entradas") or []
            if not isinstance(entradas_payload, list):
                return jsonify({"sucesso": False, "erro": "entradas_deve_ser_lista"}), 400
            entradas_payload = entradas_payload[:500]
            entradas = []

            with _conectar(cfg) as conn:
                with conn.cursor() as cur:
                    for item in entradas_payload:
                        if not isinstance(item, dict):
                            continue
                        numero_ar = str(item.get("numero_ar") or item.get("codigo_lancamento") or "").strip()
                        numero_nota = str(item.get("numero_nota") or "").strip()
                        chave = "".join(ch for ch in str(item.get("chave") or "") if ch.isdigit())
                        if not numero_ar and not numero_nota and not chave:
                            continue
                        cur.execute(
                            ENTRADA_CHAPA_SQL,
                            (
                                numero_ar,
                                numero_ar,
                                numero_ar,
                                numero_nota,
                                numero_nota,
                                numero_ar,
                                numero_nota,
                                chave,
                                chave,
                            ),
                        )
                        cols = [desc[0] for desc in cur.description]
                        rows = [dict(zip(cols, row)) for row in cur.fetchall()]
                        _enriquecer_valores_entrada(cur, rows)
                        _enriquecer_tributos_entrada(cur, rows)
                        entrada = _montar_entrada_chapa(rows, numero_ar, numero_nota, chave)
                        if entrada:
                            entradas.append(entrada)

            return jsonify({"sucesso": True, "entradas": entradas})
        except Exception as exc:
            app.logger.exception("Falha ao consultar entradas de chapa em lote no ERP")
            return jsonify({"sucesso": False, "erro": str(exc)}), 500

    @app.post("/api/erp/entrada-chapa-desde")
    def consultar_entradas_chapa_desde():
        cfg = _config()
        if not _authorized(cfg):
            return jsonify({"erro": "nao_autorizado"}), 401
        if not cfg["host"] or not cfg["database"] or not cfg["user"]:
            return jsonify({"erro": "postgres_nao_configurado"}), 500

        try:
            payload = request.get_json(silent=True) or {}
            data_minima = _parse_data_minima(payload.get("data_recebimento_minima"))
            cfops = [str(c).strip()[:4] for c in (payload.get("cfops") or ["1901", "1915", "1924"]) if str(c).strip()]
            controles = [str(c).strip() for c in (payload.get("controles_lote") or ["1", "3"]) if str(c).strip()]
            if not cfops:
                cfops = ["1901", "1915", "1924"]
            if not controles:
                controles = ["1", "3"]
            try:
                limite = int(payload.get("limite") or 500)
            except (TypeError, ValueError):
                limite = 500
            limite = max(1, min(limite, 1000))

            with _conectar(cfg) as conn:
                with conn.cursor() as cur:
                    cur.execute(ENTRADAS_CHAPA_DESDE_SQL, (data_minima, controles, controles, limite))
                    cols = [desc[0] for desc in cur.description]
                    rows = [dict(zip(cols, row)) for row in cur.fetchall()]
                    _enriquecer_valores_entrada(cur, rows)
                    _enriquecer_tributos_entrada(cur, rows)

            grupos: dict[str, list[dict[str, Any]]] = {}
            for row in rows:
                chave_grupo = str(row.get("codigo_lancamento") or row.get("numero_nota") or row.get("chave_acesso") or "")
                if chave_grupo:
                    grupos.setdefault(chave_grupo, []).append(row)

            entradas = []
            for grupo_rows in grupos.values():
                entrada = _montar_entrada_chapa(grupo_rows)
                if entrada:
                    entradas.append(entrada)

            return jsonify({
                "sucesso": True,
                "data_recebimento_minima": data_minima.isoformat(),
                "entradas": entradas,
            })
        except Exception as exc:
            app.logger.exception("Falha ao consultar entradas de chapa desde data no ERP")
            return jsonify({"sucesso": False, "erro": str(exc)}), 500

    return app


app = create_app()


if __name__ == "__main__":
    host = _env("ERP_BRIDGE_HOST", "0.0.0.0")
    port = _env_int("ERP_BRIDGE_PORT", "8088")
    app.run(host=host, port=port)
