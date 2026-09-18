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
from ..models import ExpedicaoOrdemFat, SolicitacaoNF, SolicitacaoNFItem, SolicitacaoNFLog
from .erp_lancamento_service import _conectar, _resolver_config
from .facilities_grv_service import FacilitiesGRVService, normalizar_nome
from . import teams_service

TIPOS_OPERACAO = (
    "Garantia",
    "Bonificação",
    "Remessa para Teste",
    "Materiais para atendimento técnico no cliente",
    "Remessa para Conserto",
    "Remessa de retorno de demonstração",
)
# Faturar => Notas fiscais emitidas (sem controle de retorno)
TIPOS_SEM_RETORNO = ("Garantia", "Bonificação", "Remessa de retorno de demonstração")
# Apos a separacao vao para "Aguardando faturamento" (a NF sai antes do material,
# ao contrario dos demais tipos que sao expedidos sem NF).
TIPOS_AGUARDA_FATURAMENTO = ("Remessa para Conserto", "Remessa de retorno de demonstração")
# Ao faturar, puxa os dados do destinatario da NF na bridge do ERP.
TIPOS_PUXA_NF_BRIDGE = ("Remessa para Conserto",)

STATUS_SOLICITADO = "Solicitado"
STATUS_EXPEDIDO_SEM_NF = "Expedido sem nota fiscal"
STATUS_AGUARDANDO_FAT = "Aguardando faturamento"
STATUS_NF_EMITIDA = "Notas fiscais emitidas"
STATUS_ESTOQUE_TERCEIROS = "Estoque em poder de terceiros"
STATUS_ESTOQUE_ASSISTENCIA = "Estoque em poder da Assistência técnica"
STATUS_ESTOQUE_RETORNADO = "Estoque retornado"

# Itens da mesma solicitacao podem estar em etapas diferentes, porque itens de
# tipos diferentes nao entram na mesma nota. O cabecalho mostra isso.
STATUS_PARCIAL = "Parcialmente atendida"

STATUS_PENDENTES_RETORNO = (STATUS_ESTOQUE_TERCEIROS, STATUS_ESTOQUE_ASSISTENCIA)
# Status a partir dos quais e possivel faturar (informar a NF).
STATUS_PODE_FATURAR = (STATUS_EXPEDIDO_SEM_NF, STATUS_AGUARDANDO_FAT)
# Status a partir dos quais e possivel vincular uma ordem de faturamento (OF).
STATUS_PODE_VINCULAR_OF = (STATUS_EXPEDIDO_SEM_NF, STATUS_AGUARDANDO_FAT)

STATUS_SLUGS = {
    STATUS_SOLICITADO: "em_separacao",
    STATUS_EXPEDIDO_SEM_NF: "expedido_sem_nf",
    STATUS_AGUARDANDO_FAT: "aguardando_faturamento",
    STATUS_NF_EMITIDA: "nf_emitida",
    STATUS_ESTOQUE_TERCEIROS: "estoque_terceiros",
    STATUS_ESTOQUE_ASSISTENCIA: "estoque_assistencia",
    STATUS_ESTOQUE_RETORNADO: "estoque_retornado",
    STATUS_PARCIAL: "parcialmente_atendida",
}

