"""Ações da Bia sobre romaneios (editar / estornar) via chat.

Interpreta comandos em linguagem natural que MODIFICAM o romaneio e executa a
ação correspondente, respeitando as regras:

  * Só dá para EDITAR um romaneio em Rascunho (transportadora, placa, motorista,
    documento do motorista, tipo de frete, e adicionar/remover NF).
  * Para editar um romaneio já finalizado (Pronto/Expedido) é preciso ESTORNAR
    para Rascunho. O estorno exige aprovação de um Admin:
      - Se quem pede já é Admin, o estorno é feito na hora.
      - Se não, cria-se uma solicitação pendente (fila in-app). Havendo Admin
        cadastrado, a Bia avisa que ele será notificado; caso contrário o pedido
        fica aguardando um Admin entrar.

As escritas ficam aqui (com a sessão/permissão vindas da rota), separadas do
`responder()` do assistente, que é somente leitura.
"""
from __future__ import annotations

import re
import unicodedata

from ..extensions import db
from ..models import (
    Usuario,
    ExpedicaoRomaneio,
    ExpedicaoRomaneioEstorno,
)


SUGESTOES = [
    "Estornar romaneio 45",
    "Trocar a transportadora do romaneio 45 para ...",
    "Estornos pendentes",
]

# Verbos que indicam intenção de ALTERAR (não perguntar).
_VERBOS_EDITAR = (
    "muda", "mudar", "troca", "trocar", "altera", "alterar", "corrige",
    "corrigir", "ajusta", "ajustar", "defin", "coloca", "colocar", "poe",
    "poem", "bota", "botar", "seta", "setar", "atualiza", "atualizar", "edita",
    "editar", "modifica", "modificar",
)
_VERBOS_ADD_NF = (
    "adicion", "inclu", "acrescent", "coloca", "colocar", "poe", "bota", "botar",
)
_VERBOS_REMOVE_NF = (
    "remov", "tira", "tirar", "exclu", "apaga", "apagar", "retira", "retirar",
)


def _normalizar(texto: str) -> str:
    txt = unicodedata.normalize("NFKD", str(texto or ""))
    txt = "".join(c for c in txt if not unicodedata.combining(c))
    return txt.lower().strip()


def existe_admin_cadastrado() -> bool:
    """True se houver ao menos um usuário ativo com papel administrativo."""
    try:
        return (
            Usuario.query
            .filter(Usuario.ativo.is_(True))
            .filter(db.func.lower(Usuario.role).like("%admin%"))
            .first()
            is not None
        )
    except Exception:
        return False


def _resposta(texto: str) -> dict:
    return {"resposta": texto, "pendencias": [], "sugestoes": SUGESTOES}


# --------------------------------------------------------------------------- #
# Localização do romaneio a partir do texto.
# --------------------------------------------------------------------------- #
def _extrair_ref_romaneio(q: str) -> str | None:
    """Extrai a referência do romaneio da frase. Aceita 'ROM-2026-0045' ou o
    número curto que segue a palavra 'romaneio' (ex.: 'romaneio 45')."""
    m = re.search(r"rom[-\s]?(\d{4})[-\s]?(\d{1,})", q)
    if m:
        return f"ROM-{m.group(1)}-{int(m.group(2)):04d}"
    m = re.search(r"romaneio[^\d]{0,8}(\d{1,})", q)
    if m:
        return m.group(1)
    return None


def _buscar_romaneio(ref: str) -> ExpedicaoRomaneio | None:
    ref = str(ref or "").strip()
    if not ref:
        return None
    try:
        r = ExpedicaoRomaneio.query.filter_by(numero_romaneio=ref).first()
        if r:
            return r
        if ref.isdigit():
            sufixo = f"{int(ref):04d}"
            r = (
                ExpedicaoRomaneio.query
                .filter(ExpedicaoRomaneio.numero_romaneio.like(f"ROM-%-{sufixo}"))
                .order_by(ExpedicaoRomaneio.id.desc())
                .first()
            )
            if r:
                return r
            r = ExpedicaoRomaneio.query.get(int(ref))
            if r:
                return r
        return (
            ExpedicaoRomaneio.query
            .filter(ExpedicaoRomaneio.numero_romaneio.like(f"%{ref}%"))
            .order_by(ExpedicaoRomaneio.id.desc())
            .first()
        )
    except Exception:
        return None


def _valor_apos(original: str) -> str | None:
    """Captura o valor informado após 'para'/'pra'/':'/'='. Preserva a caixa
    original (ex.: nome da transportadora)."""
    m = re.search(r"\b(?:para|pra)\b\s+(.+)$", original, re.IGNORECASE)
    if not m:
        m = re.search(r"[:=]\s*(.+)$", original)
    if not m:
        return None
    valor = m.group(1).strip().strip(".").strip()
    return valor or None


