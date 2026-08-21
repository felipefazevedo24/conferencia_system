import json
import re
import unicodedata
from datetime import datetime, timedelta
from difflib import SequenceMatcher

import requests

from flask import current_app

from ..extensions import db
from ..models import (
    CadastroWorkflowChecklist,
    CadastroWorkflowHistorico,
    CadastroWorkflowNotificacao,
    CadastroWorkflowSLAConfig,
    CadastroWorkflowSolicitacao,
    PlanoContaDominio,
)


UNIDADES_MEDIDA_MATERIAL = [
    ("UN", "UN - Unidade"),
    ("PC", "PC - Peça"),
    ("KG", "KG - Quilograma"),
    ("G", "G - Grama"),
    ("MT", "MT - Metro"),
    ("M2", "M2 - Metro quadrado"),
    ("M3", "M3 - Metro cúbico"),
    ("L", "L - Litro"),
    ("ML", "ML - Mililitro"),
    ("CX", "CX - Caixa"),
    ("PCT", "PCT - Pacote"),
    ("PAR", "PAR - Par"),
    ("RL", "RL - Rolo"),
    ("BR", "BR - Barra"),
    ("CH", "CH - Chapa"),
    ("JG", "JG - Jogo"),
    ("SV", "SV - Serviço"),
]

UTILIZACOES_MATERIAL = [
    ("00", "00 - Mercadoria para Revenda"),
    ("01", "01 - Materia-Prima"),
    ("02", "02 - Embalagem"),
    ("03", "03 - Produto em Processo"),
    ("04", "04 - Produto Acabado"),
    ("05", "05 - Subproduto"),
    ("06", "06 - Produto Intermediario"),
    ("07", "07 - Material de Uso e Consumo"),
    ("08", "08 - Ativo Imobilizado"),
    ("09", "09 - Servicos"),
    ("10", "10 - Outros insumos"),
    ("99", "99 - Outras"),
]

METODOS_ATUALIZACAO_CUSTO = [
    ("nao_atualiza", "Não atualiza custo"),
    ("custo_medio", "Custo Médio"),
    ("ultimo_processamento", "Custo do último Processamento"),
]

METODOS_REPOSICAO = [
    ("compras", "Compras"),
    ("producao_terceiros", "Produção por terceiros"),
    ("producao_propria", "Produção própria"),
]

CONTRIBUINTE_ICMS = [
    ("1", "1 - Contribuinte ICMS"),
    ("2", "2 - Contribuinte Isento de IE"),
    ("9", "9 - Não Contribuinte"),
]

CAMPO_OPCOES = {
    "unidade_medida": UNIDADES_MEDIDA_MATERIAL,
    "unidade_compra": UNIDADES_MEDIDA_MATERIAL,
    "utilizacao": UTILIZACOES_MATERIAL,
    "metodo_atualizacao_custo": METODOS_ATUALIZACAO_CUSTO,
    "metodo_reposicao": METODOS_REPOSICAO,
    "contribuinte_icms": CONTRIBUINTE_ICMS,
}

MATERIAL_SOLICITANTE_FIELDS = [
    ("descricao", "Descrição", True),
    ("unidade_medida", "Unidade de medida desejada", True),
    ("utilizacao", "Utilização", True),
    ("detalhe_utilizacao", "Como e por que vai usar", True),
    ("fornecedor_sugerido", "Fornecedor sugerido", False),
]

MATERIAL_CONTABIL_FIELDS = [
    ("plano_contas", "Número do plano de contas", True),
    ("plano_contas_descricao", "Descrição da conta", False),
    ("parecer_contabil", "Validação do contábil", False),
]

MATERIAL_COMPRAS_FIELDS = [
    ("unidade_compra", "Unidade de compra", False),
    ("metodo_reposicao", "Método de reposição", False),
]

MATERIAL_UTILIZACAO_RAPIDA = [
    {
        "label": "Manutenção",
        "utilizacao": "07",
        "texto": "Será usado na manutenção de equipamento/componente existente, para reposição de item desgastado e continuidade da operação.",
    },
    {
        "label": "Produção",
        "utilizacao": "01",
        "texto": "Será usado diretamente no processo produtivo, compondo ou viabilizando a fabricação do produto final.",
    },
    {
        "label": "Consumo interno",
        "utilizacao": "07",
        "texto": "Será consumido na rotina interna da área, sem incorporação ao produto final, para apoiar a execução das atividades diárias.",
    },
    {
        "label": "Embalagem",
        "utilizacao": "02",
        "texto": "Será usado na embalagem, proteção e expedição dos produtos, garantindo acondicionamento e envio adequados.",
    },
    {
        "label": "Imobilizado",
        "utilizacao": "08",
        "texto": "Será usado como bem durável da operação, com permanência no ativo e utilização recorrente pela área solicitante.",
    },
]

MATERIAL_FISCAL_FIELDS = [
    ("ncm_sugerido", "NCM sugerido", False),
    ("ncm_validado", "NCM validado", False),
    ("codigo_beneficio_fiscal", "Cód. de benefício fiscal", False),
    ("codigo_classificacao_tributaria", "Cód. da classificação tributária", False),
    ("metodo_atualizacao_custo", "Método de atualização de custo", False),
]

MATERIAL_FIELD_GROUPS = [
    ("solicitante", "Informações do solicitante", MATERIAL_SOLICITANTE_FIELDS),
    ("contabil", "Validação Contábil", MATERIAL_CONTABIL_FIELDS),
    ("compras", "Validação de Compras", MATERIAL_COMPRAS_FIELDS),
    ("fiscal", "Validação Fiscal", MATERIAL_FISCAL_FIELDS),
]