STATUS_BADGE = {
    STATUS_SOLICITADO: "eui-badge--warning",
    STATUS_EXPEDIDO_SEM_NF: "eui-badge--info",
    STATUS_AGUARDANDO_FAT: "eui-badge--warning",
    STATUS_NF_EMITIDA: "eui-badge--violet",
    STATUS_ESTOQUE_TERCEIROS: "eui-badge--primary",
    STATUS_ESTOQUE_ASSISTENCIA: "eui-badge--danger",
    STATUS_ESTOQUE_RETORNADO: "eui-badge--success",
    STATUS_PARCIAL: "eui-badge--info",
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
    select codigo_interno, nome, estoque_disponivel_uso,
           coalesce(nullif(localizacao_estoque, ''), '') as localizacao_estoque
    from public.tproduto
    where cod_empresa = %(empresa)s
      and (codigo_interno ilike %(termo)s or nome ilike %(termo)s)
    order by nome
    limit %(limit)s
"""

SQL_MATERIAL_POR_CODIGO = """
    select codigo_interno, nome, estoque_disponivel_uso,
           coalesce(nullif(localizacao_estoque, ''), '') as localizacao_estoque
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


def _dados_parceiro_nf(numero_nf: str) -> dict[str, str] | None:
    """Puxa o destinatario (nome + endereco) de uma NF emitida na bridge do
    ERP para a Remessa para Conserto. Best-effort: qualquer falha retorna None
    e nao bloqueia o faturamento."""
    numero_nf = str(numero_nf or "").strip()
    if not numero_nf:
        return None
    try:
        from .erp_nfe_emitidas_service import buscar_nfe_emitida_erp
        from . import danfe_service

        nota = buscar_nfe_emitida_erp(numero_nf=numero_nf)
        if not nota:
            return None
        xml_bytes = nota.get("xml_bytes")
        if not xml_bytes:
            return None
        dados = danfe_service.parse_nfe_xml(xml_bytes)
        if not dados:
            return None
        nome = str(dados.get("dest_nome") or "").strip()
        partes = [
            str(dados.get("dest_logr") or "").strip(),
            str(dados.get("dest_nro") or "").strip(),
            str(dados.get("dest_bairro") or "").strip(),
            str(dados.get("dest_mun") or "").strip(),
            str(dados.get("dest_uf") or "").strip(),
        ]
        endereco = ", ".join(p for p in partes if p)
        if not nome and not endereco:
            return None
        return {"nome": nome, "endereco": endereco}
    except Exception:
        current_app.logger.warning(
            "solicitacao_nf: falha ao puxar destinatario da NF %s na bridge", numero_nf, exc_info=True
        )
        return None


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


def listar_tipos_operacao() -> list[dict[str, Any]]:
    """Tipos ativos, com o texto de ajuda que o solicitante lê na hora de escolher.

    A tabela guarda o texto e o retorno sugerido; as regras de fluxo de cada
    tipo continuam nas constantes TIPOS_* acima. Se a tabela ainda não foi
    semeada, cai nos tipos do código para a tela nunca ficar sem opção."""
    try:
        from ..models import TipoOperacaoNF
        tipos = (TipoOperacaoNF.query.filter_by(ativo=True)
                 .order_by(TipoOperacaoNF.ordem_exibicao, TipoOperacaoNF.nome).all())
    except Exception:
        current_app.logger.warning("Tipos de operação: tabela indisponível, usando os do código.",
                                   exc_info=True)
        tipos = []
    if not tipos:
        return [{"nome": nome, "descricao_ajuda": "",
                 "requer_retorno_padrao": nome not in TIPOS_SEM_RETORNO}
                for nome in TIPOS_OPERACAO]
    return [{"nome": t.nome, "descricao_ajuda": t.descricao_ajuda or "",
             "requer_retorno_padrao": bool(t.requer_retorno_padrao)} for t in tipos]


def _retorno_sugerido(tipo_operacao: str) -> bool:
    for tipo in listar_tipos_operacao():
        if tipo["nome"] == tipo_operacao:
            return tipo["requer_retorno_padrao"]
    return tipo_operacao not in TIPOS_SEM_RETORNO


def _validar_itens(itens_payload: list, tipo_padrao: str = "",
                   retorno_padrao: bool | None = None) -> list[dict[str, Any]]:
    """Cada item carrega a própria operação e o próprio retorno.

    O que vier em branco herda a escolha feita no topo do formulário, que é o
    caso comum: uma solicitação inteira do mesmo tipo."""
    if not itens_payload:
        raise SolicitacaoNFError("Informe ao menos um material.")
    validos = {t["nome"] for t in listar_tipos_operacao()} | set(TIPOS_OPERACAO)
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
        tipo = str((bruto or {}).get("tipo_operacao") or tipo_padrao).strip()
        if tipo not in validos:
            raise SolicitacaoNFError(f"Tipo de operação inválido no item '{codigo}'.")
        informado = (bruto or {}).get("necessita_retorno")
        if informado is None:
            informado = retorno_padrao
        itens.append({
            "material_codigo": material["codigo_interno"],
            "material_nome": material["nome"],
            "material_local": str(material.get("localizacao_estoque") or "").strip() or None,
            "quantidade": quantidade,
            "tipo_operacao": tipo,
            "necessita_retorno": bool(informado) if informado is not None
                                 else _retorno_sugerido(tipo),
        })
    return itens


def criar_solicitacao(payload: dict, ip: str | None = None) -> SolicitacaoNF:
    tipo_operacao = str((payload or {}).get("tipo_operacao") or "").strip()
    # Aceita o que estiver ativo na tabela; os tipos do código seguem valendo
    # como piso, para um tipo desativado por engano não derrubar o formulário.
    validos = {t["nome"] for t in listar_tipos_operacao()} | set(TIPOS_OPERACAO)
    if tipo_operacao not in validos:
        raise SolicitacaoNFError("Tipo de operação inválido.")

    funcionario = _validar_solicitante((payload or {}).get("solicitante_nome"))
    cliente = _validar_cliente(
        (payload or {}).get("cliente_codigo"),
        (payload or {}).get("cliente_nome"),
    )
    venda_posterior = bool((payload or {}).get("venda_posterior"))
    informado = (payload or {}).get("necessita_retorno")
    itens = _validar_itens((payload or {}).get("itens") or [], tipo_operacao,
                           bool(informado) if informado is not None else None)

    # O cabecalho e' NOT NULL e ainda alimenta telas e notificacoes: guarda o
    # tipo do primeiro item, que na pratica e' o da solicitacao inteira.
    tipo_operacao = itens[0]["tipo_operacao"]
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
    # O item carrega a operação, a NF e o retorno (é o que permite uma
    # solicitação virar mais de uma nota). Hoje o tipo ainda é escolhido uma
    # vez por solicitação, então todo item nasce com o tipo do cabeçalho: o
    # processamento interno ainda decide o fluxo pelo tipo do cabeçalho, e
    # deixar itens de tipos diferentes entrarem antes disso faria a Logística
    # e o Fiscal tratarem o item pela regra errada.
    #
    # O tipo de operação SUGERE se o material volta, mas quem responde é o
    # solicitante: é ele que sabe se aquele material específico vai voltar.
    for i, item in enumerate(itens):
        solicitacao.itens.append(SolicitacaoNFItem(
            linha=i, sera_vendido=venda_posterior, status=STATUS_SOLICITADO, **item))

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
        # Conserto / retorno de demonstracao: a NF sai antes do material, entao
        # ficam "Aguardando faturamento". Os demais sao expedidos sem NF. A
        # regra passou a olhar o tipo DO ITEM, que e' o que permite itens de
        # tipos diferentes seguirem caminhos diferentes na mesma solicitacao.
        item.status = (STATUS_AGUARDANDO_FAT
                       if (item.tipo_operacao or solicitacao.tipo_operacao) in TIPOS_AGUARDA_FATURAMENTO
                       else STATUS_EXPEDIDO_SEM_NF)

    status_anterior = solicitacao.status
    solicitacao.status = _recalcular_status(solicitacao)
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


def alterar_item(solicitacao_id: int, item_id: int, usuario: str,
                 tipo_operacao: str | None = None, necessita_retorno=None,
                 data_prevista_retorno: str | None = None) -> SolicitacaoNF:
    """Corrige a operação, o retorno e o prazo de um item.

    O solicitante escolhe o tipo, mas nem sempre acerta — e quem separa e
    fatura é quem conhece a regra fiscal. Enquanto o item não tem nota, dá para
    corrigir aqui; depois dela, não, porque o tipo define como a nota saiu."""
    solicitacao = SolicitacaoNF.query.get(solicitacao_id)
    if not solicitacao:
        raise SolicitacaoNFError("Solicitação não encontrada.")
    item = next((i for i in solicitacao.itens if i.id == int(item_id)), None)
    if not item:
        raise SolicitacaoNFError("Item não encontrado nesta solicitação.")
    if item.numero_nf:
        raise SolicitacaoNFError(
            f"O item já foi faturado na NF {item.numero_nf}. Estorne antes de alterar.")

    antes = {"tipo_operacao": item.tipo_operacao, "necessita_retorno": item.necessita_retorno,
             "data_prevista_retorno": _iso(item.data_prevista_retorno)}

    if tipo_operacao is not None:
        tipo_operacao = str(tipo_operacao).strip()
        validos = {t["nome"] for t in listar_tipos_operacao()} | set(TIPOS_OPERACAO)
        if tipo_operacao not in validos:
            raise SolicitacaoNFError("Tipo de operação inválido.")
        item.tipo_operacao = tipo_operacao
        # O tipo manda para onde o item vai depois da nota, então enquanto ele
        # está só separado o status acompanha a mudança.
        if item.status in STATUS_PODE_FATURAR:
            item.status = (STATUS_AGUARDANDO_FAT if tipo_operacao in TIPOS_AGUARDA_FATURAMENTO
                           else STATUS_EXPEDIDO_SEM_NF)
        if necessita_retorno is None:
            item.necessita_retorno = _retorno_sugerido(tipo_operacao)

    if necessita_retorno is not None:
        item.necessita_retorno = bool(necessita_retorno)
    if data_prevista_retorno is not None:
        item.data_prevista_retorno = _data(data_prevista_retorno)
    if not item.necessita_retorno:
        item.data_prevista_retorno = None

    solicitacao.status = _recalcular_status(solicitacao)
    solicitacao.updated_at = datetime.now()
    db.session.add(SolicitacaoNFLog(
        solicitacao_id=solicitacao.id, item_id=item.id, acao="item alterado", usuario=usuario,
        status_anterior=antes["tipo_operacao"], status_novo=item.tipo_operacao,
        detalhes=json.dumps({"antes": antes, "depois": {
            "tipo_operacao": item.tipo_operacao,
            "necessita_retorno": item.necessita_retorno,
            "data_prevista_retorno": _iso(item.data_prevista_retorno)}}, ensure_ascii=False),
    ))
    db.session.commit()
    return solicitacao


def _data(valor):
    """Aceita 'AAAA-MM-DD' vindo do <input type=date>; vazio limpa o campo."""
    valor = str(valor or "").strip()
    if not valor:
        return None
    try:
        return datetime.strptime(valor[:10], "%Y-%m-%d").date()
    except ValueError:
        raise SolicitacaoNFError("Data de retorno inválida.")


def _status_do_item_apos_faturar(item) -> str:
    """Para onde o item vai depois que a nota sai.

    Quem diz se o material volta é o solicitante (necessita_retorno); o tipo de
    operação decide apenas em poder de quem ele fica enquanto não volta."""
    tipo = item.tipo_operacao or ""
    if not item.necessita_retorno:
        return STATUS_NF_EMITIDA
    if tipo == "Materiais para atendimento técnico no cliente":
        return STATUS_ESTOQUE_ASSISTENCIA
    return STATUS_ESTOQUE_TERCEIROS


def _recalcular_status(solicitacao: SolicitacaoNF) -> str:
    """Status da solicitação a partir dos itens.

    Estado derivado não se inventa: a solicitação está onde seus itens estão.
    Quando eles estão em etapas diferentes — o que passa a acontecer agora que
    cada item tem a própria nota — o cabeçalho mostra "Parcialmente atendida".
    A coluna continua existindo porque é indexada, filtrada na tela e lida pelo
    avanço automático da ordem de faturamento."""
    situacoes = {item.status for item in solicitacao.itens if item.status}
    if not situacoes:
        return solicitacao.status
    return situacoes.pop() if len(situacoes) == 1 else STATUS_PARCIAL


def marcar_faturada(solicitacao_id: int, usuario: str, numero_nf: str, observacao: str | None,
                    item_ids: list | None = None,
                    data_prevista_retorno: str | None = None) -> SolicitacaoNF:
    """Informa a NF. Sem `item_ids`, fatura tudo que ainda falta — é assim que
    a tela faz quando a solicitação tem um tipo só. Com `item_ids`, fatura só
    aquele grupo, que é o caso de itens de tipos diferentes: cada grupo vira
    uma nota e a solicitação fica parcialmente atendida até fechar a última."""
    solicitacao = SolicitacaoNF.query.get(solicitacao_id)
    if not solicitacao:
        raise SolicitacaoNFError("Solicitação não encontrada.")
    # Quem manda agora é o estado dos itens; no cabeçalho só resta a checagem
    # de que a separação aconteceu, senão a mensagem de erro engana o Fiscal.
    if solicitacao.status == STATUS_SOLICITADO:
        raise SolicitacaoNFError("Solicitação ainda não foi separada.")
    numero_nf = str(numero_nf or "").strip()
    if not numero_nf:
        raise SolicitacaoNFError("Informe o número da NF.")

    faturaveis = [i for i in solicitacao.itens if i.status in STATUS_PODE_FATURAR]
    if item_ids is not None:
        escolhidos = {int(i) for i in item_ids}
        alvos = [i for i in faturaveis if i.id in escolhidos]
        if not alvos:
            raise SolicitacaoNFError("Nenhum dos itens escolhidos está pronto para faturar.")
    else:
        alvos = faturaveis
    if not alvos:
        raise SolicitacaoNFError("Todos os itens desta solicitação já foram faturados.")

    agora = datetime.now()
    # O prazo de retorno é do Fiscal, informado junto com a nota, e só faz
    # sentido para o item que volta.
    prazo = _data(data_prevista_retorno) if data_prevista_retorno else None
    for item in alvos:
        item.numero_nf = numero_nf
        item.data_emissao_nf = agora
        item.status = _status_do_item_apos_faturar(item)
        if item.necessita_retorno and prazo:
            item.data_prevista_retorno = prazo

    status_anterior = solicitacao.status
    solicitacao.status = _recalcular_status(solicitacao)
    solicitacao.faturado_por = usuario
    solicitacao.faturado_at = agora
    # Uma solicitação pode ter mais de uma nota; o cabeçalho lista as que saíram.
    notas = list(dict.fromkeys([i.numero_nf for i in solicitacao.itens if i.numero_nf]))
    solicitacao.numero_nf = ", ".join(notas)[:80]
    solicitacao.observacoes_faturamento = (observacao or "")[:500]

    # Remessa para Conserto: puxa o destinatario/endereco da NF na bridge do ERP.
    if any((i.tipo_operacao or solicitacao.tipo_operacao) in TIPOS_PUXA_NF_BRIDGE for i in alvos):
        parceiro = _dados_parceiro_nf(numero_nf)
        if parceiro:
            solicitacao.nf_parceiro_nome = (parceiro.get("nome") or "")[:200] or None
            solicitacao.nf_parceiro_endereco = (parceiro.get("endereco") or "")[:400] or None

    solicitacao.updated_at = datetime.now()

    db.session.add(SolicitacaoNFLog(
        solicitacao_id=solicitacao.id,
        acao="faturada",
        usuario=usuario,
        status_anterior=status_anterior,
        status_novo=solicitacao.status,
        detalhes=json.dumps({"numero_nf": numero_nf, "observacao": observacao,
                             "itens": [i.id for i in alvos]}, ensure_ascii=False),
    ))
    db.session.commit()

    teams_service.notificar_solicitacao_nf(
        "faturada", solicitacao.protocolo, solicitacao.solicitante_nome,
        solicitacao.cliente_nome, solicitacao.tipo_operacao,
        subinfo=f"NF {numero_nf} · Faturado por {usuario}",
    )
    return solicitacao


def _buscar_ordem_fat(cod_ordem_fat) -> ExpedicaoOrdemFat | None:
    """Localiza a ordem de faturamento (cod_ordem_fat) sincronizada do ERP."""
    try:
        cod = int(str(cod_ordem_fat).strip())
    except (TypeError, ValueError):
        return None
    return ExpedicaoOrdemFat.query.filter_by(cod_ordem_fat=cod, excluido=False).first()


def vincular_ordem_faturamento(solicitacao_id: int, usuario: str, cod_ordem_fat) -> SolicitacaoNF:
    """Vincula uma ordem de faturamento (OF do ERP) a uma expedicao sem NF.
    Se a OF ja estiver faturada (numero_nf preenchido), a solicitacao avanca
    imediatamente para o status final conforme o tipo. Caso contrario, o
    vinculo fica registrado e a solicitacao avanca automaticamente quando a
    OF for faturada (ver reconciliacao em listar_ordens_avulso)."""
    solicitacao = SolicitacaoNF.query.get(solicitacao_id)
    if not solicitacao:
        raise SolicitacaoNFError("Solicitação não encontrada.")
    if solicitacao.status not in STATUS_PODE_VINCULAR_OF:
        raise SolicitacaoNFError("Só é possível vincular ordem de faturamento em expedições sem NF.")

    try:
        cod = int(str(cod_ordem_fat or "").strip())
    except (TypeError, ValueError):
        raise SolicitacaoNFError("Informe um número de ordem de faturamento válido.")

    ordem = _buscar_ordem_fat(cod)
    if not ordem:
        raise SolicitacaoNFError(f"Ordem de faturamento {cod} não encontrada.")

    status_anterior = solicitacao.status
    solicitacao.ordem_faturamento = cod
    solicitacao.updated_at = datetime.now()
    db.session.add(SolicitacaoNFLog(
        solicitacao_id=solicitacao.id,
        acao="vinculo_of",
        usuario=usuario,
        status_anterior=status_anterior,
        status_novo=status_anterior,
        detalhes=json.dumps({"cod_ordem_fat": cod, "numero_nf": ordem.numero_nf}, ensure_ascii=False),
    ))
    db.session.commit()

    # OF ja faturada: avanca a solicitacao imediatamente com a NF da OF.
    numero_nf = str(ordem.numero_nf or "").strip()
    if numero_nf:
        return marcar_faturada(
            solicitacao_id, usuario, numero_nf,
            f"Faturado pela ordem de faturamento #{cod}",
        )
    return solicitacao


def _reconciliar_of_vinculadas() -> None:
    """Avanca automaticamente as solicitacoes vinculadas a uma OF que ja foi
    faturada no ERP (numero_nf preenchido). Chamada ao listar o painel."""
    # Inclui a parcialmente atendida: ela tem itens esperando nota e, sem isso,
    # nunca mais avançaria sozinha depois do primeiro faturamento.
    pendentes = (
        SolicitacaoNF.query
        .filter(SolicitacaoNF.ordem_faturamento.isnot(None))
        .filter(SolicitacaoNF.status.in_(STATUS_PODE_VINCULAR_OF + (STATUS_PARCIAL,)))
        .all()
    )
    for sol in pendentes:
        ordem = _buscar_ordem_fat(sol.ordem_faturamento)
        numero_nf = str(ordem.numero_nf or "").strip() if ordem else ""
        if not numero_nf:
            continue
        # Uma OF corresponde a uma nota; itens de operações diferentes não
        # cabem nela. Só avança sozinho quando há uma operação pendente, e o
        # resto fica para o Fiscal faturar à mão, com a nota certa de cada um.
        faturaveis = [i for i in sol.itens if i.status in STATUS_PODE_FATURAR]
        operacoes = {i.tipo_operacao for i in faturaveis}
        if len(operacoes) > 1:
            continue
        try:
            marcar_faturada(
                sol.id, "Sistema (OF)", numero_nf,
                f"Faturado automaticamente pela ordem de faturamento #{sol.ordem_faturamento}",
            )
        except SolicitacaoNFError:
            continue


def registrar_retorno(solicitacao_id: int, usuario: str, numero_nf_retorno: str,
                      observacao: str | None, retornos: dict | None = None) -> SolicitacaoNF:
    """Registra a volta do material.

    `retornos` é {item_id: quantidade} e permite devolução parcial: o item só
    fecha quando a soma devolvida alcança a quantidade enviada. Sem ele, tudo
    que estava esperando volta inteiro — que era o único jeito antes."""
    solicitacao = SolicitacaoNF.query.get(solicitacao_id)
    if not solicitacao:
        raise SolicitacaoNFError("Solicitação não encontrada.")
    esperando = [i for i in solicitacao.itens if i.status in STATUS_PENDENTES_RETORNO]
    if not esperando:
        raise SolicitacaoNFError("Esta solicitação não está aguardando retorno de material.")
    numero_nf_retorno = str(numero_nf_retorno or "").strip()
    if not numero_nf_retorno:
        raise SolicitacaoNFError("Informe o número da NF de retorno.")

    hoje = datetime.now()
    for item in esperando:
        if retornos is not None and item.id not in retornos:
            continue
        try:
            voltou = float(retornos[item.id]) if retornos is not None else float(item.quantidade or 0)
        except (TypeError, ValueError):
            raise SolicitacaoNFError("Quantidade devolvida inválida.")
        if voltou <= 0:
            continue
        ja_tinha = float(item.quantidade_retornada or 0)
        if ja_tinha + voltou > float(item.quantidade or 0) + 1e-6:
            raise SolicitacaoNFError(
                f"A devolução de {item.material_codigo} passa da quantidade enviada.")
        item.quantidade_retornada = ja_tinha + voltou
        item.numero_nf_retorno = numero_nf_retorno
        # Só fecha quando tudo voltou; devolução parcial continua pendente.
        if item.quantidade_retornada + 1e-6 >= float(item.quantidade or 0):
            item.status = STATUS_ESTOQUE_RETORNADO
            item.data_efetiva_retorno = hoje.date()

    status_anterior = solicitacao.status
    solicitacao.status = _recalcular_status(solicitacao)
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

    if status_atual in (STATUS_EXPEDIDO_SEM_NF, STATUS_AGUARDANDO_FAT):
        novo_status = STATUS_SOLICITADO
        solicitacao.separado_por = None
        solicitacao.separado_at = None
        solicitacao.observacoes_separacao = None
        solicitacao.ordem_faturamento = None
        for item in solicitacao.itens:
            item.separado = False
            item.status = STATUS_SOLICITADO
    elif status_atual in (STATUS_NF_EMITIDA, STATUS_ESTOQUE_TERCEIROS, STATUS_ESTOQUE_ASSISTENCIA):
        # Volta para a etapa anterior ao faturamento (depende do tipo).
        novo_status = (
            STATUS_AGUARDANDO_FAT
            if solicitacao.tipo_operacao in TIPOS_AGUARDA_FATURAMENTO
            else STATUS_EXPEDIDO_SEM_NF
        )
        solicitacao.faturado_por = None
        solicitacao.faturado_at = None
        solicitacao.numero_nf = None
        solicitacao.observacoes_faturamento = None
        solicitacao.nf_parceiro_nome = None
        solicitacao.nf_parceiro_endereco = None
        # Desfaz o vinculo com a OF para nao refaturar automaticamente ao listar.
        solicitacao.ordem_faturamento = None
        for item in solicitacao.itens:
            item.numero_nf = None
            item.data_emissao_nf = None
            item.status = (STATUS_AGUARDANDO_FAT
                           if (item.tipo_operacao or solicitacao.tipo_operacao) in TIPOS_AGUARDA_FATURAMENTO
                           else STATUS_EXPEDIDO_SEM_NF)
    elif status_atual == STATUS_ESTOQUE_RETORNADO:
        novo_status = (
            STATUS_ESTOQUE_ASSISTENCIA
            if solicitacao.tipo_operacao == "Materiais para atendimento técnico no cliente"
            else STATUS_ESTOQUE_TERCEIROS
        )
        solicitacao.numero_nf_retorno = None
        solicitacao.retorno_por = None
        solicitacao.retorno_at = None
        solicitacao.observacoes_retorno = None
        for item in solicitacao.itens:
            item.numero_nf_retorno = None
            item.quantidade_retornada = 0
            item.data_efetiva_retorno = None
            item.status = _status_do_item_apos_faturar(item)
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


def resolver_funcionario_do_usuario(username: str) -> dict[str, Any] | None:
    """Funcionário do GRV correspondente à conta logada.

    O formulário é público e a pessoa se identifica pela lista de
    funcionários, então a conta do Sync não sabe, sozinha, de quem são as
    solicitações. O vínculo é resolvido pelo e-mail na primeira consulta e
    gravado no usuário; quando não dá para resolver, devolve None e a tela
    pede para a pessoa escolher o próprio nome uma vez."""
    from ..models import Usuario, UsuarioFuncionario
    username = (username or "").strip()
    if not username:
        return None
    funcionarios = FacilitiesGRVService.listar_funcionarios(ativos=True)
    vinculo = UsuarioFuncionario.query.filter_by(username=username).first()
    if vinculo:
        for func in funcionarios:
            if str(func.get("codigo") or "") == str(vinculo.funcionario_codigo):
                return func
        return None  # vínculo aponta para alguém que não está mais ativo
    usuario = Usuario.query.filter_by(username=username).first()
    email = ((usuario.email if usuario else "") or "").strip().lower()
    if not email:
        return None
    for func in funcionarios:
        if (func.get("email") or "").strip().lower() == email:
            db.session.add(UsuarioFuncionario(username=username, origem="email",
                                              funcionario_codigo=str(func.get("codigo") or "")))
            db.session.commit()
            return func
    return None


def vincular_funcionario(username: str, codigo: str) -> dict[str, Any]:
    """Grava o vínculo escolhido a mão, quando o e-mail não resolveu."""
    from ..models import UsuarioFuncionario
    username = (username or "").strip()
    codigo = str(codigo or "").strip()
    if not username:
        raise SolicitacaoNFError("Usuário não encontrado.")
    for func in FacilitiesGRVService.listar_funcionarios(ativos=True):
        if str(func.get("codigo") or "") == codigo:
            vinculo = UsuarioFuncionario.query.filter_by(username=username).first()
            if vinculo:
                vinculo.funcionario_codigo = codigo
                vinculo.origem = "manual"
            else:
                db.session.add(UsuarioFuncionario(username=username, funcionario_codigo=codigo,
                                                  origem="manual"))
            db.session.commit()
            return func
    raise SolicitacaoNFError("Funcionário não encontrado na lista de ativos.")


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
        "material_local": item.material_local,
        "quantidade": item.quantidade,
        "separado": item.separado,
        # Operação, nota e retorno por item: é o que a tela usa para agrupar
        # os itens em notas diferentes e para cobrar o que ainda não voltou.
        "tipo_operacao": item.tipo_operacao,
        "sera_vendido": item.sera_vendido,
        "necessita_retorno": item.necessita_retorno,
        "status": item.status,
        "status_slug": STATUS_SLUGS.get(item.status, ""),
        "status_badge": STATUS_BADGE.get(item.status, "eui-badge--neutral"),
        "numero_nf": item.numero_nf,
        "data_emissao_nf": _iso(item.data_emissao_nf),
        "data_prevista_retorno": _iso(item.data_prevista_retorno),
        "data_efetiva_retorno": _iso(item.data_efetiva_retorno),
        "quantidade_retornada": item.quantidade_retornada,
        "numero_nf_retorno": item.numero_nf_retorno,
        "pode_faturar": item.status in STATUS_PODE_FATURAR,
        "aguardando_retorno": item.status in STATUS_PENDENTES_RETORNO,
        "dias_de_atraso": _dias_de_atraso(item),
    }


