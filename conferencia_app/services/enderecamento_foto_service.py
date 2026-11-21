"""Foto do estoque do GRV para a tela de Endereçamento (pedido da Logística
em 21/11/2026) e a lembrança do último endereço do material.

Foto: abrir a tela baixava o estoque inteiro do GRV, e cada worker do
PythonAnywhere tinha o próprio cache de 5 min: a tela demorava a abrir. Igual
à tela de Estoque, a tela abre com a foto gravada no banco (EstoqueTelaFoto,
visão "enderecamento") e pede outra por trás quando ela envelhece. O que o
próprio Sync mudou depois da foto (movimentação, recebimento, limpeza) entra
na leitura, calculado do histórico: a tela mostra o endereço novo na hora,
sem esperar outra foto. O que mudar direto no GRV aparece na foto seguinte.

Lembrança: o material sem saldo perde o endereço (limpeza do Sync ou alguém
no GRV), e quando ele volta ninguém sabe onde ficava. O último endereço sai
do histórico (EnderecoMovimento): a limpeza e a desativação já gravam a lista
de antes, e o endereço apagado direto no GRV é detectado comparando uma foto
com a anterior.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from flask import current_app

from ..extensions import db
from ..models import EnderecoMovimento as Movimento, LocalizacaoArmazem
from ..models import RecebimentoEnderecamento as Tarefa
from ..tempo import agora_br
from . import estoque_foto_service as foto_svc

VISAO = "enderecamento"
TIPO_APAGADO_GRV = "Endereço apagado no GRV"
# Registros cuja lista "antes" é o último endereço do material.
TIPOS_LEMBRANCA = ("Saldo zerado no GRV", "Endereço desativado", TIPO_APAGADO_GRV)
# Mais que isso dos materiais endereçados perdendo o endereço de uma foto para
# a outra é falha da consulta, não gente apagando no GRV: não registra nada.
FRACAO_SUSPEITA = 0.3


def _lista(agregado: dict | None) -> list[str]:
    from .enderecamento_service import enderecos_do_agregado
    return enderecos_do_agregado(agregado or {})


def _sobreposicao(desde: datetime) -> dict[str, list[str]]:
    """{SKU: endereços} do que o Sync mudou desde `desde`. A última operação
    de cada material vale; envio ainda pendente também (é o que vai valer,
    como em enderecos_atuais)."""
    ultimas: dict[str, tuple[datetime, list[str]]] = {}
    for m in Movimento.query.filter(Movimento.criado_em >= desde).order_by(Movimento.id):
        if isinstance(m.detalhes, dict) and "depois" in m.detalhes:
            ultimas[m.sku.strip().upper()] = (m.criado_em, list(m.detalhes["depois"] or []))
    from .recebimento_enderecamento_service import enderecos
    for t in Tarefa.query.filter(Tarefa.status == "Concluído", Tarefa.concluido_em >= desde):
        sku = str(t.sku or "").strip().upper()
        if sku and (sku not in ultimas or t.concluido_em >= ultimas[sku][0]):
            ultimas[sku] = (t.concluido_em, enderecos(t.enderecos_enviados))
    return {sku: lista for sku, (_, lista) in ultimas.items()}


def estoque(buscar_se_vazio: bool = True) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """({"por_codigo": ...} no formato do buscar_estoque_grv, situação da foto).

    Sem foto ainda: consulta o GRV como antes (`buscar_se_vazio`) ou devolve
    None. Erro da bridge sobe para quem chama."""
    dados, foto = foto_svc.ler(VISAO)
    if dados is None:
        if not buscar_se_vazio:
            return None, foto
        from .erp_estoque_service import buscar_estoque_grv
        return buscar_estoque_grv(), foto
    por_codigo = dados.get("por_codigo") or {}
    mudou = {sku: lista for sku, lista in _sobreposicao(datetime.fromisoformat(dados["coletado_desde"])).items()
             if sku in por_codigo}
    if mudou:
        # A foto aberta é compartilhada entre requisições: copia, não altera.
        por_codigo = {**por_codigo, **{sku: {**por_codigo[sku], "localizacoes": lista} for sku, lista in mudou.items()}}
    return {"por_codigo": por_codigo}, foto


def atualizar(usuario: str) -> bool:
    """Busca o estoque no GRV e grava a foto. False = outra já está rodando."""
    if not foto_svc.iniciar(VISAO, usuario):
        return False
    try:
        from .erp_estoque_service import buscar_estoque_grv
        inicio = agora_br()
        anterior, _ = foto_svc.ler(VISAO)
        novo = {"por_codigo": buscar_estoque_grv(forcar_atualizacao=True).get("por_codigo") or {},
                "coletado_desde": inicio.isoformat()}
        if anterior:
            _registrar_apagados(anterior, novo["por_codigo"])
        foto_svc.concluir(VISAO, novo, usuario)
    except Exception as exc:
        foto_svc.falhar(VISAO, str(exc))
        raise
    return True


def _registrar_apagados(anterior: dict[str, Any], novo: dict[str, dict]) -> None:
    """Material que tinha endereço na foto anterior e no GRV de agora não tem:
    alguém apagou no GRV. Grava a lista de antes no histórico (é a lembrança).
    O que o próprio Sync limpou já está no histórico e fica de fora."""
    desde = datetime.fromisoformat(anterior["coletado_desde"])
    velho = anterior.get("por_codigo") or {}
    mudou = _sobreposicao(desde)
    antes = {sku: mudou.get(sku, _lista(ag)) for sku, ag in velho.items()}
    apagados = {sku: lista for sku, lista in antes.items()
                if lista and sku in novo and not _lista(novo[sku])}
    if not apagados:
        return
    com_endereco = sum(1 for lista in antes.values() if lista)
    if len(apagados) > FRACAO_SUSPEITA * com_endereco:
        current_app.logger.warning("Foto do endereçamento: %s de %s materiais perderam o endereço de uma vez; "
                                   "parece falha da consulta, nada registrado.", len(apagados), com_endereco)
        return
    ja_registrados = {sku.strip().upper() for (sku,) in db.session.query(Movimento.sku).filter(
        Movimento.criado_em >= desde, Movimento.tipo.in_(TIPOS_LEMBRANCA))}
    agora = agora_br()
    for sku, lista in apagados.items():
        if sku in ja_registrados:
            continue
        db.session.add(Movimento(
            chave=f"grv-apagou:{sku}:{agora.isoformat()}"[:100], sku=sku,
            unidade=str(novo[sku].get("unidade") or "").strip().upper()[:20],
            tipo=TIPO_APAGADO_GRV, quantidade=0, usuario="GRV",
            motivo="Endereço apagado direto no GRV", detalhes={"antes": lista, "depois": []},
            sincronizado_em=agora))
    db.session.commit()


def lembrancas(skus) -> dict[str, list[str]]:
    """{SKU: endereços onde o material ficou da última vez que teve endereço}.
    Endereço desativado não volta a ser sugerido. Quem chama só pergunta pelo
    material que está sem endereço agora."""
    alvo = {str(s or "").strip().upper() for s in skus} - {""}
    if not alvo:
        return {}
    resultado: dict[str, list[str]] = {}
    registros = (Movimento.query.filter(Movimento.sku.in_(alvo), Movimento.tipo.in_(TIPOS_LEMBRANCA))
                 .order_by(Movimento.id.desc()))
    for m in registros:
        sku = m.sku.strip().upper()
        detalhes = m.detalhes if isinstance(m.detalhes, dict) else {}
        if sku in resultado or detalhes.get("depois") or not detalhes.get("antes"):
            continue
        resultado[sku] = list(detalhes["antes"])
    if not resultado:
        return {}
    from .enderecamento_service import normalizar
    desativados = {normalizar(c) for (c,) in db.session.query(LocalizacaoArmazem.codigo).filter(
        LocalizacaoArmazem.ativo.is_(False))}
    resultado = {sku: [e for e in lista if normalizar(e) not in desativados] for sku, lista in resultado.items()}
    return {sku: lista for sku, lista in resultado.items() if lista}
