"""Análise das solicitações de compra abertas no GRV (pedido de Compras em
18/11/2026: "aparece um monte de solicitação que às vezes nem precisa").

Para cada solicitação pendente ou aprovada sem OC, diz se faz sentido pelo
planejamento do Estoque, quanto custa o que foi pedido e quanto custaria o
certo. Só leitura: a correção continua no GRV.

Regra (mínimo/máximo do planejamento do Sync): posição = disponível de agora
+ pendente das OCs abertas. Só compra se a posição estiver abaixo do mínimo
sugerido; aí o certo é o que leva a posição ao máximo. Várias solicitações do
mesmo item dividem esse certo, da mais antiga para a mais nova.

O mínimo e o máximo vêm das fotos do Estoque (as contas pesam ~0,5 s por
visão); saldo e OCs são consultados na hora, porque a foto pode ter dias e a
solicitação é de hoje."""

from __future__ import annotations

import math
from datetime import timedelta

from flask import current_app

from . import estoque_planejamento_service as planejamento_svc
from ..tempo import agora_br

# Aprovada sem OC mais velha que isso é cotação abandonada: fica de fora.
APROVADAS_JANELA_DIAS = 180

VEREDITOS = {
    "nao_precisa": "Não precisa",
    "acima": "Acima do necessário",
    "ok": "Faz sentido",
    "abaixo": "Abaixo do necessário",
    "sem_base": "Sem base para avaliar",
}


def _f(valor) -> float:
    try:
        return float(valor or 0)
    except (TypeError, ValueError):
        return 0.0


def _n(valor) -> str:
    v = float(valor or 0)
    casas = 0 if abs(v - round(v)) < 1e-9 else 2
    return f"{v:,.{casas}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _buscar_solicitacoes() -> list[dict]:
    from ..compras import queries
    from ..compras.db import fetch_all
    desde = (agora_br().date() - timedelta(days=APROVADAS_JANELA_DIAS)).isoformat()
    try:
        return fetch_all(queries.SQL_COMPRAS_SOLICITACOES_ABERTAS, {"cod_empresa": 1, "aprovadas_desde": desde})
    except Exception:
        current_app.logger.warning("Solicitações de compra: GRV não respondeu.", exc_info=True)
        raise ValueError("Não deu para consultar as solicitações no GRV agora. Tente de novo em instantes.") from None


def _planos() -> tuple[dict, dict]:
    """codigo (sem pontuação) -> {row, plano, custo} das visões com
    planejamento, e a situação das fotos usadas."""
    from ..routes.logistica_inventario_routes import ESTOQUE_VISOES, VISOES_PLANEJAMENTO, _base_painel
    parametros = planejamento_svc.obter_parametros()
    meses = planejamento_svc._meses_fechados(agora_br().date(), 12)
    visoes = [v for v in ESTOQUE_VISOES if v in VISOES_PLANEJAMENTO]
    base = _base_painel(visoes, set(), parametros, meses)
    return {planejamento_svc.chave_codigo(i["row"]["codigo"]): i for i in base["planos"]}, base["fotos"]


def _ao_vivo(codigos: list[str]) -> tuple[dict, dict | None]:
    """Saldo e OCs abertas de agora. OC que não vier fica None (usa a da foto)."""
    from .erp_estoque_service import buscar_estoque_grv, buscar_ordens_compra_abertas_grv
    saldos = {}
    try:
        saldos = {planejamento_svc.chave_codigo(c): item for c, item in (buscar_estoque_grv().get("por_codigo") or {}).items()}
    except Exception:
        current_app.logger.warning("Solicitações de compra: saldo do GRV indisponível.", exc_info=True)
    ocs = None
    try:
        ocs = buscar_ordens_compra_abertas_grv(codigos=codigos)
    except Exception:
        current_app.logger.warning("Solicitações de compra: OCs abertas indisponíveis.", exc_info=True)
    return saldos, ocs


