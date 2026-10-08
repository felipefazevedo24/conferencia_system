"""Regras da Requisicao de Vaga (RH) - ver o workflow em RhRequisicaoVaga.

Quem pode o que chega pronto no ``Ator`` (montado pela rota a partir das
permissoes): o service nao le sessao.
"""
from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy import func

from ..extensions import db
from ..models import RhCandidato, RhCandidatoAcesso, RhCargoVaga, RhRequisicaoEvento, RhRequisicaoVaga
from ..tempo import agora_br

PERM_REQUISICAO = "PAGE_RH_REQUISICAO"
PERM_DIRETORIA = "APROVAR_RH_DIRETORIA"
PERM_FINANCEIRO = "APROVAR_RH_FINANCEIRO"
PERM_GESTAO = "PAGE_RH_GESTAO"

STATUS_DIRETORIA = "Aguardando Diretoria"
STATUS_FINANCEIRO = "Aguardando Financeiro"
STATUS_RH = "Aguardando RH"
STATUS_APROVADA = "Aprovada"
STATUS_PUBLICADA = "Publicada"
STATUS_ENCERRADA = "Encerrada"
STATUS_CORRECAO = "Em correção"
STATUS_CANCELADA = "Cancelada"

# Ordem das aprovacoes: (status em que a requisicao espera, papel do Ator, nome da etapa).
ETAPAS = (
    (STATUS_DIRETORIA, "diretoria", "Diretoria"),
    (STATUS_FINANCEIRO, "financeiro", "Financeiro"),
    (STATUS_RH, "gestao", "Gerência de RH"),
)
STATUS_TODOS = (
    STATUS_DIRETORIA, STATUS_FINANCEIRO, STATUS_RH, STATUS_APROVADA,
    STATUS_PUBLICADA, STATUS_ENCERRADA, STATUS_CORRECAO, STATUS_CANCELADA,
)
STATUS_FINAIS = (STATUS_ENCERRADA, STATUS_CANCELADA)

TIPOS = {"substituicao": "Substituição", "nova": "Vaga nova"}

MAX_CURRICULO_BYTES = 5 * 1024 * 1024
# Rota publica: teto por endereco e por vaga pra ninguem encher o banco de PDF.
MAX_CANDIDATURAS_IP_HORA = 5
MAX_CANDIDATURAS_VAGA = 1000

_RE_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass(frozen=True)
class Ator:
    usuario: str
    gestao: bool = False
    diretoria: bool = False
    financeiro: bool = False

    @property
    def aprovador(self) -> bool:
        return self.gestao or self.diretoria or self.financeiro


def _texto(valor, limite: int | None = None) -> str:
    texto = re.sub(r"[ \t]+", " ", str(valor or "")).strip()
    return texto[:limite] if limite else texto


def _dinheiro(valor) -> Decimal | None:
    """Aceita numero ou texto no formato brasileiro ("3.500,00")."""
    if valor is None or str(valor).strip() == "":
        return None
    texto = str(valor).strip().replace("R$", "").replace(" ", "")
    if "," in texto:
        texto = texto.replace(".", "").replace(",", ".")
    try:
        numero = Decimal(texto)
    except InvalidOperation:
        raise ValueError("Faixa salarial inválida.")
    if numero < 0 or numero > Decimal("9999999"):
        raise ValueError("Faixa salarial inválida.")
    return numero.quantize(Decimal("0.01"))


def _faixa(dados: dict) -> tuple[Decimal | None, Decimal | None]:
    minimo, maximo = _dinheiro(dados.get("faixa_min")), _dinheiro(dados.get("faixa_max"))
    if minimo is not None and maximo is not None and minimo > maximo:
        raise ValueError("A faixa salarial mínima não pode ser maior que a máxima.")
    return minimo, maximo


def _mesmo_usuario(a: str | None, b: str | None) -> bool:
    return bool(a) and str(a).strip().lower() == str(b or "").strip().lower()


def _evento(requisicao: RhRequisicaoVaga, usuario: str, acao: str, comentario: str = "") -> None:
    requisicao.eventos.append(RhRequisicaoEvento(
        ciclo=requisicao.ciclo or 1, acao=acao[:60], comentario=comentario or None,
        usuario=usuario, criado_em=agora_br(),
    ))
    requisicao.atualizado_em = agora_br()


