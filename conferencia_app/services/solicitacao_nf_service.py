"""Solicitacao de NF (garantia/bonificacao/teste/atendimento tecnico).

Fluxo: formulario publico (sem login) cria a solicitacao -> logistica/fiscal/
admin separam os materiais -> fiscal fatura. Reaproveita a mesma conexao
Postgres do ERP (CPS) ja usada por erp_lancamento_service/grv_contas_receber
para buscar clientes (tcliente) e materiais (tproduto); funcionarios vem de
FacilitiesGRVService (ja existente).

Apos o faturamento, o status final depende do tipo_operacao: Garantia e
Bonificacao terminam ali; Teste e Atendimento tecnico "emprestam" o material
e precisam de controle de retorno (aba "Faturamento avulso" dentro da
Conferencia de Expedicao) ate ele voltar para o estoque.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import requests
from flask import current_app

from ..extensions import db
from ..models import SolicitacaoNF, SolicitacaoNFItem, SolicitacaoNFLog
from .erp_lancamento_service import _conectar, _resolver_config
from .facilities_grv_service import FacilitiesGRVService, normalizar_nome
from . import teams_service

TIPOS_OPERACAO = ("Garantia", "Bonificação", "Teste", "Materiais para atendimento técnico no cliente")
TIPOS_SEM_RETORNO = ("Garantia", "Bonificação")

STATUS_SOLICITADO = "Solicitado"
STATUS_EXPEDIDO_SEM_NF = "Expedido sem nota fiscal"
STATUS_NF_EMITIDA = "Notas fiscais emitidas"
STATUS_ESTOQUE_TERCEIROS = "Estoque em poder de terceiros"
STATUS_ESTOQUE_ASSISTENCIA = "Estoque em poder da Assistência técnica"
STATUS_ESTOQUE_RETORNADO = "Estoque retornado"

STATUS_PENDENTES_RETORNO = (STATUS_ESTOQUE_TERCEIROS, STATUS_ESTOQUE_ASSISTENCIA)

STATUS_SLUGS = {
    STATUS_SOLICITADO: "em_separacao",
    STATUS_EXPEDIDO_SEM_NF: "expedido_sem_nf",
    STATUS_NF_EMITIDA: "nf_emitida",
    STATUS_ESTOQUE_TERCEIROS: "estoque_terceiros",
    STATUS_ESTOQUE_ASSISTENCIA: "estoque_assistencia",
    STATUS_ESTOQUE_RETORNADO: "estoque_retornado",
}

STATUS_BADGE = {
    STATUS_SOLICITADO: "eui-badge--warning",
    STATUS_EXPEDIDO_SEM_NF: "eui-badge--info",
    STATUS_NF_EMITIDA: "eui-badge--violet",
    STATUS_ESTOQUE_TERCEIROS: "eui-badge--primary",
    STATUS_ESTOQUE_ASSISTENCIA: "eui-badge--danger",
    STATUS_ESTOQUE_RETORNADO: "eui-badge--success",
}


class SolicitacaoNFError(ValueError):
    """Erro de validacao do payload da solicitacao (mensagem amigavel ao usuario)."""


def _empresa() -> int:
    try:
        return int(current_app.config.get("ERP_ESTOQUE_PG_COMPANY", 1) or 1)
    except (TypeError, ValueError):
        return 1


def _executar(sql: str, params: dict) -> list[dict[str, Any]]:
    """Executa uma das queries SQL_* deste modulo.

    Producao (PythonAnywhere) nao alcanca o Postgres do ERP diretamente —
    quando ha uma bridge configurada (ERP_LANCAMENTO_API_URL), a query e
    enviada por HTTP para /api/erp/solicitacao-nf/query (rodando na VM com
    acesso ao Postgres). Sem bridge configurada, cai para conexao direta
    (uso local/rede da empresa). Mesmo padrao ja usado por
    FacilitiesGRVService/compras/db.py.
    """
    cfg = _resolver_config()
    if cfg.get("api_url"):
        try:
            return _executar_bridge(cfg, sql, params)
        except Exception:
            current_app.logger.warning(
                "solicitacao_nf: bridge indisponivel, tentando Postgres direto", exc_info=True
            )
    if not cfg["host"] or not cfg["database"] or not cfg["user"]:
        return []
    conn = _conectar(cfg)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            cols = [desc[0] for desc in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        conn.close()


def _executar_bridge(cfg: dict[str, Any], sql: str, params: dict) -> list[dict[str, Any]]:
    query_name = _QUERY_NAMES.get(sql)
    if not query_name:
        raise ValueError("Query da Solicitacao de NF nao registrada para uso via bridge")
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "ngrok-skip-browser-warning": "true",
        "User-Agent": "ColumbiaSync/SolicitacaoNF",
    }
    if cfg.get("api_token"):
        headers["Authorization"] = f"Bearer {cfg['api_token']}"
    response = requests.post(
        f"{cfg['api_url']}/api/erp/solicitacao-nf/query",
        headers=headers,
        json={"query": query_name, "params": params},
        timeout=cfg.get("api_timeout") or 30,
    )
    response.raise_for_status()
    payload = response.json()
    if not payload.get("sucesso"):
        raise RuntimeError(str(payload.get("erro") or "Falha na bridge da Solicitacao de NF"))
    return payload.get("rows") or []


_CLIENTE_SELECT = """
    select
        coalesce(to_jsonb(c)->>'codigo', '') as codigo,
        coalesce(
            nullif(to_jsonb(c)->>'razao_social', ''),
            nullif(to_jsonb(c)->>'nome', ''),
            nullif(to_jsonb(c)->>'fantasia', '')
        ) as nome,
        regexp_replace(
            coalesce(
                to_jsonb(c)->>'rg_cgc',
                to_jsonb(c)->>'cgc',
                to_jsonb(c)->>'cnpj_cpf',
                ''
            ),
            '\\D', '', 'g'
        ) as documento
    from public.tcliente c
