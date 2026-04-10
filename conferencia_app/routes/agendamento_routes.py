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
)
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
    serializar_cadastro,
    serializar_motorista,
    status_label_agendamento,
)


agendamento_bp = Blueprint("agendamento", __name__)

PRIORIDADE_ORDEM = {"Critica": 0, "Alta": 1, "Media": 2, "Baixa": 3}


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
        raise ValueError(f"{field_name} invalido.") from exc


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
    if status in {"Alocada", "EmRota"} and veiculo and str(veiculo.codigo or "").strip().upper() in VEICULOS_KANBAN:
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
        "tipo_label": "Coleta" if str(registro.tipo or "").strip() == "COLETA" else "Entrega",
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
                "pendentes": sum(1 for card in cards if card.get("kanban_coluna") == "pendentes"),
                "iveco": sum(1 for card in cards if card.get("kanban_coluna") == "IVECO"),
                "saveiro": sum(1 for card in cards if card.get("kanban_coluna") == "SAVEIRO"),
                "concluidas": sum(1 for card in cards if card.get("status") == "Concluida"),
                "em_rota": sum(1 for card in cards if card.get("status") == "EmRota"),
                "atrasadas": sum(1 for card in cards if card.get("atrasada")),
                "motoristas_ativos": AgendamentoMotorista.query.filter_by(ativo=True).count(),
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
            "motoristas": listar_motoristas_agendamento(),
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
        return jsonify({"error": "Tipo de cadastro invalido."}), 400
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
@permission_required("PAGE_LOGISTICA_SOLICITACAO")
def consultar_oc_agendamento_endpoint(numero_oc: str):
    resultado = consultar_oc_agendamento(numero_oc)
    return jsonify(resultado), (200 if resultado.get("encontrada") else 404)


@agendamento_bp.route("/api/logistica/agendamento-veiculos/referencia/nf/<numero_nf>")
@permission_required("PAGE_LOGISTICA_SOLICITACAO")
def consultar_nf_agendamento_endpoint(numero_nf: str):
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
        return jsonify({"error": "Solicitacao nao encontrada."}), 404
    itens = AgendamentoSolicitacaoItem.query.filter_by(solicitacao_id=row.id).order_by(AgendamentoSolicitacaoItem.sequencia.asc()).all()
    historico = AgendamentoSolicitacaoHistorico.query.filter_by(solicitacao_id=row.id).order_by(AgendamentoSolicitacaoHistorico.criado_em.desc()).all()
    veiculo = AgendamentoVeiculo.query.get(row.veiculo_id) if row.veiculo_id else None
    return jsonify({"solicitacao": _serializar_solicitacao(row, veiculo=veiculo, itens=itens, historico=historico)})


