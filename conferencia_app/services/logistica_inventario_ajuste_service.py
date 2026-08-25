"""Fluxo de ajuste de estoque do Inventario (Logistica) - mesma ideia de
workflow por status_modulo do modulo Comex, reduzido a 4 etapas:

    Modulo 01 (Contagem inicial) -> ja existe (LogisticaInventarioInicial).
    Modulo 02 (Validacao) -> gestor confirma a divergencia detectada
        automaticamente contra o GRV, antes de pedir o ajuste.
    Modulo 03 (Finance) -> so tracking de status: ajuste de estoque
        acontece FORA do sync (direto no ERP); aqui so confirma execucao.
    Modulo 04 (Fiscal) -> so tracking de status: emissao de NF de ajuste
        acontece FORA do sync; aqui so confirma emissao.

Ver COMEX_ESPECIFICACAO.md pro workflow que serviu de modelo.
"""
from __future__ import annotations

from datetime import datetime

from ..extensions import db
from ..models import LogisticaInventarioAjuste, LogisticaInventarioInicial

STATUS_SLUGS = {
    "Validacao": "validacao",
    "Finance": "finance",
    "Fiscal": "fiscal",
    "Concluido": "concluido",
    "Descartado": "descartado",
}

# Sequencia oficial do workflow (usada pela funcao "Pular Etapa" - mesma
# ideia do "Pular Status" do Comex). "Descartado" fica de fora: e' um ramo
# terminal que so sai de "Validacao" via descartar_divergencia, nao faz
# parte do avanco normal.
MODULOS_SEQUENCIA = ["Validacao", "Finance", "Fiscal", "Concluido"]

TOLERANCIA_DIVERGENCIA = 0.001


def status_slug(status_modulo: str) -> str:
    return STATUS_SLUGS.get(status_modulo, "validacao")


def _proximo_modulo(atual: str) -> str | None:
    try:
        idx = MODULOS_SEQUENCIA.index(atual)
    except ValueError:
        return None
    if idx + 1 >= len(MODULOS_SEQUENCIA):
        return None
    return MODULOS_SEQUENCIA[idx + 1]


def ajuste_aberto_para(codigo_produto: str, local_codigo: str) -> LogisticaInventarioAjuste | None:
    """Ajuste ja em andamento (nao concluido/descartado) pro mesmo
    codigo+local - evita abrir duplicado se o item continuar divergente em
    contagens seguintes enquanto o primeiro ainda esta em analise."""
    return (
        LogisticaInventarioAjuste.query
        .filter_by(codigo_produto=codigo_produto, local_codigo=local_codigo)
        .filter(LogisticaInventarioAjuste.status_modulo.notin_(("Concluido", "Descartado")))
        .order_by(LogisticaInventarioAjuste.id.desc())
        .first()
    )


def detectar_divergencia(
    contagem: LogisticaInventarioInicial,
    qtde_grv: float | None,
) -> LogisticaInventarioAjuste | None:
    """Chamado logo apos salvar uma contagem (Modulo 01). Se `qtde_grv` for
    None (codigo nao encontrado no GRV, ou API do GRV fora do ar), nao ha
    base de comparacao - nao cria ajuste. Se ja existir um ajuste aberto
    pro mesmo item+local, nao duplica (so o primeiro fica em fila pro
    gestor)."""
    if qtde_grv is None:
        return None
    diferenca = float(contagem.quantidade or 0) - float(qtde_grv)
    if abs(diferenca) <= TOLERANCIA_DIVERGENCIA:
        return None
    if ajuste_aberto_para(contagem.codigo_produto, contagem.local_codigo):
        return None

    ajuste = LogisticaInventarioAjuste(
        contagem_id=contagem.id,
        codigo_produto=contagem.codigo_produto,
        local_codigo=contagem.local_codigo,
        unidade_medida=contagem.unidade_medida,
        qtde_contada=float(contagem.quantidade or 0),
        qtde_estoque_no_momento=float(qtde_grv),
        diferenca=diferenca,
        status_modulo="Validacao",
        status_slug=status_slug("Validacao"),
    )
    db.session.add(ajuste)
    db.session.commit()
    return ajuste


def listar_ajustes(status_modulo: str | None = None) -> list[LogisticaInventarioAjuste]:
    query = LogisticaInventarioAjuste.query
    if status_modulo:
        query = query.filter_by(status_modulo=status_modulo)
    return query.order_by(LogisticaInventarioAjuste.criado_em.desc()).all()


def confirmar_divergencia(ajuste: LogisticaInventarioAjuste, usuario: str, justificativa: str | None = None) -> LogisticaInventarioAjuste:
    """Modulo 02 -> Modulo 03. Gestor confirma que a diferenca e' real e
    precisa de ajuste - dispara pro Finance."""
    if ajuste.status_modulo != "Validacao":
        raise ValueError("Este ajuste não está aguardando validação do gestor.")
    ajuste.gestor_justificativa = (justificativa or "").strip()[:500] or None
    ajuste.gestor_confirmado_em = datetime.now()
    ajuste.gestor_confirmado_por = usuario
    ajuste.status_modulo = "Finance"
    ajuste.status_slug = status_slug("Finance")
    db.session.commit()
    return ajuste


