"""Rotas do Romaneio de Expedição."""

from datetime import datetime
from flask import Blueprint, request, jsonify, render_template, session, current_app
from ..extensions import db
from ..models import ExpedicaoRomaneio, ExpedicaoRomaneioNF, ExpedicaoOrdemFat
from ..auth import permission_required, roles_required
from ..services import expedicao_fat_service as fat_svc

expedicao_romaneio_bp = Blueprint("expedicao_romaneio", __name__)

PERMISSION = "PAGE_EXPEDICAO_CONF_CEGA"
ROLES = ("Conferente", "Admin", "Fiscal", "Logística")


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
        }
        for r in romaneios
    ]
    
    return jsonify({"romaneios": data})


@expedicao_romaneio_bp.route("/api/expedicao/romaneio-fat", methods=["POST"])
@roles_required(*ROLES)
def criar_romaneio():
    """Cria um novo romaneio em branco."""
    payload = request.get_json(silent=True) or {}
    
    # Gera número único para o romaneio (p.ex: ROM-2026-001)
    hoje = datetime.now()
    ultimo = ExpedicaoRomaneio.query.filter(
        ExpedicaoRomaneio.criado_em >= datetime(hoje.year, 1, 1)
    ).count()
    numero_romaneio = f"ROM-{hoje.year}-{ultimo + 1:04d}"
    
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
        "peso_bruto_total": romaneio.peso_bruto_total,
        "qtde_volumes_total": romaneio.qtde_volumes_total,
        "observacao_1": romaneio.observacao_1,
        "observacao_2": romaneio.observacao_2,
        "observacao_3": romaneio.observacao_3,
        "status": romaneio.status,
        "criado_por": romaneio.criado_por,
        "criado_em": romaneio.criado_em.isoformat() if romaneio.criado_em else None,
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
    
    # Busca dados da NF na tabela ExpedicaoOrdemFat (se necessário)
    # Por enquanto, usa dados fornecidos na requisição
    orcamento = payload.get("orcamento") or romaneio.orcamento
    cliente = payload.get("cliente") or romaneio.cliente
    peso_bruto = float(payload.get("peso_bruto") or 0)
    qtde_volumes = int(payload.get("qtde_volumes") or 0)
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
    # "Em Romaneio" para "Expedido".
    for nf in romaneio.nfs or []:
        fat_svc.marcar_expedido_por_nf(nf.numero_nf, usuario=session["username"])
    
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
    
    return jsonify({"message": "Expedição estornada. Romaneio voltou para Pronto."})


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
    
    numeros_nf = [nf.numero_nf for nf in (romaneio.nfs or [])]
    
    db.session.delete(romaneio)
    db.session.commit()
    
    # Estorno da etapa atual: as ordens cujas NFs estavam neste romaneio
    # voltam para "Faturado".
    for numero_nf in numeros_nf:
        fat_svc.reverter_romaneio_por_nf(numero_nf)
    
    return jsonify({"message": "Romaneio deletado com sucesso."})


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
        remetente={
            "razao_social": current_app.config.get("EMPRESA_NOME", "COLUMBIA MACHINE BRASIL"),
            "endereco": current_app.config.get("EMPRESA_ENDERECO", "RUA CARLOS ROBERTO PRATAVIEIRA, 600 - JD. NOVA EUROPA - HORTOLÂNDIA BRAZIL - SP"),
        },
    )
