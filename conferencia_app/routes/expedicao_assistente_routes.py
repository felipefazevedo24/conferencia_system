"""Rotas do Assistente de Expedição (inteligência operacional offline).

Expõe o serviço expedicao_assistente_service para a página de Conferência de
Expedição: panorama de pendências priorizadas e chat por intenção (offline).
"""
from flask import Blueprint, jsonify, request, session

from datetime import datetime

from ..auth import permission_required, is_admin_session, login_required, has_permission
from ..services import expedicao_assistente_service as svc
from ..services import expedicao_cobranca_service as cobranca_svc
from ..services import expedicao_romaneio_bia_service as romaneio_bia_svc
from ..services import bia_mensagem_service as bia_mensagem_svc


expedicao_assistente_bp = Blueprint("expedicao_assistente", __name__)

PERMISSION = "PAGE_EXPEDICAO_CONF_CEGA"


@expedicao_assistente_bp.route("/api/expedicao/assistente/pendencias", methods=["GET"])
@permission_required(PERMISSION)
def pendencias():
    """Panorama priorizado das pendências de expedição."""
    return jsonify(svc.analisar())


@expedicao_assistente_bp.route("/api/expedicao/assistente/perguntar", methods=["POST"])
@login_required
def perguntar():
    """Responde a uma pergunta em linguagem natural.

    O CHAT (leitura/LLM) é liberado para qualquer usuário logado — a Bia atende
    o sistema inteiro. Já os COMANDOS DE AÇÃO sobre romaneios (editar/estornar/
    aprovar) só são interpretados para quem tem acesso à expedição (ou Admin),
    pois esse fluxo altera dados operacionais."""
    payload = request.get_json(silent=True) or {}
    pergunta = str(payload.get("pergunta") or "").strip()
    if not pergunta:
        return jsonify({"error": "Informe uma pergunta."}), 400
    ctx = {
        "username": str(session.get("username") or "").strip(),
        "role": session.get("role"),
        "is_admin": is_admin_session(),
    }
    # Envio de AVISOS pelo chat da Bia. Interpretamos para QUALQUER usuário
    # logado (o serviço decide a permissão e responde de forma clara a quem não
    # é Admin), evitando que o chat responda como se tivesse enviado. Só devolve
    # aqui quando a frase é realmente um comando de aviso.
    try:
        envio = bia_mensagem_svc.interpretar_envio(pergunta, ctx)
    except Exception:
        envio = None
    if envio is not None:
        return jsonify(envio)
    # Ações que ESCREVEM em romaneios só para quem opera a expedição (ou Admin).
    if is_admin_session() or has_permission(PERMISSION):
        try:
            acao = romaneio_bia_svc.interpretar(pergunta, ctx)
        except Exception:
            acao = None
        if acao is not None:
            return jsonify(acao)
    historico = payload.get("historico")
    return jsonify(svc.responder(pergunta, historico))


@expedicao_assistente_bp.route("/api/expedicao/assistente/aprender", methods=["POST"])
@permission_required(PERMISSION)
def aprender():
    """Ensina um fato novo à Bia (base de conhecimento). Só administradores."""
    if not is_admin_session():
        return jsonify({"error": "Apenas administradores podem ensinar a Bia."}), 403
    payload = request.get_json(silent=True) or {}
    texto = str(payload.get("texto") or "").strip()
    if not texto:
        return jsonify({"error": "Informe o que a Bia deve aprender."}), 400
    autor = str(session.get("username") or session.get("role") or "").strip()
    if svc.registrar_aprendizado(texto, autor=autor):
        return jsonify({"ok": True, "mensagem": "Aprendizado registrado."})
    return jsonify({"error": "Não foi possível salvar o aprendizado."}), 500


def _autor_sessao() -> str:
    return str(session.get("username") or session.get("role") or "").strip()


