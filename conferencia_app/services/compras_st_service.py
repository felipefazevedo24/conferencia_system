"""Compras > Servico terceiro - cotacao com desenho tecnico.

Decisoes do usuario (11/11/2026):
  - cadastro de fornecedor proprio, ligado a homologacao; so' entra na
    cotacao quem esta' Homologado e dentro da validade;
  - tipos de servico editaveis por Compras;
  - a Engenharia cria (itens com desenho obrigatorio), Compras escolhe;
  - o fornecedor informa por peca: net, % IPI/ICMS/ISS e bruto; o sistema
    confere o bruto pela formula da nota (ICMS/ISS por dentro, IPI por
    fora) e, se fugir mais de 1%, pede confirmacao - nao bloqueia;
  - sugerida = menor total net; escolher outra exige justificativa;
  - liga a ordem do Servico Terceiro da expedicao pela OC informada por
    Compras ou pela OS dos itens.
"""
from __future__ import annotations

import hashlib
import re
import secrets
from datetime import date, datetime, timedelta

from ..extensions import db
from ..models import (
    CompraStCotacao,
    CompraStCotacaoItem,
    CompraStDesenho,
    CompraStFornecedor,
    CompraStResposta,
    CompraStRespostaItem,
    CompraStTipoServico,
    ComprasHomologacaoFornecedor,
)
from ..tempo import agora_br

TIPOS_INICIAIS = ("Pintura", "Usinagem", "Tratamento térmico")
VALIDADE_LINK_DIAS = 14
MAX_ARQUIVO_BYTES = 20 * 1024 * 1024
TOLERANCIA_BRUTO = 0.01  # 1%
FRETES = ("CIF", "FOB")


class DivergenciaBruto(ValueError):
    """Bruto informado fugiu da formula da nota - a rota devolve 409 e o
    link pergunta antes de reenviar com `confirmar_divergencia`."""

    def __init__(self, itens: list[str]):
        super().__init__(
            "O preço bruto de " + ", ".join(itens) + " não bate com o cálculo da nota "
            "(ICMS/ISS por dentro, IPI por fora). Confira; se estiver correto, confirme o envio."
        )
        self.itens = itens


def _texto(valor, tamanho: int) -> str | None:
    return str(valor or "").strip()[:tamanho] or None


def _num(valor) -> float | None:
    if valor in (None, ""):
        return None
    texto = str(valor).strip().replace(" ", "")
    if "," in texto:
        texto = texto.replace(".", "").replace(",", ".")
    try:
        return float(texto)
    except ValueError:
        return None


def _emails(valor) -> str:
    partes = [e.strip() for e in re.split(r"[;,]", str(valor or "")) if e.strip()]
    invalidos = [e for e in partes if "@" not in e]
    if invalidos:
        raise ValueError(f"E-mail inválido: {invalidos[0]}")
    if not partes:
        raise ValueError("Informe ao menos um e-mail do fornecedor.")
    texto = "; ".join(partes)
    if len(texto) > 300:
        raise ValueError("Lista de e-mails muito longa (máximo 300 caracteres).")
    return texto


# ── Tipos de servico ───────────────────────────────────────────────────
def garantir_tipos_iniciais() -> None:
    if CompraStTipoServico.query.first():
        return
    for nome in TIPOS_INICIAIS:
        db.session.add(CompraStTipoServico(nome=nome, criado_por="sistema"))
    db.session.commit()


def listar_tipos(somente_ativos: bool = False) -> list[CompraStTipoServico]:
    garantir_tipos_iniciais()
    query = CompraStTipoServico.query
    if somente_ativos:
        query = query.filter_by(ativo=True)
    return query.order_by(CompraStTipoServico.nome).all()


def salvar_tipo(nome, usuario: str, tipo_id: int | None = None) -> CompraStTipoServico:
    nome = _texto(nome, 80)
    if not nome:
        raise ValueError("Informe o nome do tipo de serviço.")
    existente = CompraStTipoServico.query.filter(db.func.lower(CompraStTipoServico.nome) == nome.lower()).first()
    tipo = db.session.get(CompraStTipoServico, tipo_id) if tipo_id else None
    if tipo_id and not tipo:
        raise ValueError("Tipo de serviço não encontrado.")
    if existente and existente is not tipo:
        raise ValueError(f"O tipo \"{nome}\" já existe.")
    tipo = tipo or CompraStTipoServico(criado_por=usuario)
    tipo.nome = nome
    db.session.add(tipo)
    db.session.commit()
    return tipo


