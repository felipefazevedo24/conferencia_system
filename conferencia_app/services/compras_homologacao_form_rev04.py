"""Definicao do formulario F 066 rev. 04 - Avaliacao de Fornecedor.

Transcrito da planilha "Check List de Avaliacao de Fornecedor Rev. 04"
(aba "Rev-04"). Substitui o F-COM-001-01 (compras_homologacao_form.py)
nas homologacoes NOVAS; as antigas continuam no formulario em que foram
preenchidas (ver compras_homologacao_service.formulario_da).

Pontuacao (celula M70 da planilha):
    nota = (Atende + Atende Parcial / 2) / (Atende + Atende Parcial + Nao Atende)
    - "Nao Aplicavel" sai da conta (nem soma nem divide).
    - Documentos: Atende / Nao Atende / Nao Aplicavel.
    - Faixas: > 80% Eficiente/Qualificado; > 50% Qualificado com plano de
      acao; ate 50% Desqualificado.

Regras de evidencia (definidas por Compras em 30/09/2026):
    - Os 8 documentos: "Atende" so' com a copia anexada; "Nao Aplicavel"
      exige justificativa.
    - As 15 questoes: sem evidencia anexada, "Atende" vale no maximo
      "Atende Parcial" - a Columbia precisa validar a aderencia.
    - ISO 9001 vigente: com o certificado anexado, o fornecedor nao responde
      o questionario, e a nota sai so' dos documentos.
"""
from __future__ import annotations

VERSAO = "F066-R04"
CODIGO_FORMULARIO = "F 066"
REVISAO = "04"
TITULO_FORMULARIO = "AVALIAÇÃO DE FORNECEDOR"

RESPOSTA_ATENDE = "Atende"
RESPOSTA_PARCIAL = "Atende Parcial"
RESPOSTA_NAO_ATENDE = "Não Atende"
RESPOSTA_NA = "Não Aplicável"
ESCALA_DOCUMENTOS = (RESPOSTA_ATENDE, RESPOSTA_NAO_ATENDE, RESPOSTA_NA)
ESCALA_QUESTIONARIO = (RESPOSTA_ATENDE, RESPOSTA_PARCIAL, RESPOSTA_NAO_ATENDE, RESPOSTA_NA)

NOTA_MINIMA_QUALIFICADO = 0.80  # acima disso (estritamente)
NOTA_MINIMA_PLANO_ACAO = 0.50   # acima disso (estritamente)
# Cabem no String(40) da coluna classificacao.
CLASSIFICACAO_QUALIFICADO = "Eficiente / Qualificado"
CLASSIFICACAO_PLANO_ACAO = "Qualificado c/ plano de ação"
CLASSIFICACAO_DESQUALIFICADO = "Desqualificado"

SECAO_DOCUMENTOS = "Documentacao"
SECAO_QUESTIONARIO = "Gestao da Qualidade R04"
# Evidencia do certificado ISO 9001 (secao/item "virtuais", fora da tabela).
SECAO_ISO = "ISO 9001"

# Evidencia por secao: "obrigatoria" trava o envio; "limita_parcial" so'
# rebaixa o "Atende" sem anexo na nota.
SECOES = (
    {
        "chave": SECAO_DOCUMENTOS,
        "titulo": "Documentação Necessária",
        "peso": None,
        "escala": ESCALA_DOCUMENTOS,
        "evidencia": "obrigatoria",
        "justificar_na": True,
        "pula_com_iso": False,
        "itens": (
            "Cópia do Contrato Social (estadual)",
            "Licença prévia (estadual)",
            "Licença de instalação (estadual)",
            "Licença de operação (estadual) ou Dispensa",
            "Alvará de licença ou funcionamento (municipal)",
            "Certificado de Aprovação/Destinação de Resíduos Industriais (CADRI)",
            "Certidão Negativa de Débitos - CND (estadual)",
            "Certidão Negativa de Débitos - CND (federal)",
        ),
    },
    {
        "chave": SECAO_QUESTIONARIO,
        "titulo": "Requisitos do Sistema de Gestão da Qualidade",
        "peso": None,
        "escala": ESCALA_QUESTIONARIO,
        "evidencia": "limita_parcial",
        "justificar_na": False,
        "pula_com_iso": True,
        "itens": (
            "A organização realiza suas atividades em local apropriado, organizado e identificado?",
            "Os produtos produzidos pela organização são controlados conforme suas especificações? Evidencie.",
            "Os requisitos relacionados ao produto ou serviço que a organização fornece está definido e são realizados os controles de atualização?",
            "A organização utiliza-se de um sistema de manutenção de seus equipamentos, afim de assegurar que o processo não seja interrompido por quebra ou deterioração, comprometendo os prazos de entrega de seus clientes?",
            "A organização realiza atividades padronizadas afim de garantir a inspeção do produto /ou serviço antes de fornecer ao seu cliente? Como são realizadas tais atividades?",
            "Como a organização controla os Produtos identificados como não-conforme, afim de não realizar o uso indevido?",
            "São utilizados Instrumentos de Medição para monitoramento no processo, produto ou serviço?",
            "A organização identifica e controla os seus registros internos de lotes entregues?",
            "Reclamações de clientes são atendidas e tratadas? Como?",
            "Os colaboradores são submetidos a treinamentos específicos do processo? Os treinamentos são registrados?",
            "Como é realizado o monitoramento dos fornecedores?",
            "A organização atende aos prazos acordados com o cliente?",
            "A organização mantém registros das inspeções realizadas no recebimento?",
            "A organização estabelece uma metodologia para inspeção de recebimento de matéria-prima?",
            "A organização mantém matéria-prima em local adequado de armazenagem e devidamente identificado?",
        ),
    },
)

