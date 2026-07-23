"""Rotas do Romaneio de Expedição."""

import base64
import os
from datetime import datetime
from flask import Blueprint, request, jsonify, render_template, session, current_app
from ..extensions import db
from ..models import (
    ExpedicaoRomaneio,
    ExpedicaoRomaneioNF,
    ExpedicaoRomaneioExclusao,
    ExpedicaoOrdemFat,
    ExpedicaoOrdemFatItem,
    ExpedicaoConferenciaSimples,
)
from ..auth import permission_required, roles_required
from ..services import expedicao_fat_service as fat_svc
from ..services import cadastro_workflow_service as cad_svc
from ..services import danfe_service
from ..services.erp_nfe_emitidas_service import buscar_nfe_emitida_erp
from ..services.expedicao_photo_storage import using_drive, upload_bytes_to_drive
from .api_routes import _resolver_foto_expedicao, _send_foto_expedicao

expedicao_romaneio_bp = Blueprint("expedicao_romaneio", __name__)

PERMISSION = "PAGE_EXPEDICAO_CONF_CEGA"
ROLES = ("Conferente", "Admin", "Fiscal", "Logística")
ROLES_NAO_ADMIN = ("Conferente", "Fiscal", "Logística")


def _parse_float(valor, default=0.0) -> float:
    try:
        texto = str(valor if valor is not None else "").strip().replace(",", ".")
        return float(texto) if texto else default
    except (TypeError, ValueError):
        return default


def _parse_int(valor, default=0) -> int:
    return int(_parse_float(valor, default))


def _finalizar_registro_expedicao_para_nf(nf, romaneio, usuario):
    """Ao expedir o romaneio, garante um Registro de Expedicao ja Finalizado
    para a NF. Se ja existir um rascunho (fotos do material/cliente tiradas na
    etapa Faturado), promove-o; senao cria um novo. O proprio romaneio faz as
    vezes de canhoto, portanto NAO se exige foto de canhoto neste fluxo.
    Retorna o registro (ou None se a NF for vazia)."""
    numero_nf = str(getattr(nf, "numero_nf", "") or "").strip()
    if not numero_nf:
        return None
    agora = datetime.now()

    ordem = ExpedicaoOrdemFat.query.filter_by(numero_nf=numero_nf).first()
    registro = None
    if ordem is not None and ordem.expedicao_registro_id:
        registro = ExpedicaoConferenciaSimples.query.get(ordem.expedicao_registro_id)
    if registro is None:
        # Rascunho vinculado pela NF (fotos tiradas no Faturado sem link direto).
        registro = (
            ExpedicaoConferenciaSimples.query
            .filter_by(numero_nf=numero_nf, origem="Romaneio")
            .filter(ExpedicaoConferenciaSimples.status.in_(
                ["Pendente de expedição", "Pendente de expedicao"]))
            .order_by(ExpedicaoConferenciaSimples.id.desc())
            .first()
        )

    numero_os = str(getattr(nf, "numeros_os", "") or "").strip() or None
    orcamento = str(getattr(nf, "orcamento", "") or romaneio.orcamento or "").strip()
    cliente = str(getattr(nf, "cliente", "") or romaneio.cliente or "").strip()
    transportadora = str(getattr(romaneio, "transportadora", "") or "").strip() or None
    placa = str(getattr(romaneio, "placa", "") or "").strip() or None
    motorista = str(getattr(romaneio, "motorista", "") or "").strip() or None

    if registro is None:
        registro = ExpedicaoConferenciaSimples(
            orcamento=orcamento,
            tipo_referencia="Orcamento",
            numero_os=numero_os,
            conferente=usuario,
            numero_nf=numero_nf,
            nome_cliente=cliente,
            cliente_origem="Consyste",
            nf_origem="Consyste",
            origem="Romaneio",
            transportadora=transportadora,
            placa=placa,
            motorista=motorista,
            status="Finalizado",
        )
        db.session.add(registro)
    else:
        registro.origem = "Romaneio"
        if not registro.numero_os and numero_os:
            registro.numero_os = numero_os
        if not registro.nome_cliente and cliente:
            registro.nome_cliente = cliente
        if transportadora:
            registro.transportadora = transportadora
        if placa:
            registro.placa = placa
        if motorista:
            registro.motorista = motorista
        registro.status = "Finalizado"

    # Romaneio = canhoto: registro nasce finalizado, sem foto de canhoto.
    if not registro.expedido_at:
        registro.expedido_at = agora
        registro.expedido_by = usuario
    registro.finalizado_at = agora
    registro.finalizado_by = usuario
    registro.updated_at = agora
    db.session.flush()

    if ordem is not None:
        ordem.expedicao_registro_id = registro.id
    return registro


@expedicao_romaneio_bp.route("/expedicao/romaneio")
@permission_required(PERMISSION)
def lista_romaneios():
    """Página principal de gerenciamento de romaneios."""
    return render_template(
        "expedicao_romaneio.html",
        user=session["username"],
        user_role=session.get("role", ""),
        is_admin=session.get("role") == "Admin",
    )


