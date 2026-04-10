"""
Serviço de sincronização de estoque ERP → WMS
Busca dados de estoque do endpoint ERP e atualiza cadastro mestre (SKU),
gera divergências e alertas operacionais.
"""
import json
import logging
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

    ERP_HEADERS = {
        "ngrok-skip-browser-warning": "1",
        "User-Agent": "ColumbiaSyncBot/1.0",
    }

    # ── helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _get_url():
        return current_app.config.get("ERP_ESTOQUE_URL", "").strip()

    @staticmethod
    def _get_timeout():
        return current_app.config.get("ERP_ESTOQUE_TIMEOUT", 30)

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

    # ── buscar dados do ERP ──────────────────────────────────────────────

    @classmethod
    def buscar_estoque_erp(cls):
        url = cls._get_url()
        if not url:
            raise ValueError("ERP_ESTOQUE_URL não configurada.")

        resp = requests.get(url, headers=cls.ERP_HEADERS, timeout=cls._get_timeout())
        resp.raise_for_status()
        dados = resp.json()

        if isinstance(dados, dict):
            dados = dados.get("itens") or dados.get("data") or dados.get("estoque") or []

        if not isinstance(dados, list):
            raise ValueError("Formato inesperado do endpoint ERP (esperava lista).")

        return dados

    # ── sincronizar SKU mestre ───────────────────────────────────────────

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
        for item in itens_erp:
            codigo = (item.get("codigo_interno") or "").strip()
            loc_str = (item.get("localizacao_estoque") or "").strip()
            if not codigo or not loc_str:
                continue

            qtd_total = float(item.get("qtde_total") or 0)
            qtd_reservada = float(item.get("qtde_reservada") or 0)
            if qtd_total <= 0:
                continue

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
