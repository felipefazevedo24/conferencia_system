from __future__ import annotations

import re
from datetime import datetime, timedelta

from flask import Blueprint, current_app, jsonify, request, session
from sqlalchemy import or_

from ..auth import permission_required
from ..extensions import db
from ..models import (
    AgendamentoCliente,
    AgendamentoFornecedor,
    AgendamentoMotorista,
    AgendamentoSolicitacao,
    AgendamentoSolicitacaoHistorico,
    AgendamentoSolicitacaoItem,
    AgendamentoVeiculo,
    Usuario,
)
from ..services.email_service import enviar_email_agendamento_update
from ..services.agendamento_service import (
    PRIORIDADES_SOLICITACAO,
    STATUS_ATIVOS,
    STATUS_SOLICITACAO,
    TIPOS_SOLICITACAO,
    VEICULOS_KANBAN,
    consultar_nf_agendamento,
    consultar_oc_agendamento,
    estimar_rota_agendamento,
    ensure_cadastros_base_carregados,
    formatar_endereco_logistico,
    importar_cadastros_excel,
    listar_cadastros,
    listar_motoristas_agendamento,
    listar_veiculos_agendamento,
    montar_waze_url_agendamento,
    prioridade_label_agendamento,
    resumo_cadastros,
    salvar_motorista_agendamento,
    sincronizar_motoristas_usuarios,
    serializar_cadastro,
    serializar_motorista,
    status_label_agendamento,
)


agendamento_bp = Blueprint("agendamento", __name__)

PRIORIDADE_ORDEM = {"Critica": 0, "Alta": 1, "Media": 2, "Baixa": 3}


def _notificar_solicitante_agendamento(row: AgendamentoSolicitacao, titulo: str, detalhe: str = "") -> None:
    try:
        usuario = Usuario.query.filter_by(username=row.solicitante).first()
        if not usuario or not usuario.email:
            return
        veiculo = AgendamentoVeiculo.query.get(row.veiculo_id) if row.veiculo_id else None
        url = request.url_root.rstrip("/") + "/logistica/solicitar-transporte"
        enviar_email_agendamento_update(
            usuario.email,
            f"[Transporte] {titulo} - {row.codigo or 'solicitacao'}",
            titulo,
            [
                ("Codigo", row.codigo or f"#{row.id}"),
                ("Tipo", {"COLETA": "Coleta", "ENTREGA": "Entrega", "AVULSA": "Avulsa"}.get(row.tipo, row.tipo or "")),
                ("Status", status_label_agendamento(row.status)),
                ("Documento", f"{row.documento_tipo or ''} {row.documento_numero or ''}".strip()),
                ("Veiculo", veiculo.nome_exibicao if veiculo else ""),
                ("Motorista", row.motorista_nome or ""),
                ("Saida", row.data_hora_saida_prevista.strftime("%d/%m/%Y %H:%M") if row.data_hora_saida_prevista else ""),
                ("Detalhe", detalhe),
            ],
            url,
        )
    except Exception:
        current_app.logger.exception("Falha ao notificar solicitante da solicitacao %s", getattr(row, "id", None))


def _json_text(payload) -> str | None:
    if payload is None:
        return None
    try:
        import json

        return json.dumps(payload, ensure_ascii=False, default=str)[:4000]
    except Exception:
        return str(payload)[:4000]


def _parse_datetime(value, field_name: str, required: bool = False) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        if required:
            raise ValueError(f"Informe {field_name}.")
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(f"{field_name} inválido.") from exc


def _origem_documento_label(origem: str | None) -> str:
    mapping = {
        "GoogleSheets": "Google Sheets",
        "Consyste": "Consyste",
        "Manual": "Manual",
    }
    return mapping.get(str(origem or "").strip(), str(origem or "").strip() or "---")


def _kanban_coluna(registro: AgendamentoSolicitacao, veiculo: AgendamentoVeiculo | None) -> str:
    status = str(registro.status or "").strip()
    if status == "Concluida":
        return "concluido"
    if status == "Cancelada":
        return "cancelada"
    if status in {"Alocada", "EmAndamento", "EmRota"} and veiculo and str(veiculo.codigo or "").strip().upper() in VEICULOS_KANBAN:
        return str(veiculo.codigo or "").strip().upper()
    return "pendentes"


def _intervalo_planejado(
    registro: AgendamentoSolicitacao,
    *,
    veiculo: AgendamentoVeiculo | None = None,
    inicio_override: datetime | None = None,
    fim_override: datetime | None = None,
) -> tuple[datetime | None, datetime | None]:
    inicio = inicio_override or registro.data_hora_saida_prevista
    if not inicio:
        return None, None
    fim = fim_override or registro.data_hora_retorno_prevista
    if fim and fim > inicio:
        return inicio, fim
    veiculo_ref = veiculo or (AgendamentoVeiculo.query.get(registro.veiculo_id) if registro.veiculo_id else None)
    duracao = int(
        getattr(veiculo_ref, "duracao_padrao_min", 0)
        or current_app.config.get("AGENDAMENTO_DURACAO_PADRAO_MINUTOS", 120)
    )
    return inicio, inicio + timedelta(minutes=max(30, duracao))


def _serializar_item(item: AgendamentoSolicitacaoItem) -> dict:
    return {
        "id": item.id,
        "sequencia": int(item.sequencia or 0),
        "codigo_item": str(item.codigo_item or "").strip(),
        "descricao": str(item.descricao or "").strip(),
        "quantidade": float(item.quantidade or 0.0),
        "unidade": str(item.unidade or "").strip(),
        "volumes": float(item.volumes or 0.0),
        "observacoes": str(item.observacoes or "").strip(),
    }


def _serializar_historico(item: AgendamentoSolicitacaoHistorico) -> dict:
    return {
        "id": item.id,
        "evento": str(item.evento or "").strip(),
        "status_anterior": str(item.status_anterior or "").strip(),
        "status_novo": str(item.status_novo or "").strip(),
        "usuario": str(item.usuario or "").strip(),
        "detalhe": str(item.detalhe or "").strip(),
        "criado_em": item.criado_em.strftime("%d/%m/%Y %H:%M") if item.criado_em else "",
    }