@expedicao_assistente_bp.route("/api/expedicao/assistente/cobranca/proxima", methods=["GET"])
@permission_required(PERMISSION)
def cobranca_proxima():
    """Próxima pendência para a Bia cobrar o motivo (só Logística/Admin)."""
    role = session.get("role")
    if not cobranca_svc.pode_cobrar(role):
        return jsonify({"cobranca": None})
    dados = cobranca_svc.proxima_para_cobrar(role)
    return jsonify({"cobranca": dados})


@expedicao_assistente_bp.route("/api/expedicao/assistente/cobranca/responder", methods=["POST"])
@permission_required(PERMISSION)
def cobranca_responder():
    """Registra o motivo informado pelo operador para uma pendência."""
    role = session.get("role")
    if not cobranca_svc.pode_cobrar(role):
        return jsonify({"error": "Apenas a Logística pode responder às cobranças."}), 403
    payload = request.get_json(silent=True) or {}
    ref_tipo = str(payload.get("ref_tipo") or "").strip()
    ref_id = str(payload.get("ref_id") or "").strip()
    texto = str(payload.get("texto") or "").strip()
    if not ref_tipo or not ref_id:
        return jsonify({"error": "Pendência inválida."}), 400
    if not texto:
        return jsonify({"error": "Informe o motivo."}), 400
    if cobranca_svc.registrar_resposta(ref_tipo, ref_id, texto, autor=_autor_sessao()):
        return jsonify({"ok": True})
    return jsonify({"error": "Não foi possível registrar a resposta."}), 500


@expedicao_assistente_bp.route("/api/expedicao/assistente/cobranca/adiar", methods=["POST"])
@permission_required(PERMISSION)
def cobranca_adiar():
    """Adia (snooze) uma cobrança para o próximo ciclo."""
    role = session.get("role")
    if not cobranca_svc.pode_cobrar(role):
        return jsonify({"error": "Apenas a Logística pode adiar as cobranças."}), 403
    payload = request.get_json(silent=True) or {}
    ref_tipo = str(payload.get("ref_tipo") or "").strip()
    ref_id = str(payload.get("ref_id") or "").strip()
    if not ref_tipo or not ref_id:
        return jsonify({"error": "Pendência inválida."}), 400
    cobranca_svc.adiar(ref_tipo, ref_id, autor=_autor_sessao())
    return jsonify({"ok": True})


@expedicao_assistente_bp.route("/api/expedicao/assistente/cobranca/historico", methods=["GET"])
@permission_required(PERMISSION)
def cobranca_historico():
    """Histórico/motivo de follow-up de uma pendência (tela da conferência)."""
    ref_tipo = str(request.args.get("ref_tipo") or "").strip()
    ref_id = str(request.args.get("ref_id") or "").strip()
    if not ref_tipo or not ref_id:
        return jsonify({"error": "Pendência inválida."}), 400
    return jsonify(cobranca_svc.historico(ref_tipo, ref_id))


@expedicao_assistente_bp.route("/api/expedicao/assistente/cobranca/cce-feita", methods=["POST"])
@permission_required(PERMISSION)
def cobranca_cce_feita():
    """Marca a CC-e de um romaneio como emitida e resolve a cobrança."""
    role = session.get("role")
    if not cobranca_svc.pode_cobrar(role):
        return jsonify({"error": "Apenas a Logística pode marcar a CC-e."}), 403
    payload = request.get_json(silent=True) or {}
    numero = str(payload.get("numero_romaneio") or "").strip()
    resultado = cobranca_svc.marcar_cce_feita(numero, autor=_autor_sessao())
    status = 200 if resultado.get("ok") else 400
    return jsonify(resultado), status


@expedicao_assistente_bp.route("/api/expedicao/assistente/aprovacoes", methods=["GET"])
@permission_required(PERMISSION)
def aprovacoes():
    """Estornos de romaneio aguardando aprovação (só faz sentido para Admin).
    A Bia usa isto para avisar o Admin ao abrir o chat."""
    if not is_admin_session():
        return jsonify({"pendentes": [], "total": 0})
    pend = romaneio_bia_svc.estornos_pendentes()
    return jsonify({"pendentes": pend, "total": len(pend)})