@agendamento_bp.route("/api/logistica/agendamento-veiculos/solicitacoes", methods=["POST"])
@permission_required("PAGE_LOGISTICA_SOLICITACAO")
def criar_solicitacao_agendamento():
    payload = request.get_json(silent=True) or {}
    tipo = str(payload.get("tipo") or "").strip().upper()
    prioridade = str(payload.get("prioridade") or "Media").strip()
    if tipo not in TIPOS_SOLICITACAO:
        return jsonify({"error": "Tipo de solicitacao invalido."}), 400
    if prioridade not in PRIORIDADES_SOLICITACAO:
        return jsonify({"error": "Prioridade invalida."}), 400

    numero_oc = str(payload.get("numero_oc") or "").strip()
    numero_nf = re.sub(r"\D", "", str(payload.get("numero_nf") or ""))
    if tipo == "COLETA" and not numero_oc:
        return jsonify({"error": "Informe a OC para a coleta."}), 400
    if tipo == "ENTREGA" and not numero_nf:
        return jsonify({"error": "Informe a NF para a entrega."}), 400

    try:
        prazo_limite = _parse_datetime(payload.get("prazo_limite"), "o prazo")
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    consulta = consultar_oc_agendamento(numero_oc) if tipo == "COLETA" else consultar_nf_agendamento(numero_nf)
    parceiro = _resolver_parceiro_payload(payload, "fornecedor" if tipo == "COLETA" else "cliente")
    if not parceiro:
        parceiro = consulta.get("fornecedor") or consulta.get("cliente") or {}

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
        solicitante=usuario,
        criado_em=agora,
        atualizado_em=agora,
        documento_tipo="OC" if tipo == "COLETA" else "NF",
        documento_numero=numero_oc if tipo == "COLETA" else numero_nf,
        numero_oc=numero_oc or None,
        numero_nf=numero_nf or None,
        origem_documento=origem_documento,
        observacoes_solicitante=str(payload.get("observacoes_solicitante") or "").strip() or None,
        observacoes_logistica=str(payload.get("observacoes_logistica") or "").strip() or None,
        payload_origem=_json_text({"request": payload, "consulta": {"encontrada": consulta.get("encontrada"), "fonte": consulta.get("fonte")}}),
    )
    ok, msg = _aplicar_parceiro(row, parceiro, "Fornecedor" if tipo == "COLETA" else "Cliente")
    if not ok:
        return jsonify({"error": msg}), 400

    itens = payload.get("itens")
    if not isinstance(itens, list) or not itens:
        itens = list(consulta.get("itens") or [])
    if not isinstance(itens, list) or not itens:
        return jsonify({"error": "Adicione pelo menos 1 item para continuar."}), 400

    db.session.add(row)
    db.session.flush()
    row.codigo = f"LOG-{agora.strftime('%Y%m%d')}-{row.id:04d}"
    if not _sincronizar_itens(row, itens):
        db.session.rollback()
        return jsonify({"error": "Adicione pelo menos 1 item valido para continuar."}), 400
    _registrar_historico(row.id, evento="CRIADA", usuario=usuario, status_novo="Pendente", detalhe=f"Solicitacao criada via {row.documento_tipo} {row.documento_numero}.", payload=payload)
    db.session.commit()
    return jsonify({"sucesso": True, "solicitacao": _serializar_solicitacao(row)}), 201


@agendamento_bp.route("/api/logistica/agendamento-veiculos/solicitacoes/<int:solicitacao_id>/alocar", methods=["POST"])
@permission_required("PAGE_LOGISTICA_AGENDAMENTO")
def alocar_solicitacao_agendamento(solicitacao_id: int):
    row = AgendamentoSolicitacao.query.get(solicitacao_id)
    if not row:
        return jsonify({"error": "Solicitacao nao encontrada."}), 404
    if str(row.status or "").strip() in {"Concluida", "Cancelada"}:
        return jsonify({"error": "Nao e possivel alocar uma solicitacao finalizada."}), 409

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
        return jsonify({"error": "Selecione um veiculo valido."}), 400

    try:
        saida = _parse_datetime(payload.get("data_hora_saida_prevista"), "a data e hora de saida", required=True)
        retorno = _parse_datetime(payload.get("data_hora_retorno_prevista"), "a previsao de retorno")
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if retorno and retorno <= saida:
        return jsonify({"error": "A previsao de retorno deve ser maior que a saida."}), 400

    motorista = None
    try:
        motorista_id = int(payload.get("motorista_id")) if payload.get("motorista_id") not in (None, "") else None
    except (TypeError, ValueError):
        motorista_id = None
    if motorista_id:
        motorista = AgendamentoMotorista.query.get(motorista_id)
        if not motorista:
            return jsonify({"error": "Selecione um motorista valido."}), 400

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
            return jsonify({"error": f"{veiculo.nome_exibicao} ja possui uma saida programada para {outro_inicio.strftime('%d/%m/%Y %H:%M')}."}), 409

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

    detalhe = f"Alocada no veiculo {veiculo.nome_exibicao} para {saida.strftime('%d/%m/%Y %H:%M')}."
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
    return jsonify({"sucesso": True, "solicitacao": _serializar_solicitacao(row, veiculo=veiculo)})