def _dias_de_atraso(item) -> int:
    """Dias passados do prazo, para o item que ainda não voltou. 0 = em dia."""
    if item.status not in STATUS_PENDENTES_RETORNO or not item.data_prevista_retorno:
        return 0
    return max(0, (datetime.now().date() - item.data_prevista_retorno).days)


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
        "nf_parceiro_nome": s.nf_parceiro_nome,
        "nf_parceiro_endereco": s.nf_parceiro_endereco,
        "ordem_faturamento": s.ordem_faturamento,
        "ordem_faturamento_info": _info_ordem_fat(s.ordem_faturamento),
        "created_at": _iso(s.created_at),
        "itens": [_serializar_item(i) for i in s.itens],
    }


def _info_ordem_fat(cod_ordem_fat) -> dict[str, Any] | None:
    """Snapshot leve da OF vinculada (cliente/status/NF) para exibir no painel."""
    if not cod_ordem_fat:
        return None
    ordem = _buscar_ordem_fat(cod_ordem_fat)
    if not ordem:
        return {"cod_ordem_fat": cod_ordem_fat, "encontrada": False}
    return {
        "cod_ordem_fat": ordem.cod_ordem_fat,
        "encontrada": True,
        "cliente": ordem.cliente,
        "status": ordem.status,
        "numero_nf": ordem.numero_nf,
        "orcamento": ordem.orcamento,
    }


def listar_ordens_avulso() -> dict[str, Any]:
    """Lista + KPIs da aba "Faturamento avulso" (Conferencia de Expedicao)."""
    _reconciliar_of_vinculadas()
    solicitacoes = SolicitacaoNF.query.order_by(SolicitacaoNF.created_at.desc()).all()
    resumo = {
        "em_separacao": 0,
        "expedido_sem_nf": 0,
        "aguardando_faturamento": 0,
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