def _serializar_solicitacao(
    registro: AgendamentoSolicitacao,
    *,
    veiculo: AgendamentoVeiculo | None = None,
    itens: list[AgendamentoSolicitacaoItem] | None = None,
    historico: list[AgendamentoSolicitacaoHistorico] | None = None,
) -> dict:
    endereco = {
        "logradouro": str(registro.logradouro or "").strip(),
        "numero": str(registro.numero or "").strip(),
        "complemento": str(registro.complemento or "").strip(),
        "bairro": str(registro.bairro or "").strip(),
        "cidade": str(registro.cidade or "").strip(),
        "uf": str(registro.uf or "").strip(),
        "cep": str(registro.cep or "").strip(),
        "observacoes": str(registro.observacoes_endereco or "").strip(),
    }
    prazo = registro.prazo_limite
    atrasada = bool(prazo and prazo < datetime.now() and str(registro.status or "").strip() not in {"Concluida", "Cancelada"})
    return {
        "id": registro.id,
        "codigo": str(registro.codigo or f"LOG-{registro.id}").strip(),
        "tipo": str(registro.tipo or "").strip(),
        "tipo_label": {"COLETA": "Coleta", "ENTREGA": "Entrega", "AVULSA": "Avulsa"}.get(str(registro.tipo or "").strip(), str(registro.tipo or "").strip()),
        "status": str(registro.status or "").strip(),
        "status_label": status_label_agendamento(registro.status),
        "prioridade": str(registro.prioridade or "").strip(),
        "prioridade_label": prioridade_label_agendamento(registro.prioridade),
        "solicitante": str(registro.solicitante or "").strip(),
        "criado_em": registro.criado_em.strftime("%d/%m/%Y %H:%M") if registro.criado_em else "",
        "documento_tipo": str(registro.documento_tipo or "").strip(),
        "documento_numero": str(registro.documento_numero or "").strip(),
        "numero_oc": str(registro.numero_oc or "").strip(),
        "numero_nf": str(registro.numero_nf or "").strip(),
        "origem_documento": str(registro.origem_documento or "").strip(),
        "origem_documento_label": _origem_documento_label(registro.origem_documento),
        "parceiro_tipo": str(registro.parceiro_tipo or "").strip(),
        "parceiro_codigo": str(registro.parceiro_codigo or "").strip(),
        "parceiro_nome": str(registro.parceiro_nome or "").strip(),
        "parceiro_razao_social": str(registro.parceiro_razao_social or "").strip(),
        "parceiro_documento": str(registro.parceiro_documento or "").strip(),
        "contato": str(registro.contato or "").strip(),
        "telefone": str(registro.telefone or "").strip(),
        "email": str(registro.email or "").strip(),
        "endereco": endereco,
        "endereco_formatado": formatar_endereco_logistico(endereco),
        "observacoes_solicitante": str(registro.observacoes_solicitante or "").strip(),
        "observacoes_logistica": str(registro.observacoes_logistica or "").strip(),
        "motivo_cancelamento": str(registro.motivo_cancelamento or "").strip(),
        "data_desejada": registro.data_desejada.isoformat(timespec="minutes") if registro.data_desejada else "",
        "data_desejada_label": registro.data_desejada.strftime("%d/%m/%Y %H:%M") if registro.data_desejada else "",
        "cancelamento_pendente": bool(registro.cancelamento_pendente),
        "cancelamento_solicitado_por": str(registro.cancelamento_solicitado_por or "").strip(),
        "cancelamento_motivo_pendente": str(registro.cancelamento_motivo_pendente or "").strip(),
        "departamento_solicitante": str(registro.departamento_solicitante or "").strip(),
        "tempo_estimado_min": int(registro.tempo_estimado_min) if registro.tempo_estimado_min else None,
        "prazo_limite": prazo.isoformat(timespec="minutes") if prazo else "",
        "prazo_limite_label": prazo.strftime("%d/%m/%Y %H:%M") if prazo else "",
        "data_hora_saida_prevista": registro.data_hora_saida_prevista.isoformat(timespec="minutes") if registro.data_hora_saida_prevista else "",
        "data_hora_saida_prevista_label": registro.data_hora_saida_prevista.strftime("%d/%m/%Y %H:%M") if registro.data_hora_saida_prevista else "",
        "data_hora_retorno_prevista": registro.data_hora_retorno_prevista.isoformat(timespec="minutes") if registro.data_hora_retorno_prevista else "",
        "data_hora_retorno_prevista_label": registro.data_hora_retorno_prevista.strftime("%d/%m/%Y %H:%M") if registro.data_hora_retorno_prevista else "",
        "data_hora_saida_real": registro.data_hora_saida_real.isoformat(timespec="minutes") if registro.data_hora_saida_real else "",
        "data_hora_saida_real_label": registro.data_hora_saida_real.strftime("%d/%m/%Y %H:%M") if registro.data_hora_saida_real else "",
        "data_hora_retorno_real": registro.data_hora_retorno_real.isoformat(timespec="minutes") if registro.data_hora_retorno_real else "",
        "data_hora_retorno_real_label": registro.data_hora_retorno_real.strftime("%d/%m/%Y %H:%M") if registro.data_hora_retorno_real else "",
        "qtd_itens": int(registro.qtd_itens or 0),
        "qtd_volumes": float(registro.qtd_volumes or 0.0),
        "resumo_itens": str(registro.resumo_itens or "").strip(),
        "motorista": {
            "id": registro.motorista_id,
            "nome": str(registro.motorista_nome or "").strip(),
        } if registro.motorista_id or str(registro.motorista_nome or "").strip() else None,
        "km_estimado": float(registro.km_estimado) if registro.km_estimado is not None else None,
        "km_estimado_retorno": float(registro.km_estimado_retorno) if registro.km_estimado_retorno is not None else None,
        "waze_url": montar_waze_url_agendamento(
            {
                **endereco,
                "latitude": registro.destino_latitude,
                "longitude": registro.destino_longitude,
            }
        ),
        "google_maps_url": montar_waze_url_agendamento(
            {
                **endereco,
                "latitude": registro.destino_latitude,
                "longitude": registro.destino_longitude,
            }
        ),
        "veiculo": {
            "id": veiculo.id,
            "codigo": str(veiculo.codigo or "").strip(),
            "nome": str(veiculo.nome_exibicao or veiculo.codigo or "").strip(),
            "cor": str(veiculo.cor_kanban or "").strip(),
        } if veiculo else None,
        "kanban_coluna": _kanban_coluna(registro, veiculo),
        "atrasada": atrasada,
        "itens": [_serializar_item(item) for item in itens or []],
        "historico": [_serializar_historico(item) for item in historico or []],
    }


def _ordenar_cards(cards: list[dict]) -> list[dict]:
    return sorted(
        cards,
        key=lambda card: (
            PRIORIDADE_ORDEM.get(str(card.get("prioridade") or ""), 99),
            str(card.get("prazo_limite") or "9999-12-31T23:59"),
            str(card.get("data_hora_saida_prevista") or "9999-12-31T23:59"),
            str(card.get("codigo") or ""),
        ),
    )


@agendamento_bp.route("/api/logistica/agendamento-veiculos/dashboard")
@permission_required("PAGE_LOGISTICA_AGENDAMENTO")
def dashboard_agendamento_veiculos():
    ensure_cadastros_base_carregados()
    sincronizar_motoristas_usuarios(commit=True)
    _auto_transicao_em_andamento()
    termo = str(request.args.get("q") or "").strip()
    status = str(request.args.get("status") or "").strip()
    tipo = str(request.args.get("tipo") or "").strip().upper()
    prioridade = str(request.args.get("prioridade") or "").strip()
    incluir_canceladas = str(request.args.get("incluir_canceladas") or "").strip().lower() in {"1", "true", "sim"}

    query = AgendamentoSolicitacao.query
    if not incluir_canceladas:
        query = query.filter(AgendamentoSolicitacao.status != "Cancelada")
    if status:
        query = query.filter(AgendamentoSolicitacao.status == status)
    if tipo:
        query = query.filter(AgendamentoSolicitacao.tipo == tipo)
    if prioridade:
        query = query.filter(AgendamentoSolicitacao.prioridade == prioridade)
    if termo:
        like = f"%{termo}%"
        query = query.filter(
            or_(
                AgendamentoSolicitacao.codigo.ilike(like),
                AgendamentoSolicitacao.documento_numero.ilike(like),
                AgendamentoSolicitacao.parceiro_nome.ilike(like),
                AgendamentoSolicitacao.solicitante.ilike(like),
                AgendamentoSolicitacao.cidade.ilike(like),
            )
        )

    veiculos = {row.id: row for row in listar_veiculos_agendamento()}
    motoristas = listar_motoristas_agendamento()
    cards = [_serializar_solicitacao(row, veiculo=veiculos.get(row.veiculo_id)) for row in query.order_by(AgendamentoSolicitacao.criado_em.desc()).all()]
    colunas = {"pendentes": [], "IVECO": [], "SAVEIRO": [], "concluido": []}
    for card in cards:
        coluna = card.get("kanban_coluna")
        if coluna in colunas:
            colunas[coluna].append(card)
    return jsonify(
        {
            "resumo": {
                "total": len(cards),
                "pendentes": sum(1 for card in cards if card.get("status") in {"Pendente", "EmAnalise"}),
                "alocadas": sum(1 for card in cards if card.get("status") == "Alocada"),
                "em_andamento": sum(1 for card in cards if card.get("status") == "EmAndamento"),
                "em_rota": sum(1 for card in cards if card.get("status") == "EmRota"),
                "concluidas": sum(1 for card in cards if card.get("status") == "Concluida"),
                "cancelamentos_pendentes": sum(1 for card in cards if card.get("cancelamento_pendente")),
                "atrasadas": sum(1 for card in cards if card.get("atrasada")),
                "motoristas_ativos": len(motoristas),
            },
            "veiculos": [
                {
                    "id": row.id,
                    "codigo": str(row.codigo or "").strip(),
                    "nome": str(row.nome_exibicao or row.codigo or "").strip(),
                    "cor": str(row.cor_kanban or "").strip(),
                    "janela_conflito_min": int(row.janela_conflito_min or 0),
                    "duracao_padrao_min": int(row.duracao_padrao_min or 0),
                }
                for row in veiculos.values()
            ],
            "motoristas": motoristas,
            "cadastros": resumo_cadastros(),
            "colunas": {key: _ordenar_cards(value) for key, value in colunas.items()},
            "filtros": {"status": list(STATUS_SOLICITACAO), "tipos": list(TIPOS_SOLICITACAO), "prioridades": list(PRIORIDADES_SOLICITACAO)},
        }
    )


@agendamento_bp.route("/api/logistica/agendamento-veiculos/minhas-solicitacoes")
@permission_required("PAGE_LOGISTICA_SOLICITACAO")
def minhas_solicitacoes_agendamento():
    usuario = session.get("username", "desconhecido")
    rows = (
        AgendamentoSolicitacao.query
        .filter(AgendamentoSolicitacao.solicitante == usuario)
        .order_by(AgendamentoSolicitacao.criado_em.desc())
        .limit(30)
        .all()
    )
    veiculos = {row.id: row for row in listar_veiculos_agendamento()}
    return jsonify({"rows": [_serializar_solicitacao(row, veiculo=veiculos.get(row.veiculo_id)) for row in rows]})