def alternar_tipo(tipo: CompraStTipoServico, ativo: bool) -> CompraStTipoServico:
    tipo.ativo = bool(ativo)
    db.session.commit()
    return tipo


# ── Fornecedores ───────────────────────────────────────────────────────
def nome_fornecedor(f: CompraStFornecedor) -> str:
    h = f.homologacao
    if h:
        return h.nome_fantasia or h.razao_social
    return f.razao_social or f"Fornecedor {f.id}"


def motivo_nao_autorizado(f: CompraStFornecedor, hoje: date | None = None) -> str | None:
    """None = pode receber cotacao. Calculado na hora (a homologacao - ou a
    validade do cadastro sem homologacao - vence sozinha)."""
    hoje = hoje or agora_br().date()
    h = f.homologacao
    if not f.ativo:
        return "Fornecedor inativo."
    if not f.homologacao_id:
        if not f.valido_ate or f.valido_ate < hoje:
            return "Cadastro sem homologação vencido" + (f" em {f.valido_ate:%d/%m/%Y}." if f.valido_ate else ".")
        return None
    if not h or h.status != ComprasHomologacaoFornecedor.STATUS_HOMOLOGADO:
        return f"Homologação {h.status.lower() if h else 'inexistente'}."
    if not h.valido_ate or h.valido_ate < hoje:
        return "Homologação vencida" + (f" em {h.valido_ate:%d/%m/%Y}." if h.valido_ate else ".")
    return None


def homologacoes_para_vincular() -> list[ComprasHomologacaoFornecedor]:
    return (ComprasHomologacaoFornecedor.query
            .filter(ComprasHomologacaoFornecedor.status == ComprasHomologacaoFornecedor.STATUS_HOMOLOGADO)
            .order_by(ComprasHomologacaoFornecedor.razao_social).all())


def listar_fornecedores() -> list[CompraStFornecedor]:
    return sorted(CompraStFornecedor.query.all(), key=lambda f: nome_fornecedor(f).lower())


def cnpj_valido(cnpj: str) -> bool:
    if len(cnpj) != 14 or not cnpj.isdigit() or cnpj == cnpj[0] * 14:
        return False
    for tamanho in (12, 13):
        pesos = list(range(tamanho - 7, 1, -1)) + list(range(9, 1, -1))
        resto = sum(int(d) * p for d, p in zip(cnpj[:tamanho], pesos)) % 11
        if int(cnpj[tamanho]) != (0 if resto < 2 else 11 - resto):
            return False
    return True


def cnpj_do_fornecedor(f: CompraStFornecedor) -> str:
    return re.sub(r"\D", "", (f.homologacao.cnpj if f.homologacao else f.cnpj) or "")


def sem_homologacao(f: CompraStFornecedor | None) -> bool:
    return bool(f) and not f.homologacao_id


def _dados_sem_homologacao(fornecedor: CompraStFornecedor | None, dados: dict) -> dict:
    razao = _texto(dados.get("razao_social"), 200)
    cnpj = re.sub(r"\D", "", str(dados.get("cnpj") or ""))
    justificativa = (str(dados.get("justificativa") or "")).strip()
    try:
        valido_ate = date.fromisoformat(str(dados.get("valido_ate") or "")[:10])
    except ValueError:
        valido_ate = None
    if not razao:
        raise ValueError("Informe a razão social.")
    if not cnpj_valido(cnpj):
        raise ValueError("CNPJ inválido.")
    if not justificativa:
        raise ValueError("Informe a justificativa do cadastro sem homologação.")
    if not valido_ate:
        raise ValueError("Informe até quando o fornecedor pode receber cotação sem homologação.")
    if valido_ate < agora_br().date():
        raise ValueError("A validade não pode ser anterior a hoje.")
    for outro in CompraStFornecedor.query.all():
        if outro is not fornecedor and cnpj_do_fornecedor(outro) == cnpj:
            raise ValueError(f"CNPJ já cadastrado como fornecedor de serviço terceiro ({nome_fornecedor(outro)}).")
    return {"razao_social": razao, "cnpj": cnpj, "justificativa": justificativa, "valido_ate": valido_ate}


