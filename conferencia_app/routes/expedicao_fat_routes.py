"""Rotas da Conferencia de Expedicao (dashboard + conferencia cega)."""

from datetime import datetime

from flask import (
    Blueprint,
    current_app,
    jsonify,
    render_template,
    request,
    session,
)

from ..auth import permission_required, roles_required
from ..extensions import db
from ..models import ExpedicaoOrdemFat, ExpedicaoOrdemFatItem
from ..services import expedicao_fat_service as svc
from ..services import expedicao_log_service as log_svc


expedicao_fat_bp = Blueprint("expedicao_fat", __name__)

PERMISSION = "PAGE_EXPEDICAO_CONF_CEGA"
ROLES = ("Conferente", "Admin", "Fiscal", "Logística")


def _iso(value):
    return value.isoformat() if value else None


def _ordem_editavel(ordem: ExpedicaoOrdemFat) -> bool:
    """Permite conferir/editar enquanto a expedicao nao foi concluida.

    - Pendente: primeira conferencia.
    - Conferido/Ag. Fat: pode reeditar a conferencia (corrigir contagem/pesos)
      antes de emitir a NF.
    - Faturado sem conferência: NF emitida na origem sem conferencia; a
      conferencia cega e OBRIGATORIA para liberar a expedicao.
    - Faturado + conferido apos o faturamento: a conferencia foi feita tardia
      (registro conferido_pos_faturamento); permite corrigir a contagem
      enquanto a ordem ainda nao foi expedida.
    - Expedida: bloqueada (fluxo ja concluido)."""
    if ordem.status in (
        svc.STATUS_PENDENTE,
        svc.STATUS_CONFERIDO,
        svc.STATUS_FATURADO_SEM_CONF,
    ):
        return True
    if ordem.status == svc.STATUS_FATURADO and ordem.conferido_pos_faturamento:
        return True
    return False


def _ordem_resumo(ordem: ExpedicaoOrdemFat, total_itens: int | None = None) -> dict:
    if total_itens is None:
        total_itens = ExpedicaoOrdemFatItem.query.filter_by(ordem_id=ordem.id).count()
    return {
        "id": ordem.id,
        "cod_ordem_fat": ordem.cod_ordem_fat,
        "cliente": ordem.cliente,
        "orcamento": ordem.orcamento,
        "pedido": ordem.pedido,
        "liberado_faturar": ordem.liberado_faturar,
        "status": ordem.status,
        "status_slug": svc.status_slug(ordem.status),
        "divergente": bool(ordem.divergente),
        "editavel": _ordem_editavel(ordem),
        "numero_nf": ordem.numero_nf,
        "conferente": ordem.conferente,
        "conferido_pos_faturamento": bool(ordem.conferido_pos_faturamento),
        "total_itens": total_itens,
        "peso_liquido": ordem.peso_liquido,
        "peso_bruto": ordem.peso_bruto,
        "qtde_volumes": ordem.qtde_volumes,
        "especie_volumes": ordem.especie_volumes,
        "marca_volumes": ordem.marca_volumes,
        "dt_solicitacao_fat": _iso(ordem.dt_solicitacao_fat),
        "dt_previsao_entrega": _iso(ordem.dt_previsao_entrega),
        "conferido_at": _iso(ordem.conferido_at),
        "faturado_at": _iso(ordem.faturado_at),
        "expedido_at": _iso(ordem.expedido_at),
    }


@expedicao_fat_bp.route("/expedicao/conferencia-cega")
@permission_required(PERMISSION)
def conferencia_expedicao_page():
    return render_template(
        "expedicao_conf_cega.html",
        user=session["username"],
        user_role=session.get("role", ""),
        is_admin=session.get("role") == "Admin",
    )