@agendamento_bp.route("/api/logistica/agendamento-veiculos/agenda")
@permission_required("PAGE_LOGISTICA_AGENDAMENTO")
def agenda_agendamento_veiculos():
    modo = str(request.args.get("modo") or "semana").strip().lower()
    if modo not in {"dia", "semana"}:
        modo = "semana"
    data_raw = str(request.args.get("data") or "").strip()
    try:
        data_base = datetime.fromisoformat(f"{data_raw}T00:00" if data_raw else datetime.now().strftime("%Y-%m-%dT00:00"))
    except ValueError:
        data_base = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    inicio = data_base.replace(hour=0, minute=0, second=0, microsecond=0)
    fim = inicio + timedelta(days=1 if modo == "dia" else 7)
    veiculos = {row.id: row for row in listar_veiculos_agendamento()}
    rows = (
        AgendamentoSolicitacao.query
        .filter(
            AgendamentoSolicitacao.status.in_(list(STATUS_ATIVOS) + ["Concluida"]),
            AgendamentoSolicitacao.data_hora_saida_prevista.isnot(None),
            AgendamentoSolicitacao.data_hora_saida_prevista >= inicio,
            AgendamentoSolicitacao.data_hora_saida_prevista < fim,
        )
        .order_by(AgendamentoSolicitacao.data_hora_saida_prevista.asc())
        .all()
    )
    agenda = []
    for row in rows:
        veiculo = veiculos.get(row.veiculo_id)
        if not veiculo:
            continue
        agenda.append(
            {
                "id": row.id,
                "codigo": str(row.codigo or f"LOG-{row.id}").strip(),
                "veiculo_codigo": str(veiculo.codigo or "").strip(),
                "veiculo_nome": str(veiculo.nome_exibicao or veiculo.codigo or "").strip(),
                "tipo": str(row.tipo or "").strip(),
                "status": str(row.status or "").strip(),
                "status_label": status_label_agendamento(row.status),
                "documento": str(row.documento_numero or "").strip(),
                "parceiro_nome": str(row.parceiro_nome or "").strip(),
                "motorista_nome": str(row.motorista_nome or "").strip(),
                "km_estimado": float(row.km_estimado) if row.km_estimado is not None else None,
                "data": row.data_hora_saida_prevista.strftime("%Y-%m-%d"),
                "saida_label": row.data_hora_saida_prevista.strftime("%d/%m/%Y %H:%M"),
                "retorno_label": row.data_hora_retorno_prevista.strftime("%d/%m/%Y %H:%M") if row.data_hora_retorno_prevista else "",
                "resumo_itens": str(row.resumo_itens or "").strip(),
            }
        )
    return jsonify({"modo": modo, "data_base": inicio.strftime("%Y-%m-%d"), "agenda": agenda})


@agendamento_bp.route("/api/logistica/agendamento-veiculos/cadastros")
@permission_required("PAGE_LOGISTICA_AGENDAMENTO")
def listar_cadastros_agendamento():
    tipo = str(request.args.get("tipo") or "").strip().lower()
    if tipo not in {"fornecedor", "cliente"}:
        return jsonify({"error": "Informe o tipo do cadastro."}), 400
    q = str(request.args.get("q") or "").strip()
    limit = max(1, min(int(request.args.get("limit") or 30), 100))
    return jsonify({"rows": listar_cadastros(tipo, q=q, limit=limit)})


@agendamento_bp.route("/api/logistica/agendamento-veiculos/cadastros/importar", methods=["POST"])
@permission_required("PAGE_LOGISTICA_AGENDAMENTO")
def importar_cadastros_agendamento():
    tipo = str(request.form.get("tipo") or "").strip().lower()
    if tipo not in {"fornecedor", "cliente"}:
        return jsonify({"error": "Tipo de cadastro inválido."}), 400
    arquivo = request.files.get("arquivo")
    try:
        resumo = importar_cadastros_excel(tipo, arquivo=arquivo, nome_arquivo=str(getattr(arquivo, "filename", "") or ""))
    except Exception as exc:
        db.session.rollback()
        return jsonify({"error": str(exc)}), 400
    return jsonify({"sucesso": True, "resumo": resumo, "cadastros": resumo_cadastros()})


@agendamento_bp.route("/api/logistica/agendamento-veiculos/motoristas")
@permission_required("PAGE_LOGISTICA_AGENDAMENTO")
def listar_motoristas_endpoint():
    sincronizar_motoristas_usuarios(commit=True)
    q = str(request.args.get("q") or "").strip()
    incluir_inativos = str(request.args.get("incluir_inativos") or "").strip().lower() in {"1", "true", "sim"}
    return jsonify({"rows": listar_motoristas_agendamento(q=q, incluir_inativos=incluir_inativos)})


@agendamento_bp.route("/api/logistica/agendamento-veiculos/motoristas", methods=["POST"])
@permission_required("PAGE_LOGISTICA_AGENDAMENTO")
def salvar_motoristas_endpoint():
    payload = request.get_json(silent=True) or {}
    try:
        row = salvar_motorista_agendamento(payload)
        db.session.commit()
    except ValueError as exc:
        db.session.rollback()
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        db.session.rollback()
        return jsonify({"error": str(exc)}), 500
    return jsonify({"sucesso": True, "motorista": serializar_motorista(row)})


@agendamento_bp.route("/api/logistica/agendamento-veiculos/referencia/oc/<numero_oc>")
def consultar_oc_agendamento_endpoint(numero_oc: str):
    from conferencia_app.auth import has_permission
    if not (has_permission("PAGE_LOGISTICA_SOLICITACAO") or has_permission("PAGE_LOGISTICA_AGENDAMENTO")):
        return jsonify({"error": "Acesso negado."}), 403
    resultado = consultar_oc_agendamento(numero_oc)
    return jsonify(resultado), (200 if resultado.get("encontrada") else 404)


@agendamento_bp.route("/api/logistica/agendamento-veiculos/referencia/nf/<numero_nf>")
def consultar_nf_agendamento_endpoint(numero_nf: str):
    from conferencia_app.auth import has_permission
    if not (has_permission("PAGE_LOGISTICA_SOLICITACAO") or has_permission("PAGE_LOGISTICA_AGENDAMENTO")):
        return jsonify({"error": "Acesso negado."}), 403
    resultado = consultar_nf_agendamento(numero_nf)
    return jsonify(resultado), (200 if resultado.get("encontrada") else 404)


def _registrar_historico(
    solicitacao_id: int,
    *,
    evento: str,
    usuario: str,
    status_anterior: str | None = None,
    status_novo: str | None = None,
    detalhe: str | None = None,
    payload=None,
) -> None:
    db.session.add(
        AgendamentoSolicitacaoHistorico(
            solicitacao_id=solicitacao_id,
            evento=evento,
            status_anterior=status_anterior,
            status_novo=status_novo,
            usuario=usuario,
            detalhe=str(detalhe or "").strip() or None,
            payload_json=_json_text(payload),
            criado_em=datetime.now(),
        )
    )


def _aplicar_parceiro(registro: AgendamentoSolicitacao, parceiro: dict, parceiro_tipo: str) -> tuple[bool, str]:
    nome = str(parceiro.get("nome") or parceiro.get("razao_social") or "").strip()
    logradouro = str(parceiro.get("logradouro") or "").strip()
    cidade = str(parceiro.get("cidade") or "").strip()
    uf = str(parceiro.get("uf") or "").strip()
    if not nome:
        return False, f"Informe o nome do {parceiro_tipo.lower()}."
    if not logradouro or not cidade or not uf:
        return False, "Preencha o endereco completo antes de salvar."
    registro.parceiro_tipo = parceiro_tipo
    registro.parceiro_codigo = str(parceiro.get("codigo") or "").strip() or None
    registro.parceiro_nome = nome
    registro.parceiro_razao_social = str(parceiro.get("razao_social") or nome).strip() or None
    registro.parceiro_documento = re.sub(r"\D", "", str(parceiro.get("cnpj_cpf") or parceiro.get("documento") or "")) or None
    registro.contato = str(parceiro.get("contato") or "").strip() or None
    registro.telefone = str(parceiro.get("telefone") or "").strip() or None
    registro.email = str(parceiro.get("email") or "").strip() or None
    registro.logradouro = logradouro
    registro.numero = str(parceiro.get("numero") or "").strip() or None
    registro.complemento = str(parceiro.get("complemento") or "").strip() or None
    registro.bairro = str(parceiro.get("bairro") or "").strip() or None
    registro.cidade = cidade
    registro.uf = uf[:2].upper()
    registro.cep = re.sub(r"\D", "", str(parceiro.get("cep") or "")) or None
    registro.observacoes_endereco = str(parceiro.get("observacoes") or "").strip() or None
    return True, ""


def _resolver_parceiro_payload(payload: dict, tipo_cadastro: str) -> dict:
    cadastro_id = payload.get("cadastro_id")
    model = AgendamentoFornecedor if tipo_cadastro == "fornecedor" else AgendamentoCliente
    if cadastro_id not in (None, ""):
        try:
            cadastro = model.query.get(int(cadastro_id))
        except (TypeError, ValueError):
            cadastro = None
        if cadastro:
            return serializar_cadastro(cadastro, tipo_cadastro)
    parceiro = payload.get("parceiro")
    return parceiro if isinstance(parceiro, dict) else {}


def _sincronizar_itens(registro: AgendamentoSolicitacao, itens_payload: list[dict]) -> bool:
    AgendamentoSolicitacaoItem.query.filter_by(solicitacao_id=registro.id).delete()
    validos = 0
    total_volumes = 0.0
    for idx, item in enumerate(itens_payload, start=1):
        descricao = str(item.get("descricao") or "").strip()
        try:
            quantidade = float(str(item.get("quantidade") or 0).replace(",", "."))
        except (TypeError, ValueError):
            quantidade = 0.0
        if not descricao or quantidade <= 0:
            continue
        try:
            volumes = float(str(item.get("volumes") or 0).replace(",", "."))
        except (TypeError, ValueError):
            volumes = 0.0
        total_volumes += volumes
        validos += 1
        db.session.add(
            AgendamentoSolicitacaoItem(
                solicitacao_id=registro.id,
                sequencia=idx,
                codigo_item=str(item.get("codigo_item") or "").strip() or None,
                descricao=descricao,
                quantidade=quantidade,
                unidade=str(item.get("unidade") or "").strip() or None,
                volumes=volumes,
                observacoes=str(item.get("observacoes") or "").strip() or None,
            )
        )
    registro.qtd_itens = validos
    registro.qtd_volumes = total_volumes
    volumes_label = int(total_volumes) if float(total_volumes).is_integer() else round(total_volumes, 2)
    registro.resumo_itens = f"{validos} item(ns) / {volumes_label} volume(s)"
    return validos > 0


