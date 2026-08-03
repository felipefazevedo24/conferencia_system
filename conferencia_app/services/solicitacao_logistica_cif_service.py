"""Automacao de Solicitacoes Logisticas por modalidade de frete CIF.

Duas regras, ambas sem intervencao do usuario (scheduler) e com
reprocessamento manual:

Regra 1 - Coleta (Pedido de Compra CIF):
    Sempre que uma OC em aberto tiver frete = CIF, gera uma Solicitacao de
    Coleta com data programada = N dias antes da previsao de entrega. Herda
    numero da OC, fornecedor, endereco de coleta, previsao de entrega e
    observacoes. Evita duplicidade e recalcula a data se a previsao mudar.

Regra 2 - Entrega (NF de Saida CIF com Romaneio):
    Sempre que um Romaneio de saida com tipo_frete = CIF estiver em status
    Pronto ou Expedido, gera uma Solicitacao de Entrega herdando as NFs, o
    cliente, o romaneio, o volume/peso e o responsavel pela emissao. Evita
    duplicidade.

Regras gerais atendidas: log de criacao automatica (historico), rastreabilidade
via payload_origem (numero da OC / romaneio_id) e origem_documento = "AutoCIF".

Como o nome da coluna "frete por conta" no ERP nao e conhecido a priori, a OC e
lida inteira (to_jsonb) e a modalidade de frete e identificada e classificada
como CIF em Python (config SOLICITACAO_CIF_VALORES_CIF).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from flask import current_app

from ..extensions import db
from ..models import (
    AgendamentoSolicitacao,
    ExpedicaoRomaneio,
)

ORIGEM_AUTO_CIF = "AutoCIF"

# Chaves candidatas para a coluna "frete por conta" no cabecalho da OC.
_FRETE_KEY_TOKENS = (
    "frete_por_conta",
    "fretepconta",
    "frete_conta",
    "conta_frete",
    "modalidade_frete",
    "frete_modalidade",
    "tipo_frete",
    "modfrete",
    "mod_frete",
    "incoterm",
    "frete",
)

_CIF_TOKENS_PADRAO = ("CIF", "REMETENTE", "EMITENTE", "FORNECEDOR")


# ---------------------------------------------------------------------------
# Helpers genericos
# ---------------------------------------------------------------------------
def _first(*vals) -> str:
    for value in vals:
        texto = str(value if value is not None else "").strip()
        if texto:
            return texto
    return ""


def _parse_data(valor) -> datetime | None:
    if isinstance(valor, datetime):
        return valor
    if isinstance(valor, date):
        return datetime(valor.year, valor.month, valor.day)
    texto = str(valor or "").strip()
    if not texto:
        return None
    try:
        return datetime.fromisoformat(texto.replace("Z", "").split("+")[0])
    except ValueError:
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M:%S"):
            try:
                return datetime.strptime(texto[:19], fmt)
            except ValueError:
                continue
    return None


def _cif_tokens() -> list[str]:
    raw = current_app.config.get("SOLICITACAO_CIF_VALORES_CIF") or _CIF_TOKENS_PADRAO
    if isinstance(raw, str):
        tokens = [t.strip().upper() for t in raw.split(",") if t.strip()]
    else:
        tokens = [str(t).strip().upper() for t in raw if str(t).strip()]
    return tokens or list(_CIF_TOKENS_PADRAO)


def _eh_cif(valor: str) -> bool:
    texto = str(valor or "").strip().upper()
    if not texto:
        return False
    for token in _cif_tokens():
        if texto == token or token in texto:
            return True
    return False


def _valor_frete_da_oc(oc_json: dict) -> tuple[str, str]:
    """Localiza a coluna de 'frete por conta' no cabecalho da OC.

    Retorna (nome_da_chave, valor_em_texto). Vazio quando nao encontrada.
    """
    if not isinstance(oc_json, dict):
        return "", ""
    lowered = {str(k).lower(): (k, v) for k, v in oc_json.items()}
    for token in _FRETE_KEY_TOKENS:
        if token in lowered:
            _, valor = lowered[token]
            return token, "" if valor is None else str(valor).strip()
    for chave_lower, (_, valor) in lowered.items():
        if "frete" in chave_lower and any(t in chave_lower for t in ("conta", "modalidade", "tipo", "cif", "incoterm")):
            return chave_lower, "" if valor is None else str(valor).strip()
    return "", ""


def _garantir_parceiro_minimo(parceiro: dict, nome_fallback: str, obs_pendente: str) -> tuple[dict, bool]:
    """Garante nome/endereco minimos para a solicitacao aparecer na fila.

    Quando o endereco vem incompleto, preenche placeholders e anexa uma
    observacao para a logistica completar. Retorna (parceiro, endereco_incompleto).
    """
    parceiro = dict(parceiro or {})
    if not _first(parceiro.get("nome"), parceiro.get("razao_social")):
        parceiro["nome"] = nome_fallback or "A definir"
    incompleto = not (
        _first(parceiro.get("logradouro"))
        and _first(parceiro.get("cidade"))
        and _first(parceiro.get("uf"))
    )
    if not _first(parceiro.get("logradouro")):
        parceiro["logradouro"] = "A definir"
    if not _first(parceiro.get("cidade")):
        parceiro["cidade"] = "A definir"
    if not _first(parceiro.get("uf")):
        parceiro["uf"] = "--"
    if incompleto:
        obs_atual = _first(parceiro.get("observacoes"))
        parceiro["observacoes"] = (obs_atual + " | " if obs_atual else "") + obs_pendente
    return parceiro, incompleto


def _criar_solicitacao(
    *,
    tipo: str,
    documento_tipo: str,
    documento_numero: str,
    parceiro: dict,
    itens: list[dict],
    numero_oc: str | None = None,
    numero_nf: str | None = None,
    prazo_limite: datetime | None = None,
    data_desejada: datetime | None = None,
    observacoes_solicitante: str | None = None,
    observacoes_logistica: str | None = None,
    payload_origem: Any = None,
    solicitante: str = "sistema",
    evento_detalhe: str = "",
) -> AgendamentoSolicitacao | None:
    """Cria uma AgendamentoSolicitacao automatica (sem contexto de request)."""
    from ..routes.agendamento_routes import (
        _aplicar_parceiro,
        _json_text,
        _registrar_historico,
        _sincronizar_itens,
    )

    agora = datetime.now()
    row = AgendamentoSolicitacao(
        tipo=tipo,
        status="Pendente",
        prioridade="Media",
        prazo_limite=prazo_limite,
        data_desejada=data_desejada,
        solicitante=solicitante or "sistema",
        criado_em=agora,
        atualizado_em=agora,
        documento_tipo=documento_tipo,
        documento_numero=str(documento_numero or "")[:60] or f"AUTO-{agora:%Y%m%d%H%M}",
        numero_oc=(str(numero_oc)[:60] if numero_oc else None),
        numero_nf=(str(numero_nf)[:60] if numero_nf else None),
        origem_documento=ORIGEM_AUTO_CIF,
        observacoes_solicitante=(observacoes_solicitante or None),
        observacoes_logistica=(observacoes_logistica or None),
        payload_origem=(_json_text(payload_origem) if payload_origem is not None else None),
    )
    ok, msg = _aplicar_parceiro(row, parceiro, "Fornecedor" if tipo == "COLETA" else "Cliente")
    if not ok:
        current_app.logger.info(
            "CIF auto: solicitacao %s %s ignorada (%s).", tipo, documento_numero, msg
        )
        return None

    db.session.add(row)
    db.session.flush()
    row.codigo = f"LOG-{agora:%Y%m%d}-{row.id:04d}"

    if not itens:
        itens = [{"descricao": f"Documento {documento_numero}", "quantidade": 1, "unidade": "UN", "volumes": 0}]
    _sincronizar_itens(row, itens)
    _registrar_historico(
        row.id,
        evento="CRIADA_AUTO_CIF",
        usuario=solicitante or "sistema",
        status_novo="Pendente",
        detalhe=evento_detalhe or f"Solicitacao gerada automaticamente (frete CIF) via {documento_tipo} {documento_numero}.",
        payload=payload_origem,
    )
    return row


# ---------------------------------------------------------------------------
# Regra 2 - Entrega a partir de Romaneio CIF
# ---------------------------------------------------------------------------
def _buscar_entrega_existente(romaneio_id: int) -> AgendamentoSolicitacao | None:
    marcador = f'"romaneio_id": {int(romaneio_id)}'
    return (
        AgendamentoSolicitacao.query
        .filter(
            AgendamentoSolicitacao.tipo == "ENTREGA",
            AgendamentoSolicitacao.origem_documento == ORIGEM_AUTO_CIF,
            AgendamentoSolicitacao.payload_origem.like(f"%{marcador}%"),
        )
        .first()
    )


def _parceiro_cliente_romaneio(romaneio: ExpedicaoRomaneio, nfs: list) -> dict:
    nome = _first(romaneio.cliente, *[nf.cliente for nf in nfs])
    try:
        from ..services.agendamento_service import localizar_cadastro, serializar_cadastro

        cadastro = localizar_cadastro("cliente", nome=nome)
        if cadastro:
            parceiro = serializar_cadastro(cadastro, "cliente")
            if not parceiro.get("nome"):
                parceiro["nome"] = nome
            return parceiro
    except Exception:
        current_app.logger.debug("CIF auto entrega: cadastro de cliente nao localizado para %s", nome)
    return {"nome": nome}


def _itens_do_romaneio(romaneio: ExpedicaoRomaneio, nfs: list) -> list[dict]:
    itens = []
    for nf in nfs:
        volumes = int(nf.qtde_volumes or 0)
        descricao = f"NF {nf.numero_nf} — {nf.cliente or romaneio.cliente or ''}".strip(" —")
        itens.append(
            {
                "codigo_item": str(nf.numero_nf or "").strip(),
                "descricao": descricao or f"NF {nf.numero_nf}",
                "quantidade": volumes or 1,
                "unidade": "VOL",
                "volumes": volumes,
                "observacoes": str(nf.especie_volumes or "").strip(),
            }
        )
    if not itens:
        itens = [
            {
                "descricao": f"Entrega do romaneio {romaneio.numero_romaneio}",
                "quantidade": 1,
                "unidade": "UN",
                "volumes": int(romaneio.qtde_volumes_total or 0),
            }
        ]
    return itens


def gerar_solicitacao_entrega_para_romaneio(
    romaneio: ExpedicaoRomaneio,
    *,
    solicitante: str = "sistema",
    commit: bool = True,
) -> tuple[bool, AgendamentoSolicitacao | None, str]:
    """Gera (ou reaproveita) a Solicitacao de Entrega de um romaneio CIF."""
    if not romaneio:
        return False, None, "Romaneio inexistente."
    if str(romaneio.tipo_frete or "").strip().upper() != "CIF":
        return False, None, "Romaneio nao e CIF."
    status = str(romaneio.status or "").strip()
    if status not in ("Pronto", "Expedido"):
        return False, None, f"Romaneio em status '{status}' (aguardando Pronto/Expedido)."

    existente = _buscar_entrega_existente(romaneio.id)
    if existente:
        return False, existente, "Solicitacao de entrega ja existe para este romaneio."

    nfs = list(romaneio.nfs or [])
    numeros_nf = [str(nf.numero_nf or "").strip() for nf in nfs if str(nf.numero_nf or "").strip()]
    doc_nf = " / ".join(numeros_nf) if numeros_nf else str(romaneio.numero_romaneio or "")

    parceiro = _parceiro_cliente_romaneio(romaneio, nfs)
    parceiro, incompleto = _garantir_parceiro_minimo(
        parceiro,
        _first(romaneio.cliente),
        "Endereco de entrega a confirmar",
    )
    itens = _itens_do_romaneio(romaneio, nfs)
    peso = float(romaneio.peso_bruto_total or 0)
    volumes = int(romaneio.qtde_volumes_total or 0)
    payload = {
        "origem": "RomaneioCIF",
        "romaneio_id": romaneio.id,
        "romaneio_numero": romaneio.numero_romaneio,
        "nfs": numeros_nf,
        "peso_bruto_total": peso,
        "qtde_volumes_total": volumes,
        "responsavel_emissao": romaneio.criado_por,
        "data_romaneio": romaneio.data_romaneio.isoformat() if romaneio.data_romaneio else None,
    }
    obs_log = (
        f"Romaneio {romaneio.numero_romaneio} (CIF). NF(s): {doc_nf}. "
        f"Peso bruto {peso:g} kg, {volumes} volume(s). "
        f"Responsavel pela emissao: {romaneio.criado_por or '---'}."
    )

    row = _criar_solicitacao(
        tipo="ENTREGA",
        documento_tipo="NF",
        documento_numero=doc_nf,
        numero_nf=doc_nf,
        parceiro=parceiro,
        itens=itens,
        observacoes_logistica=obs_log,
        payload_origem=payload,
        solicitante=solicitante,
        evento_detalhe=f"Entrega gerada automaticamente (frete CIF) a partir do romaneio {romaneio.numero_romaneio}.",
    )
    if not row:
        if commit:
            db.session.rollback()
        return False, None, "Falha ao montar a solicitacao de entrega."
    if commit:
        db.session.commit()
    return True, row, "Solicitacao de entrega criada."


def gerar_solicitacoes_entrega_cif(app=None) -> dict:
    """Varre romaneios CIF em Pronto/Expedido e gera as entregas faltantes."""

    def _run() -> dict:
        romaneios = (
            ExpedicaoRomaneio.query
            .filter(
                ExpedicaoRomaneio.tipo_frete == "CIF",
                ExpedicaoRomaneio.status.in_(("Pronto", "Expedido")),
            )
            .all()
        )
        criadas = ignoradas = erros = 0
        solicitante = str(current_app.config.get("SOLICITACAO_CIF_SOLICITANTE", "sistema"))
        for rom in romaneios:
            try:
                ok, _row, _msg = gerar_solicitacao_entrega_para_romaneio(
                    rom, solicitante=solicitante, commit=True
                )
                if ok:
                    criadas += 1
                else:
                    ignoradas += 1
            except Exception:
                db.session.rollback()
                erros += 1
                current_app.logger.exception(
                    "CIF auto entrega: falha no romaneio %s", getattr(rom, "numero_romaneio", None)
                )
        current_app.logger.info(
            "CIF auto entrega: %s romaneio(s) CIF, %s criada(s), %s ignorada(s), %s erro(s).",
            len(romaneios), criadas, ignoradas, erros,
        )
        return {"criadas": criadas, "ignoradas": ignoradas, "erros": erros, "total": len(romaneios)}

    if app is not None:
        with app.app_context():
            return _run()
    return _run()


def cancelar_solicitacao_entrega_para_romaneio(
    romaneio,
    *,
    usuario: str = "sistema",
    motivo: str = "",
    commit: bool = True,
) -> tuple[bool, AgendamentoSolicitacao | None, str]:
    """Cancela (estorna) a Solicitacao de Entrega gerada automaticamente para um
    romaneio CIF. Usada quando o romaneio e estornado/excluido. Nao cancela
    solicitacoes que ja foram concluidas nem as ja canceladas."""
    if not romaneio:
        return False, None, "Romaneio inexistente."

    row = _buscar_entrega_existente(getattr(romaneio, "id", 0) or 0)
    if not row:
        return False, None, "Nenhuma solicitacao automatica de entrega para este romaneio."
    if row.status in ("Cancelada", "Concluida"):
        return False, row, f"Solicitacao ja esta '{row.status}'."

    agora = datetime.now()
    status_anterior = row.status
    row.status = "Cancelada"
    row.cancelado_por = usuario or "sistema"
    row.cancelado_em = agora
    row.motivo_cancelamento = (motivo or "Romaneio estornado/excluido.")[:500]
    row.cancelamento_pendente = False
    row.atualizado_em = agora

    try:
        from ..routes.agendamento_routes import _registrar_historico

        _registrar_historico(
            row.id,
            evento="CANCELADA_AUTO_CIF",
            usuario=usuario or "sistema",
            status_anterior=status_anterior,
            status_novo="Cancelada",
            detalhe=row.motivo_cancelamento,
        )
    except Exception:
        current_app.logger.debug("CIF auto entrega: falha ao registrar historico de cancelamento", exc_info=True)

    if commit:
        db.session.commit()
    return True, row, "Solicitacao de entrega cancelada."


# ---------------------------------------------------------------------------
# Regra 1 - Coleta a partir de OC CIF
# ---------------------------------------------------------------------------
def _parceiro_from_oc(oc_json: dict, fornecedor_json: dict) -> dict:
    oc = oc_json or {}
    forn = fornecedor_json or {}
    nome = _first(oc.get("fornecedor"), forn.get("nome"), forn.get("razao_social"))
    return {
        "codigo": _first(oc.get("cod_fornecedor"), forn.get("codigo")),
        "nome": nome,
        "razao_social": _first(forn.get("razao_social"), oc.get("fornecedor"), nome),
        "cnpj_cpf": _first(oc.get("fornecedor_cnpj"), forn.get("cgc")),
        "contato": _first(oc.get("contato_fornecedor"), forn.get("contato"), forn.get("nome_vendedor")),
        "telefone": _first(oc.get("fornecedor_telefone"), forn.get("fone1"), forn.get("fone2")),
        "email": _first(forn.get("e_mail"), oc.get("email")),
        "logradouro": _first(oc.get("fornecedor_endeferco"), forn.get("endereco")),
        "numero": _first(oc.get("fornecedor_numero"), forn.get("numero_imovel")),
        "complemento": _first(oc.get("fornecedor_complento"), forn.get("endereco_complemento")),
        "bairro": _first(oc.get("fornecedor_bairro"), forn.get("bairro")),
        "cidade": _first(oc.get("fornecedor_cidade"), forn.get("cidade")),
        "uf": _first(oc.get("fornecedor_uf"), forn.get("uf")),
        "cep": _first(oc.get("fornecedor_cep"), forn.get("cep")),
        "observacoes": _first(oc.get("prazo_entrega"), oc.get("observacoes")),
    }


def _itens_da_oc(numero_oc: str) -> list[dict]:
    from ..models import ItemNota

    linhas = (
        ItemNota.query
        .filter(ItemNota.pedido_compra == numero_oc)
        .order_by(ItemNota.id.asc())
        .all()
    )
    itens = []
    for item in linhas:
        try:
            qtd = float(getattr(item, "qtd_real", None) or getattr(item, "qtd", None) or 0)
        except (TypeError, ValueError):
            qtd = 0.0
        itens.append(
            {
                "codigo_item": str(getattr(item, "codigo", "") or "").strip(),
                "descricao": str(getattr(item, "descricao", "") or "").strip() or f"Item da OC {numero_oc}",
                "quantidade": qtd or 1,
                "unidade": str(getattr(item, "unidade_comercial", "") or "").strip(),
                "volumes": 0,
            }
        )
    if not itens:
        itens = [{"descricao": f"Material da OC {numero_oc}", "quantidade": 1, "unidade": "UN", "volumes": 0}]
    return itens


def _upsert_coleta_oc(
    numero_oc: str,
    data_coleta: datetime,
    previsao: datetime,
    oc_json: dict,
    fornecedor_json: dict,
    solicitante: str,
    valor_frete: str,
    antecedencia: int,
) -> str:
    existente = (
        AgendamentoSolicitacao.query
        .filter(
            AgendamentoSolicitacao.tipo == "COLETA",
            AgendamentoSolicitacao.origem_documento == ORIGEM_AUTO_CIF,
            AgendamentoSolicitacao.numero_oc == numero_oc,
        )
        .first()
    )
    if existente:
        if str(existente.status or "").strip() in ("Cancelada", "Concluida"):
            return "ignorada"
        if existente.data_desejada != data_coleta:
            from ..routes.agendamento_routes import _registrar_historico

            anterior = existente.data_desejada
            existente.data_desejada = data_coleta
            existente.prazo_limite = data_coleta
            existente.atualizado_em = datetime.now()
            _registrar_historico(
                existente.id,
                evento="RECALCULADA_AUTO_CIF",
                usuario=solicitante,
                status_anterior=existente.status,
                status_novo=existente.status,
                detalhe=(
                    f"Data de coleta recalculada de "
                    f"{anterior:%d/%m/%Y} para {data_coleta:%d/%m/%Y} "
                    f"(previsao de entrega {previsao:%d/%m/%Y}, {antecedencia} dia(s) antes)."
                    if anterior else
                    f"Data de coleta definida para {data_coleta:%d/%m/%Y} "
                    f"(previsao de entrega {previsao:%d/%m/%Y}, {antecedencia} dia(s) antes)."
                ),
            )
            return "atualizada"
        return "ignorada"

    parceiro = _parceiro_from_oc(oc_json, fornecedor_json)
    parceiro, _incompleto = _garantir_parceiro_minimo(
        parceiro, parceiro.get("nome"), "Endereco de coleta a confirmar"
    )
    itens = _itens_da_oc(numero_oc)
    payload = {
        "origem": "OC_CIF",
        "numero_oc": numero_oc,
        "frete_valor": valor_frete,
        "previsao_entrega": previsao.isoformat(),
        "data_coleta": data_coleta.isoformat(),
    }
    row = _criar_solicitacao(
        tipo="COLETA",
        documento_tipo="OC",
        documento_numero=numero_oc,
        numero_oc=numero_oc,
        parceiro=parceiro,
        itens=itens,
        prazo_limite=data_coleta,
        data_desejada=data_coleta,
        observacoes_solicitante=_first(oc_json.get("prazo_entrega"), oc_json.get("observacoes")) or None,
        observacoes_logistica=(
            f"Coleta gerada automaticamente (frete CIF). Previsao de entrega "
            f"{previsao:%d/%m/%Y}; coletar ate {data_coleta:%d/%m/%Y}."
        ),
        payload_origem=payload,
        solicitante=solicitante,
        evento_detalhe=(
            f"Coleta gerada automaticamente (frete CIF) da OC {numero_oc}. "
            f"Previsao de entrega {previsao:%d/%m/%Y}; coleta programada para {data_coleta:%d/%m/%Y}."
        ),
    )
    return "criada" if row else "ignorada"


def gerar_solicitacoes_coleta_cif(app=None) -> dict:
    """Varre as OCs em aberto no ERP e gera coletas para as de frete CIF."""

    def _run() -> dict:
        from ..compras.services import compras_service

        janela = int(current_app.config.get("SOLICITACAO_CIF_OC_JANELA_DIAS", 60))
        antecedencia = int(current_app.config.get("SOLICITACAO_CIF_COLETA_ANTECEDENCIA_DIAS", 2))
        solicitante = str(current_app.config.get("SOLICITACAO_CIF_SOLICITANTE", "sistema"))
        try:
            ocs = compras_service.ordens_compra_cif_recentes(janela_dias=janela)
        except Exception:
            current_app.logger.exception("CIF auto coleta: falha ao consultar OCs no ERP.")
            return {"criadas": 0, "atualizadas": 0, "ignoradas": 0, "erros": 1, "total": 0, "cif": 0}

        criadas = atualizadas = ignoradas = erros = cif = 0
        for oc in ocs:
            try:
                oc_json = _garantir_dict(oc.get("oc_json"))
                fornecedor_json = _garantir_dict(oc.get("fornecedor_json"))
                _chave, valor_frete = _valor_frete_da_oc(oc_json)
                if not _eh_cif(valor_frete):
                    ignoradas += 1
                    continue
                cif += 1
                numero_oc = str(oc.get("cod_ordem_compra") or "").strip()
                previsao = _parse_data(oc.get("previsao_entrega"))
                if not numero_oc or not previsao:
                    ignoradas += 1
                    continue
                data_coleta = previsao - timedelta(days=antecedencia)
                resultado = _upsert_coleta_oc(
                    numero_oc, data_coleta, previsao, oc_json, fornecedor_json,
                    solicitante, valor_frete, antecedencia,
                )
                db.session.commit()
                if resultado == "criada":
                    criadas += 1
                elif resultado == "atualizada":
                    atualizadas += 1
                else:
                    ignoradas += 1
            except Exception:
                db.session.rollback()
                erros += 1
                current_app.logger.exception(
                    "CIF auto coleta: falha na OC %s.", oc.get("cod_ordem_compra")
                )
        current_app.logger.info(
            "CIF auto coleta: %s OC(s) lidas, %s CIF, %s criada(s), %s atualizada(s), %s ignorada(s), %s erro(s).",
            len(ocs), cif, criadas, atualizadas, ignoradas, erros,
        )
        return {
            "criadas": criadas,
            "atualizadas": atualizadas,
            "ignoradas": ignoradas,
            "erros": erros,
            "total": len(ocs),
            "cif": cif,
        }

    if app is not None:
        with app.app_context():
            return _run()
    return _run()


def _garantir_dict(valor) -> dict:
    if isinstance(valor, dict):
        return valor
    if isinstance(valor, str) and valor.strip():
        try:
            import json

            parsed = json.loads(valor)
            return parsed if isinstance(parsed, dict) else {}
        except (ValueError, TypeError):
            return {}
    return {}


# ---------------------------------------------------------------------------
# Orquestracao (scheduler / reprocessamento manual)
# ---------------------------------------------------------------------------
def executar_ciclo(app=None) -> dict:
    """Executa as duas regras conforme as flags de configuracao."""

    def _run() -> dict:
        resultado: dict[str, Any] = {}
        if current_app.config.get("SOLICITACAO_CIF_ENTREGA_ENABLED", True):
            resultado["entrega"] = gerar_solicitacoes_entrega_cif()
        if current_app.config.get("SOLICITACAO_CIF_COLETA_ENABLED", True):
            resultado["coleta"] = gerar_solicitacoes_coleta_cif()
        return resultado

    if app is not None:
        with app.app_context():
            return _run()
    return _run()
