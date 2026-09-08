"""Parser do relatorio de Nesting (corte a laser/plasma) exportado em HTML
pelo software da maquina de corte (FastReport 5.0) - usado pro modulo de
Consumo de Chapa da Logistica.

O arquivo .HTML e' um export de tabela com todas as celulas em colspan/
rowspan muito granulares (grade base de ~102 colunas por pagina, cada
celula com largura de 1 a poucos pixels) - rotulo e valor quase nunca
ficam "colados" na mesma posicao relativa entre secoes diferentes do
relatorio, entao NAO da pra confiar em "olhar a celula X posicoes a
direita do rotulo" de forma generica.

Estrategia usada aqui (validada contra 3 relatorios reais da empresa,
15 paginas/nestings, 28 pecas, zero campo faltando):

1. Reconstroi cada <table> (1 por pagina/Nesting) numa GRADE (linha, coluna)
   -> texto, expandindo corretamente colspan E rowspan (`_grid_from_table`).
   Isso e' necessario porque o rotulo ("Hora:", "Peso Sucata Kg:", etc.)
   e o valor correspondente costumam estar em <tr> (linhas de origem)
   DIFERENTES, mas se sobrepoem na grade final por causa do rowspan.

2. Campos de cabecalho ("Programa:", "Material:", "Hora:", ...) sao
   procurados pelo ROTULO EXATO na grade; o valor e' a primeira celula
   nao-vazia numa coluna maior, dentro do intervalo de linhas onde o
   rotulo aparece (`_valor_apos_label`).

3. A lista de pecas cortadas dentro de cada Nesting NAO pode ser achada
   pela coluna do rotulo (o rotulo "OS-Orcamento" fica numa colspan
   diferente da coluna onde o VALOR "OS 1234 - 5678" realmente cai,
   porque a linha do rotulo/dado de cada peca "empurra" as colunas
   seguindo a ordem de leitura, nao a largura do cabecalho). Em vez
   disso, pra cada linha da grade, pega TODOS os valores distintos
   (colapsando colunas consecutivas iguais) em ordem de coluna, acha o
   indice onde bate "OS <numero>" (unico e inconfundivel), e usa a ORDEM
   POSICIONAL relativa - comprovada empiricamente e estavel nos 3
   relatorios testados:
       [..., Peca Nº, OS-Orcamento, Nome Peca, Qtd Req., Qtd Arr.,
        Peso Liq., Prox. Operacao, Cliente, ...]
"""
from __future__ import annotations

import re
from datetime import datetime

from bs4 import BeautifulSoup


def _grid_from_table(table) -> tuple[dict[tuple[int, int], str], int]:
    """Expande uma <table> HTML (com colspan/rowspan) numa grade
    (linha, coluna) -> texto. Retorna (grade, numero_de_linhas)."""
    grid: dict[tuple[int, int], str] = {}
    occupied: set[tuple[int, int]] = set()
    rows = table.find_all("tr", recursive=False)
    for r, tr in enumerate(rows):
        c = 0
        for td in tr.find_all(["td", "th"], recursive=False):
            while (r, c) in occupied:
                c += 1
            colspan = int(td.get("colspan", 1) or 1)
            rowspan = int(td.get("rowspan", 1) or 1)
            text = td.get_text(" ", strip=True)
            for rr in range(r, r + rowspan):
                for cc in range(c, c + colspan):
                    grid[(rr, cc)] = text
                    occupied.add((rr, cc))
            c += colspan
    return grid, len(rows)


def _valor_apos_label(grid: dict[tuple[int, int], str], label_exato: str, alcance_linhas: int = 6) -> str | None:
    """Acha o valor de um campo "Rotulo: valor" do FastReport. O rotulo e'
    uma celula com rowspan (ocupa varias linhas da grade); o valor de
    verdade pode estar em qualquer uma dessas linhas, numa coluna maior -
    nao necessariamente na mesma linha onde o rotulo comecou."""
    ocorrencias = [(r, c) for (r, c), t in grid.items() if t == label_exato]
    if not ocorrencias:
        return None
    linha_base = min(r for r, c in ocorrencias)
    col = min(c for r, c in ocorrencias if r == linha_base)
    linhas_label = {r for r, c in ocorrencias}
    candidatos = [
        (r, c, t) for (r, c), t in grid.items()
        if linha_base <= r < linha_base + alcance_linhas and c > col and t and t != label_exato
        and not (r in linhas_label and c <= max(cc for rr, cc in ocorrencias if rr == r))
    ]
    if not candidatos:
        return None
    candidatos.sort()
    return candidatos[0][2]


def _num_br(texto: str | None) -> float | None:
    """'2.034,40 Kg' / '94,08 %' / '4,75 mm' -> float. None se nao for numero."""
    if not texto:
        return None
    limpo = re.sub(r"[^\d,.\-]", "", texto)
    limpo = limpo.replace(".", "").replace(",", ".")
    try:
        return float(limpo)
    except ValueError:
        return None


def _data_br(texto: str | None):
    if not texto:
        return None
    try:
        return datetime.strptime(texto.strip(), "%d/%m/%Y").date()
    except ValueError:
        return None