def descartar_divergencia(ajuste: LogisticaInventarioAjuste, usuario: str, motivo: str) -> LogisticaInventarioAjuste:
    """Modulo 02 -> Descartado. Pra quando a divergencia nao e' real (ex.:
    erro de contagem) e nao precisa de ajuste nenhum - encerra sem passar
    por Finance/Fiscal."""
    if ajuste.status_modulo != "Validacao":
        raise ValueError("Este ajuste não está aguardando validação do gestor.")
    motivo = (motivo or "").strip()
    if not motivo:
        raise ValueError("Informe o motivo pra marcar como improcedente.")
    ajuste.gestor_justificativa = motivo[:500]
    ajuste.gestor_confirmado_em = datetime.now()
    ajuste.gestor_confirmado_por = usuario
    ajuste.status_modulo = "Descartado"
    ajuste.status_slug = status_slug("Descartado")
    db.session.commit()
    return ajuste


def concluir_finance(ajuste: LogisticaInventarioAjuste, usuario: str, observacao: str | None = None) -> LogisticaInventarioAjuste:
    """Modulo 03 -> Modulo 04. So tracking: confirma que o ajuste de
    estoque foi executado fora do sync e libera pro Fiscal."""
    if ajuste.status_modulo != "Finance":
        raise ValueError("Este ajuste não está com o Finance.")
    ajuste.finance_observacao = (observacao or "").strip()[:500] or None
    ajuste.finance_concluido_em = datetime.now()
    ajuste.finance_concluido_por = usuario
    ajuste.status_modulo = "Fiscal"
    ajuste.status_slug = status_slug("Fiscal")
    db.session.commit()
    return ajuste


def concluir_fiscal(ajuste: LogisticaInventarioAjuste, usuario: str, nf_numero: str | None = None) -> LogisticaInventarioAjuste:
    """Modulo 04 -> Concluido. So tracking: confirma que a NF de ajuste foi
    emitida fora do sync."""
    if ajuste.status_modulo != "Fiscal":
        raise ValueError("Este ajuste não está com o Fiscal.")
    ajuste.fiscal_nf_numero = (nf_numero or "").strip()[:60] or None
    ajuste.fiscal_concluido_em = datetime.now()
    ajuste.fiscal_concluido_por = usuario
    ajuste.status_modulo = "Concluido"
    ajuste.status_slug = status_slug("Concluido")
    db.session.commit()
    return ajuste


def pular_etapa(ajuste: LogisticaInventarioAjuste, usuario: str) -> LogisticaInventarioAjuste:
    """Funcao "Pular Etapa" (requisito geral do Inventario, espelho do
    "Pular Status" do Comex): avanca o ajuste manualmente pra proxima etapa
    do workflow (Validacao -> Finance -> Fiscal -> Concluido), IGNORANDO as
    validacoes normais de cada modulo (justificativa do gestor, observacao
    do Finance, numero da NF do Fiscal) - reservada pra ajustes
    excepcionais (ex.: decisao tomada fora do sistema, correcao rapida de
    fluxo) e exige permissao extra de gerencia
    (PAGE_LOGISTICA_INVENTARIO_PULAR_ETAPA), alem do acesso normal ao
    modulo. Nao pula pra fora do fluxo (nao reabre um "Descartado")."""
    if ajuste.status_modulo == "Descartado":
        raise ValueError("Essa diferença foi marcada como improcedente - não há como avançar.")
    proximo = _proximo_modulo(ajuste.status_modulo)
    if proximo is None:
        raise ValueError("Este ajuste já está na última etapa (Concluído) - não há como avançar mais.")

    agora = datetime.now()
    usuario = usuario or "desconhecido"
    # Preenche os campos de rastreio da etapa que esta sendo pulada, so pra
    # nao deixar a auditoria com "quem/quando" em branco pra uma etapa que
    # o registro diz ter passado - mesma logica de nao perder historico que
    # o restante do fluxo normal já segue.
    if proximo == "Finance":
        ajuste.gestor_confirmado_em = ajuste.gestor_confirmado_em or agora
        ajuste.gestor_confirmado_por = ajuste.gestor_confirmado_por or usuario
    elif proximo == "Fiscal":
        ajuste.finance_concluido_em = ajuste.finance_concluido_em or agora
        ajuste.finance_concluido_por = ajuste.finance_concluido_por or usuario
    elif proximo == "Concluido":
        ajuste.fiscal_concluido_em = ajuste.fiscal_concluido_em or agora
        ajuste.fiscal_concluido_por = ajuste.fiscal_concluido_por or usuario

    ajuste.status_modulo = proximo
    ajuste.status_slug = status_slug(proximo)
    db.session.commit()
    return ajuste
