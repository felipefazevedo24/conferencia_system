"""Spend de Compras: gasto por classificação de suprimentos.

A base é a planilha de entradas do Custos (aba Base, uma linha por item de
NF, com a conta Domínio). O GRV não serve de fonte: lá cada item só tem uma
das ~17 contas genéricas do plano do GRV, que acertam 11% do valor da
classificação; a conta Domínio do Custos acerta 99% (medido em 05/11/2026
contra a planilha "Spend baseline Oct").

Classificação = exceção (conta + item) > de-para da conta > sem classificação.
É calculada na leitura, nunca gravada no item.
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict
from datetime import date, datetime

import openpyxl

from ..extensions import db
from ..models import (
    SpendClassificacaoConta,
    SpendClassificacaoExcecao,
    SpendCompraImportacao,
    SpendCompraItem,
)
from ..tempo import agora_br


SEM_CLASSIFICACAO = "(sem classificação)"
SEM_FAMILIA = "(sem família)"
TAMANHO_MAXIMO_ARQUIVO = 25 * 1024 * 1024

# Sufixo do CFOP de entrada (1/2/3) que é compra ou serviço. O resto — saída,
# retorno de conserto/industrialização, remessa, amostra, devolução, simples
# faturamento, "outras entradas" — fica guardado mas fora do Spend. Na base
# de jan–out/2026 isso era ~R$ 186 mil.
_CFOP_COMPRA = frozenset({
    "101", "102", "111", "113", "116", "117", "118", "120", "121", "122", "124", "125", "126", "128",
    "251", "252", "253", "254", "255", "256", "257",
    "301", "302", "303", "304", "305", "306",
    "351", "352", "353", "354", "355", "356", "360",
    "401", "403", "406", "407",
    "551", "556",
    "651", "652", "653",
    "933",
})

# Cabeçalho normalizado (sem acento, minúsculo, só letra/número) -> campo.
_COLUNAS = {
    "mes": ("mes",),
    "data_lancamento": ("lancamento",),
    "data_nf": ("datadanf",),
    "nota_fiscal": ("notafiscal",),
    "fornecedor": ("fornecedor",),
    "cfop": ("cfop",),
    "descricao_cfop": ("descricaocfop",),
    "codigo_item": ("codinternocodfabricante", "codinterno"),
    "descricao": ("descricao",),
    "valor_total": ("valortotal",),
    "ipi": ("valortotalipi",),
    "desconto": ("valordesconto",),
    "conta": ("conta",),
    "nome_conta": ("classificacaocontabil",),
    "centro_custo": ("ccdescricao", "descricaocc", "centrodecusto"),
    "familia": ("familiagrv",),
    "grupo": ("grupogrv",),
    "oc": ("oc",),
    "classificacao": ("classificacaodesuprimentos",),
}
_OBRIGATORIAS = ("data_lancamento", "nota_fiscal", "fornecedor", "cfop", "valor_total", "conta")


def cfop_e_compra(cfop) -> bool:
    digitos = re.sub(r"\D", "", str(cfop or ""))
    return len(digitos) == 4 and digitos[0] in "123" and digitos[1:] in _CFOP_COMPRA


def _norm_cabecalho(valor) -> str:
    texto = unicodedata.normalize("NFKD", str(valor or ""))
    texto = "".join(ch for ch in texto if not unicodedata.combining(ch))
    return "".join(ch for ch in texto.lower() if ch.isalnum())


def _texto(valor, limite: int) -> str:
    if valor is None:
        return ""
    if isinstance(valor, float) and valor.is_integer():
        valor = int(valor)
    return " ".join(str(valor).split())[:limite]


def _numero(valor) -> float:
    # Célula contábil vazia chega como " -   ".
    if isinstance(valor, (int, float)):
        return float(valor)
    texto = str(valor or "").strip().replace("R$", "").replace(" ", "")
    if not texto or texto == "-":
        return 0.0
    if "," in texto:
        texto = texto.replace(".", "").replace(",", ".")
    try:
        return float(texto)
    except ValueError:
        return 0.0


def _data(valor) -> date | None:
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    texto = str(valor or "").strip()
    for formato in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(texto[:10], formato).date()
        except ValueError:
            continue
    return None


def _norm_conta(valor) -> str:
    return _texto(valor, 30)


def _chave_item(codigo) -> str:
    return _texto(codigo, 80).upper()


def _limpa_classificacao(valor) -> str:
    return " ".join(str(valor or "").split())


def _competencia(mes_coluna, lancamento: date) -> str:
    """Mês do Custos, com o ano do lançamento. O Custos corta o fechamento
    alguns dias depois da virada (NF lançada 02/02 conta em janeiro), então
    o ano acompanha o mês mais próximo do lançamento."""
    try:
        mes = int(_numero(mes_coluna))
    except (TypeError, ValueError):
        mes = 0
    if not 1 <= mes <= 12:
        return f"{lancamento.year:04d}-{lancamento.month:02d}"
    ano = lancamento.year
    if mes - lancamento.month > 6:
        ano -= 1
    elif lancamento.month - mes > 6:
        ano += 1
    return f"{ano:04d}-{mes:02d}"


def _localizar_cabecalho(workbook):
    planilhas = sorted(workbook.worksheets, key=lambda ws: 0 if _norm_cabecalho(ws.title) == "base" else 1)
    for ws in planilhas:
        for numero, linha in enumerate(ws.iter_rows(min_row=1, max_row=15, values_only=True), start=1):
            nomes = [_norm_cabecalho(v) for v in linha]
            indices = {}
            for campo, aliases in _COLUNAS.items():
                for alias in aliases:
                    if alias in nomes:
                        indices[campo] = nomes.index(alias)
                        break
            if all(campo in indices for campo in _OBRIGATORIAS):
                return ws, numero, indices
    return None, None, None


def _ler_planilha(arquivo) -> tuple[list[dict], int]:
    try:
        workbook = openpyxl.load_workbook(arquivo, read_only=True, data_only=True)
    except Exception:
        raise ValueError("Não consegui abrir o arquivo. Envie a planilha em .xlsx.") from None
    try:
        ws, linha_cabecalho, indices = _localizar_cabecalho(workbook)
        if ws is None:
            raise ValueError(
                "Não achei a aba com as colunas Lançamento, Nota Fiscal, Fornecedor, CFOP, Valor Total e Conta."
            )

        def campo(linha, nome):
            idx = indices.get(nome)
            return linha[idx] if idx is not None and idx < len(linha) else None

        linhas, ignoradas = [], 0
        for linha in ws.iter_rows(min_row=linha_cabecalho + 1, values_only=True):
            if not any(v not in (None, "") for v in linha):
                continue
            lancamento = _data(campo(linha, "data_lancamento"))
            valor = _numero(campo(linha, "valor_total")) + _numero(campo(linha, "ipi")) - _numero(campo(linha, "desconto"))
            if lancamento is None or abs(valor) < 0.005:
                ignoradas += 1
                continue
            linhas.append({
                "competencia": _competencia(campo(linha, "mes"), lancamento),
                "data_lancamento": lancamento,
                "data_nf": _data(campo(linha, "data_nf")),
                "nota_fiscal": _texto(campo(linha, "nota_fiscal"), 60),
                "fornecedor": _texto(campo(linha, "fornecedor"), 200),
                "cfop": re.sub(r"\D", "", _texto(campo(linha, "cfop"), 20))[:10],
                "descricao_cfop": _texto(campo(linha, "descricao_cfop"), 200),
                "codigo_item": _texto(campo(linha, "codigo_item"), 80),
                "descricao": _texto(campo(linha, "descricao"), 400),
                "valor": round(valor, 2),
                "conta": _norm_conta(campo(linha, "conta")),
                "nome_conta": _texto(campo(linha, "nome_conta"), 200),
                "centro_custo": _texto(campo(linha, "centro_custo"), 200),
                "familia": _texto(campo(linha, "familia"), 160),
                "grupo": _texto(campo(linha, "grupo"), 160),
                "oc": _texto(campo(linha, "oc"), 120),
                "classificacao": _limpa_classificacao(campo(linha, "classificacao"))[:120],
            })
        return linhas, ignoradas
    finally:
        workbook.close()


def _aprender_classificacao(linhas: list[dict], usuario: str) -> tuple[int, int]:
    """Se a planilha trouxer a coluna Classificação de Suprimentos, preenche o
    de-para das contas que ainda não têm regra (e as exceções delas). Conta
    que já tem regra no Sync fica intocada: senão um arquivo antigo desfaria,
    via exceção, uma correção feita na tela."""
    por_conta: dict[str, Counter] = defaultdict(Counter)
    por_item: dict[tuple, Counter] = defaultdict(Counter)
    descricoes: dict[tuple, str] = {}
    for linha in linhas:
        classificacao, conta = linha["classificacao"], linha["conta"]
        if not classificacao or not conta:
            continue
        por_conta[conta][classificacao] += 1
        item = _chave_item(linha["codigo_item"])
        if item:
            por_item[(conta, item)][classificacao] += 1
            descricoes.setdefault((conta, item), linha["descricao"])

    existentes = {c.conta for c in SpendClassificacaoConta.query.with_entities(SpendClassificacaoConta.conta)}
    aprendidas: dict[str, str] = {}
    for conta, contagem in por_conta.items():
        if conta in existentes:
            continue
        aprendidas[conta] = contagem.most_common(1)[0][0]
        db.session.add(SpendClassificacaoConta(conta=conta, classificacao=aprendidas[conta], atualizado_por=usuario))

    excecoes_existentes = {
        (e.conta, _chave_item(e.codigo_item))
        for e in SpendClassificacaoExcecao.query.with_entities(SpendClassificacaoExcecao.conta, SpendClassificacaoExcecao.codigo_item)
    }
    novas_excecoes = 0
    for (conta, item), contagem in por_item.items():
        if conta not in aprendidas or (conta, item) in excecoes_existentes:
            continue
        classificacao = contagem.most_common(1)[0][0]
        if classificacao == aprendidas[conta]:
            continue
        db.session.add(SpendClassificacaoExcecao(
            conta=conta, codigo_item=item, descricao=descricoes.get((conta, item), ""),
            classificacao=classificacao, atualizado_por=usuario,
        ))
        novas_excecoes += 1
    return len(aprendidas), novas_excecoes


def importar_planilha(arquivo, usuario: str) -> dict:
    if arquivo is None or not getattr(arquivo, "filename", ""):
        raise ValueError("Escolha a planilha.")
    nome = arquivo.filename
    if not nome.lower().endswith((".xlsx", ".xlsm")):
        raise ValueError("Envie a planilha em .xlsx.")
    arquivo.stream.seek(0, 2)
    tamanho = arquivo.stream.tell()
    arquivo.stream.seek(0)
    if tamanho > TAMANHO_MAXIMO_ARQUIVO:
        raise ValueError("Arquivo acima de 25 MB.")

    linhas, ignoradas = _ler_planilha(arquivo.stream)
    if not linhas:
        raise ValueError("Nenhuma linha com lançamento e valor na planilha.")

    competencias = sorted({l["competencia"] for l in linhas})
    try:
        importacao = SpendCompraImportacao(
            arquivo=nome[:255],
            competencias=", ".join(competencias)[:500],
            linhas=len(linhas),
            valor_total=round(sum(l["valor"] for l in linhas), 2),
            importado_por=usuario,
        )
        db.session.add(importacao)
        db.session.flush()
        removidas = SpendCompraItem.query.filter(SpendCompraItem.competencia.in_(competencias)).delete(
            synchronize_session=False
        )
        campos_item = {c.name for c in SpendCompraItem.__table__.columns} - {"id"}
        registros = [{**{k: v for k, v in l.items() if k in campos_item}, "importacao_id": importacao.id} for l in linhas]
        # Em lotes: um INSERT so' com o ano inteiro passa do max_allowed_packet do MySQL.
        for inicio in range(0, len(registros), 1000):
            db.session.bulk_insert_mappings(SpendCompraItem, registros[inicio:inicio + 1000])
        contas_aprendidas, excecoes_aprendidas = _aprender_classificacao(linhas, usuario)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise

    return {
        "arquivo": nome,
        "competencias": competencias,
        "linhas": len(linhas),
        "ignoradas": ignoradas,
        "substituidas": removidas,
        "contas_aprendidas": contas_aprendidas,
        "excecoes_aprendidas": excecoes_aprendidas,
    }


def _classificador():
    contas = {c.conta: c.classificacao for c in SpendClassificacaoConta.query.all()}
    excecoes = {(e.conta, _chave_item(e.codigo_item)): e.classificacao for e in SpendClassificacaoExcecao.query.all()}

    def classificar(conta, codigo_item) -> str:
        return excecoes.get((conta, _chave_item(codigo_item))) or contas.get(conta) or SEM_CLASSIFICACAO

    return classificar


def _competencia_valida(valor) -> str | None:
    texto = str(valor or "").strip()
    return texto if re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", texto) else None


def _ultima_importacao() -> dict | None:
    ultima = SpendCompraImportacao.query.order_by(SpendCompraImportacao.id.desc()).first()
    if not ultima:
        return None
    return {
        "arquivo": ultima.arquivo,
        "competencias": ultima.competencias,
        "linhas": ultima.linhas,
        "importado_por": ultima.importado_por,
        "importado_em": ultima.importado_em.isoformat() if ultima.importado_em else None,
    }


def painel(de=None, ate=None, familia=None, classificacao=None, atual=None, anterior=None) -> dict:
    competencias = [
        c for (c,) in db.session.query(SpendCompraItem.competencia).distinct().order_by(SpendCompraItem.competencia)
    ]
    if not competencias:
        return {"vazio": True, "competencias": [], "ultima_importacao": _ultima_importacao()}

    de = _competencia_valida(de) or competencias[max(0, len(competencias) - 12)]
    ate = _competencia_valida(ate) or competencias[-1]
    if de > ate:
        de, ate = ate, de
    familia = (familia or "").strip() or None
    classificacao = (classificacao or "").strip() or None
    meses = [c for c in competencias if de <= c <= ate]

    rows = (
        db.session.query(
            SpendCompraItem.competencia, SpendCompraItem.fornecedor, SpendCompraItem.nota_fiscal,
            SpendCompraItem.cfop, SpendCompraItem.codigo_item, SpendCompraItem.descricao,
            SpendCompraItem.valor, SpendCompraItem.conta, SpendCompraItem.nome_conta, SpendCompraItem.familia,
        )
        .filter(SpendCompraItem.competencia >= de, SpendCompraItem.competencia <= ate)
        .all()
    )
    classificar = _classificador()

    fora = {"valor": 0.0, "itens": 0}
    familias, classificacoes = set(), set()
    sem_classificacao: dict[str, dict] = {}
    itens = []
    for r in rows:
        if not cfop_e_compra(r.cfop):
            fora["valor"] += r.valor or 0
            fora["itens"] += 1
            continue
        cls = classificar(r.conta, r.codigo_item)
        fam = r.familia or SEM_FAMILIA
        familias.add(fam)
        classificacoes.add(cls)
        if cls == SEM_CLASSIFICACAO:
            info = sem_classificacao.setdefault(r.conta or "", {"conta": r.conta or "", "nome_conta": r.nome_conta or "", "valor": 0.0, "itens": 0})
            info["valor"] += r.valor or 0
            info["itens"] += 1
        if familia and fam != familia:
            continue
        if classificacao and cls != classificacao:
            continue
        itens.append((r, cls, fam))

    total = sum(r.valor or 0 for r, _, _ in itens)

    def pct(valor):
        return (valor / total) if total else 0.0

    por_mes = {m: 0.0 for m in meses}
    por_cls: Counter = Counter()
    consolidado: Counter = Counter()
    por_fornecedor: Counter = Counter()
    nfs_fornecedor: dict[str, set] = defaultdict(set)
    for r, cls, fam in itens:
        valor = r.valor or 0
        por_mes[r.competencia] = por_mes.get(r.competencia, 0.0) + valor
        por_cls[cls] += valor
        consolidado[(fam, cls, r.descricao or "(sem descrição)")] += valor
        fornecedor = r.fornecedor or "(sem fornecedor)"
        por_fornecedor[fornecedor] += valor
        nfs_fornecedor[fornecedor].add(r.nota_fiscal or "")

    fornecedores, acumulado = [], 0.0
    for nome, valor in por_fornecedor.most_common():
        acumulado += valor
        participacao = pct(acumulado)
        fornecedores.append({
            "fornecedor": nome, "valor": round(valor, 2), "pct": pct(valor), "acumulado": participacao,
            "curva": "A" if participacao <= 0.8 or not fornecedores else ("B" if participacao <= 0.95 else "C"),
            "nfs": len(nfs_fornecedor[nome]),
        })

    atual = _competencia_valida(atual) if _competencia_valida(atual) in meses else meses[-1] if meses else None
    if _competencia_valida(anterior) in meses and anterior != atual:
        anterior = _competencia_valida(anterior)
    else:
        anteriores = [m for m in meses if atual and m < atual]
        anterior = anteriores[-1] if anteriores else None
    comp_atual: Counter = Counter()
    comp_anterior: Counter = Counter()
    for r, cls, _ in itens:
        if r.competencia == atual:
            comp_atual[cls] += r.valor or 0
        elif r.competencia == anterior:
            comp_anterior[cls] += r.valor or 0
    comparativo = []
    for cls in set(comp_atual) | set(comp_anterior):
        a, b = comp_anterior.get(cls, 0.0), comp_atual.get(cls, 0.0)
        comparativo.append({
            "classificacao": cls, "anterior": round(a, 2), "atual": round(b, 2),
            "variacao": round(b - a, 2), "variacao_pct": ((b - a) / a) if a else None,
        })
    comparativo.sort(key=lambda c: -max(c["atual"], c["anterior"]))

    meses_com_valor = [v for v in por_mes.values() if v]
    return {
        "vazio": False,
        "competencias": competencias,
        "filtro": {"de": de, "ate": ate, "familia": familia, "classificacao": classificacao, "atual": atual, "anterior": anterior},
        "familias": sorted(familias),
        "classificacoes": sorted(classificacoes),
        "kpis": {
            "total": round(total, 2),
            "media_mensal": round(sum(meses_com_valor) / len(meses_com_valor), 2) if meses_com_valor else 0.0,
            "fornecedores": len(por_fornecedor),
            "itens": len(itens),
            "nfs": len({(r.fornecedor, r.nota_fiscal) for r, _, _ in itens}),
        },
        "por_mes": [{"competencia": m, "valor": round(v, 2)} for m, v in sorted(por_mes.items())],
        "por_classificacao": [{"classificacao": c, "valor": round(v, 2), "pct": pct(v)} for c, v in por_cls.most_common()],
        "consolidado": [
            {"familia": f, "classificacao": c, "descricao": d, "valor": round(v, 2), "pct": pct(v)}
            for (f, c, d), v in consolidado.most_common()
        ],
        "fornecedores": fornecedores,
        "comparativo": comparativo,
        "sem_classificacao": sorted(
            ({**s, "valor": round(s["valor"], 2)} for s in sem_classificacao.values()), key=lambda s: -s["valor"]
        ),
        "fora_spend": {"valor": round(fora["valor"], 2), "itens": fora["itens"]},
        "ultima_importacao": _ultima_importacao(),
    }


def listar_contas() -> dict:
    """Contas que aparecem na base, com a regra atual de cada uma."""
    mapa = {c.conta: c for c in SpendClassificacaoConta.query.all()}
    excecoes_por_conta = Counter(e.conta for e in SpendClassificacaoExcecao.query.with_entities(SpendClassificacaoExcecao.conta))
    agregado: dict[str, dict] = {}
    for conta, nome_conta, valor, cfop in db.session.query(
        SpendCompraItem.conta, SpendCompraItem.nome_conta, SpendCompraItem.valor, SpendCompraItem.cfop
    ):
        if not cfop_e_compra(cfop):
            continue
        info = agregado.setdefault(conta or "", {"conta": conta or "", "nomes": Counter(), "valor": 0.0, "itens": 0})
        info["nomes"][nome_conta or ""] += 1
        info["valor"] += valor or 0
        info["itens"] += 1
    for conta in mapa:
        agregado.setdefault(conta, {"conta": conta, "nomes": Counter(), "valor": 0.0, "itens": 0})

    contas = []
    for conta, info in agregado.items():
        regra = mapa.get(conta)
        contas.append({
            "conta": conta,
            "nome_conta": info["nomes"].most_common(1)[0][0] if info["nomes"] else "",
            "valor": round(info["valor"], 2),
            "itens": info["itens"],
            "classificacao": regra.classificacao if regra else None,
            "atualizado_por": regra.atualizado_por if regra else None,
            "excecoes": excecoes_por_conta.get(conta, 0),
        })
    contas.sort(key=lambda c: (c["classificacao"] is not None, -c["valor"]))
    return {"contas": contas, "classificacoes": _nomes_classificacao()}


def _nomes_classificacao() -> list[str]:
    nomes = {c for (c,) in db.session.query(SpendClassificacaoConta.classificacao).distinct()}
    nomes |= {c for (c,) in db.session.query(SpendClassificacaoExcecao.classificacao).distinct()}
    return sorted(nomes, key=lambda n: n.lower())


def itens_da_conta(conta) -> dict:
    conta = _norm_conta(conta)
    regra = SpendClassificacaoConta.query.filter_by(conta=conta).first()
    excecoes = {_chave_item(e.codigo_item): e for e in SpendClassificacaoExcecao.query.filter_by(conta=conta)}
    agregado: dict[str, dict] = {}
    for codigo, descricao, valor, cfop in db.session.query(
        SpendCompraItem.codigo_item, SpendCompraItem.descricao, SpendCompraItem.valor, SpendCompraItem.cfop
    ).filter(SpendCompraItem.conta == conta):
        if not cfop_e_compra(cfop):
            continue
        chave = _chave_item(codigo)
        info = agregado.setdefault(chave, {"codigo_item": codigo or "", "descricoes": Counter(), "valor": 0.0, "itens": 0})
        info["descricoes"][descricao or ""] += 1
        info["valor"] += valor or 0
        info["itens"] += 1
    itens = []
    for chave, info in agregado.items():
        excecao = excecoes.get(chave)
        itens.append({
            "codigo_item": info["codigo_item"],
            "descricao": info["descricoes"].most_common(1)[0][0],
            "valor": round(info["valor"], 2),
            "itens": info["itens"],
            "excecao": excecao.classificacao if excecao else None,
        })
    itens.sort(key=lambda i: -i["valor"])
    return {"conta": conta, "classificacao": regra.classificacao if regra else None, "itens": itens}


def salvar_classificacao_conta(conta, classificacao, usuario: str) -> None:
    conta = _norm_conta(conta)
    classificacao = _limpa_classificacao(classificacao)
    if not conta:
        raise ValueError("Informe a conta.")
    if len(classificacao) > 120:
        raise ValueError("Classificação com mais de 120 caracteres.")
    regra = SpendClassificacaoConta.query.filter_by(conta=conta).first()
    if not classificacao:
        if regra:
            db.session.delete(regra)
    else:
        if regra is None:
            regra = SpendClassificacaoConta(conta=conta)
            db.session.add(regra)
        regra.classificacao = classificacao
        regra.atualizado_por = usuario
        regra.atualizado_em = agora_br()
    db.session.commit()


def salvar_excecao(conta, codigo_item, classificacao, usuario: str) -> None:
    """Classificação vazia, ou igual à da conta, remove a exceção."""
    conta = _norm_conta(conta)
    item = _chave_item(codigo_item)
    classificacao = _limpa_classificacao(classificacao)
    if not conta or not item:
        raise ValueError("Informe a conta e o item.")
    if len(classificacao) > 120:
        raise ValueError("Classificação com mais de 120 caracteres.")
    excecao = next(
        (e for e in SpendClassificacaoExcecao.query.filter_by(conta=conta) if _chave_item(e.codigo_item) == item),
        None,
    )
    regra = SpendClassificacaoConta.query.filter_by(conta=conta).first()
    if not classificacao or (regra and regra.classificacao == classificacao):
        if excecao:
            db.session.delete(excecao)
    else:
        if excecao is None:
            descricao = (
                db.session.query(SpendCompraItem.descricao)
                .filter(SpendCompraItem.conta == conta, db.func.upper(SpendCompraItem.codigo_item) == item)
                .limit(1)
                .scalar()
            )
            excecao = SpendClassificacaoExcecao(conta=conta, codigo_item=item, descricao=descricao or "")
            db.session.add(excecao)
        excecao.classificacao = classificacao
        excecao.atualizado_por = usuario
        excecao.atualizado_em = agora_br()
    db.session.commit()
