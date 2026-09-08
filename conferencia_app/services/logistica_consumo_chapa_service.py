"""Servico do modulo de Consumo de Chapa (Nesting) da Logistica.

Workflow: Nesting (recem importado - lista gerada pelo PCP, ainda NAO
conferida pela logistica; nenhuma tratativa por peca disponivel, so' o
"Confirmar Recebimento") -> Nesting Liberado (logistica confirmou o
recebimento da lista de separacao - libera observacao/baixa/divergencia
por peca e o Concluir) -> Concluido (confirmacao manual depois, ex.:
baixa de estoque conferida). Ramo lateral "Erro": o gestor pode marcar
uma divergencia (motivo obrigatorio) a partir do Nesting Liberado -
TRAVA o "Concluir" ate' ser resolvida (volta pra Nesting Liberado).
Ver logistica_consumo_chapa_parser.py pra extracao dos dados do HTML."""
from __future__ import annotations

from datetime import datetime

from ..extensions import db
from ..models import LogisticaConsumoChapaNesting, LogisticaConsumoChapaPeca
from .logistica_consumo_chapa_parser import parse_relatorio_nesting_html

STATUS_SLUGS = {
    "Nesting": "nesting",
    "Nesting Liberado": "nesting_liberado",
    "Erro": "erro",
    "Concluido": "concluido",
}


def buscar_erp_materiais_por_os(os_numeros: set[str] | list[str]) -> tuple[dict, bool]:
    """Busca no ERP (tabela tlis_mat, via o modulo Compras - que ja fala
    com o Postgres do ERP pela bridge/Tailscale, com fallback pra conexao
    direta) a Qtde planejada (tlis_mat.qtde) e a Qtde ja utilizada
    (tlis_mat.qtde_utilizada) de materia-prima por OS, casando pelo
    codigo interno do produto (mesmo codigo que ja usamos como
    codigo_material do Nesting - ver _descricao_material nas rotas).

    Devolve (index, erp_indisponivel):
    - index: {(n_os, cod_interno): {"qtde_necessaria", "qtde_utilizada", "unidade"}}
    - erp_indisponivel: True se a bridge/Postgres do ERP falhou (tela de
      detalhe do Nesting continua funcionando normalmente sem esse dado -
      NUNCA deixa essa consulta estourar excecao pro resto da tela).
    """
    numeros = {str(n).strip() for n in (os_numeros or []) if str(n or "").strip()}
    if not numeros:
        return {}, False
    try:
        from ..compras.services import compras_service

        linhas = compras_service.listar_materiais_por_os(n_os=",".join(sorted(numeros)))
    except Exception:
        return {}, True

    index: dict = {}
    for linha in linhas or []:
        chave = (str(linha.get("n_os") or "").strip(), str(linha.get("cod_interno") or "").strip())
        index[chave] = {
            "qtde_necessaria": linha.get("qtde_necessaria"),
            "qtde_utilizada": linha.get("qtde_utilizada"),
            "unidade": linha.get("unidade"),
        }
    return index, False


def status_slug(status: str) -> str:
    return STATUS_SLUGS.get(status, "nesting")


def importar_relatorio(conteudo_html: str | bytes, nome_arquivo: str, usuario: str) -> dict:
    """Importa um relatorio de Nesting (HTML exportado pela maquina de
    corte) - um arquivo pode conter varios Nestings (uma pagina cada). Se
    o numero do Programa ja existir no banco (reimport do mesmo arquivo,
    ou correcao), ATUALIZA o cabecalho e substitui a lista de pecas -
    nao duplica. Devolve um resumo (quantos criados/atualizados) e a
    lista dos Nestings resultantes."""
    paginas = parse_relatorio_nesting_html(conteudo_html)
    if not paginas:
        raise ValueError(
            "Nenhum Nesting encontrado nesse arquivo - confira se é o relatório "
            "exportado direto da máquina de corte (.HTML)."
        )

    criados = 0
    atualizados = 0
    nestings = []
    for pagina in paginas:
        pecas_dados = pagina.pop("pecas")
        numero_programa = pagina["numero_programa"]

        nesting = LogisticaConsumoChapaNesting.query.filter_by(numero_programa=numero_programa).first()
        if nesting:
            atualizados += 1
            for campo, valor in pagina.items():
                setattr(nesting, campo, valor)
            nesting.arquivo_origem = nome_arquivo
        else:
            criados += 1
            nesting = LogisticaConsumoChapaNesting(
                status="Nesting",
                arquivo_origem=nome_arquivo,
                criado_por=usuario,
                **pagina,
            )
            db.session.add(nesting)
            db.session.flush()  # ganha nesting.id antes de linkar as pecas

        # Upsert das pecas por (peca_numero, os_numero) - reimportar o
        # mesmo arquivo (ex.: correcao) NAO pode apagar observacao/baixa ja
        # confirmada pelo gestor numa peca que continua existindo no
        # relatorio; so remove pecas que sumiram da nova versao.
        existentes = {
            (p.peca_numero, p.os_numero): p
            for p in LogisticaConsumoChapaPeca.query.filter_by(nesting_id=nesting.id).all()
        }
        vistos = set()
        for peca_dados in pecas_dados:
            chave = (peca_dados.get("peca_numero"), peca_dados.get("os_numero"))
            vistos.add(chave)
            peca_existente = existentes.get(chave)
            if peca_existente:
                for campo, valor in peca_dados.items():
                    setattr(peca_existente, campo, valor)
            else:
                db.session.add(LogisticaConsumoChapaPeca(nesting_id=nesting.id, **peca_dados))
        for chave, peca_existente in existentes.items():
            if chave not in vistos:
                db.session.delete(peca_existente)

        nestings.append(nesting)

    db.session.commit()
    return {"criados": criados, "atualizados": atualizados, "total": len(paginas), "nestings": nestings}


