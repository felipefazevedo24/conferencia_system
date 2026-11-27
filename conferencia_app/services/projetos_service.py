"""Gestao de projetos: projetos, tarefas com dependencia, linha do tempo,
painel geral, importacao/exportacao da planilha e avisos no Teams.

Regra central: uma tarefa so' pode ser INICIADA ou CONCLUIDA depois que todas
as tarefas de que ela depende estiverem concluidas. A situacao exibida e'
calculada (ver `situacao`), nunca gravada.

As funcoes levantam ValueError com mensagem em portugues pro usuario final.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime, timedelta
from io import BytesIO

from ..extensions import db
from ..models import (
    Projeto,
    ProjetoTarefa,
    ProjetoTarefaDependencia,
    ProjetoTarefaResponsavel,
    Usuario,
)
from ..tempo import agora_br

NAO_INICIADA, EM_ANDAMENTO = "nao_iniciada", "em_andamento"

# Situacao calculada -> rotulo (a tela e o Excel usam os mesmos textos)
SITUACOES = {
    "concluida": "Concluída",
    "concluida_atraso": "Concluída com atraso",
    "em_andamento": "Em andamento",
    "nao_iniciada": "Não iniciada",
    "bloqueada": "Bloqueada",
}

TEAMS_ENV, TEAMS_CHAVE = "TEAMS_WEBHOOK_PROJETOS_URL", "webhook_projetos"
DIAS_AVISO_PRAZO = 2


def hoje() -> date:
    return agora_br().date()


def _limpo(v, n: int) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()[:n]


def _sem_acento(v: str) -> str:
    v = unicodedata.normalize("NFKD", str(v or "")).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", v).strip().lower()


def _data(v, campo: str, obrigatoria: bool = True) -> date | None:
    if v in (None, ""):
        if obrigatoria:
            raise ValueError(f"Informe {campo}.")
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return datetime.strptime(str(v)[:10], "%Y-%m-%d").date()
    except ValueError:
        raise ValueError(f"{campo[0].upper()}{campo[1:]} inválida.")


# ── Situacao (calculada) ────────────────────────────────────────────────
def concluida(t: ProjetoTarefa) -> bool:
    return t.termino_real is not None


def pendencias(t: ProjetoTarefa) -> list[ProjetoTarefa]:
    """Tarefas anteriores ainda nao concluidas (o que bloqueia `t`)."""
    return sorted((d.depende_de for d in t.dependencias if not concluida(d.depende_de)), key=lambda x: x.numero)


def situacao(t: ProjetoTarefa) -> str:
    if concluida(t):
        return "concluida_atraso" if t.termino_real > t.data_fim else "concluida"
    if t.status == EM_ANDAMENTO:
        return "em_andamento"
    return "bloqueada" if pendencias(t) else "nao_iniciada"


def atrasada(t: ProjetoTarefa, ref: date | None = None) -> bool:
    return not concluida(t) and t.data_fim < (ref or hoje())


def _nomes(t: ProjetoTarefa) -> list[str]:
    return [r.nome for r in t.responsaveis]


def _eh_minha(t: ProjetoTarefa, username: str, nome_usuario: str) -> bool:
    alvo = _sem_acento(nome_usuario)
    return any(r.username == username or (alvo and _sem_acento(r.nome) == alvo) for r in t.responsaveis)


def nome_usuario(username: str) -> str:
    u = Usuario.query.filter_by(username=username).first()
    return (u.nome_exibicao or "") if u else ""


# ── Leitura ─────────────────────────────────────────────────────────────
def _tarefa_dict(t: ProjetoTarefa, ref: date) -> dict:
    sit = situacao(t)
    return {
        "id": t.id, "numero": t.numero, "grupo": t.grupo or "", "descricao": t.descricao,
        "data_inicio": t.data_inicio.isoformat(), "data_fim": t.data_fim.isoformat(),
        "termino_real": t.termino_real.isoformat() if t.termino_real else None,
        "area": t.area or "", "observacao": t.observacao or "",
        "situacao": sit, "situacao_rotulo": SITUACOES[sit], "atrasada": atrasada(t, ref),
        "responsaveis": [{"username": r.username, "nome": r.nome} for r in t.responsaveis],
        "dependencias": sorted(d.depende_de_id for d in t.dependencias),
        "dependencias_numeros": sorted(d.depende_de.numero for d in t.dependencias),
        "pendencias": [p.numero for p in pendencias(t)],
        "concluido_por": t.concluido_por,
    }


def _resumo(tarefas: list[ProjetoTarefa], ref: date) -> dict:
    """Contagem por situacao pro grafico/cartoes. 'atrasada' e' contada a
    parte (uma tarefa em andamento pode estar atrasada)."""
    cont = {k: 0 for k in SITUACOES}
    for t in tarefas:
        cont[situacao(t)] += 1
    total = len(tarefas)
    feitas = cont["concluida"] + cont["concluida_atraso"]
    return {
        "total": total, "por_situacao": cont, "concluidas": feitas,
        "atrasadas": sum(1 for t in tarefas if atrasada(t, ref)),
        "progresso": round(100 * feitas / total) if total else 0,
    }


def _projeto_resumo(p: Projeto, ref: date) -> dict:
    tarefas = list(p.tarefas)
    datas_ini = [t.data_inicio for t in tarefas]
    datas_fim = [max(t.data_fim, t.termino_real or t.data_fim) for t in tarefas]
    return {
        "id": p.id, "nome": p.nome, "descricao": p.descricao or "", "criado_por": p.criado_por,
        "inicio": min(datas_ini).isoformat() if datas_ini else None,
        "fim": max(datas_fim).isoformat() if datas_fim else None,
        **_resumo(tarefas, ref),
    }


def listar_projetos() -> list[dict]:
    ref = hoje()
    return [_projeto_resumo(p, ref) for p in Projeto.query.order_by(Projeto.nome).all()]


def obter_projeto(projeto_id: int) -> dict | None:
    p = db.session.get(Projeto, projeto_id)
    if not p:
        return None
    ref = hoje()
    return {**_projeto_resumo(p, ref), "hoje": ref.isoformat(),
            "tarefas": [_tarefa_dict(t, ref) for t in p.tarefas]}


def painel(username: str) -> dict:
    """Visao de todos os projetos: situacao geral, por projeto e por pessoa,
    mais as tarefas atrasadas/proximas do vencimento e as minhas."""
    ref = hoje()
    projetos = Projeto.query.order_by(Projeto.nome).all()
    tarefas = [t for p in projetos for t in p.tarefas]
    por_pessoa: dict[str, dict] = {}
    for t in tarefas:
        sit = situacao(t)
        for nome in (_nomes(t) or ["Sem responsável"]):
            chave = _sem_acento(nome)
            item = por_pessoa.setdefault(chave, {"nome": nome, **{k: 0 for k in SITUACOES}, "atrasadas": 0, "total": 0})
            item[sit] += 1
            item["total"] += 1
            item["atrasadas"] += atrasada(t, ref)
    meu_nome = nome_usuario(username)
    alerta = [t for t in tarefas if not concluida(t) and t.data_fim <= ref + timedelta(days=7)]
    alerta.sort(key=lambda t: (t.data_fim, t.projeto.nome, t.numero))
    minhas = [t for t in tarefas if not concluida(t) and _eh_minha(t, username, meu_nome)]
    minhas.sort(key=lambda t: (t.data_fim, t.numero))
    com_projeto = lambda t: {**_tarefa_dict(t, ref), "projeto_id": t.projeto_id, "projeto": t.projeto.nome}
    return {
        "hoje": ref.isoformat(),
        "geral": _resumo(tarefas, ref),
        "projetos": [_projeto_resumo(p, ref) for p in projetos],
        "pessoas": sorted(por_pessoa.values(), key=lambda x: (-x["total"], x["nome"])),
        "alerta": [com_projeto(t) for t in alerta],
        "minhas": [com_projeto(t) for t in minhas],
    }


def usuarios_ativos() -> list[dict]:
    us = Usuario.query.filter_by(ativo=True).order_by(Usuario.nome_exibicao, Usuario.username).all()
    return [{"username": u.username, "nome": u.nome_exibicao or u.username} for u in us]


# ── Projeto ─────────────────────────────────────────────────────────────
def salvar_projeto(dados: dict, usuario: str, projeto_id: int | None = None) -> Projeto:
    nome = _limpo(dados.get("nome"), 160)
    if not nome:
        raise ValueError("Informe o nome do projeto.")
    if projeto_id:
        p = db.session.get(Projeto, projeto_id)
        if not p:
            raise ValueError("Projeto não encontrado.")
    else:
        p = Projeto(criado_por=usuario)
        db.session.add(p)
    p.nome, p.descricao = nome, str(dados.get("descricao") or "").strip() or None
    p.atualizado_em = agora_br()
    db.session.commit()
    return p


def excluir_projeto(projeto_id: int) -> None:
    p = db.session.get(Projeto, projeto_id)
    if not p:
        raise ValueError("Projeto não encontrado.")
    db.session.delete(p)
    db.session.commit()


# ── Tarefa ──────────────────────────────────────────────────────────────
def _tarefa(tarefa_id: int) -> ProjetoTarefa:
    t = db.session.get(ProjetoTarefa, tarefa_id)
    if not t:
        raise ValueError("Tarefa não encontrada.")
    return t


def _responsaveis(lista) -> list[ProjetoTarefaResponsavel]:
    """[{username?, nome?}] -> registros. Username tem que ser usuario do
    Sync; nome sozinho e' alguem sem login."""
    saida, vistos = [], set()
    for item in lista or []:
        username = _limpo((item or {}).get("username"), 80) or None
        nome = _limpo((item or {}).get("nome"), 120)
        if username:
            u = Usuario.query.filter_by(username=username).first()
            if not u:
                raise ValueError(f"Usuário {username} não existe no Sync.")
            nome = u.nome_exibicao or u.username
        if not nome:
            continue
        chave = username or _sem_acento(nome)
        if chave in vistos:
            continue
        vistos.add(chave)
        saida.append(ProjetoTarefaResponsavel(username=username, nome=nome))
    return saida


