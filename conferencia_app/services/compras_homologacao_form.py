"""Definicao do formulario F-COM-001-01 - Homologacao de Fornecedores.

Transcrito do Excel que Compras usa hoje (aba "Planilha1" + a aba
"suporte", que e' o motor de pontuacao). Fica separado do service pra
deixar explicito QUAL e' o formulario vigente - se Compras revisar o
F-COM-001-01, e' aqui que muda.

Pontuacao (regra da aba "suporte"):
  - Cada secao tem um peso; o peso e' dividido igualmente entre os itens.
  - "Sim" e "Nao Aplicavel" valem o item inteiro; "Parcial" vale metade;
    "Nao" vale zero. Na Conformidade Legal, "Conforme" vale o item
    inteiro e "Nao Conforme" vale zero.
  - Nota final = soma de tudo (0 a 1) -> classificacao pelas faixas.

Obs.: a planilha tem duas formulas inconsistentes (o item 2 da
Conformidade Legal divide pelo peso da secao errada, e o item 1 da
Seguranca divide por 5 em vez de 3). Aqui aplicamos a regra pretendida
de forma uniforme em todas as secoes.
"""
from __future__ import annotations

CODIGO_FORMULARIO = "F-COM-001-01"
TITULO_FORMULARIO = "HOMOLOGAÇÃO DE FORNECEDORES"

# Escala de resposta das secoes de questionario.
RESPOSTA_SIM = "Sim"
RESPOSTA_PARCIAL = "Parcial"
RESPOSTA_NAO = "Nao"
RESPOSTA_NA = "Nao Aplicavel"
RESPOSTAS_QUESTIONARIO = (RESPOSTA_SIM, RESPOSTA_PARCIAL, RESPOSTA_NAO, RESPOSTA_NA)

# Escala da Conformidade Legal (documento entregue ou nao).
RESPOSTA_CONFORME = "Conforme"
RESPOSTA_NAO_CONFORME = "Nao Conforme"
RESPOSTAS_LEGAL = (RESPOSTA_CONFORME, RESPOSTA_NAO_CONFORME)

# No self assessment, o fornecedor que afirma atender (total ou parcialmente)
# precisa comprovar com evidencia anexada ao item.
RESPOSTAS_EXIGEM_EVIDENCIA = (RESPOSTA_SIM, RESPOSTA_PARCIAL, RESPOSTA_CONFORME)

# Quanto cada resposta vale do peso do item (0 a 1).
FATOR_RESPOSTA = {
    RESPOSTA_SIM: 1.0,
    RESPOSTA_NA: 1.0,        # nao aplicavel nao penaliza o fornecedor
    RESPOSTA_PARCIAL: 0.5,
    RESPOSTA_NAO: 0.0,
    RESPOSTA_CONFORME: 1.0,
    RESPOSTA_NAO_CONFORME: 0.0,
}

# Faixas de classificacao pela nota final (aba "suporte", C1:D3).
NOTA_MINIMA_APROVADO = 0.75001
NOTA_MINIMA_RESSALVAS = 0.55001
CLASSIFICACAO_APROVADO = "Aprovado"
CLASSIFICACAO_RESSALVAS = "Aprovado com ressalvas"
CLASSIFICACAO_REPROVADO = "Reprovado"

SECAO_LEGAL = "Conformidade Legal"
SECAO_QUALIDADE = "Gestao da Qualidade"
SECAO_ESTRUTURA = "Estrutura Fisica"
SECAO_SEGURANCA = "Seguranca do Trabalho"

OBS_CONFORMIDADE_LEGAL = (
    "Obs. Empresas que a columbia representa 50% ou mais de faturamento, E-social deve ser obrigatório o envio mensalmente, risco trabalhista, responsabilidade solidária em caso de encerramento das atividades."
)

# secao -> (peso, rotulo exibido, escala, perguntas)
SECOES = (
    {
        "chave": SECAO_LEGAL,
        "titulo": "Conformidade Legal",
        "peso": 0.20,
        "escala": RESPOSTAS_LEGAL,
        "itens": (
            "Cartão CNPJ",
            "Contrato Social",
            "Certidões Fiscais",
            "Certidões Trabalhistas",
            "Alvarás/Licenças",
        ),
    },
    {
        "chave": SECAO_QUALIDADE,
        "titulo": "Gestão da Qualidade",
        "peso": 0.50,
        "escala": RESPOSTAS_QUESTIONARIO,
        "itens": (
            "Empresa é certificada por um sistema de gestão da qualidade, ISO 9001 ou equivalente?",
            "São realizadas inspeções em etapas críticas do processo e nos produtos/serviços antes da entrega ao cliente?",
            "Os resultados das inspeções são registrados?",
            "A empresa tem condições de emitir um relatório da inspeção para cada produto e/ou serviço executado, com dados coincidentes com os resultados descritos no item anterior?",
            "Os produtos e/ou serviços possuem especificações documentadas para uso na inspeção?",
            "Existe um procedimento formal para comunicar , obter aprovação ou recusa formal do cliente antes de liberar produtos/serviços que não atendem às especificações?",
            "A identificação dos produtos enviados é realizada de acordo com o solicitado pelo cliente?",
            "As reclamações de clientes são registradas, analisadas quanto às causas e respondidas com ações corretivas dentro de prazos definidos?",
            "A empresa realizada a manuteção e calibração dos aparelhos de medição",
            "A empresa possui indicadores de desempenho da qualidade (KPIs) monitorados periodicamente?",
            "Os colaboradores recebem treinamento sobre os procedimentos de qualidade?",
            "Existe um processo formal para ações corretivas e preventivas?",
        ),
    },
    {
        "chave": SECAO_ESTRUTURA,
        "titulo": "Estrutura Física",
        "peso": 0.15,
        "escala": RESPOSTAS_QUESTIONARIO,
        "itens": (
            "A empresa possui instalações adequadas para o armazenamento de produtos?",
            "A empresa possui área destinada para inspeção de qualidade e RNC",
            "A empresa realizada a manuteção e calibração dos aparelhos de medição",
        ),
    },
    {
        "chave": SECAO_SEGURANCA,
        "titulo": "Segurança do Trabalho",
        "peso": 0.15,
        "escala": RESPOSTAS_QUESTIONARIO,
        "itens": (
            "A empresa possui o   Programa de Gerenciamento de Riscos (PGR). Relativo a segurança do trabalho?",
            "A empresa possui um processo de  GRO ( Gerenciamento de Riscos Ocupacionais)?",
            "A empresa possui gestão sobre entrega de EPI e EPC",
        ),
    },
)


def secao_por_chave(chave: str) -> dict | None:
    return next((s for s in SECOES if s["chave"] == chave), None)


def itens_do_formulario():
    """Devolve (secao, indice_1based, texto) de todos os itens, na ordem."""
    for secao in SECOES:
        for i, texto in enumerate(secao["itens"], start=1):
            yield secao["chave"], i, texto


def total_itens() -> int:
    return sum(len(s["itens"]) for s in SECOES)