def _eh_pergunta(q: str) -> bool:
    if q.endswith("?"):
        return True
    return bool(re.match(
        r"^(qual|quais|quanto|quantos|quantas|quando|onde|cade|como|quem|"
        r"o que|oq|porque|por que)\b",
        q,
    ))


# --------------------------------------------------------------------------- #
# Estorno com aprovação.
# --------------------------------------------------------------------------- #
def estornos_pendentes() -> list[dict]:
    """Solicitações de estorno aguardando aprovação (para o Admin)."""
    try:
        linhas = (
            ExpedicaoRomaneioEstorno.query
            .filter_by(status="Pendente")
            .order_by(ExpedicaoRomaneioEstorno.created_at.asc())
            .all()
        )
    except Exception:
        return []
    saida: list[dict] = []
    for e in linhas:
        rom = ExpedicaoRomaneio.query.get(e.romaneio_id)
        saida.append({
            "id": e.id,
            "romaneio": (rom.numero_romaneio if rom else str(e.romaneio_id)),
            "romaneio_id": e.romaneio_id,
            "solicitante": e.solicitante,
            "motivo": e.motivo,
            "status_romaneio": (rom.status if rom else e.status_romaneio) or "—",
        })
    return saida


def _solicitar_ou_estornar(rom: ExpedicaoRomaneio, ctx: dict, motivo: str) -> dict:
    autor = ctx.get("username") or ""
    if rom.status == "Rascunho":
        return _resposta(
            f"O romaneio {rom.numero_romaneio} já está em Rascunho — pode editar à "
            f"vontade. Ex.: \"trocar a transportadora do romaneio {rom.numero_romaneio} para ...\"."
        )
    if rom.status not in ("Pronto", "Expedido"):
        return _resposta(
            f"O romaneio {rom.numero_romaneio} está \"{rom.status}\" e não pode ser "
            "estornado por aqui."
        )

    # Admin faz na hora.
    if ctx.get("is_admin"):
        from ..routes.expedicao_romaneio_routes import estornar_para_rascunho
        ok, erro = estornar_para_rascunho(rom, autor)
        if not ok:
            return _resposta(erro or "Não consegui estornar esse romaneio.")
        return _resposta(
            f"Pronto! Estornei o romaneio {rom.numero_romaneio} para Rascunho. "
            "Agora dá para editar (transportadora, placa, motorista, frete, NFs)."
        )

    # Não-admin: cria solicitação pendente (uma por romaneio).
    try:
        ja = (
            ExpedicaoRomaneioEstorno.query
            .filter_by(romaneio_id=rom.id, status="Pendente")
            .first()
        )
    except Exception:
        ja = None
    if ja:
        return _resposta(
            f"Já existe um pedido de estorno pendente para o romaneio "
            f"{rom.numero_romaneio}. Assim que um Admin aprovar, eu te aviso."
        )
    pedido = ExpedicaoRomaneioEstorno(
        romaneio_id=rom.id,
        solicitante=autor or "—",
        motivo=motivo or "Solicitado via Bia para edição.",
        status_romaneio=rom.status,
        status="Pendente",
    )
    db.session.add(pedido)
    db.session.commit()
    if existe_admin_cadastrado():
        return _resposta(
            f"Registrei o pedido de estorno do romaneio {rom.numero_romaneio}. "
            "Um Admin precisa aprovar — deixei na fila de aprovações e ele será "
            "avisado ao entrar. Assim que aprovarem, o romaneio volta para Rascunho."
        )
    return _resposta(
        f"Registrei o pedido de estorno do romaneio {rom.numero_romaneio}, mas não "
        "há nenhum Admin cadastrado no momento. O pedido fica pendente e será "
        "aprovado assim que um Admin entrar no sistema."
    )