def _exclusao_pendente_dict(romaneio_id: int) -> dict | None:
    exclusao = ExpedicaoRomaneioExclusao.query.filter_by(
        romaneio_id=romaneio_id, status="Pendente"
    ).order_by(ExpedicaoRomaneioExclusao.created_at.desc()).first()
    if not exclusao:
        return None
    return {
        "id": exclusao.id,
        "solicitante": exclusao.solicitante,
        "motivo": exclusao.motivo,
        "created_at": exclusao.created_at.isoformat() if exclusao.created_at else None,
    }


@expedicao_romaneio_bp.route("/api/expedicao/romaneio-fat", methods=["GET"])
@permission_required(PERMISSION)
def listar_romaneios():
    """Lista todos os romaneios com filtros opcionais."""
    status = (request.args.get("status") or "").strip()
    busca = (request.args.get("q") or "").strip().lower()

    query = ExpedicaoRomaneio.query.order_by(ExpedicaoRomaneio.criado_em.desc())
    
    if status:
        query = query.filter_by(status=status)
    
    romaneios = query.all()
    
    # Filtro por busca (número, orçamento, cliente ou qualquer NF incluída)
    if busca:
        romaneios = [
            r for r in romaneios
            if busca in str(r.numero_romaneio or "").lower()
            or busca in str(r.orcamento or "").lower()
            or busca in str(r.cliente or "").lower()
            or any(busca in str(nf.numero_nf or "").lower() for nf in (r.nfs or []))
        ]
    
    data = [
        {
            "id": r.id,
            "numero_romaneio": r.numero_romaneio,
            "data_romaneio": r.data_romaneio.isoformat() if r.data_romaneio else None,
            "orcamento": r.orcamento,
            "cliente": r.cliente,
            "tipo_frete": r.tipo_frete,
            "peso_bruto_total": r.peso_bruto_total,
            "qtde_volumes_total": r.qtde_volumes_total,
            "qtde_nfs": len(r.nfs) if r.nfs else 0,
            "nfs": [
                {
                    "id": nf.id,
                    "numero_nf": nf.numero_nf,
                    "orcamento": nf.orcamento,
                    "cliente": nf.cliente,
                    "peso_bruto": nf.peso_bruto,
                    "qtde_volumes": nf.qtde_volumes,
                }
                for nf in (r.nfs or [])
            ],
            "status": r.status,
            "criado_por": r.criado_por,
            "criado_em": r.criado_em.isoformat() if r.criado_em else None,
            "exclusao_pendente": _exclusao_pendente_dict(r.id) if r.status == "Rascunho" else None,
        }
        for r in romaneios
    ]
    
    return jsonify({"romaneios": data})


@expedicao_romaneio_bp.route("/api/expedicao/romaneio-fat", methods=["POST"])
@roles_required(*ROLES)
def criar_romaneio():
    """Cria um novo romaneio em branco."""
    payload = request.get_json(silent=True) or {}
    
    # Gera número único para o romaneio (p.ex: ROM-2026-0001). Usa o MAIOR
    # sufixo numérico já existente no ano (não a contagem de linhas) — contar
    # linhas gera número duplicado sempre que um romaneio anterior é excluído
    # (o total cai, mas o número "vago" já pode pertencer a um romaneio que
    # ainda existe), disparando IntegrityError na coluna única.
    hoje = datetime.now()
    prefixo = f"ROM-{hoje.year}-"
    existentes = (
        ExpedicaoRomaneio.query
        .filter(ExpedicaoRomaneio.numero_romaneio.like(f"{prefixo}%"))
        .with_entities(ExpedicaoRomaneio.numero_romaneio)
        .all()
    )
    maior_sequencia = 0
    for (numero,) in existentes:
        sufixo = str(numero or "")[len(prefixo):]
        if sufixo.isdigit():
            maior_sequencia = max(maior_sequencia, int(sufixo))
    numero_romaneio = f"{prefixo}{maior_sequencia + 1:04d}"
    
    romaneio = ExpedicaoRomaneio(
        numero_romaneio=numero_romaneio,
        data_romaneio=datetime.now().date(),
        criado_por=session["username"],
    )
    
    db.session.add(romaneio)
    db.session.commit()
    
    return jsonify({
        "id": romaneio.id,
        "numero_romaneio": romaneio.numero_romaneio,
        "status": "Rascunho",
    }), 201


