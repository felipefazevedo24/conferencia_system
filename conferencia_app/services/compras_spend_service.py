"""Spend de Compras: gasto por classificação de suprimentos, direto do GRV.

A base é o espelho das entradas de NF do GRV (SpendEntradaGrv). A tela lê o
espelho e, quando ele passa de IDADE_MAXIMA_MIN, pede a atualização por
trás: ninguém importa planilha.

Entra no Spend: CFOP de compra/serviço de fornecedor de fora do grupo. O
resto (retorno, remessa, devolução e a importação de máquina do Grupo
Columbia, ~R$ 2,6 mi em jan–out/2026) fica no espelho e aparece à parte.

Classificação: o GRV não tem a conta Domínio que o Custos usa (o plano do
GRV tem ~17 contas genéricas, que sozinhas acertam 46% do valor). Ela é
aprendida de exemplos (SpendClassificacaoExemplo), do mais específico para
o mais genérico — ver _NIVEIS. Medido contra a planilha "Spend baseline
Oct": 98% do valor nos meses dela e ~85% num mês novo, sem correção. Fora
do primeiro nível é palpite e vai para "A revisar"; confirmar ou corrigir
vira exemplo manual, que ganha de tudo para aquele fornecedor + item.
É calculada na leitura, nunca gravada no item.
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from ..compras import db as grv
from ..compras import queries
from ..compras.config import get_settings
from ..extensions import db
from ..models import SpendClassificacaoExemplo as Exemplo
from ..models import SpendEntradaGrv as Entrada
from ..models import SpendEntradaGrvSync as Sync
from ..tempo import agora_br


SEM_CLASSIFICACAO = "(sem classificação)"
SEM_FAMILIA = "(sem família)"
INICIO = date(2026, 1, 1)
IDADE_MAXIMA_MIN = 60
# Atualização "em andamento" há mais tempo que isso caiu no meio (worker
# reiniciado): deixa outra começar.
ANDAMENTO_EXPIRA_MIN = 15
SEMENTE = Path(__file__).resolve().parents[1] / "data" / "spend_classificacao_planilha.json"

# Sufixo do CFOP de entrada (1/2/3) que é compra ou serviço. O resto — saída,
# retorno de conserto/industrialização, remessa, amostra, devolução, simples
# faturamento, "outras entradas" — fica guardado mas fora do Spend.
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
# Compra de dentro do grupo (máquina importada da Columbia) não é spend de
# suprimentos; o Custos tira da base dele.
_INTERCOMPANY = "COLUMBIA MACHINE"


def cfop_e_compra(cfop) -> bool:
    digitos = _digitos(cfop)
    return len(digitos) == 4 and digitos[0] in "123" and digitos[1:] in _CFOP_COMPRA


def e_intercompany(fornecedor) -> bool:
    return _INTERCOMPANY in str(fornecedor or "").upper()


def _digitos(valor) -> str:
    return re.sub(r"\D", "", str(valor or ""))


def _texto(valor, limite: int) -> str:
    if valor is None:
        return ""
    return " ".join(str(valor).split())[:limite]


def _data(valor) -> date | None:
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    try:
        return date.fromisoformat(str(valor or "")[:10])
    except ValueError:
        return None


def _limpa_classificacao(valor) -> str:
    return " ".join(str(valor or "").split())


def _competencia_valida(valor) -> str | None:
    texto = str(valor or "").strip()
    return texto if re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", texto) else None


# ---------------------------------------------------------------------------
# Espelho das entradas do GRV
# ---------------------------------------------------------------------------
def _ultima(situacao: str | None = None):
    q = Sync.query
    if situacao:
        q = q.filter(Sync.status == situacao)
    return q.order_by(Sync.id.desc()).first()


def status() -> dict:
    ok = _ultima(Sync.STATUS_OK)
    ultima = _ultima()
    agora = agora_br()
    andamento = bool(
        ultima and ultima.status == Sync.STATUS_ANDAMENTO
        and ultima.iniciado_em > agora - timedelta(minutes=ANDAMENTO_EXPIRA_MIN)
    )
    idade = int((agora - ok.concluido_em).total_seconds() // 60) if ok and ok.concluido_em else None
    return {
        "atualizado_em": ok.concluido_em.strftime("%d/%m/%Y %H:%M") if ok and ok.concluido_em else None,
        "idade_min": idade,
        "vazio": ok is None,
        "desatualizado": ok is None or idade is None or idade >= IDADE_MAXIMA_MIN,
        "em_andamento": andamento,
        "erro": ultima.erro if ultima and ultima.status == Sync.STATUS_ERRO else None,
    }


def _meses(de: date, ate: date):
    """Janelas [primeiro dia do mês, primeiro dia do mês seguinte) de `de` até `ate`."""
    ini = de.replace(day=1)
    while ini <= ate:
        fim = (ini.replace(day=28) + timedelta(days=4)).replace(day=1)
        yield ini, fim
        ini = fim


def _linha(r: dict) -> dict | None:
    lancamento = _data(r.get("data_lancamento"))
    if lancamento is None:
        return None
    cod_fornecedor = r.get("cod_fornecedor")
    return {
        "competencia": f"{lancamento.year:04d}-{lancamento.month:02d}",
        "data_lancamento": lancamento,
        "data_nf": _data(r.get("data_nf")),
        "nota_fiscal": _texto(r.get("nota_fiscal"), 60),
        "cod_fornecedor": int(cod_fornecedor) if cod_fornecedor not in (None, "") else None,
        "fornecedor": _texto(r.get("fornecedor"), 200),
        "cfop": _digitos(r.get("cfop"))[:10],
        "codigo_item": _texto(r.get("codigo_item"), 80).upper(),
        "descricao": _texto(r.get("descricao"), 400),
        "valor": float(r.get("valor") or 0),
        "conta": _texto(r.get("conta"), 60),
        "nome_conta": _texto(r.get("nome_conta"), 200),
        "familia": _texto(r.get("familia"), 160),
        "grupo": _texto(r.get("grupo"), 160),
    }


def _semear() -> int:
    """Carrega a semente da planilha do Custos uma única vez."""
    if Exemplo.query.filter_by(origem=Exemplo.ORIGEM_PLANILHA).first():
        return 0
    dados = json.loads(SEMENTE.read_text(encoding="utf-8"))
    agora = agora_br()
    linhas = [
        {**dict(zip(dados["colunas"], valores)), "origem": Exemplo.ORIGEM_PLANILHA,
         "atualizado_por": "planilha Spend baseline Oct", "atualizado_em": agora}
        for valores in dados["linhas"]
    ]
    for i in range(0, len(linhas), 1000):
        db.session.bulk_insert_mappings(Exemplo, linhas[i:i + 1000])
    return len(linhas)


def atualizar(usuario: str) -> dict:
    """Regrava do GRV as entradas lançadas desde o mês anterior ao da última
    atualização boa (na primeira, desde INICIO): pega NF lançada com atraso
    e estorno do mês que acabou de fechar. Se outra já estiver rodando, não
    começa outra."""
    if status()["em_andamento"]:
        return {"ja_em_andamento": True}
    ok = _ultima(Sync.STATUS_OK)
    hoje = agora_br().date()
    if ok and ok.concluido_em:
        desde = max(INICIO, (ok.concluido_em.date().replace(day=1) - timedelta(days=1)).replace(day=1))
    else:
        desde = INICIO
    sync = Sync(status=Sync.STATUS_ANDAMENTO, iniciado_em=agora_br(), iniciado_por=usuario, desde=desde)
    db.session.add(sync)
    db.session.commit()
    try:
        empresa = get_settings().PG_COD_EMPRESA
        linhas = []
        # Mês a mês: a bridge devolve tudo numa resposta só, e o ano inteiro passa de 3 MB.
        for ini, fim in _meses(desde, hoje):
            for r in grv.fetch_all(queries.SQL_SPEND_ENTRADAS, {
                "cod_empresa": empresa, "data_de": ini.isoformat(), "data_ate": fim.isoformat(),
            }):
                linha = _linha(r)
                if linha:
                    linhas.append(linha)
        Entrada.query.filter(Entrada.data_lancamento >= desde).delete(synchronize_session=False)
        for i in range(0, len(linhas), 1000):
            db.session.bulk_insert_mappings(Entrada, linhas[i:i + 1000])
        _semear()
        sync.status = Sync.STATUS_OK
        sync.linhas = len(linhas)
        sync.concluido_em = agora_br()
        db.session.commit()
        return {"linhas": len(linhas), "desde": desde.isoformat()}
    except Exception as exc:  # noqa: BLE001 - GRV fora nao pode deixar a atualizacao "em andamento" para sempre
        db.session.rollback()
        sync = db.session.get(Sync, sync.id)
        sync.status = Sync.STATUS_ERRO
        sync.erro = str(exc)[:500]
        sync.concluido_em = agora_br()
        db.session.commit()
        raise ValueError("Não deu para buscar as entradas do GRV agora. Tente de novo em instantes.") from exc


# ---------------------------------------------------------------------------
# Classificação aprendida
# ---------------------------------------------------------------------------
# Do mais específico para o mais genérico. Ordem escolhida treinando até o
# mês M da planilha e testando em M+1 (jul–out/2026).
_NIVEIS = (
    ("fornecedor + item", lambda f: (f["cod_fornecedor"], f["codigo_item"])),
    ("fornecedor + conta", lambda f: (f["cod_fornecedor"], f["conta"])),
    ("item", lambda f: f["codigo_item"] or None),
    ("fornecedor", lambda f: f["cod_fornecedor"]),
    ("família + grupo", lambda f: (f["familia"], f["grupo"]) if f["familia"] else None),
    ("CFOP + família", lambda f: (_digitos(f["cfop"])[-3:], f["familia"])),
    ("conta", lambda f: f["conta"] or None),
)


def _features(r) -> dict:
    return {
        "cod_fornecedor": r.cod_fornecedor,
        "codigo_item": (r.codigo_item or "").upper(),
        "conta": r.conta or "",
        "familia": r.familia or "",
        "grupo": r.grupo or "",
        "cfop": r.cfop or "",
    }


def _classificador():
    votos = [defaultdict(Counter) for _ in _NIVEIS]
    manual: dict = {}
    exemplos = db.session.query(
        Exemplo.origem, Exemplo.cod_fornecedor, Exemplo.codigo_item, Exemplo.conta,
        Exemplo.familia, Exemplo.grupo, Exemplo.cfop, Exemplo.classificacao, Exemplo.peso,
    )
    for ex in exemplos:
        f = _features(ex)
        if ex.origem == Exemplo.ORIGEM_MANUAL:
            manual[_NIVEIS[0][1](f)] = ex.classificacao
        for i, (_, chave) in enumerate(_NIVEIS):
            k = chave(f)
            if k is not None:
                votos[i][k][ex.classificacao] += ex.peso or 1
    mapas = [{k: c.most_common(1)[0][0] for k, c in v.items()} for v in votos]

    def classificar(f: dict) -> tuple[str, int | None, bool]:
        """(classificação, nível, manual). Nível 0 = já aprendido para o fornecedor + item."""
        k0 = _NIVEIS[0][1](f)
        if k0 in manual:
            return manual[k0], 0, True
        for i, (_, chave) in enumerate(_NIVEIS):
            k = chave(f)
            if k is not None and k in mapas[i]:
                return mapas[i][k], i, False
        return SEM_CLASSIFICACAO, None, False

    return classificar


def _nomes_classificacao() -> list[str]:
    nomes = {c for (c,) in db.session.query(Exemplo.classificacao).distinct()}
    return sorted(nomes, key=lambda n: n.lower())


# ---------------------------------------------------------------------------
# Leitura
# ---------------------------------------------------------------------------
def _periodo(de, ate) -> tuple[list[str], str, str]:
    competencias = [c for (c,) in db.session.query(Entrada.competencia).distinct().order_by(Entrada.competencia)]
    if not competencias:
        return [], "", ""
    de = _competencia_valida(de) or competencias[max(0, len(competencias) - 12)]
    ate = _competencia_valida(ate) or competencias[-1]
    if de > ate:
        de, ate = ate, de
    return competencias, de, ate


def _entradas(de: str, ate: str):
    return (
        db.session.query(
            Entrada.competencia, Entrada.cod_fornecedor, Entrada.fornecedor, Entrada.nota_fiscal,
            Entrada.cfop, Entrada.codigo_item, Entrada.descricao, Entrada.valor, Entrada.conta,
            Entrada.familia, Entrada.grupo,
        )
        .filter(Entrada.competencia >= de, Entrada.competencia <= ate)
        .all()
    )


def painel(de=None, ate=None, familia=None, classificacao=None, atual=None, anterior=None) -> dict:
    base = status()
    competencias, de, ate = _periodo(de, ate)
    if not competencias:
        return {"vazio": True, "competencias": [], "base": base}
    familia = (familia or "").strip() or None
    classificacao = (classificacao or "").strip() or None
    meses = [c for c in competencias if de <= c <= ate]

    classificar = _classificador()
    fora = {"cfop": {"valor": 0.0, "itens": 0}, "intercompany": {"valor": 0.0, "itens": 0}}
    revisar = {"valor": 0.0, "grupos": set()}
    total_base = 0.0
    familias, classificacoes = set(), set()
    itens = []
    for r in _entradas(de, ate):
        valor = r.valor or 0
        if not cfop_e_compra(r.cfop):
            fora["cfop"]["valor"] += valor
            fora["cfop"]["itens"] += 1
            continue
        if e_intercompany(r.fornecedor):
            fora["intercompany"]["valor"] += valor
            fora["intercompany"]["itens"] += 1
            continue
        f = _features(r)
        cls, nivel, _ = classificar(f)
        fam = r.familia or SEM_FAMILIA
        familias.add(fam)
        classificacoes.add(cls)
        total_base += valor
        if nivel != 0:
            revisar["valor"] += valor
            revisar["grupos"].add((r.cod_fornecedor, f["codigo_item"]))
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

    # O mês corrente está pela metade: comparar com o anterior cheio engana.
    hoje = agora_br().date()
    fechados = [m for m in meses if m != f"{hoje.year:04d}-{hoje.month:02d}"] or meses
    atual = _competencia_valida(atual) if _competencia_valida(atual) in meses else fechados[-1] if fechados else None
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
        "base": base,
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
        "revisar": {
            "valor": round(revisar["valor"], 2),
            "itens": len(revisar["grupos"]),
            "pct": (revisar["valor"] / total_base) if total_base else 0.0,
        },
        "fora_spend": {k: {"valor": round(v["valor"], 2), "itens": v["itens"]} for k, v in fora.items()},
    }


def revisao(de=None, ate=None) -> dict:
    """Cada fornecedor + item comprado no período, com a classificação e de
    onde ela saiu. Palpites primeiro, do maior valor para o menor."""
    competencias, de, ate = _periodo(de, ate)
    classificar = _classificador()
    grupos: dict = {}
    for r in _entradas(de, ate) if competencias else []:
        if not cfop_e_compra(r.cfop) or e_intercompany(r.fornecedor):
            continue
        f = _features(r)
        chave = (r.cod_fornecedor, f["codigo_item"])
        g = grupos.get(chave)
        if g is None:
            cls, nivel, manual = classificar(f)
            g = grupos[chave] = {
                "cod_fornecedor": r.cod_fornecedor,
                "fornecedor": r.fornecedor or "",
                "codigo_item": f["codigo_item"],
                "descricao": r.descricao or "",
                "familia": r.familia or "",
                "classificacao": cls,
                "palpite": nivel != 0,
                "origem": "manual" if manual else ("planilha" if nivel == 0 else (_NIVEIS[nivel][0] if nivel is not None else None)),
                "valor": 0.0,
            }
        g["valor"] += r.valor or 0
    itens = sorted(grupos.values(), key=lambda g: (not g["palpite"], -g["valor"]))
    for g in itens:
        g["valor"] = round(g["valor"], 2)
    return {"de": de, "ate": ate, "itens": itens, "classificacoes": _nomes_classificacao()}


def salvar_classificacao(cod_fornecedor, codigo_item, classificacao, usuario: str) -> None:
    """Confirma ou corrige a classificação de um fornecedor + item. Vazia
    desfaz a correção (volta ao que a planilha ou o palpite diz)."""
    try:
        fornecedor = int(cod_fornecedor)
    except (TypeError, ValueError):
        raise ValueError("Fornecedor inválido.") from None
    item = _texto(codigo_item, 80).upper()
    classificacao = _limpa_classificacao(classificacao)
    if len(classificacao) > 120:
        raise ValueError("Classificação com mais de 120 caracteres.")
    exemplo = Exemplo.query.filter_by(origem=Exemplo.ORIGEM_MANUAL, cod_fornecedor=fornecedor, codigo_item=item).first()
    if not classificacao:
        if exemplo:
            db.session.delete(exemplo)
            db.session.commit()
        return
    if exemplo is None:
        ref = (
            Entrada.query.filter_by(cod_fornecedor=fornecedor, codigo_item=item)
            .order_by(Entrada.data_lancamento.desc())
            .first()
        )
        if ref is None:
            raise ValueError("Item não encontrado nas entradas do GRV.")
        exemplo = Exemplo(
            origem=Exemplo.ORIGEM_MANUAL, cod_fornecedor=fornecedor, codigo_item=item,
            conta=ref.conta, familia=ref.familia, grupo=ref.grupo, cfop=ref.cfop, peso=1,
        )
        db.session.add(exemplo)
    exemplo.classificacao = classificacao
    exemplo.atualizado_por = usuario
    exemplo.atualizado_em = agora_br()
    db.session.commit()