def salvar_fornecedor(dados: dict, usuario: str, fornecedor_id: int | None = None,
                      pode_homologado: bool = True, pode_sem_homologacao: bool = False) -> CompraStFornecedor:
    """Homologado (PAGE_COMPRAS_ST_ANALISE) ou sem homologacao
    (PAGE_COMPRAS_ST_FORNECEDOR_SEM_HOMOLOGACAO, so' o time de Compras). Na
    edicao o tipo do cadastro nao muda."""
    fornecedor = db.session.get(CompraStFornecedor, fornecedor_id) if fornecedor_id else None
    if fornecedor_id and not fornecedor:
        raise ValueError("Fornecedor não encontrado.")
    sem = sem_homologacao(fornecedor) if fornecedor else bool(dados.get("sem_homologacao"))
    if sem and not pode_sem_homologacao:
        raise PermissionError("Só o time de Compras cadastra ou altera fornecedor sem homologação.")
    if not sem and not pode_homologado:
        raise PermissionError("Sem permissão para cadastrar fornecedor homologado.")
    tipos = CompraStTipoServico.query.filter(CompraStTipoServico.id.in_([int(t) for t in dados.get("tipo_ids") or []])).all()
    if sem:
        proprios = _dados_sem_homologacao(fornecedor, dados)
        homologacao = None
    else:
        homologacao = db.session.get(ComprasHomologacaoFornecedor, int(dados.get("homologacao_id") or 0))
        if not homologacao:
            raise ValueError("Escolha a homologação do fornecedor.")
        duplicado = CompraStFornecedor.query.filter_by(homologacao_id=homologacao.id).first()
        if duplicado and duplicado is not fornecedor:
            raise ValueError(f"{homologacao.razao_social} já está cadastrado como fornecedor de serviço terceiro.")
    if not tipos:
        raise ValueError("Marque ao menos um tipo de serviço.")
    fornecedor = fornecedor or CompraStFornecedor(criado_por=usuario)
    fornecedor.homologacao = homologacao
    if sem:
        for campo, valor in proprios.items():
            setattr(fornecedor, campo, valor)
    fornecedor.emails = _emails(dados.get("emails"))
    fornecedor.contato = _texto(dados.get("contato"), 120)
    fornecedor.telefone = _texto(dados.get("telefone"), 40)
    fornecedor.tipos = tipos
    if "ativo" in dados:
        fornecedor.ativo = bool(dados.get("ativo"))
    fornecedor.atualizado_em, fornecedor.atualizado_por = agora_br(), usuario
    db.session.add(fornecedor)
    db.session.commit()
    return fornecedor


def fornecedores_do_tipo(tipo_id: int) -> list[CompraStFornecedor]:
    """Todos do tipo; a tela mostra os nao autorizados desabilitados, com o motivo."""
    return [f for f in listar_fornecedores() if any(t.id == tipo_id for t in f.tipos)]


# ── Cotacao ────────────────────────────────────────────────────────────
def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def criar_cotacao(dados: dict, arquivos: dict, usuario: str) -> tuple[CompraStCotacao, list[tuple[CompraStResposta, str]]]:
    """`dados`: tipo_id, texto_fornecedor, fornecedor_ids, itens [{codigo,
    descricao, quantidade, n_os, arquivos: [chave...]}]. `arquivos`: {chave:
    (nome, content_type, bytes)}. Devolve a cotacao e (resposta, token) de
    cada fornecedor - o token bruto so' existe aqui."""
    tipo = db.session.get(CompraStTipoServico, int(dados.get("tipo_id") or 0))
    if not tipo or not tipo.ativo:
        raise ValueError("Escolha o tipo de serviço.")
    itens = dados.get("itens") or []
    if not itens:
        raise ValueError("Informe ao menos uma peça.")
    fornecedores = CompraStFornecedor.query.filter(CompraStFornecedor.id.in_([int(i) for i in dados.get("fornecedor_ids") or []])).all()
    if not fornecedores:
        raise ValueError("Escolha ao menos um fornecedor.")
    for f in fornecedores:
        motivo = motivo_nao_autorizado(f)
        if motivo:
            raise ValueError(f"{nome_fornecedor(f)} não pode receber cotação: {motivo}")
        if tipo not in f.tipos:
            raise ValueError(f"{nome_fornecedor(f)} não presta o serviço {tipo.nome}.")

    cotacao = CompraStCotacao(tipo=tipo, texto_fornecedor=str(dados.get("texto_fornecedor") or "").strip() or None,
                              status="Aberta", criado_por=usuario, criado_em=agora_br())
    for n, bruto in enumerate(itens, start=1):
        descricao = _texto(bruto.get("descricao"), 300)
        quantidade = _num(bruto.get("quantidade"))
        if not descricao:
            raise ValueError(f"Peça {n}: informe a descrição.")
        if not quantidade or quantidade <= 0:
            raise ValueError(f"Peça {n}: informe a quantidade.")
        item = CompraStCotacaoItem(ordem=n, codigo=_texto(bruto.get("codigo"), 60), descricao=descricao,
                                   quantidade=quantidade, n_os=_texto(bruto.get("n_os"), 40))
        for chave in bruto.get("arquivos") or []:
            if chave not in arquivos:
                continue
            nome, content_type, conteudo = arquivos[chave]
            if not conteudo:
                continue
            if len(conteudo) > MAX_ARQUIVO_BYTES:
                raise ValueError(f"Peça {n}: o arquivo {nome} passa de 20 MB.")
            item.desenhos.append(CompraStDesenho(nome_arquivo=_texto(nome, 255) or "desenho", content_type=content_type,
                                                 tamanho=len(conteudo), conteudo=conteudo))
        if not item.desenhos:
            raise ValueError(f"Peça {n} ({descricao}): anexe o desenho técnico.")
        cotacao.itens.append(item)

    db.session.add(cotacao)
    links = []
    expira = agora_br() + timedelta(days=VALIDADE_LINK_DIAS)
    for f in fornecedores:
        token = secrets.token_urlsafe(32)
        resposta = CompraStResposta(fornecedor=f, emails=f.emails, token_hash=_hash(token), expira_em=expira, status="Pendente")
        cotacao.respostas.append(resposta)
        links.append((resposta, token))
    db.session.commit()
    return cotacao, links