# ── Cadastro de cargos e faixas (so' Gestao do RH) ─────────────────────
def listar_cargos_vaga(apenas_ativos: bool = False) -> list[RhCargoVaga]:
    query = RhCargoVaga.query
    if apenas_ativos:
        query = query.filter_by(ativo=True)
    return query.order_by(RhCargoVaga.nome).all()


def salvar_cargo_vaga(dados: dict, usuario: str, cargo: RhCargoVaga | None = None) -> RhCargoVaga:
    nome = _texto(dados.get("nome"), 120)
    if len(nome) < 2:
        raise ValueError("Informe o nome do cargo.")
    perfil = str(dados.get("perfil") or "").strip()
    if not perfil:
        raise ValueError("Descreva o perfil padronizado do cargo.")
    minimo, maximo = _faixa(dados)
    if minimo is None or maximo is None:
        raise ValueError("Informe a faixa salarial (mínimo e máximo).")

    duplicado = RhCargoVaga.query.filter(func.lower(RhCargoVaga.nome) == nome.lower()).first()
    if duplicado and (cargo is None or duplicado.id != cargo.id):
        raise ValueError("Já existe um cargo com esse nome.")

    if cargo is None:
        cargo = RhCargoVaga(criado_por=usuario, criado_em=agora_br())
        db.session.add(cargo)
    cargo.nome, cargo.perfil, cargo.faixa_min, cargo.faixa_max = nome, perfil, minimo, maximo
    cargo.ativo = bool(dados.get("ativo", True))
    cargo.atualizado_por, cargo.atualizado_em = usuario, agora_br()
    db.session.commit()
    return cargo


# ── Requisicao ─────────────────────────────────────────────────────────
def pode_ver(requisicao: RhRequisicaoVaga, ator: Ator) -> bool:
    return ator.aprovador or _mesmo_usuario(requisicao.solicitante, ator.usuario)


def pode_ver_salario(requisicao: RhRequisicaoVaga, ator: Ator) -> bool:
    """Faixa do cadastro e' so' de quem aprova e do RH. O solicitante so' ve
    a faixa que ele mesmo propos (vaga nova fora do cadastro)."""
    if ator.aprovador:
        return True
    return requisicao.cargo_id is None and _mesmo_usuario(requisicao.solicitante, ator.usuario)


def etapa_atual(requisicao: RhRequisicaoVaga) -> tuple[str, str, str] | None:
    return next((etapa for etapa in ETAPAS if etapa[0] == requisicao.status), None)


def pode_aprovar(requisicao: RhRequisicaoVaga, ator: Ator) -> bool:
    etapa = etapa_atual(requisicao)
    return bool(etapa and getattr(ator, etapa[1]))


def pode_editar(requisicao: RhRequisicaoVaga, ator: Ator) -> bool:
    return requisicao.status == STATUS_CORRECAO and (
        ator.gestao or _mesmo_usuario(requisicao.solicitante, ator.usuario)
    )


def pode_cancelar(requisicao: RhRequisicaoVaga, ator: Ator) -> bool:
    return requisicao.status not in STATUS_FINAIS and (
        ator.gestao or _mesmo_usuario(requisicao.solicitante, ator.usuario)
    )


def pode_reabrir(requisicao: RhRequisicaoVaga, ator: Ator) -> bool:
    return requisicao.status == STATUS_CANCELADA and (
        ator.gestao or _mesmo_usuario(requisicao.solicitante, ator.usuario)
    )


def listar(ator: Ator, status: str = "", busca: str = "") -> list[RhRequisicaoVaga]:
    query = RhRequisicaoVaga.query
    if not ator.aprovador:
        query = query.filter(func.lower(RhRequisicaoVaga.solicitante) == ator.usuario.strip().lower())
    if status in STATUS_TODOS:
        query = query.filter(RhRequisicaoVaga.status == status)
    termo = _texto(busca, 80)
    if termo:
        like = f"%{termo}%"
        query = query.filter(db.or_(
            RhRequisicaoVaga.numero.ilike(like), RhRequisicaoVaga.cargo_nome.ilike(like),
            RhRequisicaoVaga.departamento.ilike(like), RhRequisicaoVaga.solicitante.ilike(like),
        ))
    return query.order_by(RhRequisicaoVaga.id.desc()).limit(500).all()