@expedicao_fat_bp.route("/api/expedicao/conf-cega/sync", methods=["POST"])
@roles_required(*ROLES)
def sincronizar_conf_cega():
    try:
        resumo = svc.sincronizar_ordens()
    except Exception as exc:  # noqa: BLE001 - retorna erro amigavel ao front
        current_app.logger.error("Falha ao sincronizar conferencia de expedicao: %s", exc)
        return jsonify({"error": f"Nao foi possivel consultar a API: {exc}"}), 502
    return jsonify({"sucesso": True, **resumo})


@expedicao_fat_bp.route("/api/expedicao/conf-cega/ordens", methods=["GET"])
@permission_required(PERMISSION)
def listar_ordens_conf_cega():
    slug = (request.args.get("status") or "").strip().lower()
    busca = (request.args.get("q") or "").strip().lower()

    ordens = ExpedicaoOrdemFat.query.order_by(
        ExpedicaoOrdemFat.cod_ordem_fat.desc(),
    ).all()

    # Contagem de itens em lote para evitar N+1
    contagens: dict[int, int] = {}
    for oid, total in (
        db.session.query(ExpedicaoOrdemFatItem.ordem_id, db.func.count(ExpedicaoOrdemFatItem.id))
        .group_by(ExpedicaoOrdemFatItem.ordem_id)
        .all()
    ):
        contagens[oid] = total

    # OS por ordem (para permitir busca por numero de OS) - carga em lote.
    os_por_ordem: dict[int, set[str]] = {}
    if busca:
        for oid, n_os in (
            db.session.query(ExpedicaoOrdemFatItem.ordem_id, ExpedicaoOrdemFatItem.n_os).all()
        ):
            os_por_ordem.setdefault(oid, set()).add(str(n_os or "").strip().lower())

    def _match_busca(ordem: ExpedicaoOrdemFat) -> bool:
        campos = " ".join([
            str(ordem.cod_ordem_fat or ""),
            str(ordem.orcamento or ""),
            str(ordem.pedido or ""),
            str(ordem.cliente or ""),
        ]).lower()
        if busca in campos:
            return True
        return any(busca in os_val for os_val in os_por_ordem.get(ordem.id, ()))

    resumos = []
    metricas = {"pendente": 0, "conferido": 0, "faturado_sem_conf": 0, "faturado": 0, "expedido": 0}
    for ordem in ordens:
        sl = svc.status_slug(ordem.status)
        metricas[sl] = metricas.get(sl, 0) + 1
        # Busca por OS/orcamento percorre TODOS os status; ignora o filtro de
        # status enquanto houver termo de busca.
        if busca:
            if not _match_busca(ordem):
                continue
        elif slug and sl != slug:
            continue
        resumos.append(_ordem_resumo(ordem, contagens.get(ordem.id, 0)))

    return jsonify({"ordens": resumos, "metricas": metricas})


@expedicao_fat_bp.route("/api/expedicao/conf-cega/ordens/<int:cod_ordem_fat>", methods=["GET"])
@permission_required(PERMISSION)
def obter_ordem_conf_cega(cod_ordem_fat):
    ordem = ExpedicaoOrdemFat.query.filter_by(cod_ordem_fat=cod_ordem_fat).first()
    if not ordem:
        return jsonify({"error": "Ordem de faturamento nao encontrada."}), 404

    conferido = ordem.status != svc.STATUS_PENDENTE
    editavel = _ordem_editavel(ordem)
    itens = []
    for it in ordem.itens:
        dados = {
            "id": it.id,
            "linha": it.linha,
            "cod_interno": it.cod_interno,
            "item": it.item,
            "n_os": it.n_os,
        }
        # Conferencia CEGA: a quantidade esperada (qtde_a_faturar) NUNCA e
        # enviada ao front-end. Ela existe apenas no back-end para validar a
        # contagem. Apos conferir, expomos somente o que o proprio operador
        # contou (qtde_conferida) e se houve divergencia (booleano) — sem
        # revelar o numero esperado.
        if conferido:
            dados["qtde_conferida"] = it.qtde_conferida
            dados["divergente"] = bool(it.divergente)
        itens.append(dados)

    resumo = _ordem_resumo(ordem, len(itens))
    resumo["itens"] = itens
    resumo["conferido"] = conferido
    resumo["editavel"] = editavel
    resumo["historico"] = log_svc.listar_logs("fat", ordem.id)
    return jsonify(resumo)