def _decidir_estorno(estorno_id: int, aprovar: bool, ctx: dict) -> dict:
    if not ctx.get("is_admin"):
        return _resposta("Só um Admin pode aprovar ou rejeitar estornos. 🙂")
    e = ExpedicaoRomaneioEstorno.query.get(estorno_id)
    if not e:
        return _resposta(f"Não encontrei o pedido de estorno #{estorno_id}.")
    if e.status != "Pendente":
        return _resposta(f"O pedido de estorno #{estorno_id} já foi {e.status.lower()}.")

    from datetime import datetime
    e.admin_usuario = ctx.get("username") or "admin"
    e.resolvido_at = datetime.now()

    if not aprovar:
        e.status = "Rejeitado"
        db.session.commit()
        return _resposta(f"Ok, rejeitei o pedido de estorno #{estorno_id}.")

    rom = ExpedicaoRomaneio.query.get(e.romaneio_id)
    if not rom:
        e.status = "Rejeitado"
        e.admin_observacao = "Romaneio não existe mais."
        db.session.commit()
        return _resposta("Esse romaneio não existe mais — cancelei o pedido.")

    from ..routes.expedicao_romaneio_routes import estornar_para_rascunho
    ok, erro = estornar_para_rascunho(rom, ctx.get("username") or "admin")
    if not ok:
        return _resposta(erro or "Não consegui estornar o romaneio.")
    e.status = "Aprovado"
    db.session.commit()
    return _resposta(
        f"Aprovado! Estornei o romaneio {rom.numero_romaneio} para Rascunho "
        f"(pedido de {e.solicitante}). Agora ele pode ser editado."
    )


def _listar_estornos_pendentes(ctx: dict) -> dict:
    if not ctx.get("is_admin"):
        return _resposta("As aprovações de estorno são feitas por um Admin. 🙂")
    pend = estornos_pendentes()
    if not pend:
        return _resposta("Não há nenhum estorno aguardando aprovação. 👍")
    linhas = ["Estornos aguardando sua aprovação:"]
    for p in pend:
        linhas.append(
            f"• #{p['id']} — Romaneio {p['romaneio']} ({p['status_romaneio']}), "
            f"pedido por {p['solicitante']}: {p['motivo']}"
        )
    linhas.append("Diga \"aprovar estorno <número>\" ou \"rejeitar estorno <número>\".")
    return _resposta("\n".join(linhas))


# --------------------------------------------------------------------------- #
# Edição de campos do romaneio (Rascunho).
# --------------------------------------------------------------------------- #
def _mensagem_precisa_estorno(rom: ExpedicaoRomaneio) -> dict:
    return _resposta(
        f"O romaneio {rom.numero_romaneio} está \"{rom.status}\", então não dá para "
        "editar direto. Para alterar, preciso estornar para Rascunho — e isso "
        f"depende da aprovação de um Admin. Quer que eu solicite? Diga "
        f"\"estornar romaneio {rom.numero_romaneio}\"."
    )


def _editar(rom: ExpedicaoRomaneio, campo: str, valor, ctx: dict, rotulo: str) -> dict:
    if rom.status != "Rascunho":
        return _mensagem_precisa_estorno(rom)
    from ..routes.expedicao_romaneio_routes import editar_romaneio_campos
    ok, erro = editar_romaneio_campos(rom, {campo: valor}, ctx.get("username") or "")
    if not ok:
        return _resposta(erro or "Não consegui alterar isso agora.")
    return _resposta(f"Feito! {rotulo} do romaneio {rom.numero_romaneio} agora é: {valor}.")


def _add_ou_remove_nf(rom: ExpedicaoRomaneio, q: str, ctx: dict, adicionar: bool) -> dict:
    if rom.status != "Rascunho":
        return _mensagem_precisa_estorno(rom)
    m = re.search(r"\b(?:nf|nota)[^\d]{0,6}(\d{2,})", q)
    if not m:
        return _resposta("Qual o número da NF? Ex.: \"tira a NF 11268 do romaneio 45\".")
    numero_nf = m.group(1)
    autor = ctx.get("username") or ""
    if adicionar:
        from ..routes.expedicao_romaneio_routes import incluir_nf_no_romaneio
        nf, erro = incluir_nf_no_romaneio(rom, numero_nf, autor)
        if erro:
            return _resposta(erro)
        return _resposta(
            f"Incluí a NF {numero_nf} no romaneio {rom.numero_romaneio}. ✅"
        )
    # remover
    from ..models import ExpedicaoRomaneioNF
    from ..routes.expedicao_romaneio_routes import remover_nf_core
    linha = (
        ExpedicaoRomaneioNF.query
        .filter_by(romaneio_id=rom.id, numero_nf=numero_nf)
        .first()
    )
    if not linha:
        return _resposta(
            f"A NF {numero_nf} não está no romaneio {rom.numero_romaneio}."
        )
    ok, erro = remover_nf_core(rom, linha)
    if erro:
        return _resposta(erro)
    return _resposta(
        f"Removi a NF {numero_nf} do romaneio {rom.numero_romaneio}. ✅"
    )