"""

# Queries nomeadas (prefixo SQL_) — o nome da constante e enviado para a
# bridge no lugar do texto SQL (allowlist), que a resolve de volta via
# _QUERY_NAMES. Nao renomeie sem atualizar a bridge (scripts/erp_lancamento_api_bridge.py).
SQL_CLIENTE_BUSCAR = (
    _CLIENTE_SELECT
    + """
    where to_jsonb(c)->>'razao_social' ilike %(termo)s
       or to_jsonb(c)->>'nome' ilike %(termo)s
       or to_jsonb(c)->>'fantasia' ilike %(termo)s
    order by nome
    limit %(limit)s
"""
)

SQL_CLIENTE_POR_CODIGO = _CLIENTE_SELECT + " where to_jsonb(c)->>'codigo' = %(codigo)s limit 1"

SQL_MATERIAL_BUSCAR = """
    select codigo_interno, nome, estoque_disponivel_uso
    from public.tproduto
    where cod_empresa = %(empresa)s
      and (codigo_interno ilike %(termo)s or nome ilike %(termo)s)
    order by nome
    limit %(limit)s
"""

SQL_MATERIAL_POR_CODIGO = """
    select codigo_interno, nome, estoque_disponivel_uso
    from public.tproduto
    where cod_empresa = %(empresa)s
      and lower(trim(codigo_interno)) = lower(trim(%(codigo)s))
    limit 1