@agendamento_bp.route("/api/logistica/agendamento-veiculos/solicitacoes/<int:solicitacao_id>/status", methods=["POST"])
@permission_required("PAGE_LOGISTICA_AGENDAMENTO")
def atualizar_status_agendamento(solicitacao_id: int):
    row = AgendamentoSolicitacao.query.get(solicitacao_id)
    if not row:
        return jsonify({"error": "Solicitacao nao encontrada."}), 404
    status_atual = str(row.status or "").strip()
    if status_atual in {"Cancelada", "Concluida"}:
        return jsonify({"error": "A solicitacao ja esta encerrada."}), 409

    payload = request.get_json(silent=True) or {}
    novo_status = str(payload.get("status") or "").strip()
    if novo_status not in STATUS_SOLICITACAO:
        return jsonify({"error": "Status invalido."}), 400
    if novo_status == "Cancelada":
        return jsonify({"error": "Use a acao de cancelamento."}), 400
    if novo_status == "Alocada":
        return jsonify({"error": "Use a acao de alocacao para definir veiculo e horario."}), 400
    if novo_status == "EmRota" and status_atual not in {"Alocada", "EmRota"}:
        return jsonify({"error": "A solicitacao precisa estar alocada antes de entrar em rota."}), 409
    if novo_status == "Concluida" and status_atual not in {"Alocada", "EmRota"}:
        return jsonify({"error": "A solicitacao precisa estar alocada ou em rota antes da conclusao."}), 409
    if novo_status == "Pendente" and status_atual == "EmRota":
        return jsonify({"error": "Nao e possivel voltar uma solicitacao em rota para pendente."}), 409

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
            row.data_hora_saida_real = _parse_datetime(payload.get("data_hora_saida_real"), "a saida real")
            row.data_hora_retorno_real = _parse_datetime(payload.get("data_hora_retorno_real"), "o retorno real")
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        row.concluido_por = session.get("username", "desconhecido")
        row.concluido_em = datetime.now()

    _registrar_historico(row.id, evento="STATUS_ALTERADO", usuario=session.get("username", "desconhecido"), status_anterior=status_atual, status_novo=novo_status, detalhe=observacao or f"Status alterado para {status_label_agendamento(novo_status)}.", payload=payload)
    db.session.commit()
    veiculo = AgendamentoVeiculo.query.get(row.veiculo_id) if row.veiculo_id else None
    return jsonify({"sucesso": True, "solicitacao": _serializar_solicitacao(row, veiculo=veiculo)})


@agendamento_bp.route("/api/logistica/agendamento-veiculos/solicitacoes/<int:solicitacao_id>/cancelar", methods=["POST"])
@permission_required("PAGE_LOGISTICA_AGENDAMENTO")
def cancelar_solicitacao_agendamento(solicitacao_id: int):
    row = AgendamentoSolicitacao.query.get(solicitacao_id)
    if not row:
        return jsonify({"error": "Solicitacao nao encontrada."}), 404
    if str(row.status or "").strip() == "Concluida":
        return jsonify({"error": "Nao e possivel cancelar uma solicitacao concluida."}), 409
    if str(row.status or "").strip() == "EmRota" and session.get("role") != "Admin":
        return jsonify({"error": "Somente admin pode cancelar uma solicitacao em rota."}), 403

    payload = request.get_json(silent=True) or {}
    motivo = str(payload.get("motivo") or "").strip()
    if not motivo:
        return jsonify({"error": "Informe o motivo do cancelamento."}), 400

    status_anterior = str(row.status or "").strip()
    row.status = "Cancelada"
    row.cancelado_por = session.get("username", "desconhecido")
    row.cancelado_em = datetime.now()
    row.motivo_cancelamento = motivo
    row.atualizado_em = datetime.now()
    _registrar_historico(row.id, evento="CANCELADA", usuario=session.get("username", "desconhecido"), status_anterior=status_anterior, status_novo="Cancelada", detalhe=motivo, payload=payload)
    db.session.commit()
    veiculo = AgendamentoVeiculo.query.get(row.veiculo_id) if row.veiculo_id else None
    return jsonify({"sucesso": True, "solicitacao": _serializar_solicitacao(row, veiculo=veiculo)})
