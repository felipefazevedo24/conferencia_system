import json
import re
from datetime import datetime, timedelta
from difflib import SequenceMatcher

from ..extensions import db
from ..models import (
    CadastroWorkflowChecklist,
    CadastroWorkflowHistorico,
    CadastroWorkflowNotificacao,
    CadastroWorkflowSLAConfig,
    CadastroWorkflowSolicitacao,
)


TIPOS_CADASTRO = {
    "material": {
        "label": "Cadastro de Material",
        "icon": "fa-boxes-stacked",
        "fields": [
            ("codigo", "Codigo", False),
            ("descricao", "Descricao", True),
            ("descricao_complementar", "Descricao complementar", False),
            ("unidade_medida", "Unidade de medida", True),
            ("grupo_produto", "Grupo de produto", True),
            ("ncm", "NCM", False),
            ("aplicacao", "Aplicacao/Finalidade", False),
            ("fabricante", "Fabricante", False),
            ("fornecedor_sugerido", "Fornecedor sugerido", False),
        ],
    },
    "cliente": {
        "label": "Cadastro de Cliente",
        "icon": "fa-user-tie",
        "fields": [
            ("razao_social", "Razao Social", True),
            ("nome_fantasia", "Nome Fantasia", False),
            ("documento", "CNPJ/CPF", True),
            ("inscricao_estadual", "Inscricao Estadual", False),
            ("inscricao_municipal", "Inscricao Municipal", False),
            ("endereco", "Endereco completo", True),
            ("email", "E-mail", False),
            ("telefone", "Telefone", False),
            ("contato", "Contato", False),
        ],
    },
    "fornecedor": {
        "label": "Cadastro de Fornecedor",
        "icon": "fa-industry",
        "fields": [
            ("razao_social", "Razao Social", True),
            ("nome_fantasia", "Nome Fantasia", False),
            ("documento", "CNPJ", True),
            ("inscricao_estadual", "Inscricao Estadual", False),
            ("endereco", "Endereco completo", True),
            ("dados_bancarios", "Dados bancarios", False),
            ("email", "E-mail", False),
            ("telefone", "Telefone", False),
            ("tipo_fornecimento", "Tipo de fornecimento", False),
        ],
    },
    "transportadora": {
        "label": "Cadastro de Transportadora",
        "icon": "fa-truck-fast",
        "fields": [
            ("razao_social", "Razao Social", True),
            ("nome_fantasia", "Nome Fantasia", False),
            ("documento", "CNPJ", True),
            ("inscricao_estadual", "Inscricao Estadual", False),
            ("antt", "ANTT", False),
            ("endereco", "Endereco completo", True),
            ("email", "E-mail", False),
            ("telefone", "Telefone", False),
            ("regiao_atendimento", "Regiao de atendimento", False),
            ("modal_transporte", "Modal de transporte", False),
        ],
    },
}

STATUS = [
    "Rascunho",
    "Enviado",
    "Em Validacao Compras",
    "Pendente de Correcao",
    "Aprovado por Compras",
    "Em Validacao Fiscal",
    "Cadastrado",
    "Reprovado",
    "Cancelado",
]

CHECKLIST_COMPRAS = [
    "Necessidade do cadastro",
    "Existencia de cadastro semelhante",
    "Dados comerciais minimos",
    "Documentacao obrigatoria",
    "Fornecedor/cliente/material ja existente",
]

CHECKLIST_FISCAL = [
    "CNPJ valido",
    "Inscricao Estadual",
    "Inscricao Municipal",
    "Regime tributario",
    "CNAE",
    "NCM quando aplicavel",
    "Tributacao padrao",
    "Retencoes aplicaveis",
    "Dados fiscais obrigatorios",
]


def normalizar_documento(value: str | None) -> str:
    return re.sub(r"\D+", "", value or "")


def get_dados(solicitacao: CadastroWorkflowSolicitacao) -> dict:
    try:
        return json.loads(solicitacao.dados_json or "{}")
    except json.JSONDecodeError:
        return {}