@agendamento_bp.route("/api/logistica/agendamento-veiculos/solicitacoes/<int:solicitacao_id>")
@permission_required("PAGE_LOGISTICA_AGENDAMENTO")
def obter_solicitacao_agendamento(solicitacao_id: int):
    row = AgendamentoSolicitacao.query.get(solicitacao_id)
    if not row:
        return jsonify({"error": "Solicitação não encontrada."}), 404
    itens = AgendamentoSolicitacaoItem.query.filter_by(solicitacao_id=row.id).order_by(AgendamentoSolicitacaoItem.sequencia.asc()).all()
    historico = AgendamentoSolicitacaoHistorico.query.filter_by(solicitacao_id=row.id).order_by(AgendamentoSolicitacaoHistorico.criado_em.desc()).all()
    veiculo = AgendamentoVeiculo.query.get(row.veiculo_id) if row.veiculo_id else None
    return jsonify({"solicitacao": _serializar_solicitacao(row, veiculo=veiculo, itens=itens, historico=historico)})


@agendamento_bp.route("/api/logistica/agendamento-veiculos/solicitacoes/<int:solicitacao_id>/documento", methods=["PATCH"])
@permission_required("PAGE_LOGISTICA_AGENDAMENTO")
def atualizar_documento_solicitacao(solicitacao_id: int):
    """Gestor informa/corrige OC ou NF — salva e enriquece automaticamente com dados do pedido."""
    row = AgendamentoSolicitacao.query.get(solicitacao_id)
    if not row:
        return jsonify({"error": "Solicitação não encontrada."}), 404
    if row.status in ("Cancelada",):
        return jsonify({"error": "Não é possível editar uma solicitação cancelada."}), 400

    payload = request.get_json(silent=True) or {}
    numero_oc = str(payload.get("numero_oc") or "").strip()
    numero_nf = re.sub(r"\D", "", str(payload.get("numero_nf") or ""))

    if not numero_oc and not numero_nf:
        return jsonify({"error": "Informe a OC ou a NF."}), 400

    usuario = session.get("username", "sistema")

    # ── 1. Salva o número no registro ──────────────────────────────────────
    if numero_oc and row.tipo == "COLETA":
        row.numero_oc = numero_oc
        row.documento_numero = numero_oc
        row.documento_tipo = "OC"
    elif numero_nf and row.tipo == "ENTREGA":
        row.numero_nf = numero_nf
        row.documento_numero = numero_nf
        row.documento_tipo = "NF"
    else:
        # AVULSA ou campo não corresponde ao tipo — aceita o que vier
        if numero_oc:
            row.numero_oc = numero_oc
            if not row.documento_numero:
                row.documento_numero = numero_oc
                row.documento_tipo = "OC"
        if numero_nf:
            row.numero_nf = numero_nf
            if not row.documento_numero or row.documento_tipo == "OC":
                row.documento_numero = numero_nf
                row.documento_tipo = "NF"

    # ── 2. Enriquece com dados do pedido/NF ───────────────────────────────
    enriquecido = False
    aviso_enriquecimento = ""
    parceiro_nome_aplicado = ""
    itens_aplicados = 0
    try:
        consulta = (
            consultar_oc_agendamento(numero_oc) if numero_oc
            else consultar_nf_agendamento(numero_nf)
        )
        if consulta.get("encontrada"):
            # OC retorna "fornecedor"; NF retorna "cliente"
            parceiro = consulta.get("fornecedor") or consulta.get("cliente") or {}

            if parceiro.get("nome"):
                row.parceiro_nome = parceiro["nome"]
                parceiro_nome_aplicado = parceiro["nome"]
            if parceiro.get("razao_social"):
                row.parceiro_razao_social = parceiro["razao_social"]
            if parceiro.get("cnpj_cpf"):
                row.parceiro_documento = re.sub(r"\D", "", str(parceiro["cnpj_cpf"]))
            if parceiro.get("codigo"):
                row.parceiro_codigo = str(parceiro["codigo"])
            if parceiro.get("logradouro"):
                row.logradouro = parceiro["logradouro"]
            if parceiro.get("numero"):
                row.numero = parceiro["numero"]
            if parceiro.get("complemento"):
                row.complemento = parceiro["complemento"]
            if parceiro.get("bairro"):
                row.bairro = parceiro["bairro"]
            if parceiro.get("cidade"):
                row.cidade = parceiro["cidade"]
            if parceiro.get("uf"):
                row.uf = str(parceiro["uf"])[:2].upper()
            if parceiro.get("cep"):
                row.cep = parceiro["cep"]
            if parceiro.get("telefone") and not row.telefone:
                row.telefone = parceiro["telefone"]
            if parceiro.get("email") and not row.email:
                row.email = parceiro["email"]

            # Substitui itens existentes pelos do pedido/NF
            itens_novos = consulta.get("itens") or []
            if itens_novos:
                AgendamentoSolicitacaoItem.query.filter_by(solicitacao_id=row.id).delete()
                for seq, it in enumerate(itens_novos, start=1):
                    db.session.add(AgendamentoSolicitacaoItem(
                        solicitacao_id=row.id,
                        sequencia=seq,
                        codigo_item=str(it.get("codigo_item") or "").strip(),
                        descricao=str(it.get("descricao") or f"Item {seq}").strip(),
                        quantidade=float(it.get("quantidade") or 0.0),
                        unidade=str(it.get("unidade") or "").strip(),
                        volumes=float(it.get("volumes") or 0.0),
                        observacoes=str(it.get("observacoes") or "").strip() or None,
                    ))
                row.qtd_itens = len(itens_novos)
                total_vol = sum(float(i.get("volumes") or 0.0) for i in itens_novos)
                row.resumo_itens = f"{len(itens_novos)} item(ns) / {total_vol:.0f} vol."
                itens_aplicados = len(itens_novos)

            if consulta.get("warning"):
                aviso_enriquecimento = consulta["warning"]
            enriquecido = True
        else:
            aviso_enriquecimento = consulta.get("error") or "Documento não encontrado na base de dados."
    except Exception as exc:
        current_app.logger.warning("Enriquecimento de documento falhou: %s", exc)
        aviso_enriquecimento = "Número salvo, mas não foi possível buscar os dados automaticamente."

    detalhe_hist = (
        f"{'OC' if numero_oc else 'NF'} {numero_oc or numero_nf} informada pelo gestor."
        + (" Dados aplicados automaticamente." if enriquecido else f" {aviso_enriquecimento}")
    )
    _registrar_historico(row.id, evento="DOCUMENTO_ATUALIZADO", usuario=usuario, detalhe=detalhe_hist)

    row.atualizado_em = datetime.now()
    db.session.commit()

    itens = (
        AgendamentoSolicitacaoItem.query
        .filter_by(solicitacao_id=row.id)
        .order_by(AgendamentoSolicitacaoItem.sequencia.asc())
        .all()
    )
    veiculo = AgendamentoVeiculo.query.get(row.veiculo_id) if row.veiculo_id else None
    return jsonify({
        "sucesso": True,
        "enriquecido": enriquecido,
        "parceiro_nome": parceiro_nome_aplicado,
        "itens_aplicados": itens_aplicados,
        "aviso": aviso_enriquecimento,
        "solicitacao": _serializar_solicitacao(row, veiculo=veiculo, itens=itens),
    })


