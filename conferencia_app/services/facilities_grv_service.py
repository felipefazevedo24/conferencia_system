"""Consultas GRV/Postgres para o modulo Facilities.

O Facilities usa o GRV como fonte de verdade para colaboradores e para o
catalogo/saldo de EPI e uniformes. As tabelas locais ficam apenas como espelho
operacional para manter historico, FK e auditoria do Sync.
"""
from __future__ import annotations

import json
import os
import re
import unicodedata
from pathlib import Path
from typing import Any

from flask import current_app
import requests


GRV_FAMILIA_USO_CONSUMO = 39
GRV_GRUPO_EPI = 3
GRV_GRUPO_UNIFORMES = 9


def normalizar_nome(valor: str) -> str:
    text = unicodedata.normalize("NFD", valor or "")
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return " ".join(text.lower().split())


def _config() -> dict[str, Any]:
    arquivo = {}
    try:
        path = Path(current_app.instance_path) / "erp_lancamento_config.json"
        if path.exists():
            arquivo = json.loads(path.read_text(encoding="utf-8")) or {}
            if not isinstance(arquivo, dict):
                arquivo = {}
    except Exception:
        arquivo = {}
    return {
        "host": str(os.environ.get("FACILITIES_GRV_PG_HOST") or os.environ.get("ERP_LANCAMENTO_PG_HOST") or arquivo.get("host") or "").strip(),
        "port": int(os.environ.get("FACILITIES_GRV_PG_PORT") or os.environ.get("ERP_LANCAMENTO_PG_PORT") or arquivo.get("port") or 5432),
        "database": str(os.environ.get("FACILITIES_GRV_PG_DB") or os.environ.get("ERP_LANCAMENTO_PG_DB") or arquivo.get("database") or "").strip(),
        "user": str(os.environ.get("FACILITIES_GRV_PG_USER") or os.environ.get("ERP_LANCAMENTO_PG_USER") or arquivo.get("user") or "").strip(),
        "password": str(os.environ.get("FACILITIES_GRV_PG_PASSWORD") or os.environ.get("ERP_LANCAMENTO_PG_PASSWORD") or arquivo.get("password") or ""),
        "connect_timeout": int(os.environ.get("FACILITIES_GRV_CONNECT_TIMEOUT") or os.environ.get("ERP_LANCAMENTO_CONNECT_TIMEOUT") or 8),
        "empresa": int(os.environ.get("FACILITIES_GRV_EMPRESA") or os.environ.get("ERP_ESTOQUE_PG_COMPANY") or 1),
        "api_url": str(os.environ.get("FACILITIES_GRV_API_URL") or os.environ.get("ERP_LANCAMENTO_API_URL") or arquivo.get("api_url") or "").strip().rstrip("/"),
        "api_token": str(os.environ.get("FACILITIES_GRV_API_TOKEN") or os.environ.get("ERP_LANCAMENTO_API_TOKEN") or arquivo.get("api_token") or ""),
        "api_timeout": int(os.environ.get("FACILITIES_GRV_API_TIMEOUT") or os.environ.get("ERP_LANCAMENTO_API_TIMEOUT") or arquivo.get("api_timeout") or 30),
    }


def _conectar(cfg: dict[str, Any]):
    import psycopg2  # type: ignore

    return psycopg2.connect(
        host=cfg["host"],
        port=cfg["port"],
        dbname=cfg["database"],
        user=cfg["user"],
        password=cfg["password"],
        connect_timeout=cfg.get("connect_timeout") or 8,
    )


def _float(valor: Any, padrao=0.0) -> float:
    try:
        return float(valor if valor is not None else padrao)
    except (TypeError, ValueError):
        return float(padrao)


def _tipo_por_grupo(cod_grupo: Any) -> str:
    try:
        grupo = int(cod_grupo or 0)
    except (TypeError, ValueError):
        grupo = 0
    if grupo == GRV_GRUPO_UNIFORMES:
        return "uniforme"
    return "epi"


def _extrair_ca(nome_item: str) -> str:
    match = re.search(r"\bC\.?\s*A\.?\s*[:\-]?\s*([0-9][0-9.\-/ ]{2,})", nome_item or "", flags=re.I)
    if not match:
        return ""
    return "".join(ch for ch in match.group(1) if ch.isdigit())[:20]


def _post_bridge(cfg: dict[str, Any], path: str, payload: dict[str, Any]) -> dict[str, Any]:
    url = f"{cfg['api_url']}{path}"
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "ngrok-skip-browser-warning": "true",
        "User-Agent": "ColumbiaSync/Facilities-GRV",
    }
    if cfg.get("api_token"):
        headers["Authorization"] = f"Bearer {cfg['api_token']}"
    current_app.logger.info("Facilities GRV via bridge: POST %s", path)
    resp = requests.post(url, headers=headers, json=payload, timeout=cfg.get("api_timeout") or 30)
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, dict) or not data.get("sucesso"):
        raise RuntimeError(str((data or {}).get("erro") or "Resposta invalida da API Facilities GRV"))
    return data


