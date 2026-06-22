"""
Serviço de sincronização de estoque ERP → WMS
Busca dados de estoque do endpoint ERP e atualiza cadastro mestre (SKU),
gera divergências e alertas operacionais.
"""
import json
import logging
import os
from pathlib import Path
from datetime import datetime

import requests
from flask import current_app
from sqlalchemy import func

from ..extensions import db
from ..models import (
    WMSSkuMestre,
    WMSReconciliacaoDivergencia,
    WMSAlertaOperacional,
    WMSIntegracaoEvento,
    EstoqueWMS,
    LocalizacaoArmazem,
    DepositoWMS,
)

logger = logging.getLogger(__name__)


class ERPSyncService:
    """Sincroniza dados de estoque vindos do ERP com o WMS local."""

    _ultimos_codigos_erp_ativos = None

    # ── helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _get_company():
        return int(current_app.config.get("ERP_ESTOQUE_PG_COMPANY") or 1)

    @staticmethod
    def _get_timeout():
        return current_app.config.get("ERP_ESTOQUE_TIMEOUT", 30)

    @staticmethod
    def _carregar_config_postgres():
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
            "host": str(os.environ.get("ERP_ESTOQUE_PG_HOST") or os.environ.get("ERP_LANCAMENTO_PG_HOST") or arquivo.get("host") or "").strip(),
            "port": int(os.environ.get("ERP_ESTOQUE_PG_PORT") or os.environ.get("ERP_LANCAMENTO_PG_PORT") or arquivo.get("port") or 5432),
            "database": str(os.environ.get("ERP_ESTOQUE_PG_DB") or os.environ.get("ERP_LANCAMENTO_PG_DB") or arquivo.get("database") or "").strip(),
            "user": str(os.environ.get("ERP_ESTOQUE_PG_USER") or os.environ.get("ERP_LANCAMENTO_PG_USER") or arquivo.get("user") or "").strip(),
            "password": str(os.environ.get("ERP_ESTOQUE_PG_PASSWORD") or os.environ.get("ERP_LANCAMENTO_PG_PASSWORD") or arquivo.get("password") or ""),
            "connect_timeout": int(os.environ.get("ERP_ESTOQUE_PG_CONNECT_TIMEOUT") or os.environ.get("ERP_LANCAMENTO_CONNECT_TIMEOUT") or 8),
            "api_url": str(os.environ.get("ERP_ESTOQUE_API_URL") or os.environ.get("ERP_LANCAMENTO_API_URL") or arquivo.get("api_url") or "").strip().rstrip("/"),
            "api_token": str(os.environ.get("ERP_ESTOQUE_API_TOKEN") or os.environ.get("ERP_LANCAMENTO_API_TOKEN") or arquivo.get("api_token") or ""),
            "api_timeout": int(os.environ.get("ERP_ESTOQUE_API_TIMEOUT") or os.environ.get("ERP_LANCAMENTO_API_TIMEOUT") or arquivo.get("api_timeout") or 30),
        }

    @staticmethod
    def _conectar_postgres(cfg):
        import psycopg2  # type: ignore

        return psycopg2.connect(
            host=cfg["host"],
            port=cfg["port"],
            dbname=cfg["database"],
            user=cfg["user"],
            password=cfg["password"],
            connect_timeout=cfg.get("connect_timeout") or 8,
        )

    @staticmethod
    def _normalizar_unidade(un):
        mapa = {"PÇ": "PC", "UN": "UN", "KG": "KG", "LT": "LT", "MT": "MT", "CX": "CX"}
        raw = (un or "UN").strip().upper()
        return mapa.get(raw, raw[:5] or "UN")

    @staticmethod
    def _extrair_familia_grupo(item):
        familia_raw = (item.get("familia") or "").strip()
        grupo_raw = (item.get("grupo") or "").strip()

        # familia vem como "N - 06 - INSUMOS DA PRODUÇÃO" → guardar toda a string
        # grupo vem como "CENTRO DE USINAGEM"
        return familia_raw, grupo_raw

    @staticmethod
    def _row_postgres_to_estoque(row):
        def _float(valor, padrao=0.0):
            try:
                return float(valor if valor is not None else padrao)
            except (TypeError, ValueError):
                return float(padrao)

        qtd_total = _float(row.get("qtde_total"))
        qtd_reservada = _float(row.get("qtde_reservada"))
        qtd_disponivel = _float(row.get("qtde_disponivel"), qtd_total - qtd_reservada)

        return {
            "codigo_interno": str(row.get("codigo_interno") or "").strip(),
            "item": str(row.get("item") or "").strip(),
            "unidade": str(row.get("unidade") or "UN").strip(),
            "qtde_total": qtd_total,
            "qtde_reservada": qtd_reservada,
            "qtde_disponivel": qtd_disponivel,
            "localizacao_estoque": str(row.get("localizacao_estoque") or "").strip(),
            "familia": str(row.get("familia") or "").strip(),
            "grupo": str(row.get("grupo") or "").strip(),
        }

    # ── buscar dados do ERP ──────────────────────────────────────────────

    # ── sincronizar SKU mestre ───────────────────────────────────────────

    @classmethod
    def buscar_estoque_erp(cls):
        cfg = cls._carregar_config_postgres()
        if cfg.get("api_url"):
            return cls._buscar_estoque_erp_api(cfg)

        if not cfg["host"] or not cfg["database"] or not cfg["user"]:
            raise ValueError("Banco do ERP nao configurado para consulta de estoque.")

        sql = """
            select
                p.codigo_interno,
                p.nome as item,
                coalesce(nullif(p.unidade, ''), nullif(p.unidade_compra, ''), 'UN') as unidade,
                coalesce(p.estoque_disponivel_uso, coalesce(p.estoque, 0) + coalesce(p.estoque_reservado, 0), 0) as qtde_total,
                coalesce(p.estoque_reservado, 0) as qtde_reservada,
                coalesce(p.estoque, coalesce(p.estoque_disponivel_uso, 0) - coalesce(p.estoque_reservado, 0), 0) as qtde_disponivel,
                p.localizacao_estoque,
                coalesce(f.nome, '') as familia,
                coalesce(p.cod_grupo::text, '') as grupo
            from public.tproduto p
            left join public.tfamilia f
              on f.cod_empresa = p.cod_empresa
             and f.codigo = p.cod_familia
            where p.cod_empresa = %s
              and coalesce(nullif(trim(p.codigo_interno), ''), '') <> ''
              and coalesce(nullif(trim(p.localizacao_estoque), ''), '') <> ''
              and coalesce(p.inativo, 0) = 0
            order by p.localizacao_estoque, p.codigo_interno
        """

        with cls._conectar_postgres(cfg) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (cls._get_company(),))
                cols = [desc[0] for desc in cur.description]
                itens = [cls._row_postgres_to_estoque(dict(zip(cols, row))) for row in cur.fetchall()]
                cur.execute(
                    """
                    select distinct p.codigo_interno
                    from public.tproduto p
                    where p.cod_empresa = %s
                      and coalesce(nullif(trim(p.codigo_interno), ''), '') <> ''
                      and coalesce(p.inativo, 0) = 0
                    """,
                    (cls._get_company(),),
                )
                cls._ultimos_codigos_erp_ativos = {str(row[0]).strip().lower() for row in cur.fetchall() if row and row[0]}
                return itens

    @classmethod
    def _buscar_estoque_erp_api(cls, cfg):
        url = f"{cfg['api_url']}/api/erp/estoque"
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "ngrok-skip-browser-warning": "true",
            "User-Agent": "ColumbiaSync/ERP-Estoque",
        }
        if cfg.get("api_token"):
            headers["Authorization"] = f"Bearer {cfg['api_token']}"

        resp = requests.post(
            url,
            headers=headers,
            json={"empresa": cls._get_company()},
            timeout=cfg.get("api_timeout") or cls._get_timeout(),
        )
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, dict) or not data.get("sucesso"):
            raise RuntimeError(str((data or {}).get("erro") or "Resposta invalida da API ERP Estoque"))

        itens_raw = data.get("itens") or []
        if not isinstance(itens_raw, list):
            return []
        cls._ultimos_codigos_erp_ativos = {
            str(codigo).strip().lower()
            for codigo in (data.get("codigos_ativos") or [])
            if str(codigo or "").strip()
        } or None
        return [cls._row_postgres_to_estoque(item) for item in itens_raw if isinstance(item, dict)]

    @classmethod
    def sincronizar_skus(cls, itens_erp):
        criados = 0
        atualizados = 0
        erros = []

        for item in itens_erp:
            codigo = (item.get("codigo_interno") or "").strip()
            if not codigo:
                continue
            try:
                unidade = cls._normalizar_unidade(item.get("unidade"))
                familia, grupo = cls._extrair_familia_grupo(item)

                sku = WMSSkuMestre.query.filter(
                    func.lower(func.trim(WMSSkuMestre.codigo_item)) == codigo.lower()
                ).first()

                if sku:
                    changed = False
                    if sku.codigo_erp != codigo:
                        sku.codigo_erp = codigo
                        changed = True
                    if sku.unidade != unidade:
                        sku.unidade = unidade
                        changed = True
                    if changed:
                        sku.atualizado_em = datetime.now()
                        atualizados += 1
                else:
                    sku = WMSSkuMestre(
                        codigo_item=codigo,
                        codigo_erp=codigo,
                        unidade=unidade,
                        fator_conversao=1.0,
                        curva_abc="C",
                        politica_validade="FIFO",
                        estoque_minimo=0,
                        estoque_maximo=0,
                        endereco_preferencial=(item.get("localizacao_estoque") or "").strip() or None,
                        ativo=True,
                    )
                    db.session.add(sku)
                    criados += 1
            except Exception as exc:
                erros.append({"codigo": codigo, "erro": str(exc)})

        db.session.commit()
        return {"criados": criados, "atualizados": atualizados, "erros": erros}

    # ── divergências ERP x WMS ───────────────────────────────────────────

    @classmethod
    def gerar_divergencias(cls, itens_erp):
        novas = 0
        for item in itens_erp:
            codigo = (item.get("codigo_interno") or "").strip()
            if not codigo:
                continue

            qtd_erp = float(item.get("qtde_total") or 0)

            # soma do estoque WMS para esse SKU
            qtd_wms = (
                db.session.query(func.coalesce(func.sum(EstoqueWMS.qtd_total), 0))
                .filter(func.lower(func.trim(EstoqueWMS.codigo_item)) == codigo.lower())
                .scalar()
            ) or 0
            qtd_wms = float(qtd_wms)

            diferenca = qtd_erp - qtd_wms
            if abs(diferenca) < 0.01:
                continue

            # Verifica se já existe divergência aberta para esse SKU
            existente = WMSReconciliacaoDivergencia.query.filter(
                func.lower(func.trim(WMSReconciliacaoDivergencia.codigo_item)) == codigo.lower(),
                WMSReconciliacaoDivergencia.numero_nota == "ERP_SYNC",
                WMSReconciliacaoDivergencia.status.in_(["Aberta", "Tratando"]),
            ).first()

            if existente:
                existente.qtd_erp = qtd_erp
                existente.qtd_wms = qtd_wms
                existente.diferenca = diferenca
            else:
                db.session.add(
                    WMSReconciliacaoDivergencia(
                        numero_nota="ERP_SYNC",
                        codigo_item=codigo,
                        qtd_erp=qtd_erp,
                        qtd_wms=qtd_wms,
                        diferenca=diferenca,
                        status="Aberta",
                        origem="ERPSync",
                        observacao=f"Divergência detectada na sincronização ERP. ERP={qtd_erp} WMS={qtd_wms}",
                    )
                )
                novas += 1

        db.session.commit()
        return novas

    # ── alertas ──────────────────────────────────────────────────────────

    @classmethod
    def gerar_alertas_estoque(cls, itens_erp):
        alertas_criados = 0
        for item in itens_erp:
            codigo = (item.get("codigo_interno") or "").strip()
            if not codigo:
                continue

            qtd_disponivel = float(item.get("qtde_disponivel") or 0)
            sku = WMSSkuMestre.query.filter(
                func.lower(func.trim(WMSSkuMestre.codigo_item)) == codigo.lower()
            ).first()

            if not sku or not sku.estoque_minimo or sku.estoque_minimo <= 0:
                continue

            if qtd_disponivel >= sku.estoque_minimo:
                continue

            # Verifica se já existe alerta aberto
            existente = WMSAlertaOperacional.query.filter_by(
                tipo="Ruptura",
                referencia=codigo,
                status="Aberto",
            ).first()
            if existente:
                continue

            db.session.add(
                WMSAlertaOperacional(
                    tipo="Ruptura",
                    severidade="ALTA",
                    referencia=codigo,
                    descricao=f"Estoque abaixo do mínimo. Disponível no ERP: {qtd_disponivel}, mínimo: {sku.estoque_minimo}",
                    status="Aberto",
                )
            )
            alertas_criados += 1

        db.session.commit()
        return alertas_criados

    # ── popular estoque WMS com localização ERP ──────────────────────────

    @classmethod
    def _obter_ou_criar_localizacao(cls, loc_str):
        """Busca ou cria uma LocalizacaoArmazem para a string de localização do ERP."""
        loc_str = (loc_str or "").strip()
        if not loc_str:
            return None

        codigo_upper = loc_str.upper()

        # Tenta match direto pelo código (qualquer estado)
        loc = LocalizacaoArmazem.query.filter(
            func.upper(func.trim(LocalizacaoArmazem.codigo)) == codigo_upper
        ).first()
        if loc:
            if not loc.ativo:
                loc.ativo = True
            return loc

        # Deposito padrão (AL)
        deposito = DepositoWMS.query.filter_by(codigo="AL", ativo=True).first()
        if not deposito:
            deposito = DepositoWMS.query.filter_by(ativo=True).first()
        if not deposito:
            return None

        # Cria localização simples com o código do ERP
        try:
            loc = LocalizacaoArmazem(
                codigo=codigo_upper,
                deposito_id=deposito.id,
                rua=codigo_upper,
                predio="01",
                nivel="01",
                apartamento="",
                corredor=codigo_upper[:10],
                prateleira="01",
                posicao="01",
                capacidade_maxima=999999.0,
                capacidade_atual=0.0,
                ativo=True,
            )
            db.session.add(loc)
            db.session.flush()
            return loc
        except Exception:
            db.session.rollback()
            # Retry lookup after rollback (race condition or case mismatch)
            return LocalizacaoArmazem.query.filter(
                func.upper(func.trim(LocalizacaoArmazem.codigo)) == codigo_upper
            ).first()

    @classmethod
    def popular_estoque_wms(cls, itens_erp):
        """Cria/atualiza registros em EstoqueWMS para itens ERP que têm localização."""
        endereçados = 0
        pares_erp = {
            ((item.get("codigo_interno") or "").strip().lower(), (item.get("localizacao_estoque") or "").strip().upper())
            for item in itens_erp
            if (item.get("codigo_interno") or "").strip() and (item.get("localizacao_estoque") or "").strip()
        }
        codigos_erp_ativos = cls._ultimos_codigos_erp_ativos or {codigo for codigo, _loc in pares_erp}
        cls._remover_estoque_obsoleto(codigos_erp_ativos, pares_erp)

        for item in itens_erp:
            codigo = (item.get("codigo_interno") or "").strip()
            loc_str = (item.get("localizacao_estoque") or "").strip()
            if not codigo or not loc_str:
                continue

            qtd_total = float(item.get("qtde_total") or 0)
            qtd_reservada = float(item.get("qtde_reservada") or 0)

            try:
                loc = cls._obter_ou_criar_localizacao(loc_str)
                if not loc:
                    continue

                estoque = EstoqueWMS.query.filter(
                    func.lower(func.trim(EstoqueWMS.codigo_item)) == codigo.lower(),
                    EstoqueWMS.localizacao_id == loc.id,
                ).first()

                if estoque:
                    estoque.qtd_total = qtd_total
                    estoque.qtd_separada = qtd_reservada
                    estoque.data_atualizacao = datetime.now()
                else:
                    estoque = EstoqueWMS(
                        codigo_item=codigo,
                        localizacao_id=loc.id,
                        qtd_total=qtd_total,
                        qtd_separada=qtd_reservada,
                        qtd_bloqueada=0.0,
                    )
                    db.session.add(estoque)
                    endereçados += 1

                db.session.commit()
            except Exception as exc:
                db.session.rollback()
                logger.warning("Erro ao popular estoque para %s@%s: %s", codigo, loc_str, exc)

        return endereçados

    # ── sincronização completa ───────────────────────────────────────────

    @classmethod
    def comparar_estoque_erp_wms(cls, itens_erp, filtro=None, limite=100):
        """Compara snapshot vivo do ERP com o saldo consolidado local do WMS."""
        filtro_norm = (filtro or "").strip().lower()

        def _chave(codigo, localizacao):
            return ((codigo or "").strip().lower(), (localizacao or "").strip().upper())

        def _inclui(codigo, localizacao, descricao=""):
            if not filtro_norm:
                return True
            texto = " ".join([str(codigo or ""), str(localizacao or ""), str(descricao or "")]).lower()
            return filtro_norm in texto

        erp_por_chave = {}
        for item in itens_erp or []:
            codigo = (item.get("codigo_interno") or "").strip()
            localizacao = (item.get("localizacao_estoque") or "").strip()
            if not codigo or not localizacao or not _inclui(codigo, localizacao, item.get("item")):
                continue
            chave = _chave(codigo, localizacao)
            atual = erp_por_chave.setdefault(
                chave,
                {
                    "codigo_item": codigo,
                    "localizacao": localizacao.upper(),
                    "descricao": item.get("item") or "",
                    "qtd_erp": 0.0,
                    "qtd_reservada_erp": 0.0,
                },
            )
            atual["qtd_erp"] += float(item.get("qtde_total") or 0)
            atual["qtd_reservada_erp"] += float(item.get("qtde_reservada") or 0)

        wms_por_chave = {}
        registros = (
            db.session.query(EstoqueWMS, LocalizacaoArmazem)
            .join(LocalizacaoArmazem, EstoqueWMS.localizacao_id == LocalizacaoArmazem.id)
            .all()
        )
        for estoque, loc in registros:
            codigo = (estoque.codigo_item or "").strip()
            localizacao = (loc.codigo or "").strip()
            if not codigo or not localizacao or not _inclui(codigo, localizacao):
                continue
            chave = _chave(codigo, localizacao)
            atual = wms_por_chave.setdefault(
                chave,
                {
                    "codigo_item": codigo,
                    "localizacao": localizacao.upper(),
                    "qtd_wms": 0.0,
                    "qtd_separada_wms": 0.0,
                },
            )
            atual["qtd_wms"] += float(estoque.qtd_total or 0)
            atual["qtd_separada_wms"] += float(estoque.qtd_separada or 0)

        divergencias = []
        resumo = {
            "faltando_no_wms": 0,
            "sobrando_no_wms": 0,
            "quantidade_divergente": 0,
            "ok": 0,
            "total_divergencias": 0,
        }
        for chave in sorted(set(erp_por_chave) | set(wms_por_chave)):
            erp = erp_por_chave.get(chave)
            wms = wms_por_chave.get(chave)
            qtd_erp = float((erp or {}).get("qtd_erp") or 0)
            qtd_wms = float((wms or {}).get("qtd_wms") or 0)
            diferenca = round(qtd_wms - qtd_erp, 6)

            if erp and wms and abs(diferenca) < 0.000001:
                resumo["ok"] += 1
                continue
            if erp and not wms:
                tipo = "FALTANDO_NO_WMS"
                resumo["faltando_no_wms"] += 1
            elif wms and not erp:
                tipo = "SOBRANDO_NO_WMS"
                resumo["sobrando_no_wms"] += 1
            else:
                tipo = "QUANTIDADE_DIVERGENTE"
                resumo["quantidade_divergente"] += 1

            base = erp or wms or {}
            divergencias.append(
                {
                    "tipo": tipo,
                    "codigo_item": base.get("codigo_item") or chave[0],
                    "localizacao": base.get("localizacao") or chave[1],
                    "descricao": (erp or {}).get("descricao") or "",
                    "qtd_erp": qtd_erp,
                    "qtd_wms": qtd_wms,
                    "diferenca_wms_menos_erp": diferenca,
                    "qtd_reservada_erp": float((erp or {}).get("qtd_reservada_erp") or 0),
                    "qtd_separada_wms": float((wms or {}).get("qtd_separada_wms") or 0),
                }
            )

        resumo["total_divergencias"] = len(divergencias)
        return {
            "resumo": resumo,
            "divergencias": divergencias[: max(int(limite or 100), 0)],
            "limite": limite,
            "total_erp": len(erp_por_chave),
            "total_wms": len(wms_por_chave),
        }

    @classmethod
    def diagnosticar_estoque_postgres_vs_wms(cls, filtro=None, limite=100):
        itens_erp = cls.buscar_estoque_erp()
        resultado = cls.comparar_estoque_erp_wms(itens_erp, filtro=filtro, limite=limite)
        resultado["total_itens_erp_lidos"] = len(itens_erp)
        resultado["gerado_em"] = datetime.now().isoformat()
        return resultado

    @classmethod
    def _remover_estoque_obsoleto(cls, codigos_erp_ativos, pares_erp):
        if not codigos_erp_ativos:
            return 0

        registros = (
            db.session.query(EstoqueWMS, LocalizacaoArmazem)
            .join(LocalizacaoArmazem, EstoqueWMS.localizacao_id == LocalizacaoArmazem.id)
            .filter(func.lower(func.trim(EstoqueWMS.codigo_item)).in_(list(codigos_erp_ativos)))
            .all()
        )

        removidos = 0
        for estoque, loc in registros:
            par = ((estoque.codigo_item or "").strip().lower(), (loc.codigo or "").strip().upper())
            if par in pares_erp:
                continue
            db.session.delete(estoque)
            removidos += 1

        if removidos:
            db.session.commit()
        return removidos

    @classmethod
    def executar_sync_completo(cls):
        inicio = datetime.now()
        resultado = {
            "inicio": inicio.isoformat(),
            "total_itens_erp": 0,
            "skus": {},
            "enderecados": 0,
            "divergencias": 0,
            "alertas": 0,
            "erro": None,
        }

        # Registra evento de integração
        idempotency_key = f"ERP_SYNC_{inicio.strftime('%Y%m%d%H%M%S')}"
        evento = WMSIntegracaoEvento(
            idempotency_key=idempotency_key,
            tipo_evento="SyncEstoqueERP",
            referencia="ERP_ESTOQUE",
            origem="ERP",
            status="Processando",
            tentativas=1,
        )
        db.session.add(evento)
        db.session.commit()

        try:
            itens_erp = cls.buscar_estoque_erp()
            resultado["total_itens_erp"] = len(itens_erp)

            resultado["skus"] = cls.sincronizar_skus(itens_erp)
            resultado["enderecados"] = cls.popular_estoque_wms(itens_erp)
            resultado["divergencias"] = cls.gerar_divergencias(itens_erp)
            resultado["alertas"] = cls.gerar_alertas_estoque(itens_erp)

            evento.status = "Sucesso"
            evento.processado_em = datetime.now()
            evento.payload_json = json.dumps(resultado, ensure_ascii=False, default=str)
            db.session.commit()

        except Exception as exc:
            logger.exception("Erro na sincronização ERP→WMS")
            resultado["erro"] = str(exc)
            evento.status = "Falha"
            evento.ultima_erro = str(exc)[:500]
            db.session.commit()
            raise

        resultado["fim"] = datetime.now().isoformat()
        return resultado

    # ── consulta resumo do último sync ───────────────────────────────────

    @classmethod
    def obter_ultimo_sync(cls):
        evento = (
            WMSIntegracaoEvento.query
            .filter_by(tipo_evento="SyncEstoqueERP")
            .order_by(WMSIntegracaoEvento.criado_em.desc())
            .first()
        )
        if not evento:
            return None
        return {
            "id": evento.id,
            "status": evento.status,
            "criado_em": evento.criado_em.isoformat() if evento.criado_em else None,
            "processado_em": evento.processado_em.isoformat() if evento.processado_em else None,
            "erro": evento.ultima_erro,
        }

    # ── consulta estoque ERP (somente leitura / preview) ─────────────────

    @classmethod
    def preview_estoque_erp(cls, filtro=None, limite=100):
        itens = cls.buscar_estoque_erp()
        if filtro:
            filtro_lower = filtro.lower()
            itens = [
                i for i in itens
                if filtro_lower in (i.get("codigo_interno") or "").lower()
                or filtro_lower in (i.get("item") or "").lower()
                or filtro_lower in (i.get("familia") or "").lower()
                or filtro_lower in (i.get("grupo") or "").lower()
            ]
        return {
            "total": len(itens),
            "itens": itens[:limite],
        }