@agendamento_bp.route("/api/logistica/agendamento-veiculos/solicitacoes", methods=["POST"])
@permission_required("PAGE_LOGISTICA_SOLICITACAO")
def criar_solicitacao_agendamento():
    payload = request.get_json(silent=True) or {}
    tipo = str(payload.get("tipo") or "").strip().upper()
    prioridade = str(payload.get("prioridade") or "Media").strip()
    if tipo not in TIPOS_SOLICITACAO:
        return jsonify({"error": "Tipo de solicitação inválido."}), 400
    if prioridade not in PRIORIDADES_SOLICITACAO:
        return jsonify({"error": "Prioridade inválida."}), 400

    avulsa = payload.get("avulsa") if isinstance(payload.get("avulsa"), dict) else {}

    numero_oc = str(payload.get("numero_oc") or "").strip()
    numero_nf = re.sub(r"\D", "", str(payload.get("numero_nf") or ""))
    referencia_avulsa = str(payload.get("referencia_avulsa") or "").strip()
    try:
        prazo_limite = _parse_datetime(payload.get("prazo_limite"), "o prazo")
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    try:
        data_desejada = _parse_datetime(payload.get("data_desejada"), "a data desejada")
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    observacoes_solicitante = str(payload.get("observacoes_solicitante") or "").strip()
    if tipo == "AVULSA":
        extras_avulsa = []
        for label, value in [
            ("Finalidade", avulsa.get("finalidade")),
            ("Centro de custo", avulsa.get("centro_custo")),
            ("Responsavel", avulsa.get("responsavel")),
            ("Local de retirada", avulsa.get("local_retirada")),
            ("Previsao de devolucao", avulsa.get("previsao_devolucao")),
        ]:
            value = str(value or "").strip()
            if value:
                extras_avulsa.append(f"{label}: {value}")
        if extras_avulsa:
            observacoes_solicitante = "\n".join([observacoes_solicitante, *extras_avulsa]).strip()

    if tipo == "AVULSA":
        consulta = {"encontrada": False, "itens": []}
        parceiro = {
            "nome": "Uso avulso de veículo",
            "logradouro": "Sem destino definido",
            "cidade": "A definir",
            "uf": "NA",
            "observacoes": observacoes_solicitante,
        }
    else:
        if (tipo == "COLETA" and numero_oc) or (tipo == "ENTREGA" and numero_nf):
            consulta = consultar_oc_agendamento(numero_oc) if tipo == "COLETA" else consultar_nf_agendamento(numero_nf)
        else:
            consulta = {"encontrada": False, "itens": []}
        parceiro = _resolver_parceiro_payload(payload, "fornecedor" if tipo == "COLETA" else "cliente")
        if not parceiro:
            parceiro = consulta.get("fornecedor") or consulta.get("cliente") or {}
        # Fallback for simplified requests where user only fills in the location
        local_solicitante = str(payload.get("local_solicitante") or "").strip()
        if local_solicitante and not parceiro.get("logradouro"):
            parceiro["logradouro"] = local_solicitante
            if not parceiro.get("cidade"):
                parceiro["cidade"] = local_solicitante.split("/")[-1].strip() if "/" in local_solicitante else "A definir"
            if not parceiro.get("uf"):
                parceiro["uf"] = "--"
        if not (parceiro.get("nome") or parceiro.get("razao_social")):
            parceiro["nome"] = "A definir"
        if not parceiro.get("logradouro"):
            parceiro["logradouro"] = str(payload.get("local_solicitante") or "A definir").strip() or "A definir"
        if not parceiro.get("cidade"):
            parceiro["cidade"] = "A definir"
        if not parceiro.get("uf"):
            parceiro["uf"] = "--"

    origem_documento = "Manual"
    if tipo == "COLETA" and consulta.get("encontrada"):
        origem_documento = str((consulta.get("fonte") or {}).get("tipo") or "GoogleSheets").strip() or "GoogleSheets"
    elif tipo == "ENTREGA" and consulta.get("encontrada"):
        origem_documento = "Consyste"

    usuario = session.get("username", "desconhecido")
    agora = datetime.now()
    row = AgendamentoSolicitacao(
        tipo=tipo,
        status="Pendente",
        prioridade=prioridade,
        prazo_limite=prazo_limite,
        data_desejada=data_desejada,
        solicitante=usuario,
        criado_em=agora,
        atualizado_em=agora,
        documento_tipo="OC" if tipo == "COLETA" else ("NF" if tipo == "ENTREGA" else "AVULSO"),
        documento_numero=numero_oc if tipo == "COLETA" else (numero_nf if tipo == "ENTREGA" else (referencia_avulsa or f"AVULSO-{agora.strftime('%Y%m%d%H%M')}")),
        numero_oc=numero_oc or None,
        numero_nf=numero_nf or None,
        origem_documento=origem_documento,
        observacoes_solicitante=observacoes_solicitante or None,
        observacoes_logistica=str(payload.get("observacoes_logistica") or "").strip() or None,
        payload_origem=_json_text({"request": payload, "consulta": {"encontrada": consulta.get("encontrada"), "fonte": consulta.get("fonte")}}),
    )
    ok, msg = _aplicar_parceiro(row, parceiro, "Fornecedor" if tipo == "COLETA" else ("Cliente" if tipo == "ENTREGA" else "Avulso"))
    if not ok:
        return jsonify({"error": msg}), 400

    rota = estimar_rota_agendamento({
        "logradouro": row.logradouro,
        "numero": row.numero,
        "bairro": row.bairro,
        "cidade": row.cidade,
        "uf": row.uf,
        "cep": row.cep
    })
    if rota.get("km_estimado") is not None:
        row.origem_latitude = rota.get("origem_latitude")
        row.origem_longitude = rota.get("origem_longitude")
        row.destino_latitude = rota.get("destino_latitude")
        row.destino_longitude = rota.get("destino_longitude")
        row.km_estimado = rota.get("km_estimado")
        row.km_estimado_retorno = rota.get("km_estimado_retorno")

    itens = payload.get("itens")
    if not isinstance(itens, list) or not itens:
        itens = list(consulta.get("itens") or [])
    if tipo == "AVULSA" and (
        not isinstance(itens, list)
        or not any(str(item.get("descricao") or "").strip() for item in itens if isinstance(item, dict))
    ):
        itens = [{"descricao": "Reserva avulsa de veículo", "quantidade": 1, "unidade": "UN", "volumes": 0}]
    if not isinstance(itens, list) or not itens:
        itens = [{"descricao": "A definir pela logística", "quantidade": 1, "unidade": "UN", "volumes": 0}]

    db.session.add(row)
    db.session.flush()
    row.codigo = f"LOG-{agora.strftime('%Y%m%d')}-{row.id:04d}"
    if not _sincronizar_itens(row, itens):
        db.session.rollback()
        return jsonify({"error": "Adicione pelo menos 1 item válido para continuar."}), 400
    _registrar_historico(row.id, evento="CRIADA", usuario=usuario, status_novo="Pendente", detalhe=f"Solicitação criada via {row.documento_tipo} {row.documento_numero}.", payload=payload)
    db.session.commit()
    _notificar_solicitante_agendamento(row, "Solicitacao de transporte criada", "Sua solicitacao foi enviada para a logistica.")
    return jsonify({"sucesso": True, "solicitacao": _serializar_solicitacao(row)}), 201


@agendamento_bp.route("/api/logistica/agendamento-veiculos/solicitacoes/<int:solicitacao_id>/alocar", methods=["POST"])
@permission_required("PAGE_LOGISTICA_AGENDAMENTO")
def alocar_solicitacao_agendamento(solicitacao_id: int):
    row = AgendamentoSolicitacao.query.get(solicitacao_id)
    if not row:
        return jsonify({"error": "Solicitação não encontrada."}), 404
    if str(row.status or "").strip() in {"Concluida", "Cancelada"}:
        return jsonify({"error": "Não é possível alocar uma solicitação finalizada."}), 409

    payload = request.get_json(silent=True) or {}
    try:
        veiculo_id = int(payload.get("veiculo_id")) if payload.get("veiculo_id") not in (None, "") else None
    except (TypeError, ValueError):
        veiculo_id = None
    veiculo = AgendamentoVeiculo.query.get(veiculo_id) if veiculo_id else None
    if not veiculo:
        codigo = str(payload.get("veiculo_codigo") or "").strip().upper()
        veiculo = AgendamentoVeiculo.query.filter_by(codigo=codigo, ativo=True).first() if codigo else None
    if not veiculo:
        return jsonify({"error": "Selecione um veículo válido."}), 400

    try:
        saida = _parse_datetime(payload.get("data_hora_saida_prevista"), "a data e hora de saída", required=True)
        retorno = _parse_datetime(payload.get("data_hora_retorno_prevista"), "a previsão de retorno")
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if retorno and retorno <= saida:
        return jsonify({"error": "A previsão de retorno deve ser maior que a saída."}), 400

    motorista = None
    try:
        motorista_id = int(payload.get("motorista_id")) if payload.get("motorista_id") not in (None, "") else None
    except (TypeError, ValueError):
        motorista_id = None
    if not motorista_id and str(row.tipo or "").strip() != "AVULSA":
        return jsonify({"error": "Selecione um motorista para a viagem."}), 400
    motorista = AgendamentoMotorista.query.get(motorista_id) if motorista_id else None
    if motorista_id and not motorista:
        return jsonify({"error": "Selecione um motorista válido."}), 400

    departamento = str(payload.get("departamento_solicitante") or "").strip().upper()
    departamentos_validos = ["COMPRAS", "ASSISTÊNCIA TÉCNICA", "ENGENHARIA/PCP", "LOGÍSTICA", "FACILITIES"]
    if not departamento or departamento not in departamentos_validos:
        return jsonify({"error": "Selecione o departamento solicitante."}), 400

    buffer_min = int(
        getattr(veiculo, "janela_conflito_min", 0)
        or current_app.config.get("AGENDAMENTO_CONFLITO_MINUTOS", 30)
    )
    query = AgendamentoSolicitacao.query.filter(
        AgendamentoSolicitacao.veiculo_id == veiculo.id,
        AgendamentoSolicitacao.status.in_(["Alocada", "EmRota"]),
        AgendamentoSolicitacao.id != row.id,
    )
    inicio_atual, fim_atual = _intervalo_planejado(AgendamentoSolicitacao(), veiculo=veiculo, inicio_override=saida, fim_override=retorno)
    for existente in query.all():
        outro_inicio, outro_fim = _intervalo_planejado(existente)
        if not outro_inicio or not outro_fim:
            continue
        if (inicio_atual - timedelta(minutes=buffer_min)) < (outro_fim + timedelta(minutes=buffer_min)) and (
            fim_atual + timedelta(minutes=buffer_min)
        ) > (outro_inicio - timedelta(minutes=buffer_min)):
            return jsonify({"error": f"{veiculo.nome_exibicao} já possui uma saída programada para {outro_inicio.strftime('%d/%m/%Y %H:%M')}."}), 409

    status_anterior = str(row.status or "").strip()
    row.veiculo_id = veiculo.id
    row.motorista_id = motorista.id if motorista else None
    row.motorista_nome = (str(motorista.nome or "").strip() or None) if motorista else None
    row.data_hora_saida_prevista = saida
    row.data_hora_retorno_prevista = retorno
    row.alocado_por = session.get("username", "desconhecido")
    row.alocado_em = datetime.now()
    row.status = "Alocada"
    row.atualizado_em = datetime.now()
    row.departamento_solicitante = departamento
    observacao = str(payload.get("observacoes_logistica") or "").strip()
    if observacao:
        row.observacoes_logistica = observacao

    rota = estimar_rota_agendamento(
        {
            "logradouro": row.logradouro,
            "numero": row.numero,
            "bairro": row.bairro,
            "cidade": row.cidade,
            "uf": row.uf,
            "cep": row.cep,
            "latitude": row.destino_latitude,
            "longitude": row.destino_longitude,
        },
        origem_latitude=payload.get("origem_latitude"),
        origem_longitude=payload.get("origem_longitude"),
    )
    row.origem_latitude = rota.get("origem_latitude")
    row.origem_longitude = rota.get("origem_longitude")
    row.destino_latitude = rota.get("destino_latitude")
    row.destino_longitude = rota.get("destino_longitude")
    row.km_estimado = rota.get("km_estimado")
    row.km_estimado_retorno = rota.get("km_estimado_retorno")

    detalhe = f"Alocada no veículo {veiculo.nome_exibicao} para {saida.strftime('%d/%m/%Y %H:%M')}."
    if motorista:
        detalhe += f" Motorista: {motorista.nome}."
    if rota.get("km_estimado") is not None:
        detalhe += f" Estimativa: {rota['km_estimado']:.1f} km ida / {rota['km_estimado_retorno']:.1f} km ida e volta."
    _registrar_historico(
        row.id,
        evento="ALOCADA",
        usuario=session.get("username", "desconhecido"),
        status_anterior=status_anterior,
        status_novo="Alocada",
        detalhe=detalhe,
        payload=payload,
    )
    db.session.commit()
    _notificar_solicitante_agendamento(row, "Solicitacao de transporte alocada", detalhe)
    return jsonify({"sucesso": True, "solicitacao": _serializar_solicitacao(row, veiculo=veiculo)})