def _necessidade(chave: str, plano_item: dict | None, saldo: dict | None, ocs: dict | None, unidade: str) -> dict:
    """O certo a comprar do item inteiro (todas as solicitações dele)."""
    if not plano_item:
        return {"certo": None, "motivo": "Família fora do planejamento do Estoque."}
    row, plano = plano_item["row"], plano_item["plano"]
    disponivel = _f(saldo.get("qtde_disponivel")) if saldo else _f(row.get("saldo_disponivel"))
    if ocs is not None:
        em_oc = sum(_f(oc.get("quantidade_pendente")) for oc in ocs.get(chave) or [])
    else:
        em_oc = _f(row.get("quantidade_pendente_oc"))
    posicao = disponivel + em_oc
    base = {"disponivel": disponivel, "em_oc": em_oc, "posicao": posicao,
            "minimo": plano.get("estoque_minimo_sugerido"), "maximo": plano.get("estoque_maximo"),
            "tipo_demanda": plano.get("tipo_demanda_label") or plano.get("tipo_demanda")}
    un = f" {unidade}" if unidade else ""
    oc_txt = f" ({_n(disponivel)} disponível + {_n(em_oc)} em OC)" if em_oc > 0 else ""
    if plano.get("nao_comprar"):
        return {**base, "certo": 0.0, "motivo": "Item marcado para não comprar mais."}
    if plano.get("tipo_demanda") == "sem_giro":
        if not plano.get("primeira_movimentacao") and disponivel <= 0 and em_oc <= 0:
            # Nunca entrou nem saiu: o planejamento chama de "sem giro", mas é
            # a primeira compra (ferramentas novas 28-01-00768..772).
            return {**base, "certo": None, "motivo": "Primeira compra: o item nunca teve movimentação."}
        return {**base, "certo": 0.0,
                "motivo": f"Sem saída em {plano.get('janela_meses')} meses. Só compre se for para um pedido ou OS."}
    minimo, maximo = base["minimo"], base["maximo"]
    if minimo is None or maximo is None:
        return {**base, "certo": None, "motivo": "Item novo, ainda sem histórico para avaliar."}
    if posicao >= _f(minimo):
        return {**base, "certo": 0.0,
                "motivo": f"Posição de {_n(posicao)}{un}{oc_txt} já cobre o mínimo de {_n(minimo)}."}
    certo = _f(maximo) - posicao
    if str(unidade or "").strip().upper() in planejamento_svc.UNIDADES_INTEIRAS:
        certo = float(math.ceil(certo - 1e-9))
    return {**base, "certo": max(0.0, certo),
            "motivo": f"Posição de {_n(posicao)}{un}{oc_txt} abaixo do mínimo de {_n(minimo)}: comprar {_n(certo)} leva ao máximo de {_n(maximo)}."}