def listar_cotacoes() -> list[CompraStCotacao]:
    return CompraStCotacao.query.order_by(CompraStCotacao.criado_em.desc()).all()


def resposta_por_token(token: str) -> CompraStResposta | None:
    return CompraStResposta.query.filter_by(token_hash=_hash(token or "")).first() if token else None


def link_valido(resposta: CompraStResposta) -> bool:
    return (resposta.status == "Pendente" and resposta.cotacao.status == "Aberta"
            and resposta.expira_em >= agora_br())


def calcular_bruto(net: float, ipi: float, icms: float, iss: float) -> float | None:
    """Como sai na nota: ICMS/ISS por dentro, IPI por fora."""
    por_dentro = (icms + iss) / 100
    if por_dentro >= 1:
        return None
    return round(net / (1 - por_dentro) * (1 + ipi / 100), 4)


def _pct(valor, rotulo: str, n: int) -> float:
    numero = _num(valor)
    numero = 0.0 if numero is None else numero
    if not 0 <= numero < 100:
        raise ValueError(f"Peça {n}: {rotulo} deve estar entre 0 e 100%.")
    return numero


def submeter_resposta(resposta: CompraStResposta, dados: dict, proposta: tuple | None = None) -> CompraStResposta:
    if not link_valido(resposta):
        raise ValueError("Este link de cotação já foi usado, expirou ou a cotação foi encerrada.")
    precos = {int(p.get("item_id") or 0): p for p in dados.get("itens") or []}
    linhas, divergentes = [], []
    for item in resposta.cotacao.itens:
        p = precos.get(item.id) or {}
        n = item.ordem
        net, bruto = _num(p.get("preco_net")), _num(p.get("preco_bruto"))
        if not net or net <= 0:
            raise ValueError(f"Peça {n} ({item.descricao}): informe o preço net por peça.")
        if not bruto or bruto <= 0:
            raise ValueError(f"Peça {n} ({item.descricao}): informe o preço bruto por peça.")
        ipi, icms, iss = _pct(p.get("ipi_pct"), "IPI", n), _pct(p.get("icms_pct"), "ICMS", n), _pct(p.get("iss_pct"), "ISS", n)
        calculado = calcular_bruto(net, ipi, icms, iss)
        if calculado is None:
            raise ValueError(f"Peça {n}: ICMS + ISS não pode chegar a 100%.")
        if abs(bruto - calculado) > calculado * TOLERANCIA_BRUTO:
            divergentes.append(f"peça {n}")
        linhas.append(CompraStRespostaItem(item=item, preco_net=net, ipi_pct=ipi, icms_pct=icms, iss_pct=iss,
                                           preco_bruto=bruto, bruto_calculado=calculado))
    if divergentes and not dados.get("confirmar_divergencia"):
        raise DivergenciaBruto(divergentes)

    prazo = _texto(dados.get("prazo_entrega"), 80)
    if not prazo:
        raise ValueError("Informe o prazo de entrega.")
    condicao = _texto(dados.get("condicao_pagamento"), 120)
    if not condicao:
        raise ValueError("Informe a condição de pagamento.")
    frete_tipo = str(dados.get("frete_tipo") or "").strip().upper()
    if frete_tipo not in FRETES:
        raise ValueError("Informe o frete: CIF ou FOB.")
    frete_valor = _num(dados.get("frete_valor"))
    if frete_valor is not None and frete_valor < 0:
        raise ValueError("Valor do frete inválido.")
    validade = None
    if dados.get("validade_proposta"):
        try:
            validade = datetime.strptime(str(dados["validade_proposta"])[:10], "%Y-%m-%d").date()
        except ValueError:
            raise ValueError("Validade da proposta inválida.")
    if proposta and proposta[2]:
        if len(proposta[2]) > MAX_ARQUIVO_BYTES:
            raise ValueError("A proposta anexada passa de 20 MB.")
        resposta.proposta_nome, resposta.proposta_content_type, resposta.proposta_arquivo = _texto(proposta[0], 255), proposta[1], proposta[2]

    resposta.itens = linhas
    resposta.prazo_entrega, resposta.condicao_pagamento, resposta.validade_proposta = prazo, condicao, validade
    resposta.frete_tipo, resposta.frete_valor = frete_tipo, frete_valor
    resposta.observacoes = str(dados.get("observacoes") or "").strip() or None
    # Frete entra no total: e' custo da compra do servico.
    frete = frete_valor or 0
    resposta.total_net = round(sum(l.preco_net * l.item.quantidade for l in linhas) + frete, 2)
    resposta.total_bruto = round(sum(l.preco_bruto * l.item.quantidade for l in linhas) + frete, 2)
    resposta.divergencia_bruto = bool(divergentes)
    resposta.status, resposta.recebida_em = "Recebida", agora_br()
    _recalcular_sugerida(resposta.cotacao)
    db.session.commit()
    return resposta