def set_dados(solicitacao: CadastroWorkflowSolicitacao, dados: dict) -> None:
    solicitacao.dados_json = json.dumps(dados, ensure_ascii=False, sort_keys=True)


def get_sla_horas(departamento: str) -> int:
    cfg = CadastroWorkflowSLAConfig.query.filter_by(departamento=departamento).first()
    return int(cfg.horas if cfg else 48)


def prazo_restante(solicitacao: CadastroWorkflowSolicitacao) -> dict:
    if solicitacao.departamento_atual not in {"Compras", "Fiscal"}:
        return {"horas": None, "vencida": False, "prazo": None}
    prazo = solicitacao.etapa_iniciada_em + timedelta(hours=get_sla_horas(solicitacao.departamento_atual))
    restante = prazo - datetime.now()
    return {"horas": round(restante.total_seconds() / 3600, 1), "vencida": restante.total_seconds() < 0, "prazo": prazo}


def tempo_na_etapa(solicitacao: CadastroWorkflowSolicitacao) -> str:
    delta = datetime.now() - solicitacao.etapa_iniciada_em
    dias = delta.days
    horas = delta.seconds // 3600
    if dias:
        return f"{dias} dia(s) e {horas}h"
    return f"{horas}h"


def proximo_numero() -> str:
    ultimo = CadastroWorkflowSolicitacao.query.order_by(CadastroWorkflowSolicitacao.id.desc()).first()
    base = int(ultimo.numero or "0") if ultimo and str(ultimo.numero).isdigit() else 0
    return f"{base + 1:06d}"


def registrar_evento(solicitacao, usuario, departamento, acao, comentario="", notificar=True):
    agora = datetime.now()
    solicitacao.data_ultima_movimentacao = agora
    db.session.add(
        CadastroWorkflowHistorico(
            solicitacao=solicitacao,
            usuario=usuario,
            departamento=departamento,
            acao=acao,
            comentario=comentario or "",
        )
    )
    if notificar:
        destinatarios = {solicitacao.solicitante}
        if solicitacao.responsavel_atual:
            destinatarios.add(solicitacao.responsavel_atual)
        for destinatario in destinatarios:
            db.session.add(
                CadastroWorkflowNotificacao(
                    solicitacao=solicitacao,
                    usuario=destinatario,
                    mensagem=f"Solicitacao {solicitacao.numero}: {acao}",
                )
            )


def buscar_duplicidades(tipo: str, dados: dict, ignorar_id: int | None = None) -> list[dict]:
    tipo = (tipo or "").strip()
    documento = normalizar_documento(dados.get("documento"))
    codigo = (dados.get("codigo") or "").strip().lower()
    descricao = (dados.get("descricao") or "").strip().lower()
    query = CadastroWorkflowSolicitacao.query.filter(CadastroWorkflowSolicitacao.tipo == tipo)
    if ignorar_id:
        query = query.filter(CadastroWorkflowSolicitacao.id != ignorar_id)
    encontrados = []
    for atual in query.limit(500).all():
        atual_dados = get_dados(atual)
        motivo = None
        score = 0.0
        if tipo in {"cliente", "fornecedor", "transportadora"}:
            doc_atual = normalizar_documento(atual_dados.get("documento"))
            if documento and doc_atual and documento == doc_atual:
                motivo, score = "Documento ja usado", 1.0
        elif tipo == "material":
            codigo_atual = (atual_dados.get("codigo") or "").strip().lower()
            desc_atual = (atual_dados.get("descricao") or "").strip().lower()
            if codigo and codigo_atual and codigo == codigo_atual:
                motivo, score = "Codigo ja usado", 1.0
            elif descricao and desc_atual:
                score = SequenceMatcher(None, descricao, desc_atual).ratio()
                if score >= 0.72:
                    motivo = "Descricao semelhante"
        if motivo:
            encontrados.append(
                {
                    "numero": atual.numero,
                    "status": atual.status,
                    "tipo": atual.tipo,
                    "motivo": motivo,
                    "score": round(score, 2),
                    "resumo": atual_dados.get("razao_social") or atual_dados.get("descricao") or atual.numero,
                }
            )
    return encontrados