def metricas(registros: list[RhRequisicaoVaga], ator: Ator) -> dict:
    return {
        "total": len(registros),
        "aguardando_mim": sum(1 for r in registros if pode_aprovar(r, ator)),
        "em_aprovacao": sum(1 for r in registros if etapa_atual(r)),
        "em_correcao": sum(1 for r in registros if r.status == STATUS_CORRECAO),
        "publicadas": sum(1 for r in registros if r.status == STATUS_PUBLICADA),
    }


def _aplicar_dados(requisicao: RhRequisicaoVaga, dados: dict) -> None:
    tipo = _texto(dados.get("tipo")).lower()
    if tipo not in TIPOS:
        raise ValueError("Informe se é substituição ou vaga nova.")

    cargo_id = dados.get("cargo_id")
    if cargo_id not in (None, "", 0, "0"):
        try:
            cargo = db.session.get(RhCargoVaga, int(cargo_id))
        except (TypeError, ValueError):
            cargo = None
        # Em correcao, o cargo ja' escolhido continua valendo mesmo se o RH o inativou depois.
        if not cargo or (not cargo.ativo and cargo.id != requisicao.cargo_id):
            raise ValueError("Cargo não encontrado no cadastro do RH.")
        requisicao.cargo_id, requisicao.cargo_nome = cargo.id, cargo.nome
        requisicao.perfil = cargo.perfil
        requisicao.faixa_min, requisicao.faixa_max = cargo.faixa_min, cargo.faixa_max
    else:
        if tipo != "nova":
            raise ValueError("Em substituição, escolha o cargo no cadastro do RH.")
        nome = _texto(dados.get("cargo_nome"), 120)
        perfil = str(dados.get("perfil") or "").strip()
        if len(nome) < 2:
            raise ValueError("Informe o cargo da vaga.")
        if not perfil:
            raise ValueError("Como o cargo não está no cadastro, descreva o perfil da vaga.")
        requisicao.cargo_id, requisicao.cargo_nome, requisicao.perfil = None, nome, perfil
        requisicao.faixa_min, requisicao.faixa_max = _faixa(dados)

    substituido = _texto(dados.get("substituido_nome"), 120)
    if tipo == "substituicao" and not substituido:
        raise ValueError("Informe quem está sendo substituído.")
    departamento = _texto(dados.get("departamento"), 80)
    if not departamento:
        raise ValueError("Informe o departamento da vaga.")
    try:
        quantidade = int(dados.get("quantidade") or 1)
    except (TypeError, ValueError):
        raise ValueError("Quantidade de vagas inválida.")
    if not 1 <= quantidade <= 50:
        raise ValueError("Quantidade de vagas inválida.")
    justificativa = str(dados.get("justificativa") or "").strip()
    if not justificativa:
        raise ValueError("Justifique a necessidade da vaga.")

    requisicao.tipo = tipo
    requisicao.substituido_nome = substituido if tipo == "substituicao" else None
    requisicao.departamento, requisicao.quantidade, requisicao.justificativa = departamento, quantidade, justificativa


def criar(dados: dict, ator: Ator) -> RhRequisicaoVaga:
    requisicao = RhRequisicaoVaga(
        # Numero definitivo depende do id: placeholder unico so' ate o flush.
        numero=f"T{secrets.token_hex(5)}", solicitante=ator.usuario, status=STATUS_DIRETORIA,
        ciclo=1, criado_em=agora_br(), cargo_nome="",
    )
    _aplicar_dados(requisicao, dados)
    db.session.add(requisicao)
    db.session.flush()
    requisicao.numero = f"RV-{requisicao.id:05d}"
    _evento(requisicao, ator.usuario, "Requisição aberta", "Enviada para aprovação da Diretoria.")
    db.session.commit()
    return requisicao


def atualizar(requisicao: RhRequisicaoVaga, dados: dict, ator: Ator) -> RhRequisicaoVaga:
    """Corrige e reenvia: as aprovacoes recomecam da Diretoria."""
    if not pode_editar(requisicao, ator):
        raise ValueError("Só dá para corrigir uma requisição devolvida para correção.")
    try:
        _aplicar_dados(requisicao, dados)
    except ValueError:
        db.session.rollback()
        raise
    requisicao.status = STATUS_DIRETORIA
    _evento(requisicao, ator.usuario, "Requisição corrigida", "Reenviada para aprovação da Diretoria.")
    db.session.commit()
    return requisicao