def _recalcular_sugerida(cotacao: CompraStCotacao) -> None:
    recebidas = sorted((r for r in cotacao.respostas if r.status == "Recebida"), key=lambda r: (r.total_net, r.id))
    for idx, r in enumerate(recebidas):
        r.is_sugerida = idx == 0


def escolher(cotacao: CompraStCotacao, resposta_id: int, justificativa: str | None, usuario: str) -> CompraStCotacao:
    if cotacao.status != "Aberta":
        raise ValueError("Esta cotação não está aberta.")
    resposta = next((r for r in cotacao.respostas if r.id == resposta_id), None)
    if not resposta or resposta.status != "Recebida":
        raise ValueError("Escolha uma proposta já recebida.")
    justificativa = str(justificativa or "").strip()
    if not resposta.is_sugerida and not justificativa:
        raise ValueError("Informe a justificativa para escolher uma proposta diferente da sugerida (menor total net).")
    cotacao.vencedora = resposta
    cotacao.justificativa = justificativa or None
    cotacao.status = "Fechada"
    cotacao.escolhida_em, cotacao.escolhida_por = agora_br(), usuario
    db.session.commit()
    return cotacao


def informar_oc(cotacao: CompraStCotacao, cod_ordem_compra) -> CompraStCotacao:
    if cotacao.status != "Fechada":
        raise ValueError("Informe a OC depois de escolher o vencedor.")
    cotacao.cod_ordem_compra = _texto(cod_ordem_compra, 40)
    db.session.commit()
    return cotacao


def cancelar(cotacao: CompraStCotacao) -> CompraStCotacao:
    if cotacao.status != "Aberta":
        raise ValueError("Só é possível cancelar uma cotação aberta.")
    cotacao.status = "Cancelada"
    db.session.commit()
    return cotacao


def cotacoes_da_ordem_st(cod_ordem_compra, numeros_os: list[str]) -> list[CompraStCotacao]:
    """Cotacoes fechadas ligadas a uma ordem do Servico Terceiro: pela OC
    informada por Compras ou pela OS de algum item."""
    oc = str(cod_ordem_compra or "").strip()
    os_validas = {str(o).strip() for o in numeros_os if str(o or "").strip()}
    achadas = []
    for c in CompraStCotacao.query.filter_by(status="Fechada").all():
        if (oc and c.cod_ordem_compra == oc) or any(i.n_os and i.n_os in os_validas for i in c.itens):
            achadas.append(c)
    return achadas