@agendamento_bp.route("/api/logistica/agendamento-veiculos/solicitacoes/<int:solicitacao_id>/status", methods=["POST"])
@permission_required("PAGE_LOGISTICA_AGENDAMENTO")
def atualizar_status_agendamento(solicitacao_id: int):
    row = AgendamentoSolicitacao.query.get(solicitacao_id)
    if not row:
        return jsonify({"error": "Solicitação não encontrada."}), 404
    status_atual = str(row.status or "").strip()
    if status_atual in {"Cancelada", "Concluida"}:
        return jsonify({"error": "A solicitação já está encerrada."}), 409

    payload = request.get_json(silent=True) or {}
    novo_status = str(payload.get("status") or "").strip()
    if novo_status not in STATUS_SOLICITACAO:
        return jsonify({"error": "Status inválido."}), 400
    if novo_status == "Cancelada":
        return jsonify({"error": "Use a ação de cancelamento."}), 400
    if novo_status == "Alocada":
        return jsonify({"error": "Use a ação de alocação para definir veículo e horário."}), 400
    if novo_status == "EmAndamento" and status_atual not in {"Alocada", "EmAndamento"}:
        return jsonify({"error": "A solicitação precisa estar alocada antes de entrar em andamento."}), 409
    if novo_status == "EmRota" and status_atual not in {"Alocada", "EmAndamento", "EmRota"}:
        return jsonify({"error": "A solicitação precisa estar alocada antes de entrar em rota."}), 409
    if novo_status == "Concluida" and status_atual not in {"Alocada", "EmAndamento", "EmRota"}:
        return jsonify({"error": "A solicitação precisa estar alocada ou em andamento antes da conclusão."}), 409
    if novo_status == "Concluida":
        username = session.get("username", "")
        role = session.get("role", "")
        is_motorista_desta_viagem = False
        if row.motorista_id:
            mot = AgendamentoMotorista.query.get(row.motorista_id)
            if mot and mot.usuario_username == username:
                is_motorista_desta_viagem = True
        if role != "Admin" and not is_motorista_desta_viagem:
            return jsonify({"error": "Somente o motorista da viagem ou admin pode concluir."}), 403
    if novo_status == "Pendente" and status_atual in {"EmRota", "EmAndamento"}:
        return jsonify({"error": "Não é possível voltar uma solicitação em andamento para pendente."}), 409

    row.status = novo_status
    row.atualizado_em = datetime.now()
    observacao = str(payload.get("observacoes_logistica") or "").strip()
    if observacao:
        row.observacoes_logistica = observacao
    if novo_status == "Pendente":
        row.veiculo_id = None
        row.motorista_id = None
        row.motorista_nome = None
        row.data_hora_saida_prevista = None
        row.data_hora_retorno_prevista = None
        row.origem_latitude = None
        row.origem_longitude = None
        row.destino_latitude = None
        row.destino_longitude = None
        row.km_estimado = None
        row.km_estimado_retorno = None
        row.alocado_por = None
        row.alocado_em = None
    if novo_status == "Concluida":
        try:
            row.data_hora_saida_real = _parse_datetime(payload.get("data_hora_saida_real"), "a saída real")
            row.data_hora_retorno_real = _parse_datetime(payload.get("data_hora_retorno_real"), "o retorno real")
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        row.concluido_por = session.get("username", "desconhecido")
        row.concluido_em = datetime.now()

    _registrar_historico(row.id, evento="STATUS_ALTERADO", usuario=session.get("username", "desconhecido"), status_anterior=status_atual, status_novo=novo_status, detalhe=observacao or f"Status alterado para {status_label_agendamento(novo_status)}.", payload=payload)
    db.session.commit()
    _notificar_solicitante_agendamento(row, "Status da solicitacao atualizado", observacao or f"Status alterado para {status_label_agendamento(novo_status)}.")
    veiculo = AgendamentoVeiculo.query.get(row.veiculo_id) if row.veiculo_id else None
    return jsonify({"sucesso": True, "solicitacao": _serializar_solicitacao(row, veiculo=veiculo)})


@agendamento_bp.route("/api/logistica/agendamento-veiculos/solicitacoes/<int:solicitacao_id>/cancelar", methods=["POST"])
@permission_required("PAGE_LOGISTICA_AGENDAMENTO")
def cancelar_solicitacao_agendamento(solicitacao_id: int):
    """Logística solicita cancelamento — o solicitante precisa aprovar."""
    row = AgendamentoSolicitacao.query.get(solicitacao_id)
    if not row:
        return jsonify({"error": "Solicitação não encontrada."}), 404
    if str(row.status or "").strip() == "Concluida":
        return jsonify({"error": "Não é possível cancelar uma solicitação concluída."}), 409
    if str(row.status or "").strip() == "Cancelada":
        return jsonify({"error": "Solicitação já cancelada."}), 409
    if str(row.status or "").strip() == "EmRota" and session.get("role") != "Admin":
        return jsonify({"error": "Somente admin pode cancelar uma solicitação em rota."}), 403

    payload = request.get_json(silent=True) or {}
    motivo = str(payload.get("motivo") or "").strip()
    if not motivo:
        return jsonify({"error": "Informe o motivo do cancelamento."}), 400

    usuario = session.get("username", "desconhecido")
    status_anterior = str(row.status or "").strip()
    if session.get("role") == "Admin":
        row.status = "Cancelada"
        row.cancelado_por = usuario
        row.cancelado_em = datetime.now()
        row.motivo_cancelamento = motivo
        row.cancelamento_pendente = False
        row.cancelamento_solicitado_por = None
        row.cancelamento_motivo_pendente = None
        row.atualizado_em = datetime.now()
        _registrar_historico(
            row.id,
            evento="CANCELAMENTO_ADMIN",
            usuario=usuario,
            status_anterior=status_anterior,
            status_novo="Cancelada",
            detalhe=f"Cancelada diretamente por admin. Motivo: {motivo}",
            payload=payload,
        )
        db.session.commit()
        _notificar_solicitante_agendamento(row, "Solicitacao cancelada", f"Cancelada diretamente por admin. Motivo: {motivo}")
        veiculo = AgendamentoVeiculo.query.get(row.veiculo_id) if row.veiculo_id else None
        return jsonify({"sucesso": True, "mensagem": "Solicitacao cancelada diretamente pelo admin.", "solicitacao": _serializar_solicitacao(row, veiculo=veiculo)})

    row.cancelamento_pendente = True
    row.cancelamento_solicitado_por = usuario
    row.cancelamento_motivo_pendente = motivo
    row.atualizado_em = datetime.now()
    _registrar_historico(row.id, evento="CANCELAMENTO_SOLICITADO", usuario=usuario, status_anterior=status_anterior, detalhe=f"Cancelamento solicitado. Motivo: {motivo}", payload=payload)
    db.session.commit()
    _notificar_solicitante_agendamento(row, "Aprovacao de cancelamento solicitada", f"A logistica solicitou cancelamento. Motivo: {motivo}")
    veiculo = AgendamentoVeiculo.query.get(row.veiculo_id) if row.veiculo_id else None
    return jsonify({"sucesso": True, "mensagem": "Cancelamento solicitado. Aguardando aprovação do solicitante.", "solicitacao": _serializar_solicitacao(row, veiculo=veiculo)})