# Responsaveis do fornecedor (quadro do cabecalho da planilha).
SETORES_RESPONSAVEIS = ("Diretoria (proprietário)", "Vendas", "Financeiro", "Qualidade")

TEXTO_FAIXAS = "> 80% Eficiente/Qualificado · > 50% Qualificado c/ plano de ação · até 50% Desqualificado"


def secao_por_chave(chave: str) -> dict | None:
    return next((s for s in SECOES if s["chave"] == chave), None)


def itens_do_formulario(iso_valido: bool = False):
    """(secao, indice_1based, texto) dos itens que precisam de resposta -
    com ISO 9001 comprovado, o questionario sai."""
    for secao in SECOES:
        if iso_valido and secao["pula_com_iso"]:
            continue
        for i, texto in enumerate(secao["itens"], start=1):
            yield secao["chave"], i, texto


def total_itens(iso_valido: bool = False) -> int:
    return sum(1 for _ in itens_do_formulario(iso_valido))


def classificar(nota: float) -> str:
    if nota > NOTA_MINIMA_QUALIFICADO:
        return CLASSIFICACAO_QUALIFICADO
    if nota > NOTA_MINIMA_PLANO_ACAO:
        return CLASSIFICACAO_PLANO_ACAO
    return CLASSIFICACAO_DESQUALIFICADO


def resposta_efetiva(secao: dict, resposta: str | None, tem_evidencia: bool) -> str | None:
    """O que a resposta vale na nota: no questionario, "Atende" sem anexo
    conta como "Atende Parcial"."""
    if secao["evidencia"] == "limita_parcial" and resposta == RESPOSTA_ATENDE and not tem_evidencia:
        return RESPOSTA_PARCIAL
    return resposta


def calcular_nota(respostas: dict, com_evidencia: set, iso_valido: bool = False) -> tuple[float, str, dict]:
    """respostas: {(secao, item): resposta}; com_evidencia: {(secao, item)}.
    Devolve (nota 0-1, classificacao, detalhe por secao)."""
    obtido_total = 0.0
    possivel_total = 0
    detalhe = {}
    for secao in SECOES:
        dispensada = iso_valido and secao["pula_com_iso"]
        contagem = {RESPOSTA_ATENDE: 0, RESPOSTA_PARCIAL: 0, RESPOSTA_NAO_ATENDE: 0, RESPOSTA_NA: 0}
        rebaixados = 0
        respondidos = 0
        for i in range(1, len(secao["itens"]) + 1):
            chave = (secao["chave"], i)
            resposta = respostas.get(chave)
            if not resposta:
                continue
            respondidos += 1
            efetiva = resposta_efetiva(secao, resposta, chave in com_evidencia)
            if efetiva != resposta:
                rebaixados += 1
            if efetiva in contagem:
                contagem[efetiva] += 1
        obtido = contagem[RESPOSTA_ATENDE] + contagem[RESPOSTA_PARCIAL] * 0.5
        possivel = contagem[RESPOSTA_ATENDE] + contagem[RESPOSTA_PARCIAL] + contagem[RESPOSTA_NAO_ATENDE]
        if not dispensada:
            obtido_total += obtido
            possivel_total += possivel
        detalhe[secao["chave"]] = {
            "titulo": secao["titulo"],
            "peso": None,
            "obtido": obtido,
            "possivel": possivel,
            "aproveitamento": round(obtido / possivel, 4) if possivel else 0.0,
            "respondidos": respondidos,
            "total_itens": len(secao["itens"]),
            "contagem": contagem,
            "rebaixados_sem_evidencia": rebaixados,
            "dispensada_por_iso": dispensada,
        }
    nota = round(obtido_total / possivel_total, 6) if possivel_total else 0.0
    return nota, classificar(nota), detalhe