@expedicao_romaneio_bp.route("/api/expedicao/romaneio-fat/<int:romaneio_id>", methods=["GET"])
@permission_required(PERMISSION)
def obter_romaneio(romaneio_id):
    """Obtém detalhes de um romaneio específico."""
    romaneio = ExpedicaoRomaneio.query.get(romaneio_id)
    if not romaneio:
        return jsonify({"error": "Romaneio não encontrado."}), 404
    
    return jsonify({
        "id": romaneio.id,
        "numero_romaneio": romaneio.numero_romaneio,
        "data_romaneio": romaneio.data_romaneio.isoformat() if romaneio.data_romaneio else None,
        "orcamento": romaneio.orcamento,
        "cliente": romaneio.cliente,
        "tipo_frete": romaneio.tipo_frete,
        "transportadora_nome": romaneio.transportadora,
        "placa": romaneio.placa,
        "motorista_nome": romaneio.motorista,
        "motorista_documento": romaneio.motorista_documento,
        "peso_bruto_total": romaneio.peso_bruto_total,
        "qtde_volumes_total": romaneio.qtde_volumes_total,
        "observacao_1": romaneio.observacao_1,
        "observacao_2": romaneio.observacao_2,
        "observacao_3": romaneio.observacao_3,
        "status": romaneio.status,
        "criado_por": romaneio.criado_por,
        "criado_em": romaneio.criado_em.isoformat() if romaneio.criado_em else None,
        "exclusao_pendente": _exclusao_pendente_dict(romaneio.id) if romaneio.status == "Rascunho" else None,
        "assinatura_conferente_url": (
            f"/api/expedicao/romaneio-fat/{romaneio.id}/assinatura/conferente"
            if romaneio.assinatura_conferente_file_name else None
        ),
        "assinatura_transportador_url": (
            f"/api/expedicao/romaneio-fat/{romaneio.id}/assinatura/transportador"
            if romaneio.assinatura_transportador_file_name else None
        ),
        "nfs": [
            {
                "id": nf.id,
                "numero_nf": nf.numero_nf,
                "orcamento": nf.orcamento,
                "cliente": nf.cliente,
                "peso_bruto": nf.peso_bruto,
                "qtde_volumes": nf.qtde_volumes,
                "especie_volumes": nf.especie_volumes,
                "numeros_os": nf.numeros_os,
                "adicionado_em": nf.adicionado_em.isoformat() if nf.adicionado_em else None,
                "adicionado_por": nf.adicionado_por,
            }
            for nf in romaneio.nfs or []
        ],
    })


@expedicao_romaneio_bp.route("/api/expedicao/romaneio-fat/<int:romaneio_id>", methods=["PUT"])
@roles_required(*ROLES)
def atualizar_romaneio(romaneio_id):
    """Atualiza informações do romaneio."""
    romaneio = ExpedicaoRomaneio.query.get(romaneio_id)
    if not romaneio:
        return jsonify({"error": "Romaneio não encontrado."}), 404
    
    if romaneio.status != "Rascunho":
        return jsonify({"error": "Apenas romaneios em Rascunho podem ser editados."}), 400
    
    payload = request.get_json(silent=True) or {}
    
    # Atualiza campos permitidos
    if "orcamento" in payload:
        romaneio.orcamento = str(payload["orcamento"]).strip()
    if "cliente" in payload:
        romaneio.cliente = str(payload["cliente"]).strip()
    if "tipo_frete" in payload:
        frete = str(payload["tipo_frete"]).strip().upper()
        if frete not in ("FOB", "CIF"):
            return jsonify({"error": "Tipo de frete deve ser FOB ou CIF."}), 400
        romaneio.tipo_frete = frete
    if "transportadora" in payload:
        romaneio.transportadora = str(payload["transportadora"]).strip()
    if "placa" in payload:
        romaneio.placa = str(payload["placa"]).strip()
    if "motorista" in payload:
        romaneio.motorista = str(payload["motorista"]).strip()
    if "motorista_documento" in payload:
        romaneio.motorista_documento = str(payload["motorista_documento"]).strip()
    if "observacao_1" in payload:
        romaneio.observacao_1 = str(payload["observacao_1"]).strip()
    if "observacao_2" in payload:
        romaneio.observacao_2 = str(payload["observacao_2"]).strip()
    if "observacao_3" in payload:
        romaneio.observacao_3 = str(payload["observacao_3"]).strip()
    
    romaneio.atualizado_por = session["username"]
    romaneio.atualizado_em = datetime.now()
    db.session.commit()
    
    return jsonify({"message": "Romaneio atualizado com sucesso."})


