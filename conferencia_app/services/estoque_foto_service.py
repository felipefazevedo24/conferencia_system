"""Foto da tela de Estoque no banco do Sync (EstoqueTelaFoto).

Abrir a tela buscava na bridge o estoque inteiro, o consumo, as reservas, as
OCs e o histórico desde 2019 da família, e cada worker do PythonAnywhere
tinha o seu cache: a tela demorava a abrir. Igual ao espelho do inventário
cíclico, a tela abre com a foto e, quando ela passa de IDADE_MAXIMA_MIN, a
própria tela pede a atualização por trás.

A foto guarda o dado cru do GRV, não o resultado: parâmetros e ajustes por
item entram na leitura e valem na hora, sem esperar outra foto.
"""
from __future__ import annotations

import json
import zlib
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError

from ..extensions import db
from ..models import EstoqueTelaFoto as Foto
from ..tempo import agora_br

IDADE_MAXIMA_MIN = 15
# Atualização "em andamento" há mais tempo que isso caiu no meio (worker
# reiniciado): deixa outra começar.
ATUALIZACAO_EXPIRA_MIN = 15
# O histórico (meses fechados, lead times, retiradas) muda devagar e é a
# consulta mais pesada do GRV: só busca de novo depois disso.
HISTORICO_IDADE_MAXIMA_H = 12


def _linha(visao: str) -> Foto | None:
    return Foto.query.filter_by(visao=visao).first()


# Foto já aberta, por visão: (gerado_em, dados). Baixar e abrir a foto leva
# ~0,25 s e a busca da tela chama a rota a cada tecla; só abre de novo quando
# outra foto é gravada. Quem lê não pode alterar os dados.
_ABERTAS: dict[str, tuple[Any, dict[str, Any]]] = {}


def ler(visao: str) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """(dados da foto ou None, situação da foto)."""
    # Sem a coluna `dados`: o blob só vem do banco quando a foto mudou.
    linha = db.session.query(Foto.gerado_em, Foto.atualizando_desde, Foto.erro).filter_by(visao=visao).first()
    agora = agora_br()
    gerado = linha.gerado_em if linha else None
    dados = None
    if gerado:
        aberta = _ABERTAS.get(visao)
        if aberta and aberta[0] == gerado:
            dados = aberta[1]
        else:
            blob = db.session.query(Foto.dados).filter_by(visao=visao).scalar()
            dados = json.loads(zlib.decompress(blob).decode("utf-8")) if blob else None
            if dados is not None:
                _ABERTAS[visao] = (gerado, dados)
    gerado = gerado if dados is not None else None
    em_andamento = bool(
        linha and linha.atualizando_desde
        and linha.atualizando_desde > agora - timedelta(minutes=ATUALIZACAO_EXPIRA_MIN)
    )
    idade = int((agora - gerado).total_seconds() // 60) if gerado else None
    return dados, {
        "vazio": dados is None,
        # Identifica a foto (cache das contas na rota).
        "versao": gerado.isoformat() if gerado else None,
        "atualizado_em": gerado.strftime("%d/%m/%Y %H:%M") if gerado else None,
        "idade_min": idade,
        "desatualizado": idade is None or idade >= IDADE_MAXIMA_MIN,
        "em_andamento": em_andamento,
        "erro": linha.erro if linha else None,
    }


def historico_reaproveitavel(dados: dict[str, Any] | None, janela_meses: int) -> bool:
    """O histórico da foto ainda serve: recente e pedido com a mesma janela."""
    if not dados or dados.get("historicos") is None or dados.get("historicos_janela_pedida") != janela_meses:
        return False
    try:
        em = datetime.fromisoformat(dados["historicos_em"])
    except (KeyError, TypeError, ValueError):
        return False
    return agora_br() - em < timedelta(hours=HISTORICO_IDADE_MAXIMA_H)


def iniciar(visao: str, usuario: str) -> bool:
    """Trava a atualização da visão. False = outra já está rodando."""
    if not _linha(visao):
        try:
            db.session.add(Foto(visao=visao))
            db.session.commit()
        except IntegrityError:
            # Outro worker criou a linha ao mesmo tempo.
            db.session.rollback()
    agora = agora_br()
    expira = agora - timedelta(minutes=ATUALIZACAO_EXPIRA_MIN)
    # UPDATE condicional: entre ler e gravar, outro worker podia travar também.
    travadas = Foto.query.filter(
        Foto.visao == visao, or_(Foto.atualizando_desde.is_(None), Foto.atualizando_desde < expira),
    ).update({"atualizando_desde": agora, "atualizando_por": usuario}, synchronize_session=False)
    db.session.commit()
    return travadas == 1


def concluir(visao: str, dados: dict[str, Any], usuario: str) -> None:
    linha = _linha(visao)
    linha.dados = zlib.compress(json.dumps(dados, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), 6)
    linha.gerado_em = agora_br()
    linha.gerado_por = usuario
    linha.atualizando_desde = None
    linha.atualizando_por = None
    linha.erro = None
    linha.erro_em = None
    db.session.commit()


def falhar(visao: str, erro: str) -> None:
    """Solta a trava e guarda o motivo; a foto anterior continua valendo."""
    db.session.rollback()
    linha = _linha(visao)
    if not linha:
        return
    linha.atualizando_desde = None
    linha.atualizando_por = None
    linha.erro = str(erro)[:500]
    linha.erro_em = agora_br()
    db.session.commit()