def _cria_ciclo(tarefa: ProjetoTarefa, anterior: ProjetoTarefa) -> bool:
    """True se `anterior` (direta ou indiretamente) ja' depende de `tarefa`."""
    pilha, vistos = [anterior], set()
    while pilha:
        atual = pilha.pop()
        if atual.id == tarefa.id:
            return True
        if atual.id in vistos:
            continue
        vistos.add(atual.id)
        pilha.extend(d.depende_de for d in atual.dependencias)
    return False


def salvar_tarefa(projeto_id: int, dados: dict, usuario: str, tarefa_id: int | None = None) -> ProjetoTarefa:
    p = db.session.get(Projeto, projeto_id)
    if not p:
        raise ValueError("Projeto não encontrado.")
    descricao = _limpo(dados.get("descricao"), 500)
    if not descricao:
        raise ValueError("Informe a descrição da tarefa.")
    inicio = _data(dados.get("data_inicio"), "a data de início")
    fim = _data(dados.get("data_fim"), "a data de fim")
    if fim < inicio:
        raise ValueError("A data de fim não pode ser antes do início.")
    if tarefa_id:
        t = _tarefa(tarefa_id)
        if t.projeto_id != p.id:
            raise ValueError("Tarefa não pertence a este projeto.")
    else:
        t = ProjetoTarefa(projeto_id=p.id, criado_por=usuario, status=NAO_INICIADA,
                          numero=max((x.numero for x in p.tarefas), default=0) + 1)
        db.session.add(t)
    t.descricao, t.data_inicio, t.data_fim = descricao, inicio, fim
    t.grupo = _limpo(dados.get("grupo"), 80) or None
    t.area = _limpo(dados.get("area"), 120) or None
    t.observacao = str(dados.get("observacao") or "").strip() or None
    t.responsaveis = _responsaveis(dados.get("responsaveis"))

    ids = []
    for v in dados.get("dependencias") or []:
        try:
            ids.append(int(v))
        except (TypeError, ValueError):
            raise ValueError("Dependência inválida.")
    novas = []
    for dep_id in dict.fromkeys(ids):
        anterior = db.session.get(ProjetoTarefa, dep_id)
        if not anterior or anterior.projeto_id != p.id:
            raise ValueError("Dependência precisa ser uma tarefa do mesmo projeto.")
        if anterior.id == t.id:
            raise ValueError("A tarefa não pode depender dela mesma.")
        if _cria_ciclo(t, anterior):
            raise ValueError(f"A #{anterior.numero} já depende desta tarefa: a dependência fecharia um ciclo.")
        novas.append(anterior)
    ja_comecou = concluida(t) or t.status == EM_ANDAMENTO
    antigas = {d.depende_de_id for d in t.dependencias}
    for anterior in novas:
        if ja_comecou and anterior.id not in antigas and not concluida(anterior):
            raise ValueError(f"Esta tarefa já começou: não pode passar a depender da #{anterior.numero}, que ainda não foi concluída.")
    t.dependencias = [ProjetoTarefaDependencia(depende_de_id=a.id) for a in novas]
    t.atualizado_em = p.atualizado_em = agora_br()
    db.session.commit()
    return t