TIPOS_CADASTRO = {
    "material": {
        "label": "Cadastro de Material",
        "icon": "fa-boxes-stacked",
        "solicitante_fields": MATERIAL_SOLICITANTE_FIELDS,
        "fields": MATERIAL_SOLICITANTE_FIELDS + MATERIAL_CONTABIL_FIELDS + MATERIAL_COMPRAS_FIELDS + MATERIAL_FISCAL_FIELDS,
        "field_groups": MATERIAL_FIELD_GROUPS,
    },
    "cliente": {
        "label": "Cadastro de Cliente",
        "icon": "fa-user-tie",
        "solicitante_fields": [("documento", "CNPJ", True), ("contribuinte_icms", "Contribuinte ICMS", True)],
        "fields": [
            ("razao_social", "Razão Social", True),
            ("nome_fantasia", "Nome Fantasia", False),
            ("documento", "CNPJ", True),
            ("contribuinte_icms", "Contribuinte ICMS", True),
            ("inscricao_estadual", "Inscrição Estadual", False),
            ("inscricao_municipal", "Inscrição Municipal", False),
            ("endereco", "Endereço completo", True),
            ("cep", "CEP", False),
            ("municipio", "Município", False),
            ("uf", "UF", False),
            ("cnae", "CNAE", False),
            ("regime_tributario", "Regime tributário", False),
            ("email", "E-mail", False),
            ("telefone", "Telefone", False),
            ("contato", "Contato", False),
        ],
    },
    "fornecedor": {
        "label": "Cadastro de Fornecedor",
        "icon": "fa-industry",
        "solicitante_fields": [("documento", "CNPJ", True), ("contribuinte_icms", "Contribuinte ICMS", True)],
        "fields": [
            ("razao_social", "Razão Social", True),
            ("nome_fantasia", "Nome Fantasia", False),
            ("documento", "CNPJ", True),
            ("contribuinte_icms", "Contribuinte ICMS", True),
            ("inscricao_estadual", "Inscrição Estadual", False),
            ("endereco", "Endereço completo", True),
            ("cep", "CEP", False),
            ("municipio", "Município", False),
            ("uf", "UF", False),
            ("cnae", "CNAE", False),
            ("regime_tributario", "Regime tributário", False),
            ("dados_bancarios", "Dados bancários", False),
            ("email", "E-mail", False),
            ("telefone", "Telefone", False),
            ("tipo_fornecimento", "Tipo de fornecimento", False),
        ],
    },
    "transportadora": {
        "label": "Cadastro de Transportadora",
        "icon": "fa-truck-fast",
        "solicitante_fields": [("documento", "CNPJ", True), ("contribuinte_icms", "Contribuinte ICMS", True)],
        "fields": [
            ("razao_social", "Razão Social", True),
            ("nome_fantasia", "Nome Fantasia", False),
            ("documento", "CNPJ", True),
            ("contribuinte_icms", "Contribuinte ICMS", True),
            ("inscricao_estadual", "Inscrição Estadual", False),
            ("antt", "ANTT", False),
            ("endereco", "Endereço completo", True),
            ("cep", "CEP", False),
            ("municipio", "Município", False),
            ("uf", "UF", False),
            ("cnae", "CNAE", False),
            ("regime_tributario", "Regime tributário", False),
            ("email", "E-mail", False),
            ("telefone", "Telefone", False),
            ("regiao_atendimento", "Região de atendimento", False),
            ("modal_transporte", "Modal de transporte", False),
        ],
    },
}

STATUS = [
    "Rascunho",
    "Enviado",
    "Em Validacao Contabil",
    "Em Validacao Compras",
    "Pendente de Correcao",
    "Aprovado por Compras",
    "Em Validacao Fiscal",
    "Cadastrado",
    "Reprovado",
    "Cancelado",
]

CHECKLIST_COMPRAS = [
    "Descrição e unidade de compra conferidas",
    "Método de reposição definido",
    "Fornecedor sugerido avaliado quando informado",
    "Cadastro semelhante pesquisado",
]

CHECKLIST_CONTABIL_POR_TIPO = {
    "material": [
        "Utilização está clara e suficiente para análise",
        "Forma de uso foi validada com a necessidade da área",
        "Plano de contas foi definido",
    ],
}

MOTIVOS_PADRAO_POR_ACAO = {
    "devolver_solicitante": [
        "Falta detalhar melhor a utilização do material",
        "Descrição insuficiente ou genérica",
        "Documento ou anexo complementar pendente",
        "Informação divergente entre campos",
    ],
    "devolver_contabil": [
        "Plano de contas precisa ser revisto",
        "Utilização não sustenta a classificação contábil",
        "Necessário alinhar natureza do gasto com a área solicitante",
    ],
    "devolver_compras": [
        "Unidade ou método de reposição precisa ajuste",
        "Fornecedor ou estratégia de compra precisa revisão",
        "Dados de compras insuficientes para seguir",
    ],
    "reprovar": [
        "Material já existe no GRV",
        "Solicitação não justificada para cadastro",
        "Cadastro não atende política interna",
    ],
}