class FacilitiesGRVService:
    @staticmethod
    def listar_funcionarios(ativos: bool = True) -> list[dict[str, Any]]:
        cfg = _config()
        if cfg.get("api_url"):
            data = _post_bridge(cfg, "/api/erp/facilities/funcionarios", {"ativos": bool(ativos)})
            rows = data.get("funcionarios") or []
            return rows if isinstance(rows, list) else []
        return FacilitiesGRVService._listar_funcionarios_postgres(cfg, ativos=ativos)

    @staticmethod
    def _listar_funcionarios_postgres(cfg: dict[str, Any] | None = None, ativos: bool = True) -> list[dict[str, Any]]:
        cfg = cfg or _config()
        if not cfg["host"] or not cfg["database"] or not cfg["user"]:
            return []
        where_ativo = "and coalesce(inativo, 0) = 0" if ativos else ""
        sql = f"""
            select
                cod_empresa,
                codigo,
                coalesce(nullif(trim(indentificacao), ''), '') as identificacao,
                coalesce(nullif(trim(apelido), ''), '') as apelido,
                coalesce(nullif(trim(nome), ''), '') as nome,
                coalesce(nullif(trim(cargo), ''), '') as cargo,
                coalesce(nullif(trim(setor), ''), '') as setor,
                cod_setor,
                coalesce(nullif(trim(e_mail_corporativo), ''), nullif(trim(e_mail_pessoal), ''), '') as email,
                coalesce(inativo, 0) as inativo
            from public.tfuncion
            where cod_empresa = %s
              and coalesce(nullif(trim(nome), ''), '') <> ''
              {where_ativo}
            order by nome
        """
        with _conectar(cfg) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (cfg["empresa"],))
                cols = [desc[0] for desc in cur.description]
                return [dict(zip(cols, row)) for row in cur.fetchall()]

    @staticmethod
    def listar_materiais_epi_uniforme(com_saldo: bool = True) -> list[dict[str, Any]]:
        cfg = _config()
        if cfg.get("api_url"):
            data = _post_bridge(cfg, "/api/erp/facilities/materiais", {"com_saldo": bool(com_saldo)})
            rows = data.get("materiais") or []
            return rows if isinstance(rows, list) else []
        return FacilitiesGRVService._listar_materiais_epi_uniforme_postgres(cfg, com_saldo=com_saldo)

    @staticmethod
    def _listar_materiais_epi_uniforme_postgres(cfg: dict[str, Any] | None = None, com_saldo: bool = True) -> list[dict[str, Any]]:
        cfg = cfg or _config()
        if not cfg["host"] or not cfg["database"] or not cfg["user"]:
            return []
        filtro_saldo = "and coalesce(p.estoque, 0) > 0" if com_saldo else ""
        sql = f"""
            select
                p.cod_empresa,
                p.codigo,
                p.codigo_interno,
                p.nome,
                coalesce(nullif(p.unidade, ''), nullif(p.unidade_compra, ''), 'UN') as unidade,
                p.cod_familia,
                coalesce(f.nome, '') as familia,
                p.cod_grupo,
                coalesce(p.estoque, 0) as saldo,
                coalesce(p.estoque_reservado, 0) as reservado,
                coalesce(p.localizacao_estoque, '') as localizacao_estoque,
                coalesce(p.inativo, 0) as inativo
            from public.tproduto p
            left join public.tfamilia f
              on f.cod_empresa = p.cod_empresa
             and f.codigo = p.cod_familia
            where p.cod_empresa = %s
              and p.cod_familia = %s
              and p.cod_grupo in (%s, %s)
              and coalesce(p.inativo, 0) = 0
              and coalesce(nullif(trim(p.codigo_interno), ''), '') <> ''
              {filtro_saldo}
            order by p.cod_grupo, p.codigo_interno
        """
        with _conectar(cfg) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (cfg["empresa"], GRV_FAMILIA_USO_CONSUMO, GRV_GRUPO_EPI, GRV_GRUPO_UNIFORMES))
                cols = [desc[0] for desc in cur.description]
                rows = []
                for row in cur.fetchall():
                    item = dict(zip(cols, row))
                    nome = str(item.get("nome") or "").strip()
                    saldo = _float(item.get("saldo"))
                    tipo = _tipo_por_grupo(item.get("cod_grupo"))
                    rows.append({
                        "id": str(item.get("codigo_interno") or "").strip(),
                        "codigo_produto_grv": item.get("codigo"),
                        "codigo_interno": str(item.get("codigo_interno") or "").strip(),
                        "nome": nome,
                        "tipo": tipo,
                        "numero_ca": _extrair_ca(nome),
                        "qtd_estoque": saldo,
                        "qtd_minima": 0,
                        "abaixo_minimo": saldo <= 2,
                        "familia": item.get("familia") or "",
                        "grupo": "UNIFORMES" if tipo == "uniforme" else "EPI",
                        "localizacao_estoque": item.get("localizacao_estoque") or "",
                        "label": f"{item.get('codigo_interno')} - {nome} (saldo GRV: {saldo:g})",
                    })
                return rows

    @staticmethod
    def saldo_material(codigo_interno: str) -> float | None:
        codigo = str(codigo_interno or "").strip()
        if not codigo:
            return None
        cfg = _config()
        if cfg.get("api_url"):
            data = _post_bridge(cfg, "/api/erp/facilities/saldo", {"codigo_interno": codigo})
            saldo = data.get("saldo")
            return _float(saldo) if saldo is not None else None
        return FacilitiesGRVService._saldo_material_postgres(codigo, cfg)

    @staticmethod
    def _saldo_material_postgres(codigo_interno: str, cfg: dict[str, Any] | None = None) -> float | None:
        codigo = str(codigo_interno or "").strip()
        if not codigo:
            return None
        cfg = cfg or _config()
        if not cfg["host"] or not cfg["database"] or not cfg["user"]:
            return None
        sql = """
            select coalesce(estoque, 0)
            from public.tproduto
            where cod_empresa = %s
              and cod_familia = %s
              and cod_grupo in (%s, %s)
              and lower(trim(codigo_interno)) = lower(trim(%s))
            limit 1
        """
        with _conectar(cfg) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (cfg["empresa"], GRV_FAMILIA_USO_CONSUMO, GRV_GRUPO_EPI, GRV_GRUPO_UNIFORMES, codigo))
                row = cur.fetchone()
                return _float(row[0]) if row else None
