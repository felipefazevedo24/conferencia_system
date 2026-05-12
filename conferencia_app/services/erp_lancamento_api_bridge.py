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

    return app


app = create_app()


if __name__ == "__main__":
    host = _env("ERP_BRIDGE_HOST", "0.0.0.0")
    port = _env_int("ERP_BRIDGE_PORT", "8088")
    app.run(host=host, port=port)