CHECKLIST_FISCAL_POR_TIPO = {
    "material": [
        "NCM validado",
        "Classificação tributária definida",
        "Benefício fiscal revisado quando aplicável",
        "Método de atualização de custo definido",
    ],
    "cliente": [
        "CNPJ consultado e válido",
        "Contribuinte ICMS conferido",
        "Inscrição Estadual coerente com contribuinte",
        "Endereço e UF conferidos",
        "CNAE e regime tributário revisados",
    ],
    "fornecedor": [
        "CNPJ consultado e válido",
        "Contribuinte ICMS conferido",
        "Inscrição Estadual coerente com contribuinte",
        "CNAE e regime tributário revisados",
        "Dados fiscais obrigatórios conferidos",
    ],
    "transportadora": [
        "CNPJ consultado e válido",
        "Contribuinte ICMS conferido",
        "Inscrição Estadual coerente com contribuinte",
        "ANTT/modal revisados quando aplicável",
        "UF e região de atendimento conferidas",
    ],
}

CADASTROS_DIRETO_FISCAL = {"cliente", "fornecedor", "transportadora"}

ROLE_CONTABIL = {"financeiro", "controladoria", "contabil", "contabilidade"}


def normalizar_texto(valor: str | None) -> str:
    txt = unicodedata.normalize("NFKD", str(valor or ""))
    txt = "".join(ch for ch in txt if not unicodedata.combining(ch))
    return " ".join(txt.lower().split())


def role_contabil(role: str | None) -> bool:
    return normalizar_texto(role) in ROLE_CONTABIL


def motivos_padrao_por_acao() -> dict[str, list[str]]:
    return {acao: list(opcoes) for acao, opcoes in MOTIVOS_PADRAO_POR_ACAO.items()}


def normalizar_documento(value: str | None) -> str:
    return re.sub(r"\D+", "", value or "")


def cnpj_valido(value: str | None) -> bool:
    doc = normalizar_documento(value)
    if len(doc) != 14 or len(set(doc)) == 1:
        return False
    pesos_1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    pesos_2 = [6] + pesos_1

    def digito(base: str, pesos: list[int]) -> str:
        soma = sum(int(numero) * peso for numero, peso in zip(base, pesos))
        resto = soma % 11
        return "0" if resto < 2 else str(11 - resto)

    return doc[-2:] == digito(doc[:12], pesos_1) + digito(doc[:13], pesos_2)


def formatar_cnpj(cnpj: str | None) -> str:
    doc = normalizar_documento(cnpj)[:14]
    if len(doc) != 14:
        return doc
    return f"{doc[:2]}.{doc[2:5]}.{doc[5:8]}/{doc[8:12]}-{doc[12:]}"


def _endereco_from_payload(payload: dict) -> str:
    partes = [
        payload.get("descricao_tipo_logradouro"),
        payload.get("logradouro"),
        payload.get("numero"),
        payload.get("complemento"),
        payload.get("bairro"),
    ]
    endereco = ", ".join(str(p).strip() for p in partes if str(p or "").strip())
    cidade = " - ".join(str(p).strip() for p in (payload.get("municipio"), payload.get("uf")) if str(p or "").strip())
    return f"{endereco} - {cidade}" if endereco and cidade else endereco or cidade


def _inscricao_estadual_from_cnpj_ws(payload: dict) -> str:
    estabelecimento = payload.get("estabelecimento") if isinstance(payload, dict) else {}
    inscricoes = estabelecimento.get("inscricoes_estaduais") if isinstance(estabelecimento, dict) else []
    if not isinstance(inscricoes, list):
        return ""
    ativas = [ie for ie in inscricoes if str(ie.get("ativo", "")).lower() in {"true", "1", "sim"}]
    alvo = ativas[0] if ativas else (inscricoes[0] if inscricoes else {})
    return str(alvo.get("inscricao_estadual") or "").strip()


def consultar_cartao_cnpj(cnpj: str, timeout: int = 8) -> dict:
    doc = normalizar_documento(cnpj)
    if not cnpj_valido(doc):
        raise ValueError("Informe um CNPJ válido com 14 dígitos.")

    dados: dict = {"documento": formatar_cnpj(doc)}
    erros = []
    try:
        resp = requests.get(f"https://brasilapi.com.br/api/cnpj/v1/{doc}", timeout=timeout)
        resp.raise_for_status()
        payload = resp.json() or {}
        dados.update(
            {
                "razao_social": payload.get("razao_social") or "",
                "nome_fantasia": payload.get("nome_fantasia") or "",
                "documento": formatar_cnpj(payload.get("cnpj") or doc),
                "endereco": _endereco_from_payload(payload),
                "cep": payload.get("cep") or "",
                "municipio": payload.get("municipio") or "",
                "uf": payload.get("uf") or "",
                "telefone": payload.get("ddd_telefone_1") or payload.get("ddd_telefone_2") or "",
                "email": payload.get("email") or "",
                "cnae": str(payload.get("cnae_fiscal") or ""),
                "regime_tributario": "MEI" if payload.get("opcao_pelo_mei") else "Simples Nacional" if payload.get("opcao_pelo_simples") else "",
                "situacao_cadastral": payload.get("descricao_situacao_cadastral") or payload.get("situacao_cadastral") or "",
                "fonte_cnpj": "BrasilAPI",
            }
        )
    except Exception as exc:
        erros.append(str(exc))

    try:
        resp_ie = requests.get(f"https://publica.cnpj.ws/cnpj/{doc}", timeout=timeout)
        resp_ie.raise_for_status()
        ie = _inscricao_estadual_from_cnpj_ws(resp_ie.json() or {})
        if ie:
            dados["inscricao_estadual"] = ie
            dados["ie_consultada"] = "Sim"
    except Exception as exc:
        erros.append(str(exc))

    dados.setdefault("inscricao_estadual", "")
    dados.setdefault("ie_consultada", "Não encontrada" if not dados.get("inscricao_estadual") else "Sim")
    if erros and len(dados) <= 3:
        raise ValueError("Não foi possível consultar o cartão CNPJ agora.")
    return dados


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
    if solicitacao.departamento_atual not in {"Contabil", "Compras", "Fiscal"}:
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
                    mensagem=f"Solicitação {solicitacao.numero}: {acao}",
                )
            )