# --------------------------------------------------------------------------- #
# Interpretador principal.
# --------------------------------------------------------------------------- #
def interpretar(pergunta: str, ctx: dict) -> dict | None:
    """Detecta e executa comandos de ação sobre romaneios. Retorna a resposta
    (dict) quando trata o comando, ou None para deixar o chat normal responder."""
    ctx = ctx or {}
    q = _normalizar(pergunta)
    if not q:
        return None

    tem_estorno = "estorn" in q
    tem_romaneio_ref = _extrair_ref_romaneio(q) is not None

    # Fora destes contextos, não é comando de ação — deixa o chat responder.
    if not (tem_estorno or tem_romaneio_ref):
        return None

    # 1) Listar estornos pendentes (Admin).
    if tem_estorno and ("pendente" in q or "aprovar" in q or "aprova" in q) \
            and not re.search(r"estorno[^\d]{0,4}\d", q) \
            and not re.search(r"\baprov\w+\b[^\d]{0,12}\d", q):
        return _listar_estornos_pendentes(ctx)

    # 2) Aprovar / rejeitar um estorno pelo número (Admin).
    if tem_estorno and re.search(r"\b(aprov\w+|rejeit\w+)\b", q):
        m = re.search(r"estorno[^\d]{0,4}(\d+)", q) or re.search(r"\b(\d+)\b", q)
        if m:
            aprovar = bool(re.search(r"\baprov\w+\b", q))
            return _decidir_estorno(int(m.group(1)), aprovar, ctx)
        return _listar_estornos_pendentes(ctx)

    # 3) Pedido de estorno de um romaneio (para poder editar).
    if tem_estorno and tem_romaneio_ref:
        rom = _buscar_romaneio(_extrair_ref_romaneio(q))
        if not rom:
            return _resposta("Não encontrei esse romaneio. Confere o número?")
        motivo_m = re.search(r"\b(?:porque|pois|motivo|por que)\b[:,\s]+(.+)$", q)
        motivo = motivo_m.group(1).strip() if motivo_m else ""
        return _solicitar_ou_estornar(rom, ctx, motivo)

    # A partir daqui só tratamos EDIÇÃO — exige referência ao romaneio.
    if not tem_romaneio_ref:
        return None

    # Não sequestrar perguntas ("qual a transportadora do romaneio 45?").
    tem_verbo = any(v in q for v in _VERBOS_EDITAR + _VERBOS_ADD_NF + _VERBOS_REMOVE_NF)
    tem_valor = bool(_valor_apos(pergunta)) or bool(re.search(r"\b(cif|fob)\b", q))
    if _eh_pergunta(q) and not (tem_verbo and tem_valor):
        return None
    if not (tem_verbo or tem_valor):
        return None

    rom = _buscar_romaneio(_extrair_ref_romaneio(q))
    if not rom:
        return _resposta("Não encontrei esse romaneio. Confere o número?")

    # 4) Adicionar / remover NF.
    if re.search(r"\b(nf|nota)\b", q):
        if any(v in q for v in _VERBOS_REMOVE_NF):
            return _add_ou_remove_nf(rom, q, ctx, adicionar=False)
        if any(v in q for v in _VERBOS_ADD_NF):
            return _add_ou_remove_nf(rom, q, ctx, adicionar=True)

    # 5) Tipo de frete.
    if "frete" in q or re.search(r"\b(cif|fob)\b", q):
        m = re.search(r"\b(cif|fob)\b", q)
        if not m:
            return _resposta("O frete deve ser CIF ou FOB. Qual dos dois?")
        return _editar(rom, "tipo_frete", m.group(1).upper(), ctx, "O frete")

    # 6) Documento do motorista (antes de 'motorista', pois contém a palavra).
    if ("documento" in q or "cpf" in q or "cnpj" in q) and "motorista" in q:
        valor = _valor_apos(pergunta)
        if not valor:
            return _resposta("Qual o documento do motorista?")
        return _editar(rom, "motorista_documento", valor, ctx, "O documento do motorista")

    # 7) Transportadora.
    if "transportadora" in q:
        valor = _valor_apos(pergunta)
        if not valor:
            return _resposta("Para qual transportadora?")
        return _editar(rom, "transportadora", valor, ctx, "A transportadora")

    # 8) Placa.
    if "placa" in q:
        valor = _valor_apos(pergunta)
        if not valor:
            m = re.search(r"([A-Za-z]{3}[-\s]?\d[A-Za-z0-9]\d{2})", pergunta)
            valor = m.group(1).upper().replace(" ", "").replace("-", "") if m else None
        if not valor:
            return _resposta("Qual a placa?")
        return _editar(rom, "placa", valor, ctx, "A placa")

    # 9) Motorista.
    if "motorista" in q:
        valor = _valor_apos(pergunta)
        if not valor:
            return _resposta("Qual o nome do motorista?")
        return _editar(rom, "motorista", valor, ctx, "O motorista")

    return None