@expedicao_assistente_bp.route("/api/expedicao/assistente/novidades", methods=["GET"])
@login_required
def novidades():
    """O que CHEGOU desde o último check do cliente (faturamento, NF de
    recebimento e solicitação de viagem). A Bia usa isto para avisar sozinha.

    Roteamento: usuário comum recebe só os módulos aos quais tem acesso; Admin
    recebe uma visão macro (os números de todos os módulos). O cliente envia
    `desde` (ISO) com o último instante checado; sem ele, só devolvemos o
    marcador de tempo (não despeja o histórico antigo)."""
    desde_raw = str(request.args.get("desde") or "").strip()
    desde = None
    if desde_raw:
        try:
            desde = datetime.fromisoformat(desde_raw)
        except Exception:
            desde = None
    dados = svc.novidades(
        desde,
        is_admin=is_admin_session(),
        mod_receb=(
            has_permission("PAGE_CONFERENCIA")
            or has_permission("PAGE_PORTARIA")
            or has_permission("PAGE_FISCAL_LIBERADAS")
        ),
        mod_exped=(
            has_permission("PAGE_EXPEDICAO_CONF_CEGA")
            or has_permission("PAGE_EXPEDICAO_CONFERENCIA")
        ),
        mod_viagem=(
            has_permission("PAGE_LOGISTICA_SOLICITACAO")
            or has_permission("PAGE_LOGISTICA_VIAGEM")
            or has_permission("PAGE_LOGISTICA_AGENDAMENTO")
        ),
    )
    return jsonify(dados)


@expedicao_assistente_bp.route("/api/expedicao/assistente/mensagens", methods=["GET"])
@login_required
def mensagens():
    """Avisos (Admin -> usuário) ainda não entregues a quem está logado. A Bia
    usa isto para mostrar o recado (toast + painel). Marca como lido ao entregar,
    para não repetir."""
    username = str(session.get("username") or "").strip()
    role = session.get("role")
    msgs = bia_mensagem_svc.mensagens_nao_lidas(username, role, marcar=True)
    return jsonify({"mensagens": msgs, "total": len(msgs)})


@expedicao_assistente_bp.route("/api/expedicao/assistente/cobranca/encaminhar", methods=["POST"])
@permission_required(PERMISSION)
def cobranca_encaminhar():
    """Repassa uma cobrança/pendência para alguém (usuário, cargo ou todos) como
    aviso da Bia. Liberado a quem opera as cobranças (Logística ou Admin)."""
    role = session.get("role")
    if not cobranca_svc.pode_cobrar(role):
        return jsonify({"error": "Sem permissão para repassar cobranças."}), 403
    payload = request.get_json(silent=True) or {}
    ref_tipo = str(payload.get("ref_tipo") or "").strip()
    ref_id = str(payload.get("ref_id") or "").strip()
    alvo = str(payload.get("alvo") or "").strip()
    if not ref_tipo or not ref_id:
        return jsonify({"error": "Pendência não informada."}), 400
    if not alvo:
        return jsonify({"error": "Informe para quem repassar."}), 400
    det = cobranca_svc.detalhe(ref_tipo, ref_id) or {
        "ref_tipo": ref_tipo,
        "ref_id": ref_id,
        "titulo": str(payload.get("titulo") or "Pendência de expedição").strip(),
        "referencia": str(payload.get("referencia") or "").strip(),
        "numero_nf": str(payload.get("numero_nf") or "").strip(),
    }
    ctx = {
        "username": str(session.get("username") or "").strip(),
        "role": role,
        "is_admin": is_admin_session(),
    }
    return jsonify(bia_mensagem_svc.encaminhar_cobranca(ctx, alvo, det))