def aprovar(requisicao: RhRequisicaoVaga, ator: Ator, comentario: str = "") -> RhRequisicaoVaga:
    etapa = etapa_atual(requisicao)
    if not etapa:
        raise ValueError("Esta requisição não está aguardando aprovação.")
    if not getattr(ator, etapa[1]):
        raise ValueError(f"Você não tem permissão para aprovar a etapa {etapa[2]}.")
    indice = ETAPAS.index(etapa)
    requisicao.status = ETAPAS[indice + 1][0] if indice + 1 < len(ETAPAS) else STATUS_APROVADA
    _evento(requisicao, ator.usuario, f"Aprovada - {etapa[2]}", _texto(comentario))
    db.session.commit()
    return requisicao


def reprovar(requisicao: RhRequisicaoVaga, ator: Ator, motivo: str) -> RhRequisicaoVaga:
    etapa = etapa_atual(requisicao)
    if not etapa:
        raise ValueError("Esta requisição não está aguardando aprovação.")
    if not getattr(ator, etapa[1]):
        raise ValueError(f"Você não tem permissão para reprovar a etapa {etapa[2]}.")
    motivo = str(motivo or "").strip()
    if not motivo:
        raise ValueError("Informe o que precisa ser corrigido.")
    requisicao.status = STATUS_CORRECAO
    _evento(requisicao, ator.usuario, f"Devolvida para correção - {etapa[2]}", motivo)
    db.session.commit()
    return requisicao


def cancelar(requisicao: RhRequisicaoVaga, ator: Ator, motivo: str) -> RhRequisicaoVaga:
    if not pode_cancelar(requisicao, ator):
        raise ValueError("Esta requisição não pode ser cancelada.")
    motivo = str(motivo or "").strip()
    if not motivo:
        raise ValueError("Justifique o cancelamento.")
    requisicao.status = STATUS_CANCELADA
    requisicao.cancelamento_motivo = motivo
    _evento(requisicao, ator.usuario, "Cancelada", motivo)
    db.session.commit()
    return requisicao


def reabrir(requisicao: RhRequisicaoVaga, ator: Ator) -> RhRequisicaoVaga:
    """Reinicia o processo: ciclo novo, todas as aprovacoes de novo e link
    publico antigo morto (a vaga precisa ser publicada outra vez)."""
    if not pode_reabrir(requisicao, ator):
        raise ValueError("Só uma requisição cancelada pode ser reaberta.")
    requisicao.ciclo = (requisicao.ciclo or 1) + 1
    requisicao.status = STATUS_DIRETORIA
    requisicao.cancelamento_motivo = None
    requisicao.token_publico = None
    requisicao.publicada_em = requisicao.publicada_por = requisicao.encerrada_em = None
    _evento(requisicao, ator.usuario, "Reaberta", "Processo reiniciado: aguardando a Diretoria.")
    db.session.commit()
    return requisicao


def publicar(requisicao: RhRequisicaoVaga, ator: Ator, titulo: str, descricao: str) -> RhRequisicaoVaga:
    if not ator.gestao:
        raise ValueError("Só o RH publica a vaga.")
    if requisicao.status != STATUS_APROVADA:
        raise ValueError("A vaga só pode ser publicada depois de todas as aprovações.")
    titulo = _texto(titulo, 160)
    descricao = str(descricao or "").strip()
    if not titulo:
        raise ValueError("Informe o título da vaga para divulgação.")
    if not descricao:
        raise ValueError("Escreva a descrição da vaga para divulgação.")
    requisicao.titulo_publico, requisicao.descricao_publica = titulo, descricao
    requisicao.token_publico = secrets.token_urlsafe(24)
    requisicao.status = STATUS_PUBLICADA
    requisicao.publicada_em, requisicao.publicada_por = agora_br(), ator.usuario
    _evento(requisicao, ator.usuario, "Vaga publicada")
    db.session.commit()
    return requisicao


def encerrar(requisicao: RhRequisicaoVaga, ator: Ator, comentario: str = "") -> RhRequisicaoVaga:
    if not ator.gestao:
        raise ValueError("Só o RH encerra a vaga.")
    if requisicao.status != STATUS_PUBLICADA:
        raise ValueError("Só uma vaga publicada pode ser encerrada.")
    requisicao.status = STATUS_ENCERRADA
    requisicao.encerrada_em = agora_br()
    _evento(requisicao, ator.usuario, "Vaga encerrada", _texto(comentario))
    db.session.commit()
    return requisicao