@agendamento_bp.route("/api/logistica/agendamento-veiculos/solicitacoes/<int:solicitacao_id>/aprovar-cancelamento", methods=["POST"])
@permission_required("PAGE_LOGISTICA_SOLICITACAO")
def aprovar_cancelamento_solicitacao(solicitacao_id: int):
    """Solicitante aprova o cancelamento pedido pela logistica."""
    row = AgendamentoSolicitacao.query.get(solicitacao_id)
    if not row:
        return jsonify({"error": "Solicitação não encontrada."}), 404
    if not row.cancelamento_pendente:
        return jsonify({"error": "Não há cancelamento pendente para esta solicitação."}), 409
    username = session.get("username", "")
    if row.solicitante != username and session.get("role") != "Admin":
        return jsonify({"error": "Somente o solicitante original ou admin pode aprovar."}), 403

    status_anterior = str(row.status or "").strip()
    motivo_aprovado = row.cancelamento_motivo_pendente
    row.status = "Cancelada"
    row.cancelado_por = row.cancelamento_solicitado_por
    row.cancelado_em = datetime.now()
    row.motivo_cancelamento = motivo_aprovado
    row.cancelamento_pendente = False
    row.cancelamento_solicitado_por = None
    row.cancelamento_motivo_pendente = None
    row.atualizado_em = datetime.now()
    _registrar_historico(row.id, evento="CANCELAMENTO_APROVADO", usuario=username, status_anterior=status_anterior, status_novo="Cancelada", detalhe=f"Cancelamento aprovado por {username}. Motivo: {motivo_aprovado}")
    db.session.commit()
    _notificar_solicitante_agendamento(row, "Cancelamento aprovado", f"Cancelamento aprovado por {username}.")
    veiculo = AgendamentoVeiculo.query.get(row.veiculo_id) if row.veiculo_id else None
    return jsonify({"sucesso": True, "solicitacao": _serializar_solicitacao(row, veiculo=veiculo)})


@agendamento_bp.route("/api/logistica/agendamento-veiculos/solicitacoes/<int:solicitacao_id>/rejeitar-cancelamento", methods=["POST"])
@permission_required("PAGE_LOGISTICA_SOLICITACAO")
def rejeitar_cancelamento_solicitacao(solicitacao_id: int):
    """Solicitante rejeita o cancelamento pedido pela logistica."""
    row = AgendamentoSolicitacao.query.get(solicitacao_id)
    if not row:
        return jsonify({"error": "Solicitação não encontrada."}), 404
    if not row.cancelamento_pendente:
        return jsonify({"error": "Não há cancelamento pendente para esta solicitação."}), 409
    username = session.get("username", "")
    if row.solicitante != username and session.get("role") != "Admin":
        return jsonify({"error": "Somente o solicitante original ou admin pode rejeitar."}), 403

    row.cancelamento_pendente = False
    row.cancelamento_solicitado_por = None
    row.cancelamento_motivo_pendente = None
    row.atualizado_em = datetime.now()
    _registrar_historico(row.id, evento="CANCELAMENTO_REJEITADO", usuario=username, detalhe=f"Cancelamento rejeitado por {username}.")
    db.session.commit()
    _notificar_solicitante_agendamento(row, "Cancelamento rejeitado", f"Cancelamento rejeitado por {username}.")
    veiculo = AgendamentoVeiculo.query.get(row.veiculo_id) if row.veiculo_id else None
    return jsonify({"sucesso": True, "solicitacao": _serializar_solicitacao(row, veiculo=veiculo)})


@agendamento_bp.route("/api/logistica/agendamento-veiculos/motoristas-km")
@permission_required("PAGE_ADMIN_DASHBOARD")
def motoristas_km_dashboard():
    """Retorna km acumulado por motorista para exibir no dashboard principal."""
    dias = int(request.args.get("dias", 30))
    desde = datetime.now() - timedelta(days=dias)
    rows = (
        db.session.query(
            AgendamentoSolicitacao.motorista_id,
            AgendamentoSolicitacao.motorista_nome,
            db.func.count(AgendamentoSolicitacao.id).label("viagens"),
            db.func.sum(AgendamentoSolicitacao.km_estimado).label("km_total"),
        )
        .filter(
            AgendamentoSolicitacao.motorista_id.isnot(None),
            AgendamentoSolicitacao.km_estimado.isnot(None),
            AgendamentoSolicitacao.status.in_(["Alocada", "EmRota", "Concluida"]),
            AgendamentoSolicitacao.criado_em >= desde,
        )
        .group_by(AgendamentoSolicitacao.motorista_id, AgendamentoSolicitacao.motorista_nome)
        .all()
    )
    motoristas = []
    for r in rows:
        km_total = float(r.km_total or 0)
        viagens = int(r.viagens or 0)
        motoristas.append({
            "motorista_id": r.motorista_id,
            "motorista_nome": str(r.motorista_nome or "").strip(),
            "viagens": viagens,
            "km_total": round(km_total, 1),
            "km_medio": round(km_total / viagens, 1) if viagens else 0,
        })
    motoristas.sort(key=lambda x: x["km_total"], reverse=True)
    return jsonify({"motoristas": motoristas, "dias": dias})


@agendamento_bp.route("/api/logistica/agendamento-veiculos/disponibilidade")
@permission_required("PAGE_LOGISTICA_SOLICITACAO")
def disponibilidade_veiculos():
    """Retorna blocos ocupados por veículo para um intervalo de datas."""
    data_inicio_str = request.args.get("data_inicio", "")
    data_fim_str = request.args.get("data_fim", "")
    try:
        data_inicio = datetime.strptime(data_inicio_str, "%Y-%m-%d") if data_inicio_str else datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        data_fim = datetime.strptime(data_fim_str, "%Y-%m-%d").replace(hour=23, minute=59, second=59) if data_fim_str else data_inicio + timedelta(days=7)
    except ValueError:
        data_inicio = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        data_fim = data_inicio + timedelta(days=7)

    veiculos = AgendamentoVeiculo.query.filter_by(ativo=True).order_by(AgendamentoVeiculo.ordem_exibicao).all()
    solicitacoes = AgendamentoSolicitacao.query.filter(
        AgendamentoSolicitacao.veiculo_id.isnot(None),
        AgendamentoSolicitacao.data_hora_saida_prevista.isnot(None),
        AgendamentoSolicitacao.status.in_(["Alocada", "EmAndamento", "EmRota"]),
        AgendamentoSolicitacao.data_hora_saida_prevista >= data_inicio,
        AgendamentoSolicitacao.data_hora_saida_prevista <= data_fim,
    ).all()

    blocos_por_veiculo = {}
    for v in veiculos:
        blocos_por_veiculo[v.id] = {
            "veiculo_id": v.id,
            "nome": v.nome_exibicao,
            "placa": v.placa or "",
            "duracao_padrao_min": v.duracao_padrao_min,
            "blocos": [],
        }

    for s in solicitacoes:
        vid = s.veiculo_id
        if vid not in blocos_por_veiculo:
            continue
        inicio = s.data_hora_saida_prevista
        duracao = s.tempo_estimado_min or blocos_por_veiculo[vid]["duracao_padrao_min"] or 120
        fim = inicio + timedelta(minutes=duracao)
        blocos_por_veiculo[vid]["blocos"].append({
            "solicitacao_id": s.id,
            "codigo": s.codigo or "",
            "tipo": s.tipo or "",
            "parceiro": s.parceiro_nome or "",
            "status": s.status,
            "inicio": inicio.strftime("%Y-%m-%d %H:%M"),
            "fim": fim.strftime("%Y-%m-%d %H:%M"),
            "duracao_min": duracao,
        })

    for vid in blocos_por_veiculo:
        blocos_por_veiculo[vid]["blocos"].sort(key=lambda b: b["inicio"])

    return jsonify({
        "veiculos": list(blocos_por_veiculo.values()),
        "data_inicio": data_inicio.strftime("%Y-%m-%d"),
        "data_fim": data_fim.strftime("%Y-%m-%d"),
    })