@expedicao_romaneio_bp.route("/api/expedicao/romaneio-fat/<int:romaneio_id>/nf", methods=["POST"])
@roles_required(*ROLES)
def adicionar_nf_ao_romaneio(romaneio_id):
    """Adiciona uma NF ao romaneio."""
    romaneio = ExpedicaoRomaneio.query.get(romaneio_id)
    if not romaneio:
        return jsonify({"error": "Romaneio não encontrado."}), 404
    
    if romaneio.status != "Rascunho":
        return jsonify({"error": "Apenas romaneios em Rascunho podem receber NFes."}), 400
    
    payload = request.get_json(silent=True) or {}
    numero_nf = (payload.get("numero_nf") or "").strip()
    
    if not numero_nf:
        return jsonify({"error": "Número da NF é obrigatório."}), 400
    
    # Verifica se NF já foi adicionada
    existe = ExpedicaoRomaneioNF.query.filter_by(
        romaneio_id=romaneio_id,
        numero_nf=numero_nf,
    ).first()
    if existe:
        return jsonify({"error": f"NF {numero_nf} já foi adicionada a este romaneio."}), 400
    
    # Busca os dados reais da NF na ordem de faturamento (preenchidos pelo
    # conferente na Conferência de Expedição) — peso/volume devem ser
    # identicos ao que consta na NF, nao digitados de novo aqui. So cai no
    # que veio no payload se a NF nao estiver rastreada em ExpedicaoOrdemFat
    # (ex.: NF fora do fluxo normal de faturamento).
    ordem_fat = ExpedicaoOrdemFat.query.filter_by(numero_nf=numero_nf).first()
    if ordem_fat:
        orcamento = ordem_fat.orcamento or payload.get("orcamento") or romaneio.orcamento
        cliente = ordem_fat.cliente or payload.get("cliente") or romaneio.cliente
        peso_bruto = _parse_float(ordem_fat.peso_bruto, _parse_float(payload.get("peso_bruto")))
        qtde_volumes = _parse_int(ordem_fat.qtde_volumes, _parse_int(payload.get("qtde_volumes")))
        especie_volumes = ordem_fat.especie_volumes or payload.get("especie_volumes") or ""
        # Numeros de OS vem dos itens da propria ordem de faturamento (mesma
        # fonte usada pelo pre-fill do Registro de Expedicao a partir da
        # Conferencia de Expedicao) — nao digitados manualmente aqui.
        oss_itens = (
            ExpedicaoOrdemFatItem.query
            .filter_by(ordem_id=ordem_fat.id)
            .with_entities(ExpedicaoOrdemFatItem.n_os)
            .distinct()
            .all()
        )
        oss_unicas = [str(v[0]).strip() for v in oss_itens if v[0] and str(v[0]).strip()]
        numeros_os = ", ".join(oss_unicas) if oss_unicas else (payload.get("numeros_os") or "")
    else:
        orcamento = payload.get("orcamento") or romaneio.orcamento
        cliente = payload.get("cliente") or romaneio.cliente
        peso_bruto = _parse_float(payload.get("peso_bruto"))
        qtde_volumes = _parse_int(payload.get("qtde_volumes"))
        especie_volumes = payload.get("especie_volumes") or ""
        numeros_os = payload.get("numeros_os") or ""
    
    nf = ExpedicaoRomaneioNF(
        romaneio_id=romaneio_id,
        numero_nf=numero_nf,
        orcamento=orcamento,
        cliente=cliente,
        peso_bruto=peso_bruto,
        qtde_volumes=qtde_volumes,
        especie_volumes=especie_volumes,
        numeros_os=numeros_os,
        adicionado_por=session["username"],
    )
    
    db.session.add(nf)
    
    # Atualiza totais do romaneio
    romaneio.peso_bruto_total = sum(n.peso_bruto for n in romaneio.nfs or []) + peso_bruto
    romaneio.qtde_volumes_total = sum(n.qtde_volumes for n in romaneio.nfs or []) + qtde_volumes
    
    # Se for a primeira NF, usa seus dados como padrão
    if len(romaneio.nfs or []) == 1:
        if not romaneio.orcamento:
            romaneio.orcamento = orcamento
        if not romaneio.cliente:
            romaneio.cliente = cliente
    
    romaneio.atualizado_em = datetime.now()
    db.session.commit()
    
    # Fluxo progressivo: a ordem cuja NF entrou no romaneio sai da etapa
    # "Faturado" e passa para "Em Romaneio".
    fat_svc.marcar_em_romaneio_por_nf(numero_nf)
    
    return jsonify({
        "id": nf.id,
        "numero_nf": nf.numero_nf,
        "message": "NF adicionada com sucesso.",
    }), 201


@expedicao_romaneio_bp.route("/api/expedicao/romaneio-fat/<int:romaneio_id>/nf/<int:nf_id>", methods=["DELETE"])
@roles_required(*ROLES)
def remover_nf_do_romaneio(romaneio_id, nf_id):
    """Remove uma NF do romaneio."""
    romaneio = ExpedicaoRomaneio.query.get(romaneio_id)
    if not romaneio:
        return jsonify({"error": "Romaneio não encontrado."}), 404
    
    if romaneio.status != "Rascunho":
        dica = (
            "Realize primeiro o estorno da Expedição e depois da Finalização."
            if romaneio.status == "Expedido"
            else "Realize primeiro o estorno da Finalização (Pronto → Rascunho)."
        )
        return jsonify({"error": f"Apenas romaneios em Rascunho podem ter NFes removidas. {dica}"}), 400
    
    nf = ExpedicaoRomaneioNF.query.get(nf_id)
    if not nf or nf.romaneio_id != romaneio_id:
        return jsonify({"error": "NF não encontrada neste romaneio."}), 404
    
    peso_removido = nf.peso_bruto
    qtde_volumes_removido = nf.qtde_volumes
    numero_nf_removido = nf.numero_nf
    
    db.session.delete(nf)
    
    # Atualiza totais
    romaneio.peso_bruto_total = max(0, romaneio.peso_bruto_total - peso_removido)
    romaneio.qtde_volumes_total = max(0, romaneio.qtde_volumes_total - qtde_volumes_removido)
    romaneio.atualizado_em = datetime.now()
    
    db.session.commit()
    
    # Estorno da etapa atual: a ordem volta de "Em Romaneio" para "Faturado".
    fat_svc.reverter_romaneio_por_nf(numero_nf_removido)
    
    return jsonify({"message": "NF removida com sucesso."})