@expedicao_fat_bp.route("/api/expedicao/conf-cega/ordens/<int:cod_ordem_fat>/conferir", methods=["POST"])
@roles_required(*ROLES)
def conferir_ordem_conf_cega(cod_ordem_fat):
    ordem = ExpedicaoOrdemFat.query.filter_by(cod_ordem_fat=cod_ordem_fat).first()
    if not ordem:
        return jsonify({"error": "Ordem de faturamento nao encontrada."}), 404
    if not _ordem_editavel(ordem):
        return jsonify({
            "error": f"Ordem '{ordem.status}' nao pode ser editada."
        }), 400

    payload = request.get_json(silent=True) or {}
    itens_payload = payload.get("itens") or []
    peso_liquido = str(payload.get("peso_liquido") or "").strip()
    peso_bruto = str(payload.get("peso_bruto") or "").strip()
    qtde_volumes = str(payload.get("qtde_volumes") or "").strip()
    especie_volumes = str(payload.get("especie_volumes") or "").strip()

    if not (peso_liquido and peso_bruto and qtde_volumes and especie_volumes):
        return jsonify({
            "error": "Preencha peso liquido, peso bruto, qtde de volumes e especie dos volumes."
        }), 400

    # Mapa item_id -> quantidade conferida
    contagens = {}
    for entry in itens_payload:
        try:
            item_id = int(entry.get("id"))
        except (TypeError, ValueError):
            continue
        contagens[item_id] = svc._parse_int(entry.get("qtde_conferida"), None)

    itens = list(ordem.itens)
    faltando = [it.id for it in itens if contagens.get(it.id) is None]
    if faltando:
        return jsonify({"error": "Informe a quantidade conferida de todos os itens."}), 400

    # Snapshot ANTES das alteracoes (para a trilha de auditoria).
    era_conferido = ordem.conferido_at is not None
    status_anterior = ordem.status
    header_antes = {
        "peso_liquido": ordem.peso_liquido,
        "peso_bruto": ordem.peso_bruto,
        "qtde_volumes": ordem.qtde_volumes,
        "especie_volumes": ordem.especie_volumes,
    }
    qtd_antes = {it.id: it.qtde_conferida for it in itens}

    ordem_divergente = False
    resultado_itens = []
    diff_itens = []
    for it in itens:
        qtd = contagens.get(it.id)
        de = qtd_antes.get(it.id)
        if de != qtd:
            diff_itens.append({
                "id": it.id,
                "cod_interno": it.cod_interno,
                "item": it.item,
                "n_os": it.n_os,
                "de": de,
                "para": qtd,
            })
        it.qtde_conferida = qtd
        it.divergente = (qtd != it.qtde_a_faturar)
        if it.divergente:
            ordem_divergente = True
        resultado_itens.append({
            "id": it.id,
            "cod_interno": it.cod_interno,
            "item": it.item,
            "n_os": it.n_os,
            "qtde_conferida": qtd,
            "divergente": it.divergente,
        })

    # Bloqueio: nao permite salvar/finalizar a conferencia enquanto houver
    # divergencia entre o conferido e o previsto. Desfaz as alteracoes em
    # memoria (rollback) e devolve os itens divergentes para o front destacar.
    if ordem_divergente:
        db.session.rollback()
        return jsonify({
            "sucesso": False,
            "bloqueado": True,
            "divergente": True,
            "mensagem": (
                "Conferencia bloqueada: ha divergencia entre a quantidade "
                "conferida e a prevista. Reveja os itens destacados antes de "
                "finalizar."
            ),
            "itens": resultado_itens,
        }), 200

    agora = datetime.now()
    ordem.divergente = ordem_divergente
    ordem.peso_liquido = peso_liquido
    ordem.peso_bruto = peso_bruto
    ordem.qtde_volumes = qtde_volumes
    ordem.especie_volumes = especie_volumes
    ordem.conferente = session.get("username") or "desconhecido"
    ordem.conferido_at = agora
    # Se a ordem ja estava faturada SEM conferencia (NF emitida na origem), a
    # conferencia agora realizada libera a expedicao: volta ao fluxo normal como
    # "Faturado". Se ja estava "Faturado" (edicao de uma conferencia feita apos
    # o faturamento), mantem o status. Caso contrario, segue para
    # "Conferido/Ag. Fat".
    if ordem.status == svc.STATUS_FATURADO_SEM_CONF:
        ordem.status = svc.STATUS_FATURADO
        if not ordem.faturado_at:
            ordem.faturado_at = agora
        # Registro permanente para consultas futuras: conferencia feita APOS o
        # faturamento (NF ja emitida no momento da conferencia).
        ordem.conferido_pos_faturamento = True
    elif ordem.status == svc.STATUS_FATURADO:
        # Edicao de uma conferencia pos-faturamento: preserva o status Faturado
        # e o registro de conferencia tardia.
        ordem.conferido_pos_faturamento = True
    else:
        ordem.status = svc.STATUS_CONFERIDO
    ordem.updated_at = agora

    # Trilha de auditoria: registra a conferencia ou a edicao posterior com o
    # detalhamento do que mudou (cabecalho + itens).
    diff_cabecalho = log_svc.montar_diff_cabecalho(header_antes, {
        "peso_liquido": peso_liquido,
        "peso_bruto": peso_bruto,
        "qtde_volumes": qtde_volumes,
        "especie_volumes": especie_volumes,
    })
    log_svc.registrar_log(
        origem="fat",
        ordem_id=ordem.id,
        cod_ordem=ordem.cod_ordem_fat,
        acao="edicao" if era_conferido else "conferencia",
        usuario=ordem.conferente,
        status_anterior=status_anterior,
        status_novo=ordem.status,
        divergente=ordem_divergente,
        pos_faturamento=bool(ordem.conferido_pos_faturamento),
        diff_cabecalho=diff_cabecalho,
        diff_itens=diff_itens,
    )

    db.session.commit()

    return jsonify({
        "sucesso": True,
        "divergente": ordem_divergente,
        "ordem": _ordem_resumo(ordem, len(itens)),
        "itens": resultado_itens,
        "historico": log_svc.listar_logs("fat", ordem.id),
    })


@expedicao_fat_bp.route("/api/expedicao/conf-cega/lookup", methods=["GET"])
@roles_required(*ROLES)
def lookup_ordem_conf_cega():
    """Prefill para o Registro de expedicao a partir do cod_ordem_fat."""
    cod = (request.args.get("cod") or "").strip()
    if not cod:
        return jsonify({"error": "Informe o codigo da ordem de faturamento."}), 400
    try:
        cod_int = int(cod)
    except (TypeError, ValueError):
        return jsonify({"error": "Codigo de ordem de faturamento invalido."}), 400

    ordem = ExpedicaoOrdemFat.query.filter_by(cod_ordem_fat=cod_int).first()
    if not ordem:
        return jsonify({"error": "Ordem de faturamento nao encontrada."}), 404

    os_lista = sorted({(it.n_os or "").strip() for it in ordem.itens if (it.n_os or "").strip()})
    return jsonify({
        "cod_ordem_fat": ordem.cod_ordem_fat,
        "orcamento": ordem.orcamento,
        "cliente": ordem.cliente,
        "numero_nf": ordem.numero_nf,
        "status": ordem.status,
        "status_slug": svc.status_slug(ordem.status),
        "n_os": os_lista,
        "numero_os": ", ".join(os_lista),
    })
