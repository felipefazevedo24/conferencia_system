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
import math
import zlib
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError

from ..extensions import db
from ..models import EstoqueTelaFoto as Foto, EstoqueValorDiario as ValorDiario
from ..models import EstoqueValorMensalInformado as ValorInformado
from ..tempo import agora_br
from . import estoque_planejamento_service as planejamento_svc

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


def registrar_valor(visao: str, dados: dict[str, Any]) -> None:
    """Grava o valor do estoque da visão no dia (EstoqueValorDiario). Item
    fora da análise (ativo da empresa) não entra, igual ao painel."""
    fora = {c for c, a in planejamento_svc.obter_ajustes().items() if a.get("fora_analise")}
    valor = 0.0
    itens = 0
    for linha in dados.get("linhas") or []:
        if planejamento_svc.chave_codigo(linha.get("codigo")) in fora:
            continue
        saldo = float(linha.get("saldo_total") or 0)
        if saldo > 0:
            itens += 1
            valor += saldo * float(linha.get("custo_medio") or 0)
    hoje = agora_br().date()
    registro = ValorDiario.query.filter_by(data=hoje, visao=visao).first()
    if not registro:
        registro = ValorDiario(data=hoje, visao=visao)
        db.session.add(registro)
    registro.valor = round(valor, 2)
    registro.itens = itens
    try:
        db.session.commit()
    except IntegrityError:
        # Outro worker gravou o mesmo dia ao mesmo tempo: o dele vale.
        db.session.rollback()


def valores_por_mes(visoes: list[str], meses: list[str]) -> dict[str, dict[str, float]]:
    """{mes 'AAAA-MM': {visao: valor do último dia gravado no mês}}. O valor
    informado à mão, quando existe, vale no lugar do gravado."""
    if not meses:
        return {}
    inicio = date.fromisoformat(meses[0] + "-01")
    resultado: dict[str, dict[str, float]] = {}
    linhas = (
        ValorDiario.query.filter(ValorDiario.data >= inicio, ValorDiario.visao.in_(visoes))
        .order_by(ValorDiario.data).all()
    )
    for linha in linhas:
        mes = linha.data.strftime("%Y-%m")
        if mes in meses:
            resultado.setdefault(mes, {})[linha.visao] = float(linha.valor or 0)
    for linha in ValorInformado.query.filter(ValorInformado.mes.in_(meses), ValorInformado.visao.in_(visoes)).all():
        resultado.setdefault(linha.mes, {})[linha.visao] = float(linha.valor or 0)
    return resultado


def _validar_mes(mes: str) -> str:
    mes = str(mes or "").strip()
    try:
        inicio = date.fromisoformat(mes + "-01")
    except ValueError:
        raise ValueError("Mês inválido.") from None
    # O mês corrente é o valor de agora, calculado na hora.
    if inicio >= agora_br().date().replace(day=1):
        raise ValueError("Só dá para informar mês já fechado.")
    return mes


def valores_do_mes(mes: str, visoes: list[str]) -> dict[str, dict[str, Any]]:
    """{visao: {gravado, informado, informado_por, informado_em}} de um mês."""
    mes = _validar_mes(mes)
    inicio = date.fromisoformat(mes + "-01")
    fim = (inicio + timedelta(days=32)).replace(day=1)
    resultado = {v: {"gravado": None, "informado": None, "informado_por": None, "informado_em": None} for v in visoes}
    gravados = (
        ValorDiario.query.filter(ValorDiario.data >= inicio, ValorDiario.data < fim, ValorDiario.visao.in_(visoes))
        .order_by(ValorDiario.data).all()
    )
    for linha in gravados:
        resultado[linha.visao]["gravado"] = float(linha.valor or 0)
    for linha in ValorInformado.query.filter(ValorInformado.mes == mes, ValorInformado.visao.in_(visoes)).all():
        resultado[linha.visao].update(
            informado=float(linha.valor or 0), informado_por=linha.informado_por,
            informado_em=linha.informado_em.strftime("%d/%m/%Y %H:%M") if linha.informado_em else None,
        )
    return resultado


def informar_valores(mes: str, valores: dict, visoes: list[str], usuario: str) -> None:
    """Grava o valor informado de cada visão no mês. Vazio apaga o informado
    e o mês volta ao gravado (se houver)."""
    mes = _validar_mes(mes)
    if not isinstance(valores, dict):
        raise ValueError("Valores inválidos.")
    novos: dict[str, float | None] = {}
    for visao, bruto in valores.items():
        if visao not in visoes:
            raise ValueError("Visão inválida.")
        texto = str(bruto if bruto is not None else "").strip()
        if not texto:
            novos[visao] = None
            continue
        try:
            valor = float(texto)
        except ValueError:
            valor = math.nan
        if not math.isfinite(valor):
            raise ValueError("Informe o valor em número.")
        if valor < 0:
            raise ValueError("O valor não pode ser negativo.")
        novos[visao] = round(valor, 2)
    existentes = {l.visao: l for l in ValorInformado.query.filter(ValorInformado.mes == mes).all()}
    for visao, valor in novos.items():
        linha = existentes.get(visao)
        if valor is None:
            if linha:
                db.session.delete(linha)
            continue
        if not linha:
            linha = ValorInformado(mes=mes, visao=visao)
            db.session.add(linha)
        if linha.valor != valor:
            linha.valor = valor
            linha.informado_por = usuario
            linha.informado_em = agora_br()
    db.session.commit()
