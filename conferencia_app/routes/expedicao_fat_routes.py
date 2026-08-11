"""Rotas da Conferencia de Expedicao (dashboard + conferencia cega)."""

import os
from datetime import datetime

from flask import (
    Blueprint,
    current_app,
    jsonify,
    render_template,
    request,
    session,
)
from werkzeug.utils import secure_filename

from ..auth import permission_required, roles_required
from ..extensions import db
from ..models import (
    ExpedicaoOrdemFat,
    ExpedicaoOrdemFatItem,
    ExpedicaoOrdemFatVolume,
    ExpedicaoConferenciaSimples,
    ExpedicaoConferenciaSimplesFoto,
)
from ..services import expedicao_fat_service as svc
from ..services import expedicao_log_service as log_svc
from ..services.expedicao_photo_storage import using_drive, upload_to_drive


expedicao_fat_bp = Blueprint("expedicao_fat", __name__)

PERMISSION = "PAGE_EXPEDICAO_CONF_CEGA"
ROLES = ("Conferente", "Admin", "Fiscal", "Logística", "Comex")

# "Faturado sem conferência": exibe apenas a partir desta solicitação de
# faturamento (cod_ordem_fat). O backlog antigo (NFs emitidas na origem antes
# do uso da conferência cega) fica oculto para não poluir a fila.
FAT_SEM_CONF_COD_MINIMO = 1594


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
        "codigo_interno": ordem.codigo_interno,
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
        "operacao_tipo": ordem.operacao_tipo or "nacional",
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
        "expedicao_registro_id": ordem.expedicao_registro_id,
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

    ordens = ExpedicaoOrdemFat.query.filter_by(excluido=False).order_by(
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
    metricas = {"pendente": 0, "conferido": 0, "faturado_sem_conf": 0, "faturado": 0, "romaneio": 0, "expedido": 0}

    # Mapeia numero_nf -> romaneio mais recente que a contém. O agrupamento em
    # romaneio é feito por NF (não por orçamento), pois um mesmo romaneio pode
    # reunir NFes de orçamentos/ordens diferentes. Usado apenas para exibir a
    # composição (badge) nas etapas "Em Romaneio" e "Expedido".
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
        # Faturado sem conferência: oculta o backlog antigo, exibindo apenas as
        # solicitações a partir de FAT_SEM_CONF_COD_MINIMO (não conta nem lista).
        if sl == "faturado_sem_conf" and (ordem.cod_ordem_fat or 0) < FAT_SEM_CONF_COD_MINIMO:
            continue
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

        # Busca por OS/orcamento percorre TODOS os status; ignora o filtro de
        # status enquanto houver termo de busca.
        if busca:
            if not _match_busca(ordem):
                continue
        elif slug and sl != slug:
            continue
        resumo = _ordem_resumo(ordem, contagens.get(ordem.id, 0))
        if sl in ("romaneio", "expedido"):
            resumo["romaneio"] = romaneio_info
        resumos.append(resumo)


    return jsonify({"ordens": resumos, "metricas": metricas})


@expedicao_fat_bp.route("/api/expedicao/conf-cega/ordens/<int:cod_ordem_fat>", methods=["GET"])
@permission_required(PERMISSION)
def obter_ordem_conf_cega(cod_ordem_fat):
    ordem = ExpedicaoOrdemFat.query.filter_by(cod_ordem_fat=cod_ordem_fat, excluido=False).first()
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

    volumes = [
        {
            "especie": v.especie,
            "quantidade": v.quantidade,
            "altura_cm": v.altura_cm,
            "comprimento_cm": v.comprimento_cm,
            "largura_cm": v.largura_cm,
            "peso_kg": v.peso_kg,
        }
        for v in ordem.volumes
    ]

    resumo = _ordem_resumo(ordem, len(itens))
    resumo["itens"] = itens
    resumo["volumes"] = volumes
    resumo["conferido"] = conferido
    resumo["editavel"] = editavel
    resumo["historico"] = log_svc.listar_logs("fat", ordem.id)
    return jsonify(resumo)


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
    armazenamento, faz fallback para salvar a foto localmente em vez de falhar.
    """
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
                "Drive sem cota para service account; salvando foto de pre-expedicao localmente: %s",
                exc,
            )
            try:
                foto.stream.seek(0)
            except Exception:  # noqa: BLE001
                pass
    caminho = os.path.join(fotos_dir, nome)
    foto.save(caminho)
    return nome, caminho


def _obter_ou_criar_registro_rascunho(ordem, usuario):
    """Localiza (ou cria) o Registro de Expedicao vinculado a uma ordem
    faturada, para armazenar as fotos de material/cliente tiradas ANTES da
    expedicao. Fica como rascunho ('Pendente de expedicao', origem Romaneio)
    ate o romaneio ser expedido, quando entao vira 'Finalizado'."""
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
            orcamento=ordem.orcamento or "",
            tipo_referencia="Orcamento",
            conferente=usuario,
            numero_nf=ordem.numero_nf or "",
            nome_cliente=ordem.cliente or "",
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


@expedicao_fat_bp.route(
    "/api/expedicao/conf-cega/ordens/<int:cod_ordem_fat>/fotos-preexpedicao",
    methods=["GET"],
)
@permission_required(PERMISSION)
def listar_fotos_preexpedicao(cod_ordem_fat):
    ordem = ExpedicaoOrdemFat.query.filter_by(cod_ordem_fat=cod_ordem_fat).first()
    if not ordem:
        return jsonify({"error": "Ordem de faturamento nao encontrada."}), 404
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


@expedicao_fat_bp.route(
    "/api/expedicao/conf-cega/ordens/<int:cod_ordem_fat>/fotos-preexpedicao",
    methods=["POST"],
)
@roles_required(*ROLES)
def upload_fotos_preexpedicao(cod_ordem_fat):
    """Anexa fotos do material e/ou do cliente a uma ordem ja Faturada, ANTES
    da expedicao. As fotos ficam num Registro de Expedicao em rascunho e sao
    reaproveitadas quando o romaneio for expedido (registro vira Finalizado)."""
    ordem = ExpedicaoOrdemFat.query.filter_by(cod_ordem_fat=cod_ordem_fat).first()
    if not ordem:
        return jsonify({"error": "Ordem de faturamento nao encontrada."}), 404
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
        current_app.logger.exception("Falha ao salvar fotos de pre-expedicao")
        return jsonify({"error": f"Falha ao salvar as fotos: {exc}"}), 502

    registro.updated_at = agora
    db.session.commit()
    return jsonify({"sucesso": True, **_fotos_preexpedicao_payload(registro)})


@expedicao_fat_bp.route("/api/expedicao/conf-cega/ordens/<int:cod_ordem_fat>", methods=["DELETE"])
@roles_required("Admin")
def excluir_ordem_conf_cega(cod_ordem_fat):
    """Exclui (soft-delete) uma ordem de faturamento do dashboard/fila.

    A linha e todo o historico de conferencia NAO sao apagados do banco -
    apenas somem da fila normal. Continuam localizaveis pela Auditoria de
    Expedicao (codigo interno, cliente, ordem etc.), garantindo que a ordem
    permaneca rastreavel mesmo apos a exclusao."""
    ordem = ExpedicaoOrdemFat.query.filter_by(cod_ordem_fat=cod_ordem_fat, excluido=False).first()
    if not ordem:
        return jsonify({"error": "Ordem de faturamento nao encontrada."}), 404

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
        origem="fat",
        ordem_id=ordem.id,
        cod_ordem=ordem.cod_ordem_fat,
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
    operacao_tipo = str(payload.get("operacao_tipo") or "nacional").strip().lower()

    if operacao_tipo not in ("nacional", "internacional"):
        return jsonify({"error": "Selecione se a operacao e nacional ou internacional."}), 400

    if not (peso_liquido and peso_bruto and qtde_volumes and especie_volumes):
        return jsonify({
            "error": "Preencha peso liquido, peso bruto, qtde de volumes e especie dos volumes."
        }), 400

    def _parse_float_pos(valor):
        try:
            num = float(str(valor).strip().replace(",", "."))
        except (TypeError, ValueError):
            return None
        return num if num > 0 else None

    # Mapa item_id -> quantidade conferida
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
            (it.cod_interno or it.item or f"item {it.id}")
            for it in faltando
        ]
        detalhe = ", ".join(str(n) for n in nomes[:5])
        if len(nomes) > 5:
            detalhe += f" (+{len(nomes) - 5})"
        return jsonify({
            "error": f"Informe a quantidade conferida de todos os itens. Pendentes: {detalhe}.",
            "itens_faltando": [it.id for it in faltando],
        }), 400

    # Operacao internacional: o conferente adiciona manualmente a lista de
    # volumes do embarque. Cada volume exige especie, quantidade e as medidas
    # (altura, comprimento, largura em cm) + peso (kg).
    volumes_limpos = []
    if operacao_tipo == "internacional":
        volumes_payload = payload.get("volumes") or []
        if not isinstance(volumes_payload, list) or not volumes_payload:
            return jsonify({
                "error": "Operacao internacional: adicione ao menos um volume do embarque."
            }), 400
        for idx, vol in enumerate(volumes_payload):
            if not isinstance(vol, dict):
                continue
            especie = str(vol.get("especie") or "").strip()
            quantidade = svc._parse_int(vol.get("quantidade"), None)
            altura = _parse_float_pos(vol.get("altura_cm"))
            comprimento = _parse_float_pos(vol.get("comprimento_cm"))
            largura = _parse_float_pos(vol.get("largura_cm"))
            peso = _parse_float_pos(vol.get("peso_kg"))
            if not (especie and quantidade and quantidade > 0 and altura
                    and comprimento and largura and peso):
                return jsonify({
                    "error": (
                        "Operacao internacional: preencha especie, quantidade, "
                        "altura, comprimento, largura (cm) e peso (kg) de todos os volumes."
                    ),
                    "volume_incompleto": idx + 1,
                }), 400
            volumes_limpos.append({
                "especie": especie[:120],
                "quantidade": quantidade,
                "altura_cm": altura,
                "comprimento_cm": comprimento,
                "largura_cm": largura,
                "peso_kg": peso,
            })

    # Snapshot ANTES das alteracoes (para a trilha de auditoria).
    era_conferido = ordem.conferido_at is not None
    status_anterior = ordem.status
    header_antes = {
        "operacao_tipo": ordem.operacao_tipo,
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
    ordem.operacao_tipo = operacao_tipo
    ordem.peso_liquido = peso_liquido

    # Volumes do embarque: regrava a lista (internacional) ou limpa (nacional).
    ExpedicaoOrdemFatVolume.query.filter_by(ordem_id=ordem.id).delete()
    if operacao_tipo == "internacional":
        for idx, vol in enumerate(volumes_limpos):
            db.session.add(ExpedicaoOrdemFatVolume(
                ordem_id=ordem.id,
                linha=idx,
                especie=vol["especie"],
                quantidade=vol["quantidade"],
                altura_cm=vol["altura_cm"],
                comprimento_cm=vol["comprimento_cm"],
                largura_cm=vol["largura_cm"],
                peso_kg=vol["peso_kg"],
            ))
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
        "operacao_tipo": operacao_tipo,
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

    # Devolve peso/volumes + liberacao pro faturamento pra API do emitente
    # (assincrono, best-effort - nunca bloqueia o salvamento local). Envia em
    # toda conferencia salva (primeira vez ou edicao), pra manter o dado deles
    # atualizado com o que foi de fato conferido.
    try:
        from ..services import erp_ordem_fat_client as erp_fat_svc

        erp_fat_svc.atualizar_ordem_faturamento(
            ordem.cod_ordem_fat,
            especie_volumes=ordem.especie_volumes,
            qtde_volumes=ordem.qtde_volumes,
            peso_liquido=ordem.peso_liquido,
            peso_bruto=ordem.peso_bruto,
            liberado_para_faturamento=1,
        )
    except Exception:
        current_app.logger.exception(
            "Falha ao acionar atualizacao de ordem-faturamento no emitente (%s)", cod_ordem_fat
        )

    # Aviso no Teams apenas na primeira conferencia concluida. Edicoes
    # posteriores (inclusive correcao administrativa em Faturado) nao reenviam.
    if not era_conferido:
        try:
            from ..services.teams_service import notificar_expedicao_conferida

            notificar_expedicao_conferida(
                ordem.cliente or "Cliente não informado",
                f"Orçamento {ordem.orcamento or '—'}",
                conferente=ordem.conferente,
                volumes=ordem.qtde_volumes,
                peso_liquido=ordem.peso_liquido,
                peso_bruto=ordem.peso_bruto,
                especie_volumes=ordem.especie_volumes,
            )
        except Exception:
            current_app.logger.exception("Falha ao notificar Teams (conf-cega FAT %s)", cod_ordem_fat)

    return jsonify({
        "sucesso": True,
        "divergente": ordem_divergente,
        "ordem": _ordem_resumo(ordem, len(itens)),
        "itens": resultado_itens,
        "historico": log_svc.listar_logs("fat", ordem.id),
    })


@expedicao_fat_bp.route(
    "/api/expedicao/conf-cega/ordens/<int:cod_ordem_fat>/finalizar-sem-conferencia",
    methods=["POST"],
)
@roles_required("Admin")
def finalizar_sem_conferencia_fat(cod_ordem_fat):
    """Encerra a ordem SEM conferencia fisica (acao exclusiva de Admin).

    A ordem sai da fila de pendentes e NAO segue para o Registro de expedicao.
    """
    ordem = ExpedicaoOrdemFat.query.filter_by(cod_ordem_fat=cod_ordem_fat).first()
    if not ordem:
        return jsonify({"error": "Ordem de faturamento nao encontrada."}), 404
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
        origem="fat",
        ordem_id=ordem.id,
        cod_ordem=ordem.cod_ordem_fat,
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


@expedicao_fat_bp.route(
    "/api/expedicao/conf-cega/ordens/<int:cod_ordem_fat>/seguir-sem-contagem",
    methods=["POST"],
)
@roles_required("Admin")
def seguir_sem_contagem_fat(cod_ordem_fat):
    """Admin: pula a contagem e envia a ordem direto para 'Aguardando faturamento'.

    Diferente de 'finalizar sem conferencia' (que encerra a ordem), esta acao
    apenas dispensa a contagem fisica e mantem a ordem no fluxo normal, seguindo
    para 'Conferido/Ag. Fat' (ou 'Faturado', quando a NF ja foi emitida).
    """
    ordem = ExpedicaoOrdemFat.query.filter_by(cod_ordem_fat=cod_ordem_fat).first()
    if not ordem:
        return jsonify({"error": "Ordem de faturamento nao encontrada."}), 404
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
        origem="fat",
        ordem_id=ordem.id,
        cod_ordem=ordem.cod_ordem_fat,
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
            ordem.cliente or "Cliente não informado",
            f"Orçamento {ordem.orcamento or '—'}",
            conferente=ordem.conferente,
            observacao=observacao,
            titulo="⏭️ Expedição seguiu sem contagem",
        )
    except Exception:
        current_app.logger.exception("Falha ao notificar Teams (seguir sem contagem FAT %s)", cod_ordem_fat)

    return jsonify({
        "sucesso": True,
        "status": ordem.status,
        "status_slug": svc.status_slug(ordem.status),
    })


@expedicao_fat_bp.route(
    "/api/expedicao/conf-cega/ordens/<int:cod_ordem_fat>/estornar-conferencia",
    methods=["POST"],
)
@roles_required("Admin")
def estornar_conferencia_fat(cod_ordem_fat):
    """Estorna a conferencia de uma ordem (acao exclusiva de Admin).

    - Conferido/Ag. Fat  -> volta para Pendente de conferencia.
    - Faturado (conferido)-> volta para "Faturado sem conferência" (mantem NF).
    - Finalizada sem conferência -> volta para Pendente de conferencia.
    Limpa a contagem e os dados de conferencia para permitir refazer.
    """
    ordem = ExpedicaoOrdemFat.query.filter_by(cod_ordem_fat=cod_ordem_fat).first()
    if not ordem:
        return jsonify({"error": "Ordem de faturamento nao encontrada."}), 404

    slug = svc.status_slug(ordem.status)
    if slug not in ("conferido", "faturado", "finalizado_sem_conf"):
        # Regra de workflow: so se pode estornar a ULTIMA etapa concluida, na
        # ordem inversa. Se ha uma etapa posterior ativa (Romaneio/Expedido),
        # orienta o usuario a estornar primeiro a etapa mais recente.
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

    if slug == "faturado":
        # Em Faturado, o estorno vira "modo correcao": habilita edicao de
        # peso/volumes sem desmontar a conferencia e sem retroceder etapa.
        ordem.status = svc.STATUS_FATURADO
        ordem.divergente = False
        ordem.conferido_pos_faturamento = True
        if not ordem.conferido_at:
            ordem.conferido_at = agora
        for it in ordem.itens:
            if it.qtde_conferida is None:
                it.qtde_conferida = it.qtde_a_faturar
            it.divergente = False
    else:
        ordem.status = svc.STATUS_PENDENTE
        ordem.conferente = None
        ordem.conferido_at = None
        ordem.divergente = False
        ordem.conferido_pos_faturamento = False
        ordem.peso_liquido = None
        ordem.peso_bruto = None
        ordem.qtde_volumes = None
        ordem.especie_volumes = None
        ordem.marca_volumes = None
        for it in ordem.itens:
            it.qtde_conferida = None
            it.divergente = False
    ordem.updated_at = agora

    diff_cab = [{"campo": "motivo", "label": "Motivo", "de": "", "para": motivo}] if motivo else []
    log_svc.registrar_log(
        origem="fat",
        ordem_id=ordem.id,
        cod_ordem=ordem.cod_ordem_fat,
        acao="estorno_para_correcao_faturado" if slug == "faturado" else "estorno_conferencia",
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
        "modo_correcao": bool(slug == "faturado"),
    })


@expedicao_fat_bp.route("/api/expedicao/conf-cega/ordens/<int:cod_ordem_fat>/corrigir-nf", methods=["POST"])
@roles_required("Admin")
def corrigir_nf_ordem_fat(cod_ordem_fat):
    """Corrige o numero da NF de uma ordem ja Faturada (ex.: NF cancelada e
    reemitida com numero novo) SEM desfazer a conferencia/pesos ja
    registrados — diferente de 'estornar', que reabre a conferencia inteira.

    A sincronizacao automatica so grava numero_nf na transicao para
    Faturado (expedicao_fat_service.py); uma vez Faturado, ela nunca mais
    sobrescreve o campo — a correcao manual feita aqui e definitiva."""
    ordem = ExpedicaoOrdemFat.query.filter_by(cod_ordem_fat=cod_ordem_fat).first()
    if not ordem:
        return jsonify({"error": "Ordem de faturamento nao encontrada."}), 404

    if ordem.status != svc.STATUS_FATURADO:
        return jsonify({"error": "Só é possível corrigir a NF de ordens no status Faturado."}), 400

    payload = request.get_json(silent=True) or {}
    numero_novo = str(payload.get("numero_nf_novo") or "").strip()
    if not numero_novo:
        return jsonify({"error": "Informe o número da nova NF."}), 400

    from ..services.erp_nfe_emitidas_service import buscar_nfe_emitida_erp

    try:
        nota = buscar_nfe_emitida_erp(numero_nf=numero_novo)
    except Exception as exc:
        current_app.logger.exception("Falha ao consultar NF %s no ERP para corrigir-nf", numero_novo)
        return jsonify({"error": f"Falha ao consultar a NF no ERP: {exc}"}), 502

    if not nota or not nota.get("autorizada"):
        return jsonify({
            "error": f"NF {numero_novo} não encontrada ou não autorizada no ERP. Confira o número antes de corrigir."
        }), 400

    numero_anterior = ordem.numero_nf
    agora = datetime.now()
    usuario = session.get("username") or "desconhecido"

    ordem.numero_nf = str(nota.get("numero") or numero_novo).strip()
    ordem.updated_at = agora

    log_svc.registrar_log(
        origem="fat",
        ordem_id=ordem.id,
        cod_ordem=ordem.cod_ordem_fat,
        acao="corrigir_nf",
        usuario=usuario,
        status_anterior=ordem.status,
        status_novo=ordem.status,
        divergente=False,
        pos_faturamento=False,
        diff_cabecalho=[{"campo": "numero_nf", "label": "Número da NF", "de": numero_anterior or "", "para": ordem.numero_nf}],
        diff_itens=[],
    )
    db.session.commit()

    return jsonify({"sucesso": True, "numero_nf": ordem.numero_nf})


@expedicao_fat_bp.route("/api/expedicao/conf-cega/ordens/<int:cod_ordem_fat>/informar-nf", methods=["POST"])
@roles_required("Admin")
def informar_nf_ordem_fat(cod_ordem_fat):
    """Informa manualmente o numero da NF de uma ordem ja Conferida, faturando-a
    (acao exclusiva de Admin).

    A sincronizacao automatica so grava numero_nf e avanca para Faturado
    quando a API externa de faturamento reporta a NF (expedicao_fat_service.py).
    Se a solicitacao de faturamento for excluida na origem antes da NF ser
    emitida, a ordem fica presa em 'Conferido/Ag. Fat' para sempre — esta acao
    destrava manualmente, buscando a NF diretamente no ERP para confirmar que
    ela realmente existe e esta autorizada antes de faturar a ordem."""
    ordem = ExpedicaoOrdemFat.query.filter_by(cod_ordem_fat=cod_ordem_fat).first()
    if not ordem:
        return jsonify({"error": "Ordem de faturamento nao encontrada."}), 404

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
        current_app.logger.exception("Falha ao consultar NF %s no ERP para informar-nf", numero_nf)
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
        origem="fat",
        ordem_id=ordem.id,
        cod_ordem=ordem.cod_ordem_fat,
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


@expedicao_fat_bp.route("/api/expedicao/dados-envio", methods=["GET"])
def dados_envio_expedicao():
    """Endpoint público de consulta para sistemas externos - SEM autenticação necessária.

    GATILHOS:
    1. Após CONFERÊNCIA: Retorna dados EXCETO data_expedicao (null)
    2. Após EXPEDIÇÃO: Retorna dados COM data_expedicao preenchida
    3. Após 10 DIAS DE EXPEDIÇÃO: Remove da API automaticamente

    Parâmetros opcionais (query string):
        status  — filtra pelo status: conferido | faturado | expedido (padrão: todos)
        desde   — data mínima de atualização no formato YYYY-MM-DD
        limite  — máximo de registros (padrão 200, máximo 1000)
        offset  — paginação - número de registros a pular (padrão 0)

    Exemplo:
        GET /api/expedicao/dados-envio?status=expedido&desde=2026-07-01&limite=50
    """
    from datetime import datetime as _datetime, date as _date, timedelta

    try:
        status_filtro = (request.args.get("status") or "").strip().lower()
        desde_str = (request.args.get("desde") or "").strip()
        limite = min(int(request.args.get("limite", 200)), 1000)
        offset = max(int(request.args.get("offset", 0)), 0)

        query = ExpedicaoOrdemFat.query

        # Filtra apenas ordens que já têm dados de conferência (descarta Pendentes)
        status_validos = {
            "conferido": svc.STATUS_CONFERIDO,
            "faturado": svc.STATUS_FATURADO,
            "expedido": svc.STATUS_EXPEDIDO,
        }
        if status_filtro and status_filtro in status_validos:
            query = query.filter(ExpedicaoOrdemFat.status == status_validos[status_filtro])
        else:
            query = query.filter(
                ExpedicaoOrdemFat.status.in_(list(status_validos.values()))
            )

        if desde_str:
            try:
                desde = _date.fromisoformat(desde_str)
                query = query.filter(ExpedicaoOrdemFat.updated_at >= desde)
            except ValueError:
                return jsonify({
                    "success": False,
                    "error": "Parâmetro 'desde' inválido. Use YYYY-MM-DD.",
                    "timestamp": _datetime.utcnow().isoformat() + "Z"
                }), 400

        ordens_brutos = (
            query
            .order_by(ExpedicaoOrdemFat.updated_at.desc())
            .all()
        )

        # FILTRO DE EXPIRAÇÃO: Remove ordens que foram expedidas há mais de 10 dias
        agora = _datetime.utcnow()
        ordens_filtradas = []
        
        for ordem in ordens_brutos:
            if ordem.status == svc.STATUS_EXPEDIDO and ordem.expedido_at:
                dias_desde_expedicao = (agora - ordem.expedido_at).days
                if dias_desde_expedicao > 10:
                    # Passou 10 dias, não incluir na API
                    continue
            
            ordens_filtradas.append(ordem)

        # Conta total de registros disponíveis (após filtro de expiração)
        total_available = len(ordens_filtradas)

        # Aplica paginação
        ordens = ordens_filtradas[offset:offset + limite]

        resultado = []
        for ordem in ordens:
            os_lista = sorted({
                (it.n_os or "").strip()
                for it in ordem.itens
                if (it.n_os or "").strip()
            })
            # Extrai últimos 4 dígitos de OS e Orçamento
            os_ultimos = ", ".join([os[-4:] if len(os) >= 4 else os for os in os_lista])
            orcamento_ultimos = ordem.orcamento[-4:] if ordem.orcamento and len(str(ordem.orcamento)) >= 4 else ordem.orcamento
            
            item = {
                "n_os": os_ultimos,
                "orcamento": orcamento_ultimos,
                "n_ordem_faturamento": f"#{ordem.cod_ordem_fat}",
                "peso_liquido": ordem.peso_liquido,
                "peso_bruto": ordem.peso_bruto,
                "qtde_volumes": ordem.qtde_volumes,
                "especie_volumes": ordem.especie_volumes,
            }
            
            # GATILHO 1: Se conferido/faturado (NÃO expedido) → data_expedicao = None
            # GATILHO 2: Se expedido → data_expedicao com a data
            if ordem.status == svc.STATUS_EXPEDIDO:
                item["data_expedicao"] = _iso(ordem.expedido_at)
            else:
                item["data_expedicao"] = None
            
            resultado.append(item)

        resposta = {
            "success": True,
            "data": resultado,
            "count": len(resultado),
            "total_available": total_available,
            "offset": offset,
            "limit": limite,
            "timestamp": _datetime.utcnow().isoformat() + "Z"
        }

        return jsonify(resposta), 200

    except Exception as e:
        from datetime import datetime as _datetime
        return jsonify({
            "success": False,
            "error": str(e),
            "timestamp": _datetime.utcnow().isoformat() + "Z"
        }), 500