def excluir_tarefa(tarefa_id: int) -> None:
    t = _tarefa(tarefa_id)
    db.session.delete(t)
    db.session.commit()


def _exigir_liberada(t: ProjetoTarefa, acao: str) -> None:
    faltam = pendencias(t)
    if faltam:
        nums = ", ".join(f"#{x.numero}" for x in faltam)
        raise ValueError(f"Não dá para {acao} a #{t.numero}: ela depende de {nums}, ainda não concluída(s).")


def iniciar(tarefa_id: int, usuario: str) -> ProjetoTarefa:
    t = _tarefa(tarefa_id)
    if concluida(t):
        raise ValueError("A tarefa já está concluída.")
    _exigir_liberada(t, "iniciar")
    t.status, t.iniciado_em, t.atualizado_em = EM_ANDAMENTO, agora_br(), agora_br()
    db.session.commit()
    return t


def concluir(tarefa_id: int, usuario: str, termino=None) -> tuple[ProjetoTarefa, list[ProjetoTarefa]]:
    """Conclui e devolve (tarefa, sucessoras que ficaram liberadas)."""
    t = _tarefa(tarefa_id)
    if concluida(t):
        raise ValueError("A tarefa já está concluída.")
    _exigir_liberada(t, "concluir")
    quando = _data(termino, "a data de término", obrigatoria=False) or hoje()
    if quando > hoje():
        raise ValueError("A data de término não pode ser no futuro.")
    t.termino_real, t.concluido_por, t.status = quando, usuario, EM_ANDAMENTO
    t.atualizado_em = agora_br()
    db.session.flush()
    liberadas = [d.tarefa for d in t.sucessoras
                 if not concluida(d.tarefa) and d.tarefa.status == NAO_INICIADA and not pendencias(d.tarefa)]
    db.session.commit()
    _avisar_liberadas(liberadas, t)
    return t, liberadas