def sugerir_plano_contas_material(dados: dict | None, limite: int = 6) -> list[dict]:
    payload = dados or {}
    utilizacao = str(payload.get("utilizacao") or "").strip()
    descricao = normalizar_texto(payload.get("descricao"))
    fornecedor = normalizar_texto(payload.get("fornecedor_sugerido"))
    detalhe = normalizar_texto(payload.get("detalhe_utilizacao"))
    if not any([utilizacao, descricao, fornecedor, detalhe]):
        return []

    keywords_by_use = {
        "01": ["materia prima", "insumo", "producao"],
        "02": ["embalagem"],
        "07": ["uso e consumo", "consumo", "material", "manutencao", "escritorio", "limpeza"],
        "08": ["imobilizado", "ativo"],
        "09": ["servico", "prestacao"],
    }
    keywords = list(keywords_by_use.get(utilizacao, []))
    for origem in (descricao, fornecedor, detalhe):
        if not origem:
            continue
        tokens = [t for t in re.split(r"[^a-z0-9]+", origem) if len(t) >= 4]
        keywords.extend(tokens[:8])

    keywords_norm = []
    for word in keywords:
        norm = normalizar_texto(word)
        if norm and norm not in keywords_norm:
            keywords_norm.append(norm)

    rows = PlanoContaDominio.query.order_by(PlanoContaDominio.codigo_conta.asc()).limit(1500).all()
    ranked = []
    for row in rows:
        nome = normalizar_texto(row.nome_conta)
        classificacao = normalizar_texto(row.classificacao_conta)
        tipo = normalizar_texto(row.tipo_conta)
        search_blob = " ".join([nome, classificacao, tipo]).strip()
        if not search_blob:
            continue
        score = 0
        matched = []
        for word in keywords_norm:
            if word and word in search_blob:
                matched.append(word)
                score += max(6, min(len(word), 18))
        if utilizacao == "08" and "imobilizado" in search_blob:
            score += 20
        if utilizacao == "02" and "embalagem" in search_blob:
            score += 20
        if utilizacao == "07" and ("consumo" in search_blob or "manutencao" in search_blob):
            score += 16
        if descricao and descricao in search_blob:
            score += 24
        elif descricao:
            score += int(SequenceMatcher(None, descricao[:80], search_blob[:80]).ratio() * 10)
        if score <= 0:
            continue
        ranked.append(
            {
                "codigo": row.codigo_conta,
                "nome": row.nome_conta,
                "classificacao": row.classificacao_conta or "",
                "tipo": row.tipo_conta or "",
                "score": score,
                "motivo": ", ".join(matched[:4]),
            }
        )
    ranked.sort(key=lambda item: (-item["score"], item["codigo"]))
    return ranked[: max(1, min(int(limite or 6), 10))]


def montar_comentario_acao(acao: str, comentario: str, motivo_padrao: str = "") -> str:
    motivo = str(motivo_padrao or "").strip()
    comentario_limpo = str(comentario or "").strip()
    if motivo and comentario_limpo:
        return f"Motivo padrão: {motivo}. {comentario_limpo}"
    if motivo:
        return f"Motivo padrão: {motivo}."
    return comentario_limpo


def _buscar_materiais_grv_api(cfg: dict, codigo: str, descricao: str, limite: int = 12) -> list[dict]:
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "ngrok-skip-browser-warning": "true",
        "User-Agent": "ColumbiaSync/CadastroWorkflow",
    }
    if cfg.get("api_token"):
        headers["Authorization"] = f"Bearer {cfg['api_token']}"
    resp = requests.post(
        f"{cfg['api_url']}/api/erp/cadastro-workflow/materiais/buscar",
        headers=headers,
        json={"codigo": codigo, "descricao": descricao, "limite": limite},
        timeout=cfg.get("api_timeout") or 30,
    )
    resp.raise_for_status()
    payload = resp.json() or {}
    if not isinstance(payload, dict) or not payload.get("sucesso"):
        raise RuntimeError(str((payload or {}).get("erro") or "Falha ao consultar materiais no GRV"))
    rows = payload.get("materiais") or []
    return rows if isinstance(rows, list) else []


def _buscar_materiais_grv_postgres(cfg: dict, codigo: str, descricao: str, limite: int = 12) -> list[dict]:
    from .erp_lancamento_service import _conectar

    codigo_like = f"%{codigo}%" if codigo else ""
    desc_like = f"%{descricao}%" if descricao else ""
    filtros = []
    params: list = [1]
    if codigo:
        filtros.append("lower(coalesce(nullif(trim(p.codigo_interno), ''), p.codigo::text, '')) like %s")
        params.append(codigo_like)
    if descricao:
        filtros.append("lower(coalesce(nullif(trim(p.nome), ''), '')) like %s")
        params.append(desc_like)
    if not filtros:
        return []
    params.append(max(1, min(int(limite or 12), 30)))
    sql = f"""
        select
            p.codigo::text as codigo_grv,
            coalesce(nullif(trim(p.codigo_interno), ''), p.codigo::text) as codigo_material,
            coalesce(nullif(trim(p.nome), ''), '') as descricao,
            coalesce(nullif(trim(p.unidade), ''), nullif(trim(p.unidade_compra), ''), '') as unidade,
            coalesce(f.nome, '') as familia,
            coalesce(p.cod_grupo::text, '') as grupo,
            coalesce(nullif(trim(p.localizacao_estoque), ''), '') as localizacao_estoque,
            coalesce(p.inativo, 0) as inativo
        from public.tproduto p
        left join public.tfamilia f
          on f.cod_empresa = p.cod_empresa
         and f.codigo = p.cod_familia
        where p.cod_empresa = %s
          and coalesce(nullif(trim(p.nome), ''), '') <> ''
          and ({' or '.join(filtros)})
        order by coalesce(p.inativo, 0), p.codigo desc
        limit %s
    """
    with _conectar(cfg) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, tuple(params))
            cols = [desc[0] for desc in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]