def analisar() -> dict:
    linhas = _buscar_solicitacoes()
    planos, fotos = _planos()
    codigos = sorted({str(l.get("codigo") or "").strip() for l in linhas if l.get("codigo")})
    saldos, ocs = _ao_vivo(codigos)

    por_item: dict[str, list[dict]] = {}
    for l in linhas:
        por_item.setdefault(planejamento_svc.chave_codigo(l.get("codigo")), []).append(l)

    itens = []
    for chave, solicitacoes in por_item.items():
        plano_item = planos.get(chave)
        unidade = str(solicitacoes[0].get("unidade") or "").strip()
        nec = _necessidade(chave, plano_item, saldos.get(chave), ocs, unidade)
        restante = nec["certo"]
        custo_plano = _f((plano_item or {}).get("custo"))
        # Da mais antiga para a mais nova: a primeira leva o certo.
        solicitacoes.sort(key=lambda s: (str(s.get("dt_solicitacao") or ""), s.get("cod_solicitacao") or 0))
        de_estoque = [s for s in solicitacoes if not s.get("cod_os")]
        for s in solicitacoes:
            pedido = max(0.0, _f(s.get("qtde")) - _f(s.get("qtde_entregue")))
            custo = custo_plano or _f(s.get("preco_custo")) or None
            para_os = bool(s.get("cod_os"))
            if para_os:
                # Necessidade de OS não passa pelo mínimo/máximo do estoque.
                certo, motivo = None, "Solicitação para OS: a necessidade vem da OS, não do estoque."
            elif restante is None:
                certo, motivo = None, nec["motivo"]
            else:
                certo = restante if s is de_estoque[-1] else min(pedido, restante)
                restante -= certo
                motivo = nec["motivo"]
                if len(de_estoque) > 1:
                    motivo += f" O item está em {len(de_estoque)} solicitações abertas."
            if certo is None:
                veredito = "sem_base"
            elif certo <= 1e-9:
                veredito = "nao_precisa"
            elif abs(pedido - certo) <= 1e-9:
                veredito = "ok"
            else:
                veredito = "acima" if pedido > certo else "abaixo"
            valor_pedido = round(pedido * custo, 2) if custo else None
            valor_certo = round(certo * custo, 2) if custo and certo is not None else None
            itens.append({
                "solicitacao": s.get("cod_solicitacao"),
                "situacao": s.get("situacao"),
                "em_cotacao": bool(s.get("em_cotacao")),
                "data": str(s.get("dt_solicitacao") or "")[:10] or None,
                "necessidade": str(s.get("dt_necessidade") or "")[:10] or None,
                "solicitante": str(s.get("solicitante") or "").strip(),
                "codigo": str(s.get("codigo") or "").strip(),
                "descricao": str(s.get("descricao") or "").strip(),
                "familia": str(s.get("familia") or "").strip(),
                "unidade": unidade,
                "cod_os": s.get("cod_os"),
                "obs": str(s.get("obs") or s.get("obs_sistema") or "").strip(),
                "pedido": pedido,
                "certo": certo,
                "custo": custo,
                "valor_pedido": valor_pedido,
                "valor_certo": valor_certo,
                "excesso_valor": round(max(0.0, valor_pedido - valor_certo), 2) if valor_pedido is not None and valor_certo is not None else None,
                "veredito": veredito,
                "veredito_label": VEREDITOS[veredito],
                "motivo": motivo,
                "disponivel": nec.get("disponivel"),
                "em_oc": nec.get("em_oc"),
                "minimo": nec.get("minimo"),
                "maximo": nec.get("maximo"),
                "tipo_demanda": nec.get("tipo_demanda"),
                "grv_minimo": _f((saldos.get(chave) or {}).get("estoque_minimo")) or None,
                "grv_lote": _f((saldos.get(chave) or {}).get("lote_economico")) or None,
            })

    ordem = {"nao_precisa": 0, "acima": 1, "abaixo": 2, "ok": 3, "sem_base": 4}
    itens.sort(key=lambda x: (ordem[x["veredito"]], -(x["excesso_valor"] or 0), x["data"] or ""))
    avaliados = [x for x in itens if x["veredito"] != "sem_base"]
    resumo = {
        "linhas": len(itens),
        "por_veredito": {v: sum(1 for x in itens if x["veredito"] == v) for v in VEREDITOS},
        "valor_pedido": round(sum(x["valor_pedido"] or 0 for x in itens), 2),
        "valor_pedido_avaliado": round(sum(x["valor_pedido"] or 0 for x in avaliados), 2),
        "valor_certo": round(sum(x["valor_certo"] or 0 for x in avaliados), 2),
        "excesso_valor": round(sum(x["excesso_valor"] or 0 for x in avaliados), 2),
        "sem_custo": sum(1 for x in itens if not x["custo"]),
    }
    return {
        "itens": itens,
        "resumo": resumo,
        "fotos": fotos,
        "ocs_da_foto": ocs is None,
        "aprovadas_janela_dias": APROVADAS_JANELA_DIAS,
        "gerado_em": agora_br().strftime("%d/%m/%Y %H:%M"),
    }