# ── Candidatura: pagina publica, sem login ─────────────────────────────
def obter_vaga_publica(token: str) -> RhRequisicaoVaga | None:
    """So' devolve a vaga enquanto estiver Publicada: encerrar, cancelar ou
    reabrir mata o link."""
    if not token or len(token) > 64:
        return None
    requisicao = RhRequisicaoVaga.query.filter_by(token_publico=token).first()
    if not requisicao or requisicao.status != STATUS_PUBLICADA:
        return None
    return requisicao


def registrar_candidatura(requisicao: RhRequisicaoVaga, dados, arquivo, ip: str) -> RhCandidato | None:
    """Devolve None quando o e-mail ja' se candidatou a esta vaga: vale a
    primeira candidatura, e a pagina responde igual pra nao revelar a um
    terceiro quem se candidatou."""
    nome = _texto(dados.get("nome"), 120)
    email = _texto(dados.get("email"), 160).lower()
    telefone = _texto(dados.get("telefone"), 40)
    mensagem = str(dados.get("mensagem") or "").strip()[:2000]
    if len(nome) < 3:
        raise ValueError("Informe seu nome completo.")
    if not _RE_EMAIL.match(email):
        raise ValueError("Informe um e-mail válido.")
    if len(re.sub(r"\D", "", telefone)) < 8:
        raise ValueError("Informe um telefone para contato.")
    if str(dados.get("consentimento") or "").strip().lower() not in {"1", "true", "on", "sim"}:
        raise ValueError("É preciso autorizar o uso dos seus dados para participar do processo seletivo.")

    if arquivo is None or not (arquivo.filename or "").strip():
        raise ValueError("Anexe seu currículo em PDF.")
    nome_arquivo = re.sub(r"[^\w.\- ]", "_", (arquivo.filename or "").replace("\\", "/").rsplit("/", 1)[-1]).strip()
    if not nome_arquivo.lower().endswith(".pdf"):
        raise ValueError("O currículo precisa ser um arquivo PDF.")
    conteudo = arquivo.read(MAX_CURRICULO_BYTES + 1)
    if not conteudo:
        raise ValueError("Arquivo vazio.")
    if len(conteudo) > MAX_CURRICULO_BYTES:
        raise ValueError("Currículo muito grande (máximo 5 MB).")
    # Confere o conteudo, nao so' a extensao: o arquivo sera aberto pelo RH.
    if not conteudo.lstrip()[:5] == b"%PDF-":
        raise ValueError("O arquivo enviado não é um PDF válido.")

    agora = agora_br()
    if ip and RhCandidato.query.filter(
        RhCandidato.ip_address == ip, RhCandidato.criado_em >= agora - timedelta(hours=1)
    ).count() >= MAX_CANDIDATURAS_IP_HORA:
        raise ValueError("Muitas candidaturas enviadas deste endereço. Tente novamente mais tarde.")
    if RhCandidato.query.filter_by(requisicao_id=requisicao.id).count() >= MAX_CANDIDATURAS_VAGA:
        raise ValueError("Esta vaga não está mais recebendo candidaturas.")
    if RhCandidato.query.filter_by(requisicao_id=requisicao.id, email=email).first():
        return None

    candidato = RhCandidato(
        requisicao_id=requisicao.id, nome=nome, email=email, telefone=telefone, mensagem=mensagem or None,
        curriculo_nome=nome_arquivo[:260], curriculo_tamanho=len(conteudo), curriculo_dados=conteudo,
        consentimento_em=agora, ip_address=(ip or "")[:64] or None, criado_em=agora,
    )
    db.session.add(candidato)
    db.session.commit()
    return candidato


def registrar_acesso_candidato(candidato: RhCandidato, usuario: str, acao: str) -> None:
    db.session.add(RhCandidatoAcesso(
        requisicao_id=candidato.requisicao_id, candidato_id=candidato.id, candidato_nome=candidato.nome,
        acao=acao, usuario=usuario, criado_em=agora_br(),
    ))


def excluir_candidato(candidato: RhCandidato, usuario: str) -> None:
    registrar_acesso_candidato(candidato, usuario, "excluiu")
    db.session.delete(candidato)
    db.session.commit()