def buscar_materiais_grv(codigo: str | None = None, descricao: str | None = None, limite: int = 12) -> list[dict]:
    from .erp_lancamento_service import _resolver_config

    codigo_limpo = normalizar_texto(codigo)
    descricao_limpa = normalizar_texto(descricao)
    if not codigo_limpo and not descricao_limpa:
        return []
    cfg = _resolver_config()
    try:
        if cfg.get("api_url"):
            return _buscar_materiais_grv_api(cfg, codigo_limpo, descricao_limpa, limite=limite)
    except Exception:
        current_app.logger.warning("Falha ao consultar materiais na bridge ERP para cadastro workflow", exc_info=True)
    if not cfg.get("host") or not cfg.get("database") or not cfg.get("user"):
        return []
    try:
        return _buscar_materiais_grv_postgres(cfg, codigo_limpo, descricao_limpa, limite=limite)
    except Exception:
        current_app.logger.warning("Falha ao consultar materiais no Postgres ERP para cadastro workflow", exc_info=True)
        return []


def _pontuar_material_grv(codigo: str, descricao: str, row: dict) -> tuple[str | None, float]:
    codigo_busca = normalizar_texto(codigo)
    descricao_busca = normalizar_texto(descricao)
    codigo_row = normalizar_texto(row.get("codigo_material") or row.get("codigo_grv"))
    descricao_row = normalizar_texto(row.get("descricao"))
    if codigo_busca and codigo_row and codigo_busca == codigo_row:
        return "Código já existe no GRV", 1.0
    if descricao_busca and descricao_row:
        if descricao_busca == descricao_row:
            return "Descrição igual no GRV", 0.99
        score = SequenceMatcher(None, descricao_busca, descricao_row).ratio()
        if descricao_busca in descricao_row or descricao_row in descricao_busca:
            score = max(score, 0.86)
        if score >= 0.62:
            return "Descrição semelhante no GRV", round(score, 2)
    return None, 0.0


def buscar_duplicidades(tipo: str, dados: dict, ignorar_id: int | None = None) -> list[dict]:
    tipo = (tipo or "").strip()
    documento = normalizar_documento(dados.get("documento"))
    codigo = (dados.get("codigo") or "").strip().lower()
    descricao = (dados.get("descricao") or "").strip().lower()
    if tipo == "material":
        encontrados = []
        for row in buscar_materiais_grv(codigo=codigo, descricao=descricao, limite=12):
            motivo, score = _pontuar_material_grv(codigo, descricao, row)
            if not motivo:
                continue
            encontrados.append(
                {
                    "codigo_material": row.get("codigo_material") or row.get("codigo_grv") or "",
                    "codigo_grv": row.get("codigo_grv") or "",
                    "descricao": row.get("descricao") or "",
                    "familia": row.get("familia") or "",
                    "grupo": row.get("grupo") or "",
                    "unidade": row.get("unidade") or "",
                    "localizacao_estoque": row.get("localizacao_estoque") or "",
                    "motivo": motivo,
                    "score": score,
                    "resumo": row.get("descricao") or row.get("codigo_material") or row.get("codigo_grv") or "",
                }
            )
        return encontrados
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
                motivo, score = "Documento já usado", 1.0
        elif tipo == "material":
            codigo_atual = (atual_dados.get("codigo") or "").strip().lower()
            desc_atual = (atual_dados.get("descricao") or "").strip().lower()
            if codigo and codigo_atual and codigo == codigo_atual:
                motivo, score = "Código já usado", 1.0
            elif descricao and desc_atual:
                score = SequenceMatcher(None, descricao, desc_atual).ratio()
                if score >= 0.72:
                    motivo = "Descrição semelhante"
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


def sugerir_ncm_material(descricao: str | None) -> dict:
    texto = (descricao or "").lower()
    regras = [
        (("parafuso", "porca", "arruela", "prisioneiro"), "73181500", "Parafusos, porcas, arruelas e artefatos semelhantes de ferro ou aço"),
        (("rolamento", "mancal"), "84821010", "Rolamentos de esferas"),
        (("correia", "esteira"), "40103900", "Correias transportadoras ou de transmissão de borracha vulcanizada"),
        (("motor elétrico", "motor eletrico"), "85015210", "Motores elétricos de corrente alternada"),
        (("sensor", "fim de curso", "chave indutiva"), "85365090", "Aparelhos para interrupção, seccionamento ou proteção de circuitos elétricos"),
        (("cabo elétrico", "cabo eletrico", "fio elétrico", "fio eletrico"), "85444900", "Fios e cabos elétricos isolados"),
        (("tinta", "verniz"), "32089010", "Tintas e vernizes à base de polímeros sintéticos"),
        (("óleo", "oleo", "lubrificante", "graxa"), "27101932", "Óleos lubrificantes"),
        (("embalagem", "caixa papelão", "caixa papelao", "papelão", "papelao"), "48191000", "Caixas de papel ou cartão ondulados"),
        (("etiqueta", "rótulo", "rotulo"), "48211000", "Etiquetas de papel ou cartão"),
        (("luva", "epi"), "40151900", "Luvas de borracha vulcanizada não endurecida"),
        (("fresa", "broca", "ferramenta corte"), "82075011", "Ferramentas de furar ou fresar para metais"),
        (("chapa aço", "chapa aco", "aço carbono", "aco carbono"), "72085100", "Produtos laminados planos de ferro ou aço não ligado"),
        (("tubo", "perfil aço", "perfil aco"), "73069090", "Tubos e perfis ocos de ferro ou aço"),
    ]
    for termos, ncm, justificativa in regras:
        if any(termo in texto for termo in termos):
            return {"ncm": ncm, "justificativa": justificativa}
    return {"ncm": "", "justificativa": "Sem sugestão automática. Fiscal deve classificar manualmente."}


