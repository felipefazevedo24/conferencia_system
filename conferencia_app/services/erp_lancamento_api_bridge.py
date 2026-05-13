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

import os
import base64
from datetime import date, datetime
from typing import Any

from flask import Flask, jsonify, request
import psycopg2  # type: ignore


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


def _normalizar_linhas_lancamento(rows: list[tuple[Any, Any, Any]]) -> tuple[str, Any, str] | None:
    linhas_validas = [
        (str(row[0]).strip(), row[1], str(row[2] or "").strip())
        for row in rows
        if row and row[0] is not None and str(row[0]).strip()
    ]
    if not linhas_validas:
        return None

    chaves = {chv_nfe for _codigo, _dt_nf, chv_nfe in linhas_validas if chv_nfe}
    if len(chaves) == 1:
        return linhas_validas[0]

    # Fallback para bases antigas/linhas sem chave: so aceita se codigo e data tambem forem identicos.
    assinaturas = {(codigo, _iso_dt(dt_nf)) for codigo, dt_nf, _chv_nfe in linhas_validas}
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


ENTRADA_CHAPA_SQL = """
    select
        c.codigo::text as codigo_lancamento,
        coalesce(nullif(c.romaneio, ''), nullif(lot.descricao, ''), '') as numero_ar,
        c.n_nf::text as numero_nota,
        c.dt_nf,
        c.dt_recebimento,
        c.dt_lancamento,
        coalesce(nullif(c.chv_nfe, ''), '') as chave_acesso,
        coalesce(
            nullif(c.fornecedor, ''),
            nullif(c.cliente, ''),
            nullif(f.razao_social, ''),
            nullif(f.nome, ''),
            nullif(cli.razao_social, ''),
            nullif(cli.nome, '')
        ) as parceiro_nome,
        coalesce(nullif(f.cgc, ''), nullif(cli.rg_cgc, '')) as parceiro_documento,
        coalesce(nullif(c.cfop, ''), '') as cfop_cabecalho,
        a.numero_item,
        coalesce(nullif(a.cfop, ''), nullif(c.cfop, '')) as cfop_item,
        coalesce(nullif(a.descricao_cfop, ''), nullif(c.tipo_movimento, ''), nullif(c.codigo_movimentacao, '')) as natureza_operacao,
        coalesce(nullif(a.cod_interno, ''), nullif(p.codigo_interno, '')) as cod_interno,
        coalesce(nullif(a.produto, ''), nullif(p.nome, '')) as descricao,
        coalesce(a.qtde, 0) as quantidade,
        coalesce(nullif(a.unidade, ''), nullif(p.unidade, ''), nullif(p.unidade_compra, '')) as unidade,
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
    left join public.tcom_aux_loteserie lot
      on lot.cod_empresa = a.cod_empresa
     and lot.guid_pai = a.guid_linha
    where (
        (%s <> '' and c.codigo::text = %s)
        or (%s <> '' and c.n_nf::text = %s)
        or (%s <> '' and regexp_replace(coalesce(c.chv_nfe, ''), '\\D', '', 'g') = %s)
    )
    order by c.dt_lancamento desc nulls last, c.dt_nf desc nulls last, c.codigo desc, a.numero_item, a.guid_linha
    limit 200
"""


def _authorized(cfg: dict[str, Any]) -> bool:
    token = str(cfg.get("token") or "")
    if not token:
        return False
    auth = request.headers.get("Authorization", "")
    return auth == f"Bearer {token}"


def create_app() -> Flask:
    app = Flask(__name__)

    @app.get("/health")
    def health():
        return jsonify({"ok": True, "service": "erp-lancamento-api-bridge"})

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
            sql_com_data = f"SELECT codigo, dt_nf, chv_nfe FROM {table} WHERE n_nf = %s AND dt_nf::date = %s LIMIT 50"
            sql_sem_data = f"SELECT codigo, dt_nf, chv_nfe FROM {table} WHERE n_nf = %s LIMIT 50"

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
                                resultados[n_nf] = {"codigo": row[0], "dt_nf": _iso_dt(row[1]), "chv_nfe": row[2]}
                            else:
                                # Fallback seguro: se a dt_nf do ERP nao for a emissao
                                # do XML, tenta por numero e aceita apenas retorno unico.
                                cur.execute(sql_sem_data, (n_nf,))
                                rows_sem_data = cur.fetchall()
                                row_sem_data = _normalizar_linhas_lancamento(rows_sem_data)
                                if row_sem_data:
                                    resultados[n_nf] = {
                                        "codigo": row_sem_data[0],
                                        "dt_nf": _iso_dt(row_sem_data[1]),
                                        "chv_nfe": row_sem_data[2],
                                    }
                                elif rows_sem_data:
                                    status[n_nf] = "ERP encontrou o numero, mas com multiplas chaves/datas - confirme manualmente"
                                else:
                                    status[n_nf] = "Aguardando lancamento no ERP"
                        else:
                            cur.execute(sql_sem_data, (n_nf,))
                            rows = cur.fetchall()
                            row = _normalizar_linhas_lancamento(rows)
                            if row:
                                resultados[n_nf] = {"codigo": row[0], "dt_nf": _iso_dt(row[1]), "chv_nfe": row[2]}
                            elif rows:
                                status[n_nf] = "Multiplas chaves de acesso para esse numero no ERP - vincule manualmente"
                            else:
                                status[n_nf] = "Aguardando lancamento no ERP"

            return jsonify({"sucesso": True, "resultados": resultados, "status": status})
        except Exception as exc:
            app.logger.exception("Falha ao consultar lancamentos no ERP")
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
                    cur.execute(ENTRADA_CHAPA_SQL, (numero_ar, numero_ar, numero_nota, numero_nota, chave, chave))
                    cols = [desc[0] for desc in cur.description]
                    rows = [dict(zip(cols, row)) for row in cur.fetchall()]

            if not rows:
                return jsonify({"sucesso": True, "entrada": None})

            cab = rows[0]
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
                    "tipo_controle": row.get("tipo_controle") or 0,
                    "controle_lote_serie": row.get("controle_lote_serie") or 0,
                    "lote": row.get("lote") or "",
                })

            entrada = {
                "codigo_lancamento": cab.get("codigo_lancamento") or numero_ar,
                "numero_ar": next((r.get("numero_ar") for r in rows if r.get("numero_ar")), ""),
                "numero_nota": cab.get("numero_nota") or numero_nota,
                "dt_nf": _date_to_api(cab.get("dt_nf")),
                "dt_recebimento": _date_to_api(cab.get("dt_recebimento")),
                "dt_lancamento": _date_to_api(cab.get("dt_lancamento")),
                "chave_acesso": cab.get("chave_acesso") or chave,
                "parceiro_nome": cab.get("parceiro_nome") or "",
                "parceiro_documento": cab.get("parceiro_documento") or "",
                "cfop_cabecalho": cab.get("cfop_cabecalho") or "",
                "itens": itens,
            }
            return jsonify({"sucesso": True, "entrada": entrada})
        except Exception as exc:
            app.logger.exception("Falha ao consultar entrada de chapa no ERP")
            return jsonify({"sucesso": False, "erro": str(exc)}), 500

    return app


app = create_app()


if __name__ == "__main__":
    host = _env("ERP_BRIDGE_HOST", "0.0.0.0")
    port = _env_int("ERP_BRIDGE_PORT", "8088")
    app.run(host=host, port=port)