@expedicao_romaneio_bp.route("/api/expedicao/romaneio-fat/<int:romaneio_id>/finalizar", methods=["POST"])
@roles_required(*ROLES)
def finalizar_romaneio(romaneio_id):
    """Finaliza o romaneio (muda status de Rascunho para Pronto)."""
    romaneio = ExpedicaoRomaneio.query.get(romaneio_id)
    if not romaneio:
        return jsonify({"error": "Romaneio não encontrado."}), 404
    
    if romaneio.status != "Rascunho":
        return jsonify({"error": "Apenas romaneios em Rascunho podem ser finalizados."}), 400
    
    if not romaneio.nfs or len(romaneio.nfs) == 0:
        return jsonify({"error": "Adicione ao menos uma NF antes de finalizar o romaneio."}), 400
    
    romaneio.status = "Pronto"
    romaneio.atualizado_por = session["username"]
    romaneio.atualizado_em = datetime.now()
    db.session.commit()
    
    return jsonify({"message": "Romaneio finalizado com sucesso."})


@expedicao_romaneio_bp.route("/api/expedicao/romaneio-fat/<int:romaneio_id>/expedir", methods=["POST"])
@roles_required(*ROLES)
def expedir_romaneio(romaneio_id):
    """Marca o romaneio como expedido."""
    romaneio = ExpedicaoRomaneio.query.get(romaneio_id)
    if not romaneio:
        return jsonify({"error": "Romaneio não encontrado."}), 404
    
    if romaneio.status != "Pronto":
        return jsonify({"error": "Apenas romaneios em Pronto podem ser expedidos."}), 400
    
    romaneio.status = "Expedido"
    romaneio.expedido_por = session["username"]
    romaneio.expedido_em = datetime.now()
    romaneio.atualizado_em = datetime.now()
    db.session.commit()
    
    # Fluxo progressivo: cada ordem cuja NF esta neste romaneio avanca de
    # "Em Romaneio" para "Expedido". O Registro de Expedicao nasce ja
    # Finalizado (o romaneio faz as vezes de canhoto), reaproveitando as fotos
    # tiradas na etapa Faturado quando existirem.
    for nf in romaneio.nfs or []:
        registro = _finalizar_registro_expedicao_para_nf(nf, romaneio, session["username"])
        fat_svc.marcar_expedido_por_nf(
            nf.numero_nf,
            registro_id=(registro.id if registro else None),
            usuario=session["username"],
        )
    db.session.commit()

    return jsonify({"message": "Romaneio expedido com sucesso."})


@expedicao_romaneio_bp.route("/api/expedicao/romaneio-fat/<int:romaneio_id>/estornar-finalizacao", methods=["POST"])
@roles_required("Admin")
def estornar_finalizacao_romaneio(romaneio_id):
    """Estorna a finalização do romaneio (Pronto -> Rascunho). As ordens
    associadas permanecem em "Em Romaneio" (nao ha mudanca de etapa das
    ordens aqui, apenas do romaneio)."""
    romaneio = ExpedicaoRomaneio.query.get(romaneio_id)
    if not romaneio:
        return jsonify({"error": "Romaneio não encontrado."}), 404
    
    if romaneio.status != "Pronto":
        return jsonify({"error": "Apenas romaneios em Pronto podem ter a finalização estornada."}), 400
    
    romaneio.status = "Rascunho"
    romaneio.atualizado_por = session["username"]
    romaneio.atualizado_em = datetime.now()
    db.session.commit()
    
    return jsonify({"message": "Finalização estornada. Romaneio voltou para Rascunho."})


@expedicao_romaneio_bp.route("/api/expedicao/romaneio-fat/<int:romaneio_id>/estornar-expedicao", methods=["POST"])
@roles_required("Admin")
def estornar_expedicao_romaneio(romaneio_id):
    """Estorna a expedição do romaneio (Expedido -> Pronto). Cada ordem
    associada volta de "Expedido" para "Em Romaneio" — unica forma de
    retroceder a partir da etapa final."""
    romaneio = ExpedicaoRomaneio.query.get(romaneio_id)
    if not romaneio:
        return jsonify({"error": "Romaneio não encontrado."}), 404
    
    if romaneio.status != "Expedido":
        return jsonify({"error": "Apenas romaneios Expedidos podem ter a expedição estornada."}), 400
    
    romaneio.status = "Pronto"
    romaneio.expedido_por = None
    romaneio.expedido_em = None
    romaneio.atualizado_por = session["username"]
    romaneio.atualizado_em = datetime.now()
    db.session.commit()
    
    for nf in romaneio.nfs or []:
        fat_svc.reverter_expedicao_por_nf(nf.numero_nf)
        # Reverte o Registro de Expedicao auto-finalizado de volta para
        # rascunho (mantem as fotos ja tiradas), permitindo re-expedir depois.
        numero_nf = str(nf.numero_nf or "").strip()
        if numero_nf:
            registros = (
                ExpedicaoConferenciaSimples.query
                .filter_by(numero_nf=numero_nf, origem="Romaneio", status="Finalizado")
                .all()
            )
            for reg in registros:
                reg.status = "Pendente de expedição"
                reg.expedido_at = None
                reg.expedido_by = None
                reg.finalizado_at = None
                reg.finalizado_by = None
                reg.updated_at = datetime.now()
    db.session.commit()

    return jsonify({"message": "Expedição estornada. Romaneio voltou para Pronto."})