def criar_solicitacao(tipo: str, dados: dict, solicitante: str, anexos: str = "") -> CadastroWorkflowSolicitacao:
    if tipo not in TIPOS_CADASTRO:
        raise ValueError("Tipo de cadastro inválido.")
    campos_obrigatorios = TIPOS_CADASTRO[tipo].get("solicitante_fields") or TIPOS_CADASTRO[tipo]["fields"]
    for campo, label, obrigatorio in campos_obrigatorios:
        if obrigatorio and not str(dados.get(campo) or "").strip():
            raise ValueError(f"Campo obrigatório: {label}.")
        opcoes = CAMPO_OPCOES.get(campo)
        if opcoes and str(dados.get(campo) or "").strip():
            valores_validos = {value for value, _label in opcoes}
            if str(dados.get(campo) or "").strip() not in valores_validos:
                raise ValueError(f"Valor inválido para {label}.")
    if tipo in CADASTROS_DIRETO_FISCAL:
        if not cnpj_valido(dados.get("documento")):
            raise ValueError("Informe um CNPJ válido com 14 dígitos.")
        dados = {
            "documento": formatar_cnpj(dados.get("documento")),
            "contribuinte_icms": str(dados.get("contribuinte_icms") or "").strip(),
        }
    elif tipo == "material":
        sugestao_ncm = sugerir_ncm_material(dados.get("descricao"))
        dados.setdefault("unidade_compra", dados.get("unidade_medida") or "")
        dados.setdefault("ncm_sugerido", sugestao_ncm["ncm"])
        dados.setdefault("ncm_sugestao_justificativa", sugestao_ncm["justificativa"])
    duplicidades = buscar_duplicidades(tipo, dados)
    if tipo in CADASTROS_DIRETO_FISCAL:
        departamento_inicial = "Fiscal"
        status_inicial = "Em Validacao Fiscal"
    elif tipo == "material":
        departamento_inicial = "Contabil"
        status_inicial = "Em Validacao Contabil"
    else:
        departamento_inicial = "Compras"
        status_inicial = "Em Validacao Compras"
    sol = CadastroWorkflowSolicitacao(
        numero=proximo_numero(),
        tipo=tipo,
        status=status_inicial,
        etapa_atual=departamento_inicial,
        solicitante=solicitante,
        departamento_atual=departamento_inicial,
        anexos=anexos,
        alerta_duplicidade=json.dumps(duplicidades, ensure_ascii=False) if duplicidades else None,
    )
    set_dados(sol, dados)
    db.session.add(sol)
    registrar_evento(sol, solicitante, "Solicitante", "Solicitação criada", f"Enviada para validação de {departamento_inicial}.")
    registrar_evento(sol, solicitante, "Solicitante", "Solicitação enviada", f"Fluxo iniciado em {departamento_inicial}.")
    db.session.flush()
    inicializar_checklists(sol)
    db.session.commit()
    return sol


def inicializar_checklists(solicitacao):
    existentes = {(c.departamento, c.item) for c in solicitacao.checklists}
    grupos = [("Fiscal", CHECKLIST_FISCAL_POR_TIPO.get(solicitacao.tipo, []))]
    if solicitacao.tipo == "material":
        grupos.insert(0, ("Compras", CHECKLIST_COMPRAS))
        grupos.insert(0, ("Contabil", CHECKLIST_CONTABIL_POR_TIPO.get(solicitacao.tipo, [])))
    elif solicitacao.tipo not in CADASTROS_DIRETO_FISCAL:
        grupos.insert(0, ("Compras", CHECKLIST_COMPRAS))
    for depto, itens in grupos:
        for item in itens:
            if (depto, item) not in existentes:
                db.session.add(CadastroWorkflowChecklist(solicitacao=solicitacao, departamento=depto, item=item))


def atualizar_checklist(solicitacao, departamento, valores: dict, usuario: str):
    inicializar_checklists(solicitacao)
    agora = datetime.now()
    permitidos = {"Sim", "Não", "Não se aplica", "Nao", "Nao se aplica"}
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


CATEGORIAS_VISIVEIS = [
    ("em_analise", "Em análise"),
    ("atendimento_contabil", "Em atendimento (Contábil)"),
    ("atendimento_compras", "Em atendimento (Compras)"),
    ("atendimento_fiscal", "Em atendimento (Fiscal)"),
    ("finalizado", "Cadastro finalizado"),
]