def listar_nestings(status: str | None = None, busca: str = "") -> list[LogisticaConsumoChapaNesting]:
    query = LogisticaConsumoChapaNesting.query
    if status:
        query = query.filter_by(status=status)
    busca = (busca or "").strip()
    if busca:
        termo = f"%{busca}%"
        query = query.filter(
            db.or_(
                LogisticaConsumoChapaNesting.numero_programa.ilike(termo),
                LogisticaConsumoChapaNesting.codigo_material.ilike(termo),
                LogisticaConsumoChapaNesting.material.ilike(termo),
                LogisticaConsumoChapaNesting.nome_tarefa.ilike(termo),
            )
        )
    return query.order_by(LogisticaConsumoChapaNesting.criado_em.desc()).all()


# ── Confirmacao de recebimento pela logistica (Nesting -> Nesting Liberado)
# - enquanto estiver em "Nesting" (recem importado), NENHUMA acao por peca
# fica disponivel, so' essa confirmacao.
def confirmar_recebimento_nesting(nesting: LogisticaConsumoChapaNesting, usuario: str) -> LogisticaConsumoChapaNesting:
    if nesting.status != "Nesting":
        raise ValueError("O recebimento desse Nesting já foi confirmado.")
    nesting.status = "Nesting Liberado"
    nesting.confirmado_em = datetime.now()
    nesting.confirmado_por = usuario
    db.session.commit()
    return nesting


def concluir_nesting(nesting: LogisticaConsumoChapaNesting, usuario: str) -> LogisticaConsumoChapaNesting:
    if nesting.status == "Nesting":
        raise ValueError("Confirme o recebimento da lista de separação antes de concluir.")
    if nesting.status == "Erro":
        raise ValueError("Esse Nesting está marcado como erro - resolva a divergência antes de concluir.")
    if nesting.status != "Nesting Liberado":
        raise ValueError("Esse Nesting já está concluído.")
    nesting.status = "Concluido"
    nesting.concluido_em = datetime.now()
    nesting.concluido_por = usuario
    db.session.commit()
    return nesting


def estornar_nesting(nesting: LogisticaConsumoChapaNesting) -> LogisticaConsumoChapaNesting:
    if nesting.status != "Concluido":
        raise ValueError("Esse Nesting não está concluído.")
    nesting.status = "Nesting Liberado"
    nesting.concluido_em = None
    nesting.concluido_por = None
    db.session.commit()
    return nesting


# ── Ramo lateral "Erro" (divergencia) - trava o Concluir ate' ser resolvido.
def marcar_erro_nesting(
    nesting: LogisticaConsumoChapaNesting, motivo: str, usuario: str
) -> LogisticaConsumoChapaNesting:
    if nesting.status == "Nesting":
        raise ValueError("Confirme o recebimento da lista de separação antes de marcar uma divergência.")
    if nesting.status == "Concluido":
        raise ValueError("Esse Nesting já está concluído - estorne antes de marcar uma divergência.")
    if nesting.status == "Erro":
        raise ValueError("Esse Nesting já está marcado como erro.")
    motivo = (motivo or "").strip()
    if not motivo:
        raise ValueError("Informe o motivo da divergência.")
    nesting.status = "Erro"
    nesting.motivo_erro = motivo[:1000]
    nesting.erro_marcado_em = datetime.now()
    nesting.erro_marcado_por = usuario
    db.session.commit()
    return nesting


def resolver_erro_nesting(nesting: LogisticaConsumoChapaNesting, usuario: str) -> LogisticaConsumoChapaNesting:
    if nesting.status != "Erro":
        raise ValueError("Esse Nesting não está marcado como erro.")
    nesting.status = "Nesting Liberado"
    nesting.erro_resolvido_em = datetime.now()
    nesting.erro_resolvido_por = usuario
    db.session.commit()
    return nesting


# ── Observacao e confirmacao de baixa POR PECA (independente da conclusao
# do Nesting inteiro - ver concluir_nesting/estornar_nesting acima) - so'
# ficam disponiveis depois que a logistica confirmar o recebimento (ver
# confirmar_recebimento_nesting acima; nesting.status != "Nesting"). ──────
def _garantir_tratativa_liberada(peca: LogisticaConsumoChapaPeca) -> None:
    if peca.nesting.status == "Nesting":
        raise ValueError("Confirme o recebimento da lista de separação antes de mexer nessa peça.")


def salvar_observacao_peca(peca: LogisticaConsumoChapaPeca, observacao: str | None) -> LogisticaConsumoChapaPeca:
    _garantir_tratativa_liberada(peca)
    peca.observacao = (observacao or "").strip()[:2000] or None
    db.session.commit()
    return peca


def confirmar_baixa_peca(peca: LogisticaConsumoChapaPeca, usuario: str) -> LogisticaConsumoChapaPeca:
    _garantir_tratativa_liberada(peca)
    if peca.baixado:
        raise ValueError("Essa peça já está com a baixa confirmada.")
    peca.baixado = True
    peca.baixado_em = datetime.now()
    peca.baixado_por = usuario
    db.session.commit()
    return peca


def estornar_baixa_peca(peca: LogisticaConsumoChapaPeca) -> LogisticaConsumoChapaPeca:
    _garantir_tratativa_liberada(peca)
    if not peca.baixado:
        raise ValueError("Essa peça ainda não teve a baixa confirmada.")
    peca.baixado = False
    peca.baixado_em = None
    peca.baixado_por = None
    db.session.commit()
    return peca