def _excluir_romaneio(romaneio: ExpedicaoRomaneio) -> None:
    """Exclui o romaneio (so chamar com status ja validado como Rascunho) e
    devolve as ordens cujas NFs estavam nele para 'Faturado'. Reutilizada
    tanto pela exclusao direta do Admin quanto pela aprovacao de uma
    solicitacao de exclusao."""
    numeros_nf = [nf.numero_nf for nf in (romaneio.nfs or [])]
    db.session.delete(romaneio)
    db.session.commit()
    for numero_nf in numeros_nf:
        fat_svc.reverter_romaneio_por_nf(numero_nf)


@expedicao_romaneio_bp.route("/api/expedicao/romaneio-fat/<int:romaneio_id>/deletar", methods=["DELETE"])
@roles_required("Admin")
def deletar_romaneio(romaneio_id):
    """Deleta um romaneio (apenas Admin, apenas Rascunho)."""
    romaneio = ExpedicaoRomaneio.query.get(romaneio_id)
    if not romaneio:
        return jsonify({"error": "Romaneio não encontrado."}), 404

    if romaneio.status != "Rascunho":
        dica = (
            " Realize primeiro o estorno da Expedição e depois da Finalização."
            if romaneio.status == "Expedido"
            else " Realize primeiro o estorno da Finalização (Pronto → Rascunho)."
        )
        return jsonify({"error": f"Apenas romaneios em Rascunho podem ser deletados.{dica}"}), 400

    _excluir_romaneio(romaneio)
    return jsonify({"message": "Romaneio deletado com sucesso."})


@expedicao_romaneio_bp.route("/api/expedicao/romaneio-fat/<int:romaneio_id>/solicitar-exclusao", methods=["POST"])
@roles_required(*ROLES_NAO_ADMIN)
def solicitar_exclusao_romaneio(romaneio_id):
    """Quem nao e Admin solicita a exclusao de um romaneio em Rascunho —
    fica Pendente ate um Admin aprovar ou rejeitar."""
    romaneio = ExpedicaoRomaneio.query.get(romaneio_id)
    if not romaneio:
        return jsonify({"error": "Romaneio não encontrado."}), 404
    if romaneio.status != "Rascunho":
        return jsonify({"error": "Apenas romaneios em Rascunho podem ter exclusão solicitada."}), 400

    ja_pendente = ExpedicaoRomaneioExclusao.query.filter_by(
        romaneio_id=romaneio_id, status="Pendente"
    ).first()
    if ja_pendente:
        return jsonify({"error": "Já existe uma solicitação de exclusão pendente para este romaneio."}), 400

    payload = request.get_json(silent=True) or {}
    motivo = str(payload.get("motivo") or "").strip()
    if not motivo:
        return jsonify({"error": "Informe o motivo da exclusão."}), 400

    exclusao = ExpedicaoRomaneioExclusao(
        romaneio_id=romaneio_id,
        solicitante=session["username"],
        motivo=motivo,
        status="Pendente",
    )
    db.session.add(exclusao)
    db.session.commit()
    return jsonify({"message": "Solicitação de exclusão enviada. Aguarde aprovação de um Admin."}), 201


@expedicao_romaneio_bp.route("/api/expedicao/romaneio-fat/exclusao/<int:exclusao_id>/<acao>", methods=["POST"])
@roles_required("Admin")
def decidir_exclusao_romaneio(exclusao_id, acao):
    """Admin aprova (exclui de fato) ou rejeita uma solicitação de exclusão."""
    if acao not in ("aprovar", "rejeitar"):
        return jsonify({"error": "Ação inválida."}), 400

    exclusao = ExpedicaoRomaneioExclusao.query.get(exclusao_id)
    if not exclusao:
        return jsonify({"error": "Solicitação de exclusão não encontrada."}), 404
    if exclusao.status != "Pendente":
        return jsonify({"error": "Esta solicitação já foi decidida."}), 400

    payload = request.get_json(silent=True) or {}
    exclusao.admin_usuario = session["username"]
    exclusao.admin_observacao = str(payload.get("observacao") or "").strip() or None
    exclusao.resolvido_at = datetime.now()

    if acao == "aprovar":
        romaneio = ExpedicaoRomaneio.query.get(exclusao.romaneio_id)
        if not romaneio:
            exclusao.status = "Rejeitado"
            exclusao.admin_observacao = "Romaneio não existe mais."
            db.session.commit()
            return jsonify({"error": "Romaneio não encontrado (já excluído?)."}), 404
        if romaneio.status != "Rascunho":
            return jsonify({"error": "Romaneio não está mais em Rascunho — não é possível excluir."}), 400
        exclusao.status = "Aprovado"
        db.session.commit()
        _excluir_romaneio(romaneio)
        return jsonify({"message": "Exclusão aprovada. Romaneio removido."})

    exclusao.status = "Rejeitado"
    db.session.commit()
    return jsonify({"message": "Solicitação de exclusão rejeitada."})