def categoria_visivel(solicitacao) -> str | None:
    """Deriva uma das 4 categorias do workflow (mesmo esquema visual da
    Conferencia de Expedicao) a partir do status/departamento/responsavel
    ja existentes - nao muda o modelo de status, so agrupa pra exibicao.
    Retorna None para os status de excecao (correcao/reprovado/cancelado),
    que continuam visiveis na lista e no filtro de status, so nao entram
    numa das 4 categorias principais."""
    if solicitacao.status == "Cadastrado":
        return "finalizado"
    if solicitacao.status == "Em Validacao Contabil":
        return "atendimento_contabil" if solicitacao.responsavel_atual else "em_analise"
    if solicitacao.status == "Em Validacao Compras":
        return "atendimento_compras" if solicitacao.responsavel_atual else "em_analise"
    if solicitacao.status == "Em Validacao Fiscal":
        return "atendimento_fiscal" if solicitacao.responsavel_atual else "em_analise"
    return None


def executar_acao(solicitacao, acao: str, usuario: str, role: str, comentario: str = "", form=None):
    form = form or {}
    comentario = montar_comentario_acao(acao, comentario, form.get("motivo_padrao", ""))
    depto_usuario = "Contábil" if role_contabil(role) else "Compras" if role == "Compras" else "Fiscal" if role == "Fiscal" else "Administrador" if role == "Admin" else "Solicitante"
    exige_comentario = {"corrigir", "devolver_solicitante", "devolver_compras", "devolver_contabil", "reprovar"}
    if acao in exige_comentario and not comentario:
        raise ValueError("Comentário obrigatório para esta ação.")

    # Atribui automaticamente o responsavel na primeira acao real que
    # Compras/Fiscal/Admin fizer sobre o chamado - a solicitacao sai de
    # "Em analise" pra "Em atendimento" no momento em que alguem realmente
    # mexe nela, sem depender de um botao "Atender" separado (que nao
    # travava nenhuma outra acao e so confundia na tela de detalhe).
    acoes_de_atendimento = {
        "salvar_dados", "atualizar_anexos", "consultar_cnpj", "aprovar_contabil", "aprovar_compras",
        "devolver_solicitante", "devolver_compras", "devolver_contabil", "finalizar", "reprovar",
    }
    if (
        acao in acoes_de_atendimento
        and (role in {"Compras", "Fiscal", "Admin"} or role_contabil(role))
        and solicitacao.departamento_atual in {"Contabil", "Compras", "Fiscal"}
        and not solicitacao.responsavel_atual
    ):
        solicitacao.responsavel_atual = usuario

    if acao == "assumir":
        if role not in {"Compras", "Fiscal", "Admin"} and not role_contabil(role):
            raise ValueError("Usuário sem permissão para assumir atendimento.")
        if solicitacao.departamento_atual not in {"Contabil", "Compras", "Fiscal"}:
            raise ValueError("Esta solicitação não está em fila de atendimento.")
        solicitacao.responsavel_atual = usuario
        registrar_evento(solicitacao, usuario, depto_usuario, "Atendimento assumido", comentario)
    elif acao == "salvar_dados":
        if role not in {"Compras", "Fiscal", "Admin"} and not role_contabil(role):
            raise ValueError("Usuário sem permissão para editar dados.")
        if role_contabil(role) and solicitacao.departamento_atual != "Contabil":
            raise ValueError("Contábil só pode editar solicitações na etapa Contábil.")
        if role == "Compras" and solicitacao.departamento_atual != "Compras":
            raise ValueError("Compras só pode editar solicitações na etapa de Compras.")
        if role == "Fiscal" and solicitacao.departamento_atual != "Fiscal":
            raise ValueError("Fiscal só pode editar solicitações na etapa Fiscal.")
        dados = get_dados(solicitacao)
        for key, value in form.items():
            if key.startswith("campo_"):
                dados[key.replace("campo_", "", 1)] = value
        set_dados(solicitacao, dados)
        registrar_evento(solicitacao, usuario, depto_usuario, "Dados atualizados", comentario or "Informações revisadas pelo departamento responsável.")
    elif acao == "atualizar_anexos":
        # A gravacao do arquivo em si (request.files) acontece na rota, que
        # tem acesso ao request - aqui so valida permissao e registra o
        # evento no historico, igual as demais acoes.
        if role not in {"Compras", "Fiscal", "Admin"} and not role_contabil(role):
            raise ValueError("Usuário sem permissão para atualizar anexos.")
        if role_contabil(role) and solicitacao.departamento_atual != "Contabil":
            raise ValueError("Contábil só pode atualizar anexos na etapa Contábil.")
        if role == "Compras" and solicitacao.departamento_atual != "Compras":
            raise ValueError("Compras só pode atualizar anexos na etapa de Compras.")
        if role == "Fiscal" and solicitacao.departamento_atual != "Fiscal":
            raise ValueError("Fiscal só pode atualizar anexos na etapa Fiscal.")
        registrar_evento(solicitacao, usuario, depto_usuario, "Anexos atualizados", comentario or "Anexo/observações atualizados.")
    elif acao == "consultar_cnpj":
        if role not in {"Fiscal", "Admin"}:
            raise ValueError("Somente Fiscal pode consultar o cartão CNPJ nesta etapa.")
        if solicitacao.departamento_atual != "Fiscal":
            raise ValueError("A consulta do CNPJ deve ser feita na etapa Fiscal.")
        if solicitacao.tipo not in CADASTROS_DIRETO_FISCAL:
            raise ValueError("Consulta CNPJ automática disponível para cliente, fornecedor e transportadora.")
        dados = get_dados(solicitacao)
        consulta = consultar_cartao_cnpj(dados.get("documento") or form.get("campo_documento") or "")
        atualizado = dict(dados)
        atualizado.update({k: v for k, v in consulta.items() if str(v or "").strip()})
        set_dados(solicitacao, atualizado)
        registrar_evento(solicitacao, usuario, "Fiscal", "Cartão CNPJ consultado", "Dados do cartão CNPJ e IE preenchidos automaticamente quando disponíveis.")
    elif acao == "aprovar_contabil":
        if role not in {"Admin"} and not role_contabil(role):
            raise ValueError("Somente Contábil pode encaminhar para Compras.")
        if solicitacao.departamento_atual != "Contabil":
            raise ValueError("Esta solicitação não está na etapa Contábil.")
        atualizar_checklist(solicitacao, "Contabil", form, usuario)
        mover_etapa(solicitacao, "Em Validacao Compras", "Compras", "Compras")
        registrar_evento(solicitacao, usuario, "Contábil", "Aprovado e encaminhado para Compras", comentario)
    elif acao == "aprovar_compras":
        if role not in {"Compras", "Admin"}:
            raise ValueError("Somente Compras pode encaminhar ao Fiscal.")
        if solicitacao.departamento_atual != "Compras":
            raise ValueError("Esta solicitação não está na etapa de Compras.")
        atualizar_checklist(solicitacao, "Compras", form, usuario)
        mover_etapa(solicitacao, "Em Validacao Fiscal", "Fiscal", "Fiscal")
        registrar_evento(solicitacao, usuario, "Compras", "Aprovado e encaminhado ao Fiscal", comentario)
    elif acao == "devolver_solicitante":
        if role not in {"Compras", "Fiscal", "Admin"}:
            raise ValueError("Usuário sem permissão para solicitar correção.")
        origem = solicitacao.departamento_atual
        mover_etapa(solicitacao, "Pendente de Correcao", "Solicitante", "Solicitante", solicitacao.solicitante)
        registrar_evento(solicitacao, usuario, depto_usuario, f"Devolvido para correção pelo solicitante ({origem})", comentario)
    elif acao == "devolver_compras":
        if role not in {"Fiscal", "Admin"}:
            raise ValueError("Somente Fiscal pode devolver para Compras.")
        if solicitacao.tipo in CADASTROS_DIRETO_FISCAL:
            raise ValueError("Este tipo de cadastro é tratado somente pelo Fiscal.")
        mover_etapa(solicitacao, "Em Validacao Compras", "Compras", "Compras")
        registrar_evento(solicitacao, usuario, "Fiscal", "Devolvido para Compras", comentario)
    elif acao == "devolver_contabil":
        if role not in {"Compras", "Fiscal", "Admin"}:
            raise ValueError("Somente Compras ou Fiscal podem devolver para Contábil.")
        if solicitacao.tipo != "material":
            raise ValueError("A etapa Contábil existe apenas para cadastro de material.")
        if solicitacao.departamento_atual not in {"Compras", "Fiscal"}:
            raise ValueError("Só é possível devolver para Contábil a partir de Compras ou Fiscal.")
        origem = solicitacao.departamento_atual
        mover_etapa(solicitacao, "Em Validacao Contabil", "Contabil", "Contabil")
        registrar_evento(solicitacao, usuario, depto_usuario, f"Devolvido para Contábil ({origem})", comentario)
    elif acao == "responder_correcao":
        if usuario != solicitacao.solicitante and role != "Admin":
            raise ValueError("Somente o solicitante pode responder à correção.")
        dados = get_dados(solicitacao)
        for key, value in form.items():
            if key.startswith("campo_"):
                dados[key.replace("campo_", "", 1)] = value
        set_dados(solicitacao, dados)
        if solicitacao.tipo in CADASTROS_DIRETO_FISCAL:
            destino = "Fiscal"
        elif solicitacao.tipo == "material":
            destino = "Contabil"
        else:
            destino = "Compras"
        mover_etapa(solicitacao, f"Em Validacao {destino}", destino, destino)
        registrar_evento(solicitacao, usuario, "Solicitante", f"Correção respondida para {destino}", comentario or "Dados corrigidos.")
    elif acao == "finalizar":
        if role not in {"Fiscal", "Admin"}:
            raise ValueError("Somente Fiscal pode finalizar cadastro.")
        if solicitacao.departamento_atual != "Fiscal":
            raise ValueError("Esta solicitação não está na etapa Fiscal.")
        atualizar_checklist(solicitacao, "Fiscal", form, usuario)
        mover_etapa(solicitacao, "Cadastrado", "Cadastro Concluído", "Concluido", usuario)
        solicitacao.concluido_em = datetime.now()
        registrar_evento(solicitacao, usuario, "Fiscal", "Cadastro concluído", comentario)
    elif acao == "reprovar":
        if role not in {"Compras", "Fiscal", "Admin"}:
            raise ValueError("Usuário sem permissão para reprovar.")
        mover_etapa(solicitacao, "Reprovado", "Encerrado", "Encerrado", usuario)
        registrar_evento(solicitacao, usuario, depto_usuario, "Solicitação reprovada", comentario)
    elif acao == "cancelar":
        if usuario != solicitacao.solicitante and role != "Admin":
            raise ValueError("Somente o solicitante pode cancelar.")
        if solicitacao.status == "Cadastrado":
            raise ValueError("Solicitação concluída não pode ser cancelada.")
        mover_etapa(solicitacao, "Cancelado", "Encerrado", "Encerrado", usuario)
        solicitacao.cancelado_em = datetime.now()
        registrar_evento(solicitacao, usuario, "Solicitante", "Solicitação cancelada", comentario or "Cancelada pelo solicitante.")
    else:
        raise ValueError("Ação inválida.")
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