def reabrir(tarefa_id: int) -> ProjetoTarefa:
    t = _tarefa(tarefa_id)
    if not concluida(t):
        raise ValueError("A tarefa não está concluída.")
    andando = sorted((d.tarefa for d in t.sucessoras if concluida(d.tarefa) or d.tarefa.status == EM_ANDAMENTO),
                     key=lambda x: x.numero)
    if andando:
        nums = ", ".join(f"#{x.numero}" for x in andando)
        raise ValueError(f"Não dá para reabrir: {nums} depende(m) desta tarefa e já começou/começaram.")
    t.termino_real, t.concluido_por, t.status = None, None, EM_ANDAMENTO
    t.atualizado_em = agora_br()
    db.session.commit()
    return t


def salvar_observacao(tarefa_id: int, texto: str) -> ProjetoTarefa:
    t = _tarefa(tarefa_id)
    t.observacao = str(texto or "").strip() or None
    t.atualizado_em = agora_br()
    db.session.commit()
    return t


# ── Teams ───────────────────────────────────────────────────────────────
def _avisar_liberadas(liberadas: list[ProjetoTarefa], anterior: ProjetoTarefa) -> None:
    if not liberadas:
        return
    try:
        from . import teams_service
        for t in liberadas:
            quem = ", ".join(_nomes(t)) or "sem responsável"
            teams_service.enviar_card(
                f"🔓 Tarefa liberada — {t.projeto.nome}",
                f"#{t.numero} {t.descricao}",
                f"A #{anterior.numero} foi concluída. Responsável: {quem}. Prazo: {t.data_fim:%d/%m/%Y}.",
                env_var=TEAMS_ENV, config_key=TEAMS_CHAVE,
            )
            t.aviso_liberada_em = agora_br()
        db.session.commit()
    except Exception:  # aviso nunca derruba a conclusao
        db.session.rollback()