"""

_QUERY_NAMES = {
    value: name
    for name, value in list(globals().items())
    if name.startswith("SQL_") and isinstance(value, str)
}


def buscar_clientes(termo: str, limit: int = 15) -> list[dict[str, Any]]:
    termo = (termo or "").strip()
    if len(termo) < 2:
        return []
    rows = _executar(SQL_CLIENTE_BUSCAR, {"termo": f"%{termo}%", "limit": limit})
    return [row for row in rows if row.get("codigo") and row.get("nome")]


def _buscar_cliente_por_codigo(codigo: str) -> dict[str, Any] | None:
    codigo = str(codigo or "").strip()
    if not codigo:
        return None
    rows = _executar(SQL_CLIENTE_POR_CODIGO, {"codigo": codigo})
    return rows[0] if rows else None


def buscar_materiais(termo: str, limit: int = 15) -> list[dict[str, Any]]:
    termo = (termo or "").strip()
    if len(termo) < 2:
        return []
    rows = _executar(SQL_MATERIAL_BUSCAR, {"empresa": _empresa(), "termo": f"%{termo}%", "limit": limit})
    return [row for row in rows if row.get("codigo_interno") and row.get("nome")]


def _buscar_material_por_codigo(codigo: str) -> dict[str, Any] | None:
    codigo = str(codigo or "").strip()
    if not codigo:
        return None
    rows = _executar(SQL_MATERIAL_POR_CODIGO, {"empresa": _empresa(), "codigo": codigo})
    return rows[0] if rows else None


def listar_funcionarios_para_solicitacao() -> list[dict[str, Any]]:
    funcionarios = FacilitiesGRVService.listar_funcionarios(ativos=True)
    return [
        {"codigo": f.get("codigo"), "nome": f.get("nome"), "setor": f.get("setor")}
        for f in funcionarios
        if f.get("nome")
    ]


def _validar_solicitante(nome: str) -> dict[str, Any]:
    nome = (nome or "").strip()
    if not nome:
        raise SolicitacaoNFError("Informe o nome do solicitante.")
    alvo = normalizar_nome(nome)
    for func in FacilitiesGRVService.listar_funcionarios(ativos=True):
        if normalizar_nome(func.get("nome") or "") == alvo:
            return func
    raise SolicitacaoNFError("Solicitante não encontrado na lista de funcionários ativos.")


def _validar_cliente(codigo: str, nome: str) -> dict[str, Any]:
    cliente = _buscar_cliente_por_codigo(codigo)
    if not cliente:
        raise SolicitacaoNFError("Cliente não encontrado. Selecione um cliente da lista.")
    return cliente


def _validar_itens(itens_payload: list) -> list[dict[str, Any]]:
    if not itens_payload:
        raise SolicitacaoNFError("Informe ao menos um material.")
    itens = []
    for bruto in itens_payload:
        codigo = str((bruto or {}).get("material_codigo") or "").strip()
        try:
            quantidade = float((bruto or {}).get("quantidade") or 0)
        except (TypeError, ValueError):
            quantidade = 0
        if not codigo or quantidade <= 0:
            raise SolicitacaoNFError("Cada item precisa de material e quantidade maior que zero.")
        material = _buscar_material_por_codigo(codigo)
        if not material:
            raise SolicitacaoNFError(f"Material '{codigo}' não encontrado.")
        itens.append({
            "material_codigo": material["codigo_interno"],
            "material_nome": material["nome"],
            "quantidade": quantidade,
        })
    return itens


def criar_solicitacao(payload: dict, ip: str | None = None) -> SolicitacaoNF:
    tipo_operacao = str((payload or {}).get("tipo_operacao") or "").strip()
    if tipo_operacao not in TIPOS_OPERACAO:
        raise SolicitacaoNFError("Tipo de operação inválido.")

    funcionario = _validar_solicitante((payload or {}).get("solicitante_nome"))
    cliente = _validar_cliente(
        (payload or {}).get("cliente_codigo"),
        (payload or {}).get("cliente_nome"),
    )
    itens = _validar_itens((payload or {}).get("itens") or [])
    venda_posterior = bool((payload or {}).get("venda_posterior"))

    solicitacao = SolicitacaoNF(
        solicitante_codigo=str(funcionario.get("codigo") or ""),
        solicitante_nome=funcionario.get("nome") or "",
        solicitante_setor=funcionario.get("setor") or "",
        tipo_operacao=tipo_operacao,
        venda_posterior=venda_posterior,
        cliente_codigo=str(cliente.get("codigo") or ""),
        cliente_nome=cliente.get("nome") or "",
        cliente_documento=cliente.get("documento") or "",
        status=STATUS_SOLICITADO,
        ip_solicitante=(ip or "")[:64],
    )
    for i, item in enumerate(itens):
        solicitacao.itens.append(SolicitacaoNFItem(linha=i, **item))

    db.session.add(solicitacao)
    db.session.flush()  # garante solicitacao.id para o protocolo
    solicitacao.protocolo = f"SNF-{solicitacao.id:06d}"

    db.session.add(SolicitacaoNFLog(
        solicitacao_id=solicitacao.id,
        acao="criada",
        usuario=solicitacao.solicitante_nome,
        status_anterior=None,
        status_novo=STATUS_SOLICITADO,
        detalhes=json.dumps({"itens": itens}, ensure_ascii=False),
    ))
    db.session.commit()

    teams_service.notificar_solicitacao_nf(
        "criada",
        solicitacao.protocolo,
        solicitacao.solicitante_nome,
        solicitacao.cliente_nome,
        solicitacao.tipo_operacao,
    )
    return solicitacao


def marcar_separada(solicitacao_id: int, usuario: str, itens_separados: list, observacao: str | None) -> SolicitacaoNF:
    solicitacao = SolicitacaoNF.query.get(solicitacao_id)
    if not solicitacao:
        raise SolicitacaoNFError("Solicitação não encontrada.")
    if solicitacao.status != STATUS_SOLICITADO:
        raise SolicitacaoNFError("Solicitação já foi separada ou não está mais pendente.")

    ids_separados = {int(i) for i in (itens_separados or [])}
    for item in solicitacao.itens:
        item.separado = item.id in ids_separados

    status_anterior = solicitacao.status
    solicitacao.status = STATUS_EXPEDIDO_SEM_NF
    solicitacao.separado_por = usuario
    solicitacao.separado_at = datetime.now()
    solicitacao.observacoes_separacao = (observacao or "")[:500]
    solicitacao.updated_at = datetime.now()

    db.session.add(SolicitacaoNFLog(
        solicitacao_id=solicitacao.id,
        acao="separada",
        usuario=usuario,
        status_anterior=status_anterior,
        status_novo=solicitacao.status,
        detalhes=json.dumps({"observacao": observacao}, ensure_ascii=False),
    ))
    db.session.commit()

    teams_service.notificar_solicitacao_nf(
        "separada", solicitacao.protocolo, solicitacao.solicitante_nome,
        solicitacao.cliente_nome, solicitacao.tipo_operacao,
        subinfo=f"Separado por {usuario}",
    )
    return solicitacao


def marcar_faturada(solicitacao_id: int, usuario: str, numero_nf: str, observacao: str | None) -> SolicitacaoNF:
    solicitacao = SolicitacaoNF.query.get(solicitacao_id)
    if not solicitacao:
        raise SolicitacaoNFError("Solicitação não encontrada.")
    if solicitacao.status != STATUS_EXPEDIDO_SEM_NF:
        raise SolicitacaoNFError("Solicitação ainda não foi separada.")
    numero_nf = str(numero_nf or "").strip()
    if not numero_nf:
        raise SolicitacaoNFError("Informe o número da NF.")

    if solicitacao.tipo_operacao in TIPOS_SEM_RETORNO:
        novo_status = STATUS_NF_EMITIDA
    elif solicitacao.tipo_operacao == "Teste":
        novo_status = STATUS_ESTOQUE_TERCEIROS
    else:  # Materiais para atendimento técnico no cliente
        novo_status = STATUS_ESTOQUE_ASSISTENCIA

    status_anterior = solicitacao.status
    solicitacao.status = novo_status
    solicitacao.faturado_por = usuario
    solicitacao.faturado_at = datetime.now()
    solicitacao.numero_nf = numero_nf
    solicitacao.observacoes_faturamento = (observacao or "")[:500]
    solicitacao.updated_at = datetime.now()

    db.session.add(SolicitacaoNFLog(
        solicitacao_id=solicitacao.id,
        acao="faturada",
        usuario=usuario,
        status_anterior=status_anterior,
        status_novo=solicitacao.status,
        detalhes=json.dumps({"numero_nf": numero_nf, "observacao": observacao}, ensure_ascii=False),
    ))
    db.session.commit()

    teams_service.notificar_solicitacao_nf(
        "faturada", solicitacao.protocolo, solicitacao.solicitante_nome,
        solicitacao.cliente_nome, solicitacao.tipo_operacao,
        subinfo=f"NF {numero_nf} · Faturado por {usuario}",
    )
    return solicitacao


def registrar_retorno(solicitacao_id: int, usuario: str, numero_nf_retorno: str, observacao: str | None) -> SolicitacaoNF:
    solicitacao = SolicitacaoNF.query.get(solicitacao_id)
    if not solicitacao:
        raise SolicitacaoNFError("Solicitação não encontrada.")
    if solicitacao.status not in STATUS_PENDENTES_RETORNO:
        raise SolicitacaoNFError("Esta solicitação não está aguardando retorno de material.")
    numero_nf_retorno = str(numero_nf_retorno or "").strip()
    if not numero_nf_retorno:
        raise SolicitacaoNFError("Informe o número da NF de retorno.")

    status_anterior = solicitacao.status
    solicitacao.status = STATUS_ESTOQUE_RETORNADO
    solicitacao.numero_nf_retorno = numero_nf_retorno
    solicitacao.retorno_por = usuario
    solicitacao.retorno_at = datetime.now()
    solicitacao.observacoes_retorno = (observacao or "")[:500]
    solicitacao.updated_at = datetime.now()

    db.session.add(SolicitacaoNFLog(
        solicitacao_id=solicitacao.id,
        acao="retorno",
        usuario=usuario,
        status_anterior=status_anterior,
        status_novo=solicitacao.status,
        detalhes=json.dumps({"numero_nf_retorno": numero_nf_retorno, "observacao": observacao}, ensure_ascii=False),
    ))
    db.session.commit()

    teams_service.notificar_solicitacao_nf(
        "retorno", solicitacao.protocolo, solicitacao.solicitante_nome,
        solicitacao.cliente_nome, solicitacao.tipo_operacao,
        subinfo=f"NF retorno {numero_nf_retorno} · Registrado por {usuario}",
    )
    return solicitacao


def estornar_solicitacao(solicitacao_id: int, usuario: str, motivo: str | None) -> SolicitacaoNF:
    """Admin only: volta a solicitacao para a etapa anterior, limpando os
    dados registrados na etapa que esta sendo desfeita."""
    solicitacao = SolicitacaoNF.query.get(solicitacao_id)
    if not solicitacao:
        raise SolicitacaoNFError("Solicitação não encontrada.")

    status_atual = solicitacao.status
    if status_atual == STATUS_SOLICITADO:
        raise SolicitacaoNFError("Esta solicitação já está na primeira etapa.")

    if status_atual == STATUS_EXPEDIDO_SEM_NF:
        novo_status = STATUS_SOLICITADO
        solicitacao.separado_por = None
        solicitacao.separado_at = None
        solicitacao.observacoes_separacao = None
        for item in solicitacao.itens:
            item.separado = False
    elif status_atual in (STATUS_NF_EMITIDA, STATUS_ESTOQUE_TERCEIROS, STATUS_ESTOQUE_ASSISTENCIA):
        novo_status = STATUS_EXPEDIDO_SEM_NF
        solicitacao.faturado_por = None
        solicitacao.faturado_at = None
        solicitacao.numero_nf = None
        solicitacao.observacoes_faturamento = None
    elif status_atual == STATUS_ESTOQUE_RETORNADO:
        novo_status = STATUS_ESTOQUE_TERCEIROS if solicitacao.tipo_operacao == "Teste" else STATUS_ESTOQUE_ASSISTENCIA
        solicitacao.numero_nf_retorno = None
        solicitacao.retorno_por = None
        solicitacao.retorno_at = None
        solicitacao.observacoes_retorno = None
    else:
        raise SolicitacaoNFError("Não é possível estornar esta solicitação.")

    solicitacao.status = novo_status
    solicitacao.updated_at = datetime.now()

    db.session.add(SolicitacaoNFLog(
        solicitacao_id=solicitacao.id,
        acao="estorno",
        usuario=usuario,
        status_anterior=status_atual,
        status_novo=novo_status,
        detalhes=json.dumps({"motivo": motivo}, ensure_ascii=False),
    ))
    db.session.commit()

    teams_service.notificar_solicitacao_nf(
        "estorno", solicitacao.protocolo, solicitacao.solicitante_nome,
        solicitacao.cliente_nome, solicitacao.tipo_operacao,
        subinfo=f"Estornado por {usuario}" + (f": {motivo}" if motivo else ""),
    )
    return solicitacao


def excluir_solicitacao(solicitacao_id: int, usuario: str, motivo: str | None) -> None:
    """Admin only: remove a solicitacao permanentemente. Mantem um registro
    final em SolicitacaoNFLog (sem FK) para trilha de auditoria."""
    solicitacao = SolicitacaoNF.query.get(solicitacao_id)
    if not solicitacao:
        raise SolicitacaoNFError("Solicitação não encontrada.")

    protocolo = solicitacao.protocolo
    solicitante_nome = solicitacao.solicitante_nome
    cliente_nome = solicitacao.cliente_nome
    tipo_operacao = solicitacao.tipo_operacao

    db.session.add(SolicitacaoNFLog(
        solicitacao_id=solicitacao.id,
        acao="excluida",
        usuario=usuario,
        status_anterior=solicitacao.status,
        status_novo=None,
        detalhes=json.dumps({"motivo": motivo}, ensure_ascii=False),
    ))
    db.session.delete(solicitacao)
    db.session.commit()

    teams_service.notificar_solicitacao_nf(
        "excluida", protocolo, solicitante_nome, cliente_nome, tipo_operacao,
        subinfo=f"Excluído por {usuario}" + (f": {motivo}" if motivo else ""),
    )


def listar_minhas_solicitacoes(solicitante_codigo: str) -> list[dict[str, Any]]:
    solicitante_codigo = str(solicitante_codigo or "").strip()
    if not solicitante_codigo:
        return []
    solicitacoes = (
        SolicitacaoNF.query
        .filter_by(solicitante_codigo=solicitante_codigo)
        .order_by(SolicitacaoNF.created_at.desc())
        .all()
    )
    return [_serializar(s) for s in solicitacoes]


def _iso(value):
    return value.isoformat() if value else None


def _serializar_item(item: SolicitacaoNFItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "material_codigo": item.material_codigo,
        "material_nome": item.material_nome,
        "quantidade": item.quantidade,
        "separado": item.separado,
    }


def _serializar(s: SolicitacaoNF) -> dict[str, Any]:
    return {
        "id": s.id,
        "protocolo": s.protocolo,
        "solicitante_nome": s.solicitante_nome,
        "solicitante_setor": s.solicitante_setor,
        "tipo_operacao": s.tipo_operacao,
        "venda_posterior": s.venda_posterior,
        "cliente_nome": s.cliente_nome,
        "cliente_documento": s.cliente_documento,
        "status": s.status,
        "status_slug": STATUS_SLUGS.get(s.status, ""),
        "status_badge": STATUS_BADGE.get(s.status, "eui-badge--neutral"),
        "separado_por": s.separado_por,
        "separado_at": _iso(s.separado_at),
        "observacoes_separacao": s.observacoes_separacao,
        "faturado_por": s.faturado_por,
        "faturado_at": _iso(s.faturado_at),
        "numero_nf": s.numero_nf,
        "observacoes_faturamento": s.observacoes_faturamento,
        "numero_nf_retorno": s.numero_nf_retorno,
        "retorno_por": s.retorno_por,
        "retorno_at": _iso(s.retorno_at),
        "observacoes_retorno": s.observacoes_retorno,
        "created_at": _iso(s.created_at),
        "itens": [_serializar_item(i) for i in s.itens],
    }


def listar_ordens_avulso() -> dict[str, Any]:
    """Lista + KPIs da aba "Faturamento avulso" (Conferencia de Expedicao)."""
    solicitacoes = SolicitacaoNF.query.order_by(SolicitacaoNF.created_at.desc()).all()
    resumo = {
        "em_separacao": 0,
        "expedido_sem_nf": 0,
        "nf_emitida": 0,
        "estoque_terceiros": 0,
        "estoque_assistencia": 0,
        "estoque_retornado": 0,
    }
    for s in solicitacoes:
        slug = STATUS_SLUGS.get(s.status)
        if slug in resumo:
            resumo[slug] += 1
    resumo["pendencia_estoque"] = resumo["estoque_terceiros"] + resumo["estoque_assistencia"]
    return {"resumo": resumo, "ordens": [_serializar(s) for s in solicitacoes]}