def _remetente_padrao() -> dict:
    cnpj_bruto = current_app.config.get("EMPRESA_CNPJ", "")
    return {
        "razao_social": current_app.config.get("EMPRESA_NOME", "COLUMBIA MACHINE BRASIL"),
        "nome_fantasia": "",
        "documento": cad_svc.formatar_cnpj(cnpj_bruto) if cnpj_bruto else "",
        "endereco": current_app.config.get("EMPRESA_ENDERECO", "RUA CARLOS ROBERTO PRATAVIEIRA, 600 - JD. NOVA EUROPA - HORTOLÂNDIA BRAZIL - SP"),
        "municipio": "",
        "uf": "",
        "cep": "",
        "inscricao_estadual": "",
        "telefone": "",
        "situacao_cadastral": "",
        # E-mails fixos do romaneio — nunca vem da consulta de CNPJ.
        "email_logistica": "logistica@colmac.com",
        "email_fiscal": "fiscal@colmac.com",
    }


def _remetente_completo() -> dict:
    """Dados do remetente (nossa empresa) para o documento do romaneio —
    tenta enriquecer com o cartao CNPJ (BrasilAPI); cai no fallback fixo se
    a consulta externa falhar."""
    dados = _remetente_padrao()
    cnpj = current_app.config.get("EMPRESA_CNPJ", "")
    if not cnpj:
        return dados
    try:
        cartao = cad_svc.consultar_cartao_cnpj(cnpj)
        dados.update({
            "razao_social": cartao.get("razao_social") or dados["razao_social"],
            "nome_fantasia": cartao.get("nome_fantasia") or "",
            "documento": cartao.get("documento") or dados["documento"],
            "endereco": cartao.get("endereco") or dados["endereco"],
            "municipio": cartao.get("municipio") or "",
            "uf": cartao.get("uf") or "",
            "cep": cartao.get("cep") or "",
            "inscricao_estadual": cartao.get("inscricao_estadual") or "",
            "telefone": cartao.get("telefone") or "",
            "situacao_cadastral": cartao.get("situacao_cadastral") or "",
        })
    except Exception:
        current_app.logger.warning("Falha ao consultar cartão CNPJ do remetente para romaneio", exc_info=True)
    return dados


def _destinatario_padrao(romaneio) -> dict:
    return {
        "razao_social": romaneio.cliente or "—",
        "documento": "",
        "inscricao_estadual": "",
        "endereco": "",
        "municipio": "",
        "uf": "",
        "cep": "",
        "telefone": "",
    }


def _destinatario_completo(romaneio) -> dict:
    """Dados do destinatário (cliente) para o documento do romaneio — busca
    CNPJ/endereço direto na NF-e emitida (mesmo XML autorizado usado para
    gerar o DANFE). Cai no fallback (só o nome do cliente) se a NF não for
    encontrada no ERP ou não houver XML disponível."""
    dados = _destinatario_padrao(romaneio)
    primeira_nf = romaneio.nfs[0] if romaneio.nfs else None
    if not primeira_nf or not primeira_nf.numero_nf:
        return dados
    try:
        nota = buscar_nfe_emitida_erp(numero_nf=primeira_nf.numero_nf)
        xml_bytes = (nota or {}).get("xml_bytes")
        if not xml_bytes:
            return dados
        nfe = danfe_service.parse_nfe_xml(xml_bytes)
        endereco = ", ".join(
            parte.strip() for parte in [
                nfe.get("dest_logr"),
                nfe.get("dest_nro"),
                nfe.get("dest_cpl"),
                nfe.get("dest_bairro"),
            ] if str(parte or "").strip()
        )
        dados.update({
            "razao_social": nfe.get("dest_nome") or dados["razao_social"],
            "documento": nfe.get("dest_cnpj") or dados["documento"],
            "inscricao_estadual": nfe.get("dest_ie") or "",
            "endereco": endereco,
            "municipio": nfe.get("dest_mun") or "",
            "uf": nfe.get("dest_uf") or "",
            "cep": nfe.get("dest_cep") or "",
            "telefone": nfe.get("dest_fone") or "",
        })
    except Exception:
        current_app.logger.warning(
            "Falha ao obter dados do destinatário via NF-e emitida para o romaneio %s", romaneio.id, exc_info=True
        )
    return dados


