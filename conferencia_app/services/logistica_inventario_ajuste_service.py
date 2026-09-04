"""Fluxo de ajuste de estoque do Inventario (Logistica) - mesma ideia de
workflow por status_modulo do modulo Comex:

    Modulo 01 (Contagem inicial) -> ja existe (LogisticaInventarioInicial).
    Modulo 02 (Validacao) -> gestor confirma que a divergencia detectada
        automaticamente contra o GRV e' de verdade (ou descarta, se nao
        for) - ainda NAO gera o relatorio formal.
    Modulo 03 (Relatorio) -> itens ja confirmados como divergencia real,
        aguardando entrar num FORM-08.52 (Ajuste para Faturamento): o
        gestor seleciona varios de uma vez (checkbox) e gera o PDF - so'
        ai' vao pro Finance juntos (ver gerar_relatorio_ajuste).
    Modulo 04 (Finance) -> so tracking de status: ajuste de estoque
        acontece FORA do sync (direto no ERP); aqui so confirma execucao.
    Modulo 05 (Fiscal) -> so tracking de status: emissao de NF de ajuste
        acontece FORA do sync; aqui so confirma emissao.

Ver COMEX_ESPECIFICACAO.md pro workflow que serviu de modelo.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import func

from ..extensions import db
from ..models import (
    LogisticaInventarioAjuste,
    LogisticaInventarioAnaliseCausa,
    LogisticaInventarioInicial,
    LogisticaInventarioRelatorioAjuste,
    RELATORIO_AJUSTE_DEPOSITO_TIPOS,
    RELATORIO_AJUSTE_MAX_ITENS,
    RELATORIO_AJUSTE_MOTIVOS,
    RELATORIO_AJUSTE_TIPOS,
)

STATUS_SLUGS = {
    "Validacao": "validacao",
    "Relatorio": "relatorio",
    "Finance": "finance",
    "Fiscal": "fiscal",
    "Concluido": "concluido",
    "Descartado": "descartado",
}

# Sequencia oficial do workflow (usada pela funcao "Pular Etapa" - mesma
# ideia do "Pular Status" do Comex). "Descartado" fica de fora: e' um ramo
# terminal que so sai de "Validacao" via descartar_divergencia, nao faz
# parte do avanco normal.
MODULOS_SEQUENCIA = ["Validacao", "Relatorio", "Finance", "Fiscal", "Concluido"]

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
    custo_medio: float | None = None,
) -> LogisticaInventarioAjuste | None:
    """Chamado logo apos salvar uma contagem (Modulo 01). Se `qtde_grv` for
    None (codigo nao encontrado no GRV, ou API do GRV fora do ar), nao ha
    base de comparacao - nao cria ajuste. Se ja existir um ajuste aberto
    pro mesmo item+local, nao duplica (so o primeiro fica em fila pro
    gestor). `custo_medio` (tproduto_deposito.custo_medio) e' opcional -
    fica None se o GRV nao tiver custo pro codigo - e vira snapshot no
    ajuste pra calcular o impacto financeiro (R$) da divergencia."""
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
        custo_medio=float(custo_medio) if custo_medio is not None else None,
        status_modulo="Validacao",
        status_slug=status_slug("Validacao"),
    )
    db.session.add(ajuste)
    db.session.commit()
    return ajuste


def listar_ajustes(status_modulo: str | None = None, busca: str = "") -> list[LogisticaInventarioAjuste]:
    query = LogisticaInventarioAjuste.query
    if status_modulo:
        query = query.filter_by(status_modulo=status_modulo)
    busca = (busca or "").strip()
    if busca:
        termo = f"%{busca}%"
        query = query.filter(
            db.or_(
                LogisticaInventarioAjuste.local_codigo.ilike(termo),
                LogisticaInventarioAjuste.codigo_produto.ilike(termo),
            )
        )
    return query.order_by(LogisticaInventarioAjuste.criado_em.desc()).all()


def confirmar_divergencia(
    ajuste: LogisticaInventarioAjuste,
    usuario: str,
    justificativa: str | None = None,
    solicitar_analise_causa: bool = False,
) -> LogisticaInventarioAjuste:
    """Modulo 02 -> Modulo 03. Gestor confirma que a diferenca e' real -
    NAO manda direto pro Finance: entra na fila de "Relatorio", aguardando
    ser agrupada num FORM-08.52 (ver gerar_relatorio_ajuste) antes de
    seguir pro Finance de verdade.

    `solicitar_analise_causa`: o gestor pode marcar, no mesmo passo, que
    esse item precisa de uma investigacao mais profunda do motivo (causa
    raiz). Isso NAO muda o fluxo normal do ajuste - so cria (ou reaproveita,
    se ja existir) uma entrada na fila separada de Analise de Causa Raiz,
    pro operador/gestor preencherem a analise detalhada depois, na propria
    tela, no ritmo deles, sem competir com Relatorio/Finance/Fiscal."""
    if ajuste.status_modulo != "Validacao":
        raise ValueError("Este ajuste não está aguardando validação do gestor.")
    ajuste.gestor_justificativa = (justificativa or "").strip()[:500] or None
    ajuste.gestor_confirmado_em = datetime.now()
    ajuste.gestor_confirmado_por = usuario
    ajuste.status_modulo = "Relatorio"
    ajuste.status_slug = status_slug("Relatorio")

    if solicitar_analise_causa and not ajuste.analise_causa:
        db.session.add(LogisticaInventarioAnaliseCausa(
            ajuste_id=ajuste.id,
            status="Pendente",
            solicitado_por=usuario,
        ))

    db.session.commit()
    return ajuste


def _proximo_numero_documento(mes: int, ano: int) -> tuple[int, str]:
    """Sequencial do FORM-08.52 reinicia a cada mes/ano - ex.: "09/2026-001",
    "09/2026-002", ..., depois "10/2026-001" no mes seguinte."""
    ultimo = (
        db.session.query(func.max(LogisticaInventarioRelatorioAjuste.sequencial))
        .filter_by(mes_referencia=mes, ano_referencia=ano)
        .scalar()
    )
    sequencial = (ultimo or 0) + 1
    return sequencial, f"{mes:02d}/{ano}-{sequencial:03d}"


def gerar_relatorio_ajuste(
    ajuste_ids: list[int],
    usuario: str,
    *,
    tipo_ajuste: str,
    motivo_ajuste: str,
    deposito_tipo: str,
    justificativas: dict | None = None,
    tipo_ajuste_detalhe: str | None = None,
    motivo_ajuste_detalhe: str | None = None,
    deposito_local: str | None = None,
    responsavel: str | None = None,
    solicitante: str | None = None,
    depto: str | None = None,
    observacoes_ajuste: str | None = None,
    observacoes_itens: str | None = None,
    solicitar_analise_causa: bool = False,
) -> LogisticaInventarioRelatorioAjuste:
    """Gera o FORM-08.52 (Ajuste para Faturamento) pra um LOTE de itens JA
    CONFIRMADOS como divergencia real (Modulo 03 - Relatorio, ver
    confirmar_divergencia) - o gestor seleciona varios de uma vez
    (checkbox), preenche esse formulario uma unica vez pro lote inteiro, e
    so' ENTAO todos vao pro Finance juntos, com o mesmo numero de
    documento (ver _proximo_numero_documento).

    O motivo detalhado NAO e' generico pro lote inteiro - e' atrelado a
    CADA item (`justificativas`: dict ajuste_id -> texto). Pre-carrega da
    justificativa dada na aprovacao (Modulo 02 - ver confirmar_divergencia,
    campo `gestor_justificativa`) quando o gestor nao mandar um texto novo
    pra aquele item; se o gestor editar aqui, o `gestor_justificativa` do
    ajuste e' atualizado com o texto final. Cada item selecionado PRECISA
    terminar com uma justificativa preenchida (existente ou nova) - e' isso
    que aparece atrelado ao item no PDF, nao mais um texto unico e generico
    pro documento inteiro. `motivo_ajuste_detalhe` (se informado) vira so'
    uma observacao geral/opcional do lote.

    `solicitar_analise_causa` se aplica a TODOS os ajustes do lote (mesma
    ideia de confirmar_divergencia, so que em lote - util quando o gestor
    so' percebe que precisa de investigacao mais profunda na hora de
    agrupar o relatorio, nao antes)."""
    if not ajuste_ids:
        raise ValueError("Selecione ao menos um item pra gerar o relatório.")
    if len(ajuste_ids) > RELATORIO_AJUSTE_MAX_ITENS:
        raise ValueError(
            f"O formulário FORM-08.52 comporta no máximo {RELATORIO_AJUSTE_MAX_ITENS} itens por "
            f"documento - selecione até {RELATORIO_AJUSTE_MAX_ITENS} itens por vez."
        )

    ajustes = (
        LogisticaInventarioAjuste.query
        .filter(LogisticaInventarioAjuste.id.in_(ajuste_ids))
        .all()
    )
    if len(ajustes) != len(set(ajuste_ids)):
        raise ValueError("Um ou mais itens selecionados não foram encontrados.")
    fora_do_relatorio = [a for a in ajustes if a.status_modulo != "Relatorio"]
    if fora_do_relatorio:
        raise ValueError(
            "Um ou mais itens selecionados não estão mais aguardando o relatório "
            "(ainda não foram confirmados pelo gestor, já entraram em outro relatório, "
            "ou estão em outra etapa)."
        )

    # Justificativa por item: usa a que o gestor mandar agora (edicao no
    # relatorio) ou, se nao mandar, a que ja tinha sido dada na aprovacao
    # (Modulo 02). Nunca fica generica - cada item precisa da sua.
    justificativas = justificativas or {}
    justificativas_finais: dict[int, str] = {}
    sem_justificativa = []
    for ajuste in ajustes:
        texto = str(justificativas.get(str(ajuste.id), justificativas.get(ajuste.id, "")) or "").strip()
        if not texto:
            texto = (ajuste.gestor_justificativa or "").strip()
        if not texto:
            sem_justificativa.append(f"{ajuste.codigo_produto} ({ajuste.local_codigo})")
        justificativas_finais[ajuste.id] = texto
    if sem_justificativa:
        raise ValueError(
            "Preencha a justificativa (motivo) de cada item antes de gerar o relatório - faltando: "
            + ", ".join(sem_justificativa) + "."
        )

    tipo_ajuste = (tipo_ajuste or "").strip()
    if tipo_ajuste not in RELATORIO_AJUSTE_TIPOS:
        raise ValueError("Selecione um Tipo de Ajuste válido.")
    if tipo_ajuste == "Outros" and not (tipo_ajuste_detalhe or "").strip():
        raise ValueError("Detalhe o Tipo de Ajuste quando selecionar 'Outros'.")

    motivo_ajuste = (motivo_ajuste or "").strip()
    if motivo_ajuste not in RELATORIO_AJUSTE_MOTIVOS:
        raise ValueError("Selecione um Motivo do Ajuste válido.")
    motivo_ajuste_detalhe = (motivo_ajuste_detalhe or "").strip()

    deposito_tipo = (deposito_tipo or "").strip()
    if deposito_tipo not in RELATORIO_AJUSTE_DEPOSITO_TIPOS:
        raise ValueError("Selecione um tipo de Depósito/Local válido.")

    agora = datetime.now()
    sequencial, numero_documento = _proximo_numero_documento(agora.month, agora.year)

    relatorio = LogisticaInventarioRelatorioAjuste(
        mes_referencia=agora.month,
        ano_referencia=agora.year,
        sequencial=sequencial,
        numero_documento=numero_documento,
        tipo_ajuste=tipo_ajuste,
        tipo_ajuste_detalhe=(tipo_ajuste_detalhe or "").strip()[:300] or None,
        motivo_ajuste=motivo_ajuste,
        motivo_ajuste_detalhe=motivo_ajuste_detalhe,
        deposito_tipo=deposito_tipo,
        deposito_local=(deposito_local or "").strip()[:200] or None,
        responsavel=(responsavel or "").strip()[:100] or None,
        solicitante=(solicitante or "").strip()[:100] or None,
        depto=(depto or "").strip()[:100] or None,
        observacoes_ajuste=(observacoes_ajuste or "").strip() or None,
        observacoes_itens=(observacoes_itens or "").strip() or None,
        criado_por=usuario,
    )
    db.session.add(relatorio)
    db.session.flush()  # ganha relatorio.id antes de linkar os ajustes

    for ajuste in ajustes:
        ajuste.relatorio_id = relatorio.id
        # Justificativa final (atrelada a ESSE item, ver bloco acima) -
        # atualiza gestor_justificativa com o texto que efetivamente vai
        # pro PDF, seja o original da aprovacao ou uma edicao feita agora.
        ajuste.gestor_justificativa = justificativas_finais[ajuste.id][:500]
        # gestor_confirmado_em/por ja foram gravados quando o item entrou
        # em "Relatorio" (ver confirmar_divergencia) - so preenche aqui se
        # por algum motivo ainda estiver em branco (ex.: chegou via Pular
        # Etapa, que ignora essa validacao).
        ajuste.gestor_confirmado_em = ajuste.gestor_confirmado_em or agora
        ajuste.gestor_confirmado_por = ajuste.gestor_confirmado_por or usuario
        ajuste.status_modulo = "Finance"
        ajuste.status_slug = status_slug("Finance")
        if solicitar_analise_causa and not ajuste.analise_causa:
            db.session.add(LogisticaInventarioAnaliseCausa(
                ajuste_id=ajuste.id,
                status="Pendente",
                solicitado_por=usuario,
            ))

    db.session.commit()
    return relatorio


def buscar_relatorio_ajuste(relatorio_id: int) -> LogisticaInventarioRelatorioAjuste | None:
    return db.session.get(LogisticaInventarioRelatorioAjuste, relatorio_id)


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


def estornar_para_validacao(ajuste: LogisticaInventarioAjuste) -> LogisticaInventarioAjuste:
    """Reabre um ajuste em qualquer etapa, devolvendo-o para Validacao."""
    ajuste.gestor_justificativa = None
    ajuste.gestor_confirmado_em = None
    ajuste.gestor_confirmado_por = None
    ajuste.finance_observacao = None
    ajuste.finance_concluido_em = None
    ajuste.finance_concluido_por = None
    ajuste.fiscal_nf_numero = None
    ajuste.fiscal_concluido_em = None
    ajuste.fiscal_concluido_por = None
    ajuste.status_modulo = "Validacao"
    ajuste.status_slug = status_slug("Validacao")
    db.session.commit()
    return ajuste


def pular_etapa(ajuste: LogisticaInventarioAjuste, usuario: str) -> LogisticaInventarioAjuste:
    """Funcao "Pular Etapa" (requisito geral do Inventario, espelho do
    "Pular Status" do Comex): avanca o ajuste manualmente pra proxima etapa
    do workflow (Validacao -> Relatorio -> Finance -> Fiscal -> Concluido),
    IGNORANDO as validacoes normais de cada modulo (justificativa do
    gestor, preenchimento do FORM-08.52, observacao do Finance, numero da
    NF do Fiscal) - reservada pra ajustes excepcionais (ex.: decisao
    tomada fora do sistema, correcao rapida de fluxo) e exige permissao
    extra de gerencia (PAGE_LOGISTICA_INVENTARIO_PULAR_ETAPA), alem do
    acesso normal ao modulo. Nao pula pra fora do fluxo (nao reabre um
    "Descartado")."""
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
    if proximo == "Relatorio" or proximo == "Finance":
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


# ── Analise de Causa Raiz (fila separada, ver LogisticaInventarioAnaliseCausa) ──
def listar_analises_causa(status: str | None = None) -> list[LogisticaInventarioAnaliseCausa]:
    query = LogisticaInventarioAnaliseCausa.query
    if status:
        query = query.filter_by(status=status)
    return query.order_by(LogisticaInventarioAnaliseCausa.solicitado_em.desc()).all()


def preencher_analise_causa(
    analise: LogisticaInventarioAnaliseCausa, motivo: str, usuario: str
) -> LogisticaInventarioAnaliseCausa:
    """Operador ou gestor registra a analise detalhada do motivo e marca a
    entrada como Concluida - nao afeta o status_modulo do ajuste (Finance/
    Fiscal/Concluido continuam seguindo seu proprio ritmo, independente
    dessa analise)."""
    motivo = (motivo or "").strip()
    if not motivo:
        raise ValueError("Informe a análise detalhada do motivo.")
    analise.motivo_causa_raiz = motivo
    analise.status = "Concluida"
    analise.analisado_por = usuario
    analise.analisado_em = datetime.now()
    db.session.commit()
    return analise
