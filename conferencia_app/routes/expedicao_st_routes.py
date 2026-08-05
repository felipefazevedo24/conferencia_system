"""Rotas da Conferencia de Expedicao - aba Servico de Terceiro (ST).

Espelha a logica da aba de faturamento, porem usando a ORDEM DE COMPRA como
base (Ordem de Compra -> ST -> OS -> material enviado)."""

import os
from datetime import datetime

from flask import (
    Blueprint,
    current_app,
    jsonify,
    request,
    session,
)
from werkzeug.utils import secure_filename

from ..auth import permission_required, roles_required
from ..extensions import db
from ..models import (
    ExpedicaoOrdemST,
    ExpedicaoOrdemSTItem,
    ExpedicaoConferenciaSimples,
    ExpedicaoConferenciaSimplesFoto,
)
from ..services import expedicao_st_service as svc
from ..services import expedicao_log_service as log_svc
from ..services.expedicao_photo_storage import using_drive, upload_to_drive


expedicao_st_bp = Blueprint("expedicao_st", __name__)

PERMISSION = "PAGE_EXPEDICAO_CONF_CEGA"
ROLES = ("Conferente", "Admin", "Fiscal", "Logística")


def _iso(value):
    return value.isoformat() if value else None


def _ordem_editavel(ordem: ExpedicaoOrdemST) -> bool:
    """Permite conferir/editar enquanto a expedicao nao foi concluida.

    - Pendente: primeira conferencia.
    - Conferido/Ag. Fat: pode reeditar a conferencia antes de emitir a NF.
    - Faturado sem confer\u00eancia: conferencia obrigatoria para liberar a expedicao.
    - Faturado + conferido apos o faturamento: permite corrigir a contagem
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


def _ordem_resumo(ordem: ExpedicaoOrdemST, total_itens: int | None = None) -> dict:
    if total_itens is None:
        total_itens = ExpedicaoOrdemSTItem.query.filter_by(ordem_id=ordem.id).count()
    return {
        "id": ordem.id,
        "codigo_interno": ordem.codigo_interno,
        "cod_ordem_compra": ordem.cod_ordem_compra,
        "fornecedor": ordem.fornecedor,
        "n_os": ordem.n_os,
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
        "dt_solicitacao": _iso(ordem.dt_solicitacao),
        "dt_prevista_entrega": _iso(ordem.dt_prevista_entrega),
        "conferido_at": _iso(ordem.conferido_at),
        "faturado_at": _iso(ordem.faturado_at),
        "expedido_at": _iso(ordem.expedido_at),
        "expedicao_registro_id": ordem.expedicao_registro_id,
    }


def _fotos_dir() -> str:
    fotos_dir = current_app.config.get("EXPEDICAO_CONFERENCIA_FOTOS_DIR", "")
    if not fotos_dir:
        fotos_dir = os.path.join(current_app.instance_path, "expedicao_conferencia_simples")
    os.makedirs(fotos_dir, exist_ok=True)
    return fotos_dir


def _is_drive_quota_service_account_error(exc):
    """Detecta o erro de service account sem cota de armazenamento no Drive."""
    msg = str(exc).lower()
    return (
        "service account" in msg
        and (
            "cota" in msg
            or "quota" in msg
            or "storage" in msg
            or "drive compartilhado" in msg
        )
    )


def _salvar_foto_expedicao(foto, fotos_dir, prefix, registro_id):
    """Persiste um FileStorage (Drive ou disco) e retorna (nome, caminho).

    Quando o Drive esta configurado mas a service account nao tem cota de
    armazenamento, faz fallback para salvar a foto localmente em vez de falhar."""
    ext = os.path.splitext(secure_filename(foto.filename or ""))[1] or ".jpg"
    nome = f"{prefix}_reg{registro_id}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}{ext}"
    if using_drive():
        try:
            stored = upload_to_drive(foto, nome)
            return nome, stored.file_path
        except Exception as exc:  # noqa: BLE001
            if not _is_drive_quota_service_account_error(exc):
                raise
            current_app.logger.warning(
                "Drive sem cota para service account; salvando foto de pre-expedicao (ST) localmente: %s",
                exc,
            )
            try:
                foto.stream.seek(0)
            except Exception:  # noqa: BLE001
                pass
    caminho = os.path.join(fotos_dir, nome)
    foto.save(caminho)
    return nome, caminho


def _obter_ou_criar_registro_rascunho(ordem: ExpedicaoOrdemST, usuario):
    """Localiza (ou cria) o Registro de Expedicao vinculado a uma ordem ST
    faturada, para armazenar as fotos de material/cliente tiradas ANTES da
    expedicao. Mesma logica do FAT, usando fornecedor/ordem de compra no
    lugar de cliente/orcamento."""
    registro = None
    if ordem.expedicao_registro_id:
        registro = ExpedicaoConferenciaSimples.query.get(ordem.expedicao_registro_id)
    if registro is None and ordem.numero_nf:
        registro = (
            ExpedicaoConferenciaSimples.query
            .filter_by(numero_nf=ordem.numero_nf, origem="Romaneio")
            .filter(ExpedicaoConferenciaSimples.status.in_(
                ["Pendente de expedição", "Pendente de expedicao"]))
            .order_by(ExpedicaoConferenciaSimples.id.desc())
            .first()
        )
    if registro is None:
        registro = ExpedicaoConferenciaSimples(
            orcamento="",
            ordem_compra=ordem.cod_ordem_compra or "",
            numero_os=ordem.n_os or "",
            tipo_referencia="OrdemCompra",
            conferente=usuario,
            numero_nf=ordem.numero_nf or "",
            nome_cliente=ordem.fornecedor or "",
            cliente_origem="Consyste",
            nf_origem="Consyste",
            origem="Romaneio",
            status="Pendente de expedição",
        )
        db.session.add(registro)
        db.session.flush()
        ordem.expedicao_registro_id = registro.id
    return registro


def _fotos_preexpedicao_payload(registro):
    """Serializa as fotos ja anexadas ao registro (material + cliente)."""
    if registro is None:
        return {"fotos_material": [], "foto_cliente_url": None}
    fotos = (
        ExpedicaoConferenciaSimplesFoto.query
        .filter_by(conferencia_id=registro.id)
        .order_by(ExpedicaoConferenciaSimplesFoto.id.asc())
        .all()
    )
    return {
        "registro_id": registro.id,
        "fotos_material": [
            {"id": f.id, "url": f"/api/expedicao/conferencia-simples/{registro.id}/foto/{f.id}"}
            for f in fotos
        ],
        "foto_cliente_url": (
            f"/api/expedicao/conferencia-simples/{registro.id}/foto-cliente"
            if registro.foto_cliente_file_name else None
        ),
    }


@expedicao_st_bp.route(
    "/api/expedicao/conf-cega-st/ordens/<path:cod_ordem_compra>/fotos-preexpedicao",
    methods=["GET"],
)
@permission_required(PERMISSION)
def listar_fotos_preexpedicao_st(cod_ordem_compra):
    ordem = ExpedicaoOrdemST.query.filter_by(cod_ordem_compra=cod_ordem_compra).first()
    if not ordem:
        return jsonify({"error": "Ordem de compra nao encontrada."}), 404
    registro = None
    if ordem.expedicao_registro_id:
        registro = ExpedicaoConferenciaSimples.query.get(ordem.expedicao_registro_id)
    elif ordem.numero_nf:
        registro = (
            ExpedicaoConferenciaSimples.query
            .filter_by(numero_nf=ordem.numero_nf, origem="Romaneio")
            .order_by(ExpedicaoConferenciaSimples.id.desc())
            .first()
        )
    return jsonify(_fotos_preexpedicao_payload(registro))


@expedicao_st_bp.route(
    "/api/expedicao/conf-cega-st/ordens/<path:cod_ordem_compra>/fotos-preexpedicao",
    methods=["POST"],
)
@roles_required(*ROLES)
def upload_fotos_preexpedicao_st(cod_ordem_compra):
    """Anexa fotos do material e/ou do cliente a uma ordem ST ja faturada,
    ANTES da expedicao. Mesma logica do FAT (ver expedicao_fat_routes.py)."""
    ordem = ExpedicaoOrdemST.query.filter_by(cod_ordem_compra=cod_ordem_compra).first()
    if not ordem:
        return jsonify({"error": "Ordem de compra nao encontrada."}), 404
    if ordem.status == svc.STATUS_EXPEDIDO:
        return jsonify({"error": "As fotos não podem mais ser alteradas após a expedição."}), 400

    fotos_material = [f for f in request.files.getlist("fotos_material") if f and f.filename]
    foto_cliente = request.files.get("foto_cliente")
    if not fotos_material and not (foto_cliente and foto_cliente.filename):
        return jsonify({"error": "Envie ao menos uma foto do material ou do cliente."}), 400

    usuario = session.get("username", "desconhecido")
    registro = _obter_ou_criar_registro_rascunho(ordem, usuario)
    fotos_dir = _fotos_dir()
    agora = datetime.now()

    try:
        for foto in fotos_material:
            nome, caminho = _salvar_foto_expedicao(foto, fotos_dir, "material", registro.id)
            db.session.add(ExpedicaoConferenciaSimplesFoto(
                conferencia_id=registro.id, file_name=nome, file_path=caminho,
            ))
        if foto_cliente and foto_cliente.filename:
            nome, caminho = _salvar_foto_expedicao(foto_cliente, fotos_dir, "cliente", registro.id)
            registro.foto_cliente_file_name = nome
            registro.foto_cliente_file_path = caminho
            registro.foto_cliente_uploaded_at = agora
            registro.foto_cliente_uploaded_by = usuario
    except Exception as exc:  # noqa: BLE001
        db.session.rollback()
        current_app.logger.exception("Falha ao salvar fotos de pre-expedicao (ST)")
        return jsonify({"error": f"Falha ao salvar as fotos: {exc}"}), 502

    registro.updated_at = agora
    db.session.commit()
    return jsonify({"sucesso": True, **_fotos_preexpedicao_payload(registro)})


@expedicao_st_bp.route("/api/expedicao/conf-cega-st/ordens/<path:cod_ordem_compra>", methods=["DELETE"])
@roles_required("Admin")
def excluir_ordem_conf_st(cod_ordem_compra):
    """Exclui (soft-delete) uma ordem ST do dashboard/fila. Mesma logica do
    FAT (ver expedicao_fat_routes.py::excluir_ordem_conf_cega): a linha e o
    historico continuam no banco, localizaveis pela Auditoria de Expedicao."""
    ordem = ExpedicaoOrdemST.query.filter_by(cod_ordem_compra=cod_ordem_compra, excluido=False).first()
    if not ordem:
        return jsonify({"error": "Ordem de compra nao encontrada."}), 404

    payload = request.get_json(silent=True) or {}
    motivo = str(payload.get("motivo") or "").strip()
    if not motivo:
        return jsonify({"error": "Informe o motivo da exclusão."}), 400

    usuario = session.get("username", "desconhecido")
    agora = datetime.now()
    ordem.excluido = True
    ordem.excluido_at = agora
    ordem.excluido_by = usuario
    ordem.excluido_motivo = motivo
    ordem.updated_at = agora

    log_svc.registrar_log(
        origem="st",
        ordem_id=ordem.id,
        cod_ordem=ordem.cod_ordem_compra,
        acao="exclusao",
        usuario=usuario,
        status_anterior=ordem.status,
        status_novo=ordem.status,
        divergente=bool(ordem.divergente),
        pos_faturamento=bool(ordem.conferido_pos_faturamento),
        diff_cabecalho=[{"campo": "excluido", "label": "Exclusão", "de": "Não", "para": motivo}],
        diff_itens=[],
    )
    db.session.commit()
    return jsonify({"sucesso": True})


@expedicao_st_bp.route("/api/expedicao/conf-cega-st/sync", methods=["POST"])
@roles_required(*ROLES)
def sincronizar_conf_st():
    try:
        resumo = svc.sincronizar_ordens()
    except Exception as exc:  # noqa: BLE001
        current_app.logger.error("Falha ao sincronizar conferencia ST: %s", exc)
        return jsonify({"error": f"Nao foi possivel consultar a API de expedicao ST: {exc}"}), 502
    return jsonify({"sucesso": True, **resumo})


@expedicao_st_bp.route("/api/expedicao/conf-cega-st/ordens", methods=["GET"])
@permission_required(PERMISSION)
def listar_ordens_conf_st():
    slug = (request.args.get("status") or "").strip().lower()
    busca = (request.args.get("q") or "").strip().lower()

    ordens = ExpedicaoOrdemST.query.filter_by(excluido=False).order_by(
        ExpedicaoOrdemST.cod_ordem_compra.desc(),
    ).all()

    contagens: dict[int, int] = {}
    for oid, total in (
        db.session.query(ExpedicaoOrdemSTItem.ordem_id, db.func.count(ExpedicaoOrdemSTItem.id))
        .group_by(ExpedicaoOrdemSTItem.ordem_id)
        .all()
    ):
        contagens[oid] = total

    # OS por ordem (para busca por numero de OS) - carga em lote.
    os_por_ordem: dict[int, set[str]] = {}
    if busca:
        for oid, n_os in (
            db.session.query(ExpedicaoOrdemSTItem.ordem_id, ExpedicaoOrdemSTItem.n_os).all()
        ):
            os_por_ordem.setdefault(oid, set()).add(str(n_os or "").strip().lower())

    def _match_busca(ordem: ExpedicaoOrdemST) -> bool:
        campos = " ".join([
            str(ordem.cod_ordem_compra or ""),
            str(ordem.n_os or ""),
            str(ordem.fornecedor or ""),
        ]).lower()
        if busca in campos:
            return True
        return any(busca in os_val for os_val in os_por_ordem.get(ordem.id, ()))

    resumos = []
    metricas = {"pendente": 0, "conferido": 0, "faturado_sem_conf": 0, "faturado": 0, "romaneio": 0, "expedido": 0}
    # Fila de conferencia (visao padrao): apenas ordens ainda nao faturadas.
    # Depois que a NF e emitida (Faturado) a ordem sai da fila e passa a ser
    # vista somente pelos filtros Faturado/Expedido (a "outra aba").
    fila_conferencia = ("pendente", "conferido")

    # Mapeia numero_nf -> romaneio mais recente que a contem (mesma logica do
    # FAT), para exibir a composicao (badge) nas etapas "Em Romaneio"/"Expedido".
    from ..models import ExpedicaoRomaneio, ExpedicaoRomaneioNF

    nf_para_romaneio = {}
    linhas_nf = (
        db.session.query(ExpedicaoRomaneioNF, ExpedicaoRomaneio)
        .join(ExpedicaoRomaneio, ExpedicaoRomaneioNF.romaneio_id == ExpedicaoRomaneio.id)
        .all()
    )
    for nf_row, rom in linhas_nf:
        chave = str(nf_row.numero_nf or "").strip()
        if not chave:
            continue
        existente = nf_para_romaneio.get(chave)
        if not existente or rom.id > existente.id:
            nf_para_romaneio[chave] = rom

    for ordem in ordens:
        sl = svc.status_slug(ordem.status)
        metricas[sl] = metricas.get(sl, 0) + 1

        romaneio_info = None
        if sl in ("romaneio", "expedido"):
            rom = nf_para_romaneio.get(str(ordem.numero_nf or "").strip())
            if rom:
                romaneio_info = {
                    "id": rom.id,
                    "numero_romaneio": rom.numero_romaneio,
                    "status": rom.status,
                    "tipo_frete": rom.tipo_frete,
                }
                if sl == "expedido" and ordem.expedicao_registro_id:
                    registro = ExpedicaoConferenciaSimples.query.get(ordem.expedicao_registro_id)
                    if registro is not None:
                        romaneio_info["canhoto_pendente"] = registro.status == "Expedido" and not bool(registro.canhoto_file_name)

        # Busca por OS percorre TODOS os status; ignora a fila padrao e o
        # filtro de status enquanto houver termo de busca.
        if busca:
            if not _match_busca(ordem):
                continue
        elif slug:
            if sl != slug:
                continue
        elif sl not in fila_conferencia:
            continue
        resumo = _ordem_resumo(ordem, contagens.get(ordem.id, 0))
        if sl in ("romaneio", "expedido"):
            resumo["romaneio"] = romaneio_info
        resumos.append(resumo)

    return jsonify({"ordens": resumos, "metricas": metricas})


@expedicao_st_bp.route("/api/expedicao/conf-cega-st/ordens/<path:cod_ordem_compra>", methods=["GET"])
@permission_required(PERMISSION)
def obter_ordem_conf_st(cod_ordem_compra):
    ordem = ExpedicaoOrdemST.query.filter_by(cod_ordem_compra=cod_ordem_compra, excluido=False).first()
    if not ordem:
        return jsonify({"error": "Ordem de compra nao encontrada."}), 404

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
    resumo["historico"] = log_svc.listar_logs("st", ordem.id)
    return jsonify(resumo)


@expedicao_st_bp.route("/api/expedicao/conf-cega-st/ordens/<path:cod_ordem_compra>/conferir", methods=["POST"])
@roles_required(*ROLES)
def conferir_ordem_conf_st(cod_ordem_compra):
    ordem = ExpedicaoOrdemST.query.filter_by(cod_ordem_compra=cod_ordem_compra).first()
    if not ordem:
        return jsonify({"error": "Ordem de compra nao encontrada."}), 404
    if not _ordem_editavel(ordem):
        return jsonify({"error": f"Ordem '{ordem.status}' nao pode ser editada."}), 400

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

    contagens = {}
    for entry in itens_payload:
        try:
            item_id = int(entry.get("id"))
        except (TypeError, ValueError):
            continue
        contagens[item_id] = svc._parse_int(entry.get("qtde_conferida"), None)

    itens = list(ordem.itens)
    faltando = [it for it in itens if contagens.get(it.id) is None]
    if faltando:
        nomes = [
            (getattr(it, "cod_interno", None) or getattr(it, "item", None) or f"item {it.id}")
            for it in faltando
        ]
        detalhe = ", ".join(str(n) for n in nomes[:5])
        if len(nomes) > 5:
            detalhe += f" (+{len(nomes) - 5})"
        return jsonify({
            "error": f"Informe a quantidade conferida de todos os itens. Pendentes: {detalhe}.",
            "itens_faltando": [it.id for it in faltando],
        }), 400

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
    # Se a ordem ja estava faturada SEM conferencia, a conferencia agora feita
    # libera a expedicao (volta a "Faturado") e fica registrada como conferida
    # apos o faturamento para consultas futuras. Se ja estava "Faturado" (edicao
    # de conferencia pos-faturamento), preserva o status.
    if ordem.status == svc.STATUS_FATURADO_SEM_CONF:
        ordem.status = svc.STATUS_FATURADO
        if not ordem.faturado_at:
            ordem.faturado_at = agora
        ordem.conferido_pos_faturamento = True
    elif ordem.status == svc.STATUS_FATURADO:
        ordem.conferido_pos_faturamento = True
    else:
        ordem.status = svc.STATUS_CONFERIDO
    ordem.updated_at = agora

    # Trilha de auditoria: registra a conferencia ou a edicao posterior.
    diff_cabecalho = log_svc.montar_diff_cabecalho(header_antes, {
        "peso_liquido": peso_liquido,
        "peso_bruto": peso_bruto,
        "qtde_volumes": qtde_volumes,
        "especie_volumes": especie_volumes,
    })
    log_svc.registrar_log(
        origem="st",
        ordem_id=ordem.id,
        cod_ordem=ordem.cod_ordem_compra,
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

    # Aviso no Teams a cada finalizacao bem-sucedida da conferencia.
    try:
        from ..services.teams_service import notificar_expedicao_conferida

        notificar_expedicao_conferida(
            ordem.fornecedor or "Fornecedor não informado",
            f"Ordem de compra {ordem.cod_ordem_compra}",
            conferente=ordem.conferente,
            volumes=ordem.qtde_volumes,
            peso_liquido=ordem.peso_liquido,
            peso_bruto=ordem.peso_bruto,
            especie_volumes=ordem.especie_volumes,
            env_var="TEAMS_WEBHOOK_EXPEDICAO_ST_URL",
            config_key="webhook_expedicao_st",
        )
    except Exception:
        current_app.logger.exception("Falha ao notificar Teams (conf-cega ST %s)", cod_ordem_compra)

    return jsonify({
        "sucesso": True,
        "divergente": ordem_divergente,
        "ordem": _ordem_resumo(ordem, len(itens)),
        "itens": resultado_itens,
        "historico": log_svc.listar_logs("st", ordem.id),
    })


@expedicao_st_bp.route(
    "/api/expedicao/conf-cega-st/ordens/<path:cod_ordem_compra>/finalizar-sem-conferencia",
    methods=["POST"],
)
@roles_required("Admin")
def finalizar_sem_conferencia_st(cod_ordem_compra):
    """Encerra a ordem ST SEM conferencia fisica (acao exclusiva de Admin).

    A ordem sai da fila de pendentes e NAO segue para o Registro de expedicao.
    """
    ordem = ExpedicaoOrdemST.query.filter_by(cod_ordem_compra=cod_ordem_compra).first()
    if not ordem:
        return jsonify({"error": "Ordem de compra nao encontrada."}), 404
    if ordem.status in (svc.STATUS_EXPEDIDO, svc.STATUS_FINALIZADO_SEM_CONF):
        return jsonify({"error": f"Ordem '{ordem.status}' nao pode ser finalizada."}), 400

    payload = request.get_json(silent=True) or {}
    motivo = str(payload.get("motivo") or "").strip()

    agora = datetime.now()
    usuario = session.get("username") or "desconhecido"
    status_anterior = ordem.status
    ordem.status = svc.STATUS_FINALIZADO_SEM_CONF
    ordem.conferente = usuario
    ordem.updated_at = agora

    diff_cab = [{"campo": "motivo", "label": "Motivo", "de": "", "para": motivo}] if motivo else []
    log_svc.registrar_log(
        origem="st",
        ordem_id=ordem.id,
        cod_ordem=ordem.cod_ordem_compra,
        acao="finalizacao_sem_conferencia",
        usuario=usuario,
        status_anterior=status_anterior,
        status_novo=ordem.status,
        divergente=False,
        pos_faturamento=bool(ordem.conferido_pos_faturamento),
        diff_cabecalho=diff_cab,
        diff_itens=[],
    )
    db.session.commit()

    return jsonify({
        "sucesso": True,
        "status": ordem.status,
        "status_slug": svc.status_slug(ordem.status),
    })


@expedicao_st_bp.route(
    "/api/expedicao/conf-cega-st/ordens/<path:cod_ordem_compra>/seguir-sem-contagem",
    methods=["POST"],
)
@roles_required("Admin")
def seguir_sem_contagem_st(cod_ordem_compra):
    """Admin: pula a contagem e envia a ordem ST direto para 'Aguardando faturamento'.

    Diferente de 'finalizar sem conferencia' (que encerra a ordem), esta acao
    apenas dispensa a contagem fisica e mantem a ordem no fluxo normal, seguindo
    para 'Conferido/Ag. Fat' (ou 'Faturado', quando a NF ja foi emitida).
    """
    ordem = ExpedicaoOrdemST.query.filter_by(cod_ordem_compra=cod_ordem_compra).first()
    if not ordem:
        return jsonify({"error": "Ordem de compra nao encontrada."}), 404
    if ordem.status not in (svc.STATUS_PENDENTE, svc.STATUS_FATURADO_SEM_CONF):
        return jsonify({
            "error": f"Ordem '{ordem.status}' nao pode seguir para faturamento sem contagem."
        }), 400

    payload = request.get_json(silent=True) or {}
    motivo = str(payload.get("motivo") or "").strip()

    agora = datetime.now()
    usuario = session.get("username") or "desconhecido"
    status_anterior = ordem.status
    ordem.conferente = usuario
    ordem.conferido_at = agora
    if ordem.status == svc.STATUS_FATURADO_SEM_CONF:
        ordem.status = svc.STATUS_FATURADO
        if not ordem.faturado_at:
            ordem.faturado_at = agora
        ordem.conferido_pos_faturamento = True
    else:
        ordem.status = svc.STATUS_CONFERIDO
    ordem.updated_at = agora

    diff_cab = [{
        "campo": "acao",
        "label": "Ação",
        "de": "",
        "para": "Seguiu para faturamento sem contagem (Admin)",
    }]
    if motivo:
        diff_cab.append({"campo": "motivo", "label": "Motivo", "de": "", "para": motivo})
    log_svc.registrar_log(
        origem="st",
        ordem_id=ordem.id,
        cod_ordem=ordem.cod_ordem_compra,
        acao="seguir_sem_contagem",
        usuario=usuario,
        status_anterior=status_anterior,
        status_novo=ordem.status,
        divergente=False,
        pos_faturamento=bool(ordem.conferido_pos_faturamento),
        diff_cabecalho=diff_cab,
        diff_itens=[],
    )
    db.session.commit()

    # Aviso no Teams: mesmo sem contagem fisica, a ordem segue para faturamento.
    try:
        from ..services.teams_service import notificar_expedicao_conferida

        observacao = "Sem contagem física (Admin)" + (f" — {motivo}" if motivo else "")
        notificar_expedicao_conferida(
            ordem.fornecedor or "Fornecedor não informado",
            f"Ordem de compra {ordem.cod_ordem_compra}",
            conferente=ordem.conferente,
            observacao=observacao,
            titulo="⏭️ Expedição seguiu sem contagem",
            env_var="TEAMS_WEBHOOK_EXPEDICAO_ST_URL",
            config_key="webhook_expedicao_st",
        )
    except Exception:
        current_app.logger.exception("Falha ao notificar Teams (seguir sem contagem ST %s)", cod_ordem_compra)

    return jsonify({
        "sucesso": True,
        "status": ordem.status,
        "status_slug": svc.status_slug(ordem.status),
    })


@expedicao_st_bp.route(
    "/api/expedicao/conf-cega-st/ordens/<path:cod_ordem_compra>/estornar-conferencia",
    methods=["POST"],
)
@roles_required("Admin")
def estornar_conferencia_st(cod_ordem_compra):
    """Estorna a conferencia de uma ordem ST (acao exclusiva de Admin).

    - Conferido/Ag. Fat  -> volta para Pendente de conferencia.
    - Faturado (conferido)-> volta para "Faturado sem conferência" (mantem NF).
    - Finalizada sem conferência -> volta para Pendente de conferencia.
    Limpa a contagem e os dados de conferencia para permitir refazer.
    """
    ordem = ExpedicaoOrdemST.query.filter_by(cod_ordem_compra=cod_ordem_compra).first()
    if not ordem:
        return jsonify({"error": "Ordem de compra nao encontrada."}), 404

    slug = svc.status_slug(ordem.status)
    if slug not in ("conferido", "faturado", "finalizado_sem_conf"):
        if slug == "romaneio":
            return jsonify({
                "error": "Nao e possivel estornar a Nota Fiscal, pois existe uma etapa posterior ativa (Romaneio). "
                         "Realize primeiro o estorno do Romaneio (remova a NF do romaneio ou delete o romaneio)."
            }), 400
        if slug == "expedido":
            return jsonify({
                "error": "Nao e possivel estornar a Nota Fiscal, pois existe uma etapa posterior ativa (Expedicao). "
                         "Realize primeiro o estorno da Expedicao e depois do Romaneio."
            }), 400
        return jsonify({"error": "Nao ha conferencia para estornar nesta ordem."}), 400

    payload = request.get_json(silent=True) or {}
    motivo = str(payload.get("motivo") or "").strip()

    agora = datetime.now()
    usuario = session.get("username") or "desconhecido"
    status_anterior = ordem.status

    ordem.status = svc.STATUS_FATURADO_SEM_CONF if slug == "faturado" else svc.STATUS_PENDENTE
    ordem.conferente = None
    ordem.conferido_at = None
    ordem.divergente = False
    ordem.conferido_pos_faturamento = False
    ordem.peso_liquido = None
    ordem.peso_bruto = None
    ordem.qtde_volumes = None
    ordem.especie_volumes = None
    for it in ordem.itens:
        it.qtde_conferida = None
        it.divergente = False
    ordem.updated_at = agora

    diff_cab = [{"campo": "motivo", "label": "Motivo", "de": "", "para": motivo}] if motivo else []
    log_svc.registrar_log(
        origem="st",
        ordem_id=ordem.id,
        cod_ordem=ordem.cod_ordem_compra,
        acao="estorno_conferencia",
        usuario=usuario,
        status_anterior=status_anterior,
        status_novo=ordem.status,
        divergente=False,
        pos_faturamento=False,
        diff_cabecalho=diff_cab,
        diff_itens=[],
    )
    db.session.commit()

    return jsonify({
        "sucesso": True,
        "status": ordem.status,
        "status_slug": svc.status_slug(ordem.status),
    })


@expedicao_st_bp.route("/api/expedicao/conf-cega-st/ordens/<path:cod_ordem_compra>/informar-nf", methods=["POST"])
@roles_required("Admin")
def informar_nf_ordem_st(cod_ordem_compra):
    """Informa manualmente o numero da NF de uma ordem ST ja Conferida,
    faturando-a (acao exclusiva de Admin).

    A sincronizacao automatica so grava numero_nf e avanca para Faturado
    quando a API externa de ST reporta a NF (expedicao_st_service.py). Se a
    solicitacao de faturamento for excluida na origem antes da NF ser
    emitida, a ordem fica presa em 'Conferido/Ag. Fat' para sempre — esta acao
    destrava manualmente, buscando a NF diretamente no ERP para confirmar que
    ela realmente existe e esta autorizada antes de faturar a ordem."""
    ordem = ExpedicaoOrdemST.query.filter_by(cod_ordem_compra=cod_ordem_compra).first()
    if not ordem:
        return jsonify({"error": "Ordem de compra nao encontrada."}), 404

    if ordem.status != svc.STATUS_CONFERIDO:
        return jsonify({"error": "Só é possível informar a NF manualmente de ordens no status Conferido/Ag. Fat."}), 400

    payload = request.get_json(silent=True) or {}
    numero_nf = str(payload.get("numero_nf") or "").strip()
    if not numero_nf:
        return jsonify({"error": "Informe o número da NF."}), 400

    from ..services.erp_nfe_emitidas_service import buscar_nfe_emitida_erp

    try:
        nota = buscar_nfe_emitida_erp(numero_nf=numero_nf)
    except Exception as exc:
        current_app.logger.exception("Falha ao consultar NF %s no ERP para informar-nf (ST)", numero_nf)
        return jsonify({"error": f"Falha ao consultar a NF no ERP: {exc}"}), 502

    if not nota or not nota.get("autorizada"):
        return jsonify({
            "error": f"NF {numero_nf} não encontrada ou não autorizada no ERP. Confira o número antes de informar."
        }), 400

    agora = datetime.now()
    usuario = session.get("username") or "desconhecido"
    status_anterior = ordem.status

    ordem.numero_nf = str(nota.get("numero") or numero_nf).strip()
    ordem.status = svc.STATUS_FATURADO
    ordem.faturado_at = agora
    ordem.updated_at = agora

    log_svc.registrar_log(
        origem="st",
        ordem_id=ordem.id,
        cod_ordem=ordem.cod_ordem_compra,
        acao="informar_nf_manual",
        usuario=usuario,
        status_anterior=status_anterior,
        status_novo=ordem.status,
        divergente=False,
        pos_faturamento=False,
        diff_cabecalho=[{"campo": "numero_nf", "label": "Número da NF", "de": "", "para": ordem.numero_nf}],
        diff_itens=[],
    )
    db.session.commit()

    return jsonify({
        "sucesso": True,
        "numero_nf": ordem.numero_nf,
        "status": ordem.status,
        "status_slug": svc.status_slug(ordem.status),
    })


@expedicao_st_bp.route("/api/expedicao/conf-cega-st/lookup", methods=["GET"])
@roles_required(*ROLES)
def lookup_ordem_conf_st():
    """Prefill para o Registro de expedicao a partir do cod_ordem_compra (ST)."""
    cod = (request.args.get("cod") or "").strip()
    if not cod:
        return jsonify({"error": "Informe o codigo da ordem de compra."}), 400

    ordem = ExpedicaoOrdemST.query.filter_by(cod_ordem_compra=cod).first()
    if not ordem:
        return jsonify({"error": "Ordem de compra nao encontrada."}), 404

    os_lista = sorted({(it.n_os or "").strip() for it in ordem.itens if (it.n_os or "").strip()})
    if not os_lista and (ordem.n_os or "").strip():
        os_lista = sorted({
            parte.strip()
            for parte in str(ordem.n_os).replace(";", ",").split(",")
            if parte.strip()
        })
    return jsonify({
        "cod_ordem_compra": ordem.cod_ordem_compra,
        "ordem_compra": ordem.cod_ordem_compra,
        "fornecedor": ordem.fornecedor,
        "cliente": ordem.fornecedor,
        "numero_nf": ordem.numero_nf,
        "status": ordem.status,
        "status_slug": svc.status_slug(ordem.status),
        "n_os": os_lista,
        "numero_os": ", ".join(os_lista),
    })