def enviar_avisos_prazo(ref: date | None = None, dias: int = DIAS_AVISO_PRAZO) -> dict:
    """Um card por projeto com as tarefas abertas que vencem em ate' `dias`
    dias ou ja' venceram. Cada tarefa entra no maximo uma vez por dia.
    Rodar 1x por dia (scripts/aviso_prazo_projetos.py)."""
    from . import teams_service
    ref = ref or hoje()
    limite = ref + timedelta(days=dias)
    enviados = 0
    for p in Projeto.query.order_by(Projeto.nome).all():
        alvo = [t for t in p.tarefas if not concluida(t) and t.data_fim <= limite and t.aviso_prazo_em != ref]
        if not alvo:
            continue
        linhas = []
        for t in sorted(alvo, key=lambda x: (x.data_fim, x.numero)):
            prazo = "VENCIDA" if t.data_fim < ref else ("vence hoje" if t.data_fim == ref else "vence")
            quem = ", ".join(_nomes(t)) or "sem responsável"
            linhas.append(f"#{t.numero} {t.descricao} — {prazo} {t.data_fim:%d/%m} — {quem}")
            t.aviso_prazo_em = ref
        vencidas = sum(1 for t in alvo if t.data_fim < ref)
        teams_service.enviar_card(
            f"⏰ Prazos — {p.nome}",
            f"{len(alvo)} tarefa(s) vencendo ou vencida(s)" + (f" · {vencidas} atrasada(s)" if vencidas else ""),
            "\n".join(linhas), env_var=TEAMS_ENV, config_key=TEAMS_CHAVE,
        )
        enviados += 1
    db.session.commit()
    return {"projetos": enviados, "data": ref.isoformat()}


# ── Planilha ────────────────────────────────────────────────────────────
COLUNAS = ["#", "Grupo", "Descrição", "Data Inicio", "Data Fim", "Termino Real", "Área / Responsável",
           "Ass. Responsável", "status", "Dependencia", "Definição/observação"]


def _texto_celula(v) -> str:
    return "" if v is None else str(v).strip()


def importar_planilha(arquivo, nome: str, usuario: str) -> tuple[Projeto, list[str]]:
    """Le o formato da planilha "projetos.xlsx" (aba com cabecalho #, Grupo,
    Descricao, Data Inicio, Data Fim, Termino Real, Area, Ass. Responsavel,
    status, Dependencia, observacao) e cria um projeto novo.
    Devolve (projeto, avisos do que foi corrigido/ignorado)."""
    import openpyxl

    nome = _limpo(nome, 160)
    if not nome:
        raise ValueError("Informe o nome do projeto.")
    try:
        wb = openpyxl.load_workbook(arquivo, data_only=True)
    except Exception:
        raise ValueError("Não consegui ler o arquivo. Envie um .xlsx.")
    ws, cab_linha, idx = None, None, {}
    for aba in wb.worksheets:
        for r in range(1, 6):
            cab = [_sem_acento(_texto_celula(c.value)) for c in aba[r]]
            if "descricao" in cab and "data inicio" in cab:
                ws, cab_linha = aba, r
                idx = {v: i for i, v in enumerate(cab) if v}
                break
        if ws:
            break
    if not ws:
        raise ValueError("Não achei o cabeçalho (Descrição, Data Inicio, Data Fim...) em nenhuma aba.")

    def col(linha, *nomes):
        for n in nomes:
            if n in idx and idx[n] < len(linha):
                return linha[idx[n]].value
        return None

    usuarios = {_sem_acento(u.nome_exibicao): u for u in Usuario.query.filter_by(ativo=True) if u.nome_exibicao}
    avisos: list[str] = []
    linhas = []
    for i, linha in enumerate(ws.iter_rows(min_row=cab_linha + 1), start=1):
        descricao = _limpo(col(linha, "descricao"), 500)
        if not descricao:
            continue
        num_bruto = col(linha, "#")
        numero = int(num_bruto) if isinstance(num_bruto, (int, float)) else i  # "=A3+1" sem cache -> posicao
        linhas.append((numero, linha, descricao))

    p = Projeto(nome=nome, criado_por=usuario)
    db.session.add(p)
    db.session.flush()
    por_numero: dict[int, ProjetoTarefa] = {}
    deps_brutas: list[tuple[ProjetoTarefa, str]] = []
    for numero, linha, descricao in linhas:
        if numero in por_numero:
            avisos.append(f"#{numero} repetido na planilha: a segunda linha virou #{max(por_numero) + 1}.")
            numero = max(por_numero) + 1
        inicio = col(linha, "data inicio")
        fim = col(linha, "data fim")
        inicio = inicio.date() if isinstance(inicio, datetime) else inicio
        fim = fim.date() if isinstance(fim, datetime) else fim
        if not isinstance(inicio, date) and isinstance(fim, date):
            inicio = fim
        if not isinstance(fim, date) and isinstance(inicio, date):
            fim = inicio
        if not isinstance(inicio, date):
            avisos.append(f"#{numero} sem datas: ficou com início e fim hoje.")
            inicio = fim = hoje()
        if fim < inicio:
            corrigida = fim.replace(year=fim.year + 1) if fim.month != 2 or fim.day != 29 else fim
            novo_fim = corrigida if corrigida >= inicio else inicio
            avisos.append(f"#{numero}: fim {fim:%d/%m/%Y} era antes do início {inicio:%d/%m/%Y}; ficou {novo_fim:%d/%m/%Y}.")
            fim = novo_fim
        termino = col(linha, "termino real")
        termino = termino.date() if isinstance(termino, datetime) else (termino if isinstance(termino, date) else None)
        status_txt = _sem_acento(_texto_celula(col(linha, "status")))
        status = EM_ANDAMENTO if "andamento" in status_txt else NAO_INICIADA
        if status_txt.startswith("concluido") and not termino:
            termino = min(fim, hoje())
            avisos.append(f"#{numero} estava Concluído sem Término Real: usei {termino:%d/%m/%Y}.")
        if termino:
            status = EM_ANDAMENTO
        t = ProjetoTarefa(projeto_id=p.id, numero=numero, descricao=descricao, data_inicio=inicio, data_fim=fim,
                          termino_real=termino, status=status, criado_por=usuario,
                          concluido_por=usuario if termino else None,
                          grupo=_limpo(col(linha, "grupo"), 80) or None,
                          area=_limpo(col(linha, "area / responsavel", "area"), 120) or None,
                          observacao=_texto_celula(col(linha, "definicao/observacao", "observacao")) or None)
        resp = []
        for parte in re.split(r"\s*[|/;]\s*", _texto_celula(col(linha, "ass. responsavel", "responsavel"))):
            parte = _limpo(parte, 120)
            if not parte:
                continue
            u = usuarios.get(_sem_acento(parte))
            resp.append({"username": u.username} if u else {"nome": parte})
        t.responsaveis = _responsaveis(resp)
        db.session.add(t)
        por_numero[numero] = t
        deps_brutas.append((t, _texto_celula(col(linha, "dependencia"))))
    db.session.flush()

    for t, bruto in deps_brutas:
        for parte in re.findall(r"\d+", bruto):
            anterior = por_numero.get(int(parte))
            if not anterior or anterior is t:
                avisos.append(f"#{t.numero}: dependência {parte} não existe; ignorada.")
                continue
            if _cria_ciclo(t, anterior):
                avisos.append(f"#{t.numero}: dependência da #{anterior.numero} fecharia um ciclo; ignorada.")
                continue
            t.dependencias.append(ProjetoTarefaDependencia(depende_de_id=anterior.id))
            db.session.flush()
    for t in por_numero.values():
        if (concluida(t) or t.status == EM_ANDAMENTO) and pendencias(t):
            nums = ", ".join(f"#{x.numero}" for x in pendencias(t))
            avisos.append(f"#{t.numero} já começou/concluiu, mas depende de {nums}, ainda aberta(s). Confira.")
    if not por_numero:
        db.session.rollback()
        raise ValueError("A planilha não tem nenhuma tarefa com descrição.")
    db.session.commit()
    return p, avisos