@agendamento_bp.route("/api/logistica/agendamento-veiculos/sugestao-rota")
@permission_required("PAGE_LOGISTICA_AGENDAMENTO")
def sugestao_rota():
    """Sugere a ordem das coletas/entregas de um dia usando nearest-neighbor."""
    from math import radians, sin, cos, sqrt, atan2
    data_str = request.args.get("data", "")
    veiculo_id = request.args.get("veiculo_id", "")
    try:
        data = datetime.strptime(data_str, "%Y-%m-%d") if data_str else datetime.now()
    except ValueError:
        data = datetime.now()
    dia_inicio = data.replace(hour=0, minute=0, second=0, microsecond=0)
    dia_fim = dia_inicio + timedelta(days=1)

    q = AgendamentoSolicitacao.query.filter(
        AgendamentoSolicitacao.status.in_(["Alocada", "EmAndamento"]),
        AgendamentoSolicitacao.data_hora_saida_prevista.isnot(None),
        AgendamentoSolicitacao.data_hora_saida_prevista >= dia_inicio,
        AgendamentoSolicitacao.data_hora_saida_prevista < dia_fim,
    )
    if veiculo_id:
        q = q.filter(AgendamentoSolicitacao.veiculo_id == int(veiculo_id))
    rows = q.all()

    if not rows:
        return jsonify({"sugestao": [], "mensagem": "Nenhuma solicitação alocada para este dia."})

    from conferencia_app.config import Config
    base_lat = Config.AGENDAMENTO_BASE_LAT
    base_lng = Config.AGENDAMENTO_BASE_LNG

    def haversine(lat1, lon1, lat2, lon2):
        R = 6371
        dlat = radians(lat2 - lat1)
        dlon = radians(lon2 - lon1)
        a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
        return R * 2 * atan2(sqrt(a), sqrt(1 - a))

    pontos = []
    for r in rows:
        lat = r.destino_latitude or 0
        lng = r.destino_longitude or 0
        pontos.append({
            "id": r.id,
            "codigo": r.codigo or "",
            "tipo": r.tipo or "",
            "parceiro": r.parceiro_nome or "",
            "cidade": r.cidade_destino or "",
            "uf": r.uf_destino or "",
            "lat": float(lat),
            "lng": float(lng),
            "km_estimado": float(r.km_estimado or 0),
            "saida_prevista": r.data_hora_saida_prevista.strftime("%Y-%m-%d %H:%M") if r.data_hora_saida_prevista else "",
        })

    # Nearest-neighbor a partir da base
    ordenados = []
    restantes = list(pontos)
    cur_lat, cur_lng = base_lat, base_lng
    velocidade_media_kmh = 50
    tempo_parada_min = 30

    while restantes:
        melhor = None
        melhor_dist = float("inf")
        for p in restantes:
            if p["lat"] == 0 and p["lng"] == 0:
                d = 9999
            else:
                d = haversine(cur_lat, cur_lng, p["lat"], p["lng"])
            if d < melhor_dist:
                melhor_dist = d
                melhor = p
        restantes.remove(melhor)
        km_trecho = round(melhor_dist * 1.28, 1) if melhor_dist < 9000 else 0
        tempo_trecho_min = round((km_trecho / velocidade_media_kmh) * 60) if km_trecho > 0 else 0
        melhor["ordem"] = len(ordenados) + 1
        melhor["km_trecho"] = km_trecho
        melhor["tempo_trecho_min"] = tempo_trecho_min
        melhor["tempo_parada_min"] = tempo_parada_min
        melhor["tempo_total_min"] = tempo_trecho_min + tempo_parada_min
        ordenados.append(melhor)
        if melhor["lat"] != 0 or melhor["lng"] != 0:
            cur_lat, cur_lng = melhor["lat"], melhor["lng"]

    tempo_acumulado = 0
    for p in ordenados:
        tempo_acumulado += p["tempo_total_min"]
        p["tempo_acumulado_min"] = tempo_acumulado

    return jsonify({"sugestao": ordenados, "data": data.strftime("%Y-%m-%d"), "veiculo_id": veiculo_id or None})


def _auto_transicao_em_andamento():
    """Muda Alocada -> EmAndamento quando a data de saída prevista chegou."""
    hoje = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    rows = AgendamentoSolicitacao.query.filter(
        AgendamentoSolicitacao.status == "Alocada",
        AgendamentoSolicitacao.data_hora_saida_prevista.isnot(None),
        AgendamentoSolicitacao.data_hora_saida_prevista < hoje + timedelta(days=1),
    ).all()
    for row in rows:
        row.status = "EmAndamento"
        row.atualizado_em = datetime.now()
        _registrar_historico(
            row.id,
            evento="AUTO_EM_ANDAMENTO",
            usuario="sistema",
            status_anterior="Alocada",
            status_novo="EmAndamento",
            detalhe="Status alterado automaticamente: dia da viagem chegou.",
        )
    if rows:
        db.session.commit()


@agendamento_bp.route("/api/logistica/motorista/minhas-viagens")
@permission_required("PAGE_LOGISTICA_MOTORISTA")
def minhas_viagens_motorista():
    """Retorna viagens do motorista logado (usa usuario_username)."""
    username = session.get("username", "")
    sincronizar_motoristas_usuarios(commit=True)
    motorista = AgendamentoMotorista.query.filter_by(usuario_username=username).first()
    if not motorista:
        return jsonify({"viagens": [], "motorista": None})
    rows = (
        AgendamentoSolicitacao.query
        .filter(
            AgendamentoSolicitacao.motorista_id == motorista.id,
            AgendamentoSolicitacao.status.in_(STATUS_ATIVOS | {"Concluida"}),
        )
        .order_by(AgendamentoSolicitacao.data_hora_saida_prevista.asc())
        .all()
    )
    viagens = []
    for row in rows:
        veiculo = AgendamentoVeiculo.query.get(row.veiculo_id) if row.veiculo_id else None
        viagens.append(_serializar_solicitacao(row, veiculo=veiculo))
    return jsonify({
        "viagens": viagens,
        "motorista": serializar_motorista(motorista),
    })


@agendamento_bp.route("/api/logistica/motorista/finalizar/<int:solicitacao_id>", methods=["POST"])
@permission_required("PAGE_LOGISTICA_MOTORISTA")
def motorista_finalizar_viagem(solicitacao_id: int):
    """Motorista marca viagem como Concluída."""
    username = session.get("username", "")
    sincronizar_motoristas_usuarios(commit=True)
    motorista = AgendamentoMotorista.query.filter_by(usuario_username=username).first()
    if not motorista:
        return jsonify({"error": "Motorista não encontrado para este usuário."}), 404
    row = AgendamentoSolicitacao.query.get(solicitacao_id)
    if not row:
        return jsonify({"error": "Solicitação não encontrada."}), 404
    if row.motorista_id != motorista.id:
        return jsonify({"error": "Esta viagem não pertence a você."}), 403
    status_atual = str(row.status or "").strip()
    if status_atual in {"Concluida", "Cancelada"}:
        return jsonify({"error": "Viagem já encerrada."}), 409
    if status_atual not in {"EmRota"}:
        return jsonify({"error": "Você precisa iniciar a viagem antes de finalizar."}), 409
    row.status = "Concluida"
    row.concluido_por = username
    row.concluido_em = datetime.now()
    row.data_hora_retorno_real = datetime.now()
    row.atualizado_em = datetime.now()
    _registrar_historico(
        row.id,
        evento="CONCLUIDA_MOTORISTA",
        usuario=username,
        status_anterior=status_atual,
        status_novo="Concluida",
        detalhe=f"Viagem finalizada pelo motorista {motorista.nome}.",
    )
    db.session.commit()
    veiculo = AgendamentoVeiculo.query.get(row.veiculo_id) if row.veiculo_id else None
    return jsonify({"sucesso": True, "solicitacao": _serializar_solicitacao(row, veiculo=veiculo)})


@agendamento_bp.route("/api/logistica/motorista/iniciar/<int:solicitacao_id>", methods=["POST"])
@permission_required("PAGE_LOGISTICA_MOTORISTA")
def motorista_iniciar_viagem(solicitacao_id: int):
    """Motorista confirma que iniciou a viagem (Alocada/EmAndamento -> EmRota)."""
    username = session.get("username", "")
    sincronizar_motoristas_usuarios(commit=True)
    motorista = AgendamentoMotorista.query.filter_by(usuario_username=username).first()
    if not motorista:
        return jsonify({"error": "Motorista não encontrado para este usuário."}), 404
    row = AgendamentoSolicitacao.query.get(solicitacao_id)
    if not row:
        return jsonify({"error": "Solicitação não encontrada."}), 404
    if row.motorista_id != motorista.id:
        return jsonify({"error": "Esta viagem não pertence a você."}), 403
    status_atual = str(row.status or "").strip()
    if status_atual in {"Concluida", "Cancelada"}:
        return jsonify({"error": "Viagem já encerrada."}), 409
    if status_atual not in {"Alocada", "EmAndamento"}:
        return jsonify({"error": "Viagem precisa estar alocada para iniciar."}), 409
    row.status = "EmRota"
    row.data_hora_saida_real = datetime.now()
    row.atualizado_em = datetime.now()
    _registrar_historico(
        row.id,
        evento="INICIADA_MOTORISTA",
        usuario=username,
        status_anterior=status_atual,
        status_novo="EmRota",
        detalhe=f"Viagem iniciada pelo motorista {motorista.nome}.",
    )
    db.session.commit()
    veiculo = AgendamentoVeiculo.query.get(row.veiculo_id) if row.veiculo_id else None
    return jsonify({"sucesso": True, "solicitacao": _serializar_solicitacao(row, veiculo=veiculo)})