def criar_solicitacao(tipo: str, dados: dict, solicitante: str, anexos: str = "") -> CadastroWorkflowSolicitacao:
    if tipo not in TIPOS_CADASTRO:
        raise ValueError("Tipo de cadastro invalido.")
    for campo, label, obrigatorio in TIPOS_CADASTRO[tipo]["fields"]:
        if obrigatorio and not str(dados.get(campo) or "").strip():
            raise ValueError(f"Campo obrigatorio: {label}.")
    duplicidades = buscar_duplicidades(tipo, dados)
    sol = CadastroWorkflowSolicitacao(
        numero=proximo_numero(),
        tipo=tipo,
        status="Em Validacao Compras",
        etapa_atual="Compras",
        solicitante=solicitante,
        departamento_atual="Compras",
        anexos=anexos,
        alerta_duplicidade=json.dumps(duplicidades, ensure_ascii=False) if duplicidades else None,
    )
    set_dados(sol, dados)
    db.session.add(sol)
    registrar_evento(sol, solicitante, "Solicitante", "Solicitacao criada", "Enviada para validacao de Compras.")
    registrar_evento(sol, solicitante, "Solicitante", "Solicitacao enviada", "Fluxo iniciado em Compras.")
    db.session.flush()
    inicializar_checklists(sol)
    db.session.commit()
    return sol


def inicializar_checklists(solicitacao):
    existentes = {(c.departamento, c.item) for c in solicitacao.checklists}
    for depto, itens in (("Compras", CHECKLIST_COMPRAS), ("Fiscal", CHECKLIST_FISCAL)):
        for item in itens:
            if (depto, item) not in existentes:
                db.session.add(CadastroWorkflowChecklist(solicitacao=solicitacao, departamento=depto, item=item))


def atualizar_checklist(solicitacao, departamento, valores: dict, usuario: str):
    inicializar_checklists(solicitacao)
    agora = datetime.now()
    permitidos = {"Sim", "Nao", "Nao se aplica"}
    for chk in solicitacao.checklists:
        if chk.departamento != departamento:
            continue
        valor = valores.get(f"check_{chk.id}") or valores.get(chk.item)
        if valor in permitidos:
            chk.valor = valor
            chk.atualizado_por = usuario
            chk.atualizado_em = agora


def mover_etapa(solicitacao, status, etapa, departamento, responsavel=None):
    solicitacao.status = status
    solicitacao.etapa_atual = etapa
    solicitacao.departamento_atual = departamento
    solicitacao.responsavel_atual = responsavel
    solicitacao.etapa_iniciada_em = datetime.now()


