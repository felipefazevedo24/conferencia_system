"""Servico do modulo de Consumo de Chapa (Nesting) da Logistica.

Workflow simples: Nesting (importado do relatorio da maquina de corte,
chapas ja consumidas fisicamente) -> Concluido (confirmacao manual
depois, ex.: baixa de estoque conferida). Ver logistica_consumo_chapa_
parser.py pra extracao dos dados do HTML."""
from __future__ import annotations

from datetime import datetime

from ..extensions import db
from ..models import LogisticaConsumoChapaNesting, LogisticaConsumoChapaPeca
from .logistica_consumo_chapa_parser import parse_relatorio_nesting_html

STATUS_SLUGS = {"Nesting": "nesting", "Concluido": "concluido"}


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
            LogisticaConsumoChapaPeca.query.filter_by(nesting_id=nesting.id).delete()
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

        for peca_dados in pecas_dados:
            db.session.add(LogisticaConsumoChapaPeca(nesting_id=nesting.id, **peca_dados))

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
                LogisticaConsumoChapaNesting.nome_tarefa.ilike(termo),
            )
        )
    return query.order_by(LogisticaConsumoChapaNesting.criado_em.desc()).all()


def concluir_nesting(nesting: LogisticaConsumoChapaNesting, usuario: str) -> LogisticaConsumoChapaNesting:
    if nesting.status != "Nesting":
        raise ValueError("Esse Nesting já está concluído.")
    nesting.status = "Concluido"
    nesting.concluido_em = datetime.now()
    nesting.concluido_por = usuario
    db.session.commit()
    return nesting


def estornar_nesting(nesting: LogisticaConsumoChapaNesting) -> LogisticaConsumoChapaNesting:
    if nesting.status != "Concluido":
        raise ValueError("Esse Nesting não está concluído.")
    nesting.status = "Nesting"
    nesting.concluido_em = None
    nesting.concluido_por = None
    db.session.commit()
    return nesting