@expedicao_romaneio_bp.route("/expedicao/romaneio/<int:romaneio_id>/visualizar")
@permission_required(PERMISSION)
def visualizar_romaneio(romaneio_id):
    """Visualiza/imprime o romaneio conforme modelo Excel."""
    romaneio = ExpedicaoRomaneio.query.get(romaneio_id)
    if not romaneio:
        return "Romaneio não encontrado.", 404

    return render_template(
        "expedicao_romaneio_visualizar.html",
        romaneio=romaneio,
        remetente=_remetente_completo(),
        destinatario=_destinatario_completo(romaneio),
    )


@expedicao_romaneio_bp.route("/api/expedicao/romaneio-fat/<int:romaneio_id>/assinatura/<tipo>", methods=["POST"])
@roles_required(*ROLES)
def salvar_assinatura_romaneio(romaneio_id, tipo):
    """Salva a assinatura digital (PNG em base64) do conferente ou do
    transportador, capturada num canvas (ex.: tablet do motorista)."""
    if tipo not in ("conferente", "transportador"):
        return jsonify({"error": "Tipo de assinatura inválido."}), 400

    romaneio = ExpedicaoRomaneio.query.get(romaneio_id)
    if not romaneio:
        return jsonify({"error": "Romaneio não encontrado."}), 404

    payload = request.get_json(silent=True) or {}
    imagem = str(payload.get("imagem_base64") or "").strip()
    if not imagem:
        return jsonify({"error": "Nenhuma assinatura recebida."}), 400
    if "," in imagem:
        imagem = imagem.split(",", 1)[1]

    try:
        dados_png = base64.b64decode(imagem)
    except Exception:
        return jsonify({"error": "Assinatura inválida."}), 400

    nome_arquivo = f"romaneio{romaneio_id}_{tipo}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.png"

    if using_drive():
        try:
            stored = upload_bytes_to_drive(dados_png, nome_arquivo, mimetype="image/png")
            caminho = stored.file_path
        except Exception as exc:
            current_app.logger.exception("Falha ao enviar assinatura do romaneio para o Drive")
            return jsonify({"error": f"Falha ao enviar assinatura para o Drive: {exc}"}), 502
    else:
        fotos_dir = current_app.config.get("EXPEDICAO_CONFERENCIA_FOTOS_DIR", "") or os.path.join(
            current_app.instance_path, "expedicao_romaneio_assinaturas"
        )
        os.makedirs(fotos_dir, exist_ok=True)
        caminho = os.path.join(fotos_dir, nome_arquivo)
        with open(caminho, "wb") as f:
            f.write(dados_png)

    agora = datetime.now()
    usuario = session["username"]
    if tipo == "conferente":
        romaneio.assinatura_conferente_file_name = nome_arquivo
        romaneio.assinatura_conferente_file_path = caminho
        romaneio.assinatura_conferente_uploadado_em = agora
        romaneio.assinatura_conferente_uploadado_por = usuario
    else:
        romaneio.assinatura_transportador_file_name = nome_arquivo
        romaneio.assinatura_transportador_file_path = caminho
        romaneio.assinatura_uploadado_em = agora
        romaneio.assinatura_uploadado_por = usuario
    romaneio.atualizado_em = agora
    db.session.commit()

    return jsonify({
        "message": "Assinatura salva com sucesso.",
        "url": f"/api/expedicao/romaneio-fat/{romaneio_id}/assinatura/{tipo}",
    })


@expedicao_romaneio_bp.route("/api/expedicao/romaneio-fat/<int:romaneio_id>/assinatura/<tipo>", methods=["GET"])
@permission_required(PERMISSION)
def obter_assinatura_romaneio(romaneio_id, tipo):
    if tipo not in ("conferente", "transportador"):
        return jsonify({"error": "Tipo de assinatura inválido."}), 400

    romaneio = ExpedicaoRomaneio.query.get(romaneio_id)
    if not romaneio:
        return jsonify({"error": "Romaneio não encontrado."}), 404

    if tipo == "conferente":
        file_name = romaneio.assinatura_conferente_file_name
        file_path = romaneio.assinatura_conferente_file_path
    else:
        file_name = romaneio.assinatura_transportador_file_name
        file_path = romaneio.assinatura_transportador_file_path

    if not file_name:
        return jsonify({"error": "Assinatura não encontrada."}), 404

    fotos_dir = current_app.config.get("EXPEDICAO_CONFERENCIA_FOTOS_DIR", "") or os.path.join(
        current_app.instance_path, "expedicao_romaneio_assinaturas"
    )
    caminho = _resolver_foto_expedicao(fotos_dir, file_name, file_path)
    if not caminho:
        return jsonify({"error": "Arquivo da assinatura não encontrado."}), 404

    try:
        return _send_foto_expedicao(caminho, file_name)
    except Exception as exc:
        current_app.logger.exception("Falha ao baixar assinatura do romaneio")
        return jsonify({"error": f"Falha ao baixar assinatura: {exc}"}), 502