def exportar_xlsx(projeto_id: int) -> tuple[bytes, str]:
    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill

    p = db.session.get(Projeto, projeto_id)
    if not p:
        raise ValueError("Projeto não encontrado.")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Detalhado"
    ws.append(COLUNAS)
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1F3A5F")
        c.alignment = Alignment(vertical="center", wrap_text=True)
    for t in p.tarefas:
        ws.append([
            t.numero, t.grupo or "", t.descricao, t.data_inicio, t.data_fim, t.termino_real,
            t.area or "", " | ".join(_nomes(t)), SITUACOES[situacao(t)],
            ", ".join(str(n) for n in sorted(d.depende_de.numero for d in t.dependencias)) or "NA",
            t.observacao or "",
        ])
    for linha in ws.iter_rows(min_row=2):
        for c in linha[3:6]:
            c.number_format = "DD/MM/YYYY"
        for c in linha:
            c.alignment = Alignment(vertical="top", wrap_text=True)
    for letra, larg in zip("ABCDEFGHIJK", (6, 16, 60, 12, 12, 12, 22, 26, 20, 12, 50)):
        ws.column_dimensions[letra].width = larg
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    buf = BytesIO()
    wb.save(buf)
    nome = re.sub(r"[^A-Za-z0-9_-]+", "_", _sem_acento(p.nome)).strip("_") or "projeto"
    return buf.getvalue(), f"projeto_{nome}.xlsx"