def _parse_pagina(table) -> dict | None:
    grid, num_rows = _grid_from_table(table)
    max_col = max((c for (_r, c) in grid.keys()), default=0)

    programa = _valor_apos_label(grid, "Programa:")
    if not programa:
        return None  # tabela sem cabecalho de Nesting (ex.: pagina de rosto/capa)

    qtde_chapas_txt = None
    for (_r, _c), texto in grid.items():
        if re.match(r"^x\s*\d+$", texto or ""):
            qtde_chapas_txt = texto
            break

    nome_tarefa = None
    for (_r, _c), texto in grid.items():
        if texto and texto.startswith("Nome da Tarefa"):
            m = re.search(r":\s*(\S+)", texto)
            nome_tarefa = m.group(1) if m else texto
            break

    formato_nesting = None
    for (_r, _c), texto in grid.items():
        if re.match(r"^\d+ de \d+$", texto or ""):
            formato_nesting = texto
            break
    pagina_atual = pagina_total = None
    if formato_nesting:
        m = re.match(r"^(\d+) de (\d+)$", formato_nesting)
        if m:
            pagina_atual, pagina_total = int(m.group(1)), int(m.group(2))

    material = _valor_apos_label(grid, "Material:")
    codigo_material = None
    if material and "/" in material:
        codigo_material = material.rsplit("/", 1)[-1].strip()

    # ── Pecas cortadas dentro desse Nesting - ver estrategia no docstring ──
    pecas = []
    vistos = set()
    for r in range(num_rows):
        valores = []
        anterior = None
        for c in range(max_col + 1):
            texto = grid.get((r, c), "")
            if texto and texto != anterior:
                valores.append(texto)
            anterior = texto if texto else None
        os_idx = next((i for i, v in enumerate(valores) if re.match(r"^OS\s+\d+", v)), None)
        if os_idx is None or os_idx < 1 or os_idx + 6 >= len(valores):
            continue
        peca_numero = valores[os_idx - 1]
        os_texto = valores[os_idx]
        nome_peca = valores[os_idx + 1]
        chave = (os_texto, nome_peca, peca_numero)
        if chave in vistos:
            continue  # a mesma peca "vaza" pra linha seguinte da grade (rowspan) - so pega uma vez
        vistos.add(chave)

        os_numero = None
        m_os = re.match(r"^OS\s+(\d+)", os_texto)
        if m_os:
            os_numero = m_os.group(1)

        pecas.append({
            "peca_numero": peca_numero,
            "nome_peca": nome_peca,
            "qtd_requerida": _num_br(valores[os_idx + 2]),
            "qtd_arranjada": _num_br(valores[os_idx + 3]),
            "peso_liquido_kg": _num_br(valores[os_idx + 4]),
            "prox_operacao": valores[os_idx + 5],
            "cliente": valores[os_idx + 6],
            "os_orcamento": os_texto,
            "os_numero": os_numero,
        })

    return {
        "numero_programa": programa,
        "pagina_atual": pagina_atual,
        "pagina_total": pagina_total,
        "programador": _valor_apos_label(grid, "Programador:"),
        "maquina": _valor_apos_label(grid, "Máquina:"),
        "hora_corte": _valor_apos_label(grid, "Hora:"),
        "data_corte": _data_br(_valor_apos_label(grid, "Data:")),
        "material": material,
        "codigo_material": codigo_material,
        "espessura_mm": _num_br(_valor_apos_label(grid, "Espessura:")),
        "tempo_corte": _valor_apos_label(grid, "Tempo de Corte:"),
        "nome_tarefa": nome_tarefa,
        "qtde_chapas": int(_num_br(qtde_chapas_txt) or 0) if qtde_chapas_txt else None,
        "peso_sucata_kg": _num_br(_valor_apos_label(grid, "Peso Sucata Kg:")),
        "peso_pecas_kg": _num_br(_valor_apos_label(grid, "Peso Peças Kg:")),
        "peso_retalho_kg": _num_br(_valor_apos_label(grid, "Peso Retalho Kg:")),
        "peso_total_kg": _num_br(_valor_apos_label(grid, "Peso Total Kg:")),
        "aproveitamento_pct": _num_br(_valor_apos_label(grid, "Aproveitamento:")),
        "retalho_pct": _num_br(_valor_apos_label(grid, "Retalho:")),
        "sucata_pct": _num_br(_valor_apos_label(grid, "Sucata:")),
        "pecas": pecas,
    }


def parse_relatorio_nesting_html(conteudo_html: str | bytes) -> list[dict]:
    """Extrai todos os Nestings (1 por pagina) de um relatorio HTML de
    corte exportado pela maquina (FastReport). Retorna uma lista de dict,
    um por Nesting, cada um com o cabecalho do programa/material/pesos e a
    lista de pecas cortadas (`pecas`) - TODAS as linhas de OS, nao so a
    primeira."""
    if isinstance(conteudo_html, bytes):
        conteudo_html = conteudo_html.decode("utf-8", errors="replace")
    soup = BeautifulSoup(conteudo_html, "html.parser")
    resultado = []
    for table in soup.find_all("table"):
        pagina = _parse_pagina(table)
        if pagina:
            resultado.append(pagina)
    return resultado
