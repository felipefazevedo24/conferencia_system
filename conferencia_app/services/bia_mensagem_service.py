"""Mensageria da Bia: avisos/recados de um Admin para os usuários.

Só quem é Admin envia (a rota já valida). O Admin manda pelo chat da Bia com um
comando em linguagem natural:

    avisar todos: <mensagem>              -> broadcast (todo mundo)
    avisar cargo Logística: <mensagem>    -> todos de um cargo
    avisar joao: <mensagem>               -> um usuário específico

A entrega é in-app pela própria Bia (toast + registro no painel). Uma mensagem
de cargo/broadcast atinge várias pessoas; por isso a leitura fica em
``BiaMensagemLeitura`` (uma linha por mensagem/usuário) — assim a Bia entrega
cada aviso uma única vez para cada destinatário.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timedelta

from sqlalchemy import func

from ..extensions import db
from ..models import BiaMensagem, BiaMensagemLeitura, Usuario


SUGESTOES = [
    "avisar todos: ",
    "avisar cargo Logística: ",
]

# Verbo de aviso no início da frase (interpretado como comando de envio).
_RE_VERBO = re.compile(
    r"^(avisar|avise|aviso|comunicar|comunique|comunicado|notificar|notifique|"
    r"mandar|manda|enviar|envie)\b",
    re.IGNORECASE,
)


def _normalizar(texto) -> str:
    txt = unicodedata.normalize("NFKD", str(texto or ""))
    txt = "".join(c for c in txt if not unicodedata.combining(c))
    return txt.lower().strip()


def _fmt(dt) -> str:
    if not dt:
        return ""
    try:
        return dt.strftime("%d/%m/%Y %H:%M")
    except Exception:
        return ""


def _resposta(texto: str) -> dict:
    return {"resposta": texto, "pendencias": [], "sugestoes": SUGESTOES}


# --------------------------------------------------------------------------- #
# Envio (Admin, via chat).
# --------------------------------------------------------------------------- #
def _nome_de(username: str) -> str:
    try:
        u = Usuario.query.filter(func.lower(Usuario.username) == _normalizar(username)).first()
        return (u.nome_exibicao or u.username) if u else (username or "")
    except Exception:
        return username or ""


def _buscar_usuario(alvo: str):
    """Localiza um usuário pelo username (exato, sem caixa) ou pelo nome de
    exibição (igual ou contido). Devolve o Usuario ou None."""
    alvo_norm = _normalizar(alvo)
    if not alvo_norm:
        return None
    try:
        u = Usuario.query.filter(func.lower(Usuario.username) == alvo_norm).first()
        if u:
            return u
        ativos = Usuario.query.filter(Usuario.ativo.is_(True)).all()
        for u in ativos:
            if _normalizar(u.nome_exibicao) == alvo_norm:
                return u
        for u in ativos:
            nome = _normalizar(u.nome_exibicao)
            if nome and alvo_norm in nome:
                return u
    except Exception:
        return None
    return None


def _contar_cargo(cargo: str) -> int:
    alvo = _normalizar(cargo)
    if not alvo:
        return 0
    try:
        return sum(
            1
            for u in Usuario.query.filter(Usuario.ativo.is_(True)).all()
            if _normalizar(u.role) == alvo
        )
    except Exception:
        return 0


def _contar_ativos() -> int:
    try:
        return Usuario.query.filter(Usuario.ativo.is_(True)).count()
    except Exception:
        return 0


def _salvar(ctx: dict, destino_tipo: str, destino_valor: str, texto: str) -> bool:
    remetente = ctx.get("username") or "admin"
    try:
        msg = BiaMensagem(
            remetente=remetente,
            remetente_nome=_nome_de(remetente),
            destino_tipo=destino_tipo,
            destino_valor=destino_valor or "",
            texto=texto.strip(),
        )
        db.session.add(msg)
        db.session.commit()
        return True
    except Exception:
        db.session.rollback()
        return False


def _enviar_usuario(ctx: dict, alvo: str, texto: str) -> dict:
    u = _buscar_usuario(alvo)
    if not u:
        return _resposta(
            f"Não encontrei o usuário \"{alvo.strip()}\". Confere o nome de usuário? "
            "Você também pode enviar por cargo (\"avisar cargo Logística: ...\") "
            "ou para todos (\"avisar todos: ...\")."
        )
    if not _salvar(ctx, "usuario", u.username, texto):
        return _resposta("Não consegui registrar o aviso agora. Tenta de novo?")
    nome = u.nome_exibicao or u.username
    return _resposta(
        f"Pronto! Registrei seu aviso para {nome}. A Bia entrega assim que a "
        "pessoa estiver no sistema. 📬"
    )


def _enviar_cargo(ctx: dict, cargo: str, texto: str) -> dict:
    cargo = (cargo or "").strip()
    n = _contar_cargo(cargo)
    if n == 0:
        return _resposta(
            f"Não achei ninguém com o cargo \"{cargo}\". Confere o nome do cargo? "
            "(ex.: Logística, Expedição, Compras...)"
        )
    if not _salvar(ctx, "cargo", cargo, texto):
        return _resposta("Não consegui registrar o aviso agora. Tenta de novo?")
    plural = "pessoas" if n > 1 else "pessoa"
    return _resposta(f"Enviei seu aviso para o cargo {cargo} ({n} {plural}). 📬")


def _enviar_broadcast(ctx: dict, texto: str) -> dict:
    n = _contar_ativos()
    if not _salvar(ctx, "broadcast", "", texto):
        return _resposta("Não consegui registrar o aviso agora. Tenta de novo?")
    plural = "pessoas" if n != 1 else "pessoa"
    return _resposta(f"Enviei seu aviso para todo mundo ({n} {plural}). 📢")


def interpretar_envio(pergunta: str, ctx: dict) -> dict | None:
    """Interpreta um comando de ENVIO de aviso do Admin. Devolve a resposta da
    Bia (dict) ou None quando a frase não é um comando de envio (para o fluxo
    seguir para o chat normal)."""
    if not ctx.get("is_admin"):
        return None
    original = str(pergunta or "").strip()
    if not original or not _RE_VERBO.match(original):
        return None

    if ":" not in original:
        return _resposta(
            "Para enviar um aviso, use: \"avisar <alvo>: <mensagem>\".\n"
            "Ex.: \"avisar todos: reunião às 15h\", \"avisar cargo Logística: ...\", "
            "\"avisar joao: ...\"."
        )

    cabec, texto = original.split(":", 1)
    texto = texto.strip()
    if not texto:
        return _resposta(
            "Faltou a mensagem depois dos dois-pontos. Ex.: \"avisar todos: <mensagem>\"."
        )

    alvo = _normalizar(cabec)
    # Remove o verbo e conectores iniciais para isolar o alvo.
    alvo = re.sub(
        r"^(avisar|avise|aviso|comunicar|comunique|comunicado|notificar|notifique|"
        r"mandar|manda|enviar|envie)\s+",
        "",
        alvo,
    )
    alvo = re.sub(r"^(um|uma)\s+", "", alvo)
    alvo = re.sub(r"^(recado|mensagem|aviso|comunicado)\s+", "", alvo)
    alvo = re.sub(r"^(para|pra|pro|ao|aos|a|o|as|os)\s+", "", alvo)
    alvo = alvo.strip()

    # Broadcast.
    if (
        not alvo
        or re.search(
            r"\b(todos|todas|todo mundo|geral|todo o time|toda a equipe|"
            r"todo o pessoal|pessoal|galera|equipe toda|time todo)\b",
            alvo,
        )
    ):
        return _enviar_broadcast(ctx, texto)

    # Cargo.
    m = re.match(
        r"^(cargo|setor|time|equipe|perfil|papel|funcao|função|grupo|departamento|"
        r"turma|role)\s+(.+)$",
        alvo,
    )
    if m:
        return _enviar_cargo(ctx, m.group(2).strip(), texto)

    # Usuário explícito.
    m = re.match(r"^(usuario|usuário|user|pessoa|colaborador|funcionario|funcionário)\s+(.+)$", alvo)
    if m:
        return _enviar_usuario(ctx, m.group(2).strip(), texto)

    # Nome "solto" -> tratado como usuário.
    return _enviar_usuario(ctx, alvo, texto)


# --------------------------------------------------------------------------- #
# Entrega (qualquer usuário).
# --------------------------------------------------------------------------- #
def mensagens_nao_lidas(
    username: str, role, marcar: bool = True, dias: int = 7
) -> list[dict]:
    """Avisos ainda não entregues a este usuário (broadcast, do cargo dele ou
    endereçados a ele), criados nos últimos ``dias``. Se ``marcar``, registra a
    leitura para não reentregar. Best-effort: falha devolve lista vazia."""
    username = (username or "").strip()
    if not username:
        return []
    user_norm = _normalizar(username)
    role_norm = _normalizar(role)
    corte = datetime.now() - timedelta(days=dias)

    try:
        candidatas = (
            BiaMensagem.query.filter(BiaMensagem.created_at >= corte)
            .order_by(BiaMensagem.created_at.asc())
            .all()
        )
    except Exception:
        return []

    try:
        lidas = {
            r.mensagem_id
            for r in BiaMensagemLeitura.query.filter(
                func.lower(BiaMensagemLeitura.username) == user_norm
            ).all()
        }
    except Exception:
        lidas = set()

    entregar: list[BiaMensagem] = []
    for m in candidatas:
        if m.id in lidas:
            continue
        if _normalizar(m.remetente) == user_norm:
            continue  # não devolve o próprio aviso ao remetente
        tipo = (m.destino_tipo or "").strip()
        if tipo == "broadcast":
            alvo = True
        elif tipo == "usuario":
            alvo = _normalizar(m.destino_valor) == user_norm
        elif tipo == "cargo":
            alvo = _normalizar(m.destino_valor) == role_norm
        else:
            alvo = False
        if alvo:
            entregar.append(m)

    resultado = [
        {
            "id": m.id,
            "de": m.remetente_nome or m.remetente,
            "texto": m.texto,
            "quando": _fmt(m.created_at),
        }
        for m in entregar
    ]

    if marcar and entregar:
        try:
            for m in entregar:
                db.session.add(
                    BiaMensagemLeitura(mensagem_id=m.id, username=username)
                )
            db.session.commit()
        except Exception:
            db.session.rollback()

    return resultado