def executar_acao(solicitacao, acao: str, usuario: str, role: str, comentario: str = "", form=None):
    form = form or {}
    comentario = (comentario or "").strip()
    depto_usuario = "Compras" if role == "Compras" else "Fiscal" if role == "Fiscal" else "Administrador" if role == "Admin" else "Solicitante"
    exige_comentario = {"corrigir", "devolver_solicitante", "devolver_compras", "reprovar"}
    if acao in exige_comentario and not comentario:
        raise ValueError("Comentario obrigatorio para esta acao.")

    if acao == "assumir":
        if role not in {"Compras", "Fiscal", "Admin"}:
            raise ValueError("Usuario sem permissao para assumir atendimento.")
        if solicitacao.departamento_atual not in {"Compras", "Fiscal"}:
            raise ValueError("Esta solicitacao nao esta em fila de atendimento.")
        solicitacao.responsavel_atual = usuario
        registrar_evento(solicitacao, usuario, depto_usuario, "Atendimento assumido", comentario)
    elif acao == "aprovar_compras":
        if role not in {"Compras", "Admin"}:
            raise ValueError("Somente Compras pode encaminhar ao Fiscal.")
        atualizar_checklist(solicitacao, "Compras", form, usuario)
        mover_etapa(solicitacao, "Em Validacao Fiscal", "Fiscal", "Fiscal")
        registrar_evento(solicitacao, usuario, "Compras", "Aprovado e encaminhado ao Fiscal", comentario)
    elif acao == "devolver_solicitante":
        if role not in {"Compras", "Fiscal", "Admin"}:
            raise ValueError("Usuario sem permissao para solicitar correcao.")
        origem = solicitacao.departamento_atual
        mover_etapa(solicitacao, "Pendente de Correcao", "Solicitante", "Solicitante", solicitacao.solicitante)
        registrar_evento(solicitacao, usuario, depto_usuario, f"Devolvido para correcao pelo solicitante ({origem})", comentario)
    elif acao == "devolver_compras":
        if role not in {"Fiscal", "Admin"}:
            raise ValueError("Somente Fiscal pode devolver para Compras.")
        mover_etapa(solicitacao, "Em Validacao Compras", "Compras", "Compras")
        registrar_evento(solicitacao, usuario, "Fiscal", "Devolvido para Compras", comentario)
    elif acao == "responder_correcao":
        if usuario != solicitacao.solicitante and role != "Admin":
            raise ValueError("Somente o solicitante pode responder a correcao.")
        dados = get_dados(solicitacao)
        for key, value in form.items():
            if key.startswith("campo_"):
                dados[key.replace("campo_", "", 1)] = value
        set_dados(solicitacao, dados)
        destino = "Compras"
        mover_etapa(solicitacao, "Em Validacao Compras", "Compras", "Compras")
        registrar_evento(solicitacao, usuario, "Solicitante", f"Correcao respondida para {destino}", comentario or "Dados corrigidos.")
    elif acao == "finalizar":
        if role not in {"Fiscal", "Admin"}:
            raise ValueError("Somente Fiscal pode finalizar cadastro.")
        atualizar_checklist(solicitacao, "Fiscal", form, usuario)
        mover_etapa(solicitacao, "Cadastrado", "Cadastro Concluido", "Concluido", usuario)
        solicitacao.concluido_em = datetime.now()
        registrar_evento(solicitacao, usuario, "Fiscal", "Cadastro concluido", comentario)
    elif acao == "reprovar":
        if role not in {"Compras", "Fiscal", "Admin"}:
            raise ValueError("Usuario sem permissao para reprovar.")
        mover_etapa(solicitacao, "Reprovado", "Encerrado", "Encerrado", usuario)
        registrar_evento(solicitacao, usuario, depto_usuario, "Solicitacao reprovada", comentario)
    elif acao == "cancelar":
        if usuario != solicitacao.solicitante and role != "Admin":
            raise ValueError("Somente o solicitante pode cancelar.")
        if solicitacao.status == "Cadastrado":
            raise ValueError("Solicitacao concluida nao pode ser cancelada.")
        mover_etapa(solicitacao, "Cancelado", "Encerrado", "Encerrado", usuario)
        solicitacao.cancelado_em = datetime.now()
        registrar_evento(solicitacao, usuario, "Solicitante", "Solicitacao cancelada", comentario or "Cancelada pelo solicitante.")
    else:
        raise ValueError("Acao invalida.")
    db.session.commit()


def relatorio_indicadores():
    rows = CadastroWorkflowSolicitacao.query.all()
    por_status = {}
    por_tipo = {}
    por_departamento = {}
    vencidas = 0
    for row in rows:
        por_status[row.status] = por_status.get(row.status, 0) + 1
        por_tipo[row.tipo] = por_tipo.get(row.tipo, 0) + 1
        por_departamento[row.departamento_atual] = por_departamento.get(row.departamento_atual, 0) + 1
        if prazo_restante(row)["vencida"]:
            vencidas += 1
    return {
        "total": len(rows),
        "por_status": por_status,
        "por_tipo": por_tipo,
        "por_departamento": por_departamento,
        "vencidas": vencidas,
    }
