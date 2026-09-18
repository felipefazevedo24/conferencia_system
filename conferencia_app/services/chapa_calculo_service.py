"""Validação e persistência compartilhada do cálculo físico de chapas."""
from __future__ import annotations

import json
import math
from datetime import datetime

from ..extensions import db
from ..models import ChapaCalculo, ChapaCalculoLog

DENSIDADES = {
    "aco_carbono": 7.85, "inox": 8.00, "aluminio": 2.71,
    "cobre": 8.96, "latao": 8.53, "bronze": 8.80,
}

FORMATOS = {
    "chapa": ("espessura", "largura", "comprimento"),
    "bobina": ("espessura", "largura", "comprimento"),
    "barra_quadrada": ("lado", "comprimento"),
    "barra_retangular": ("espessura", "largura", "comprimento"),
    "barra_redonda": ("diametro", "comprimento"),
    "barra_sextavada": ("bitola", "comprimento"),
    "tubo_redondo": ("diametro", "parede", "comprimento"),
    "tubo_quadrado": ("lado", "parede", "comprimento"),
    "tubo_retangular": ("altura", "largura", "parede", "comprimento"),
    "perfil_l": ("aba", "espessura", "comprimento"),
    "perfil_t": ("aba", "espessura", "comprimento"),
    "perfil_u": ("altura", "aba", "espessura", "comprimento"),
}


def _numero_positivo(valor, nome):
    try:
        numero = float(str(valor).replace(",", "."))
    except (TypeError, ValueError):
        raise ValueError(f"Informe {nome} em milímetros.")
    if not math.isfinite(numero) or numero <= 0:
        raise ValueError(f"Informe {nome} maior que zero.")
    return numero


def validar_calculo(dados):
    if not isinstance(dados, dict):
        raise ValueError("Informe a quantidade e as medidas da chapa.")
    material = str(dados.get("material") or "").strip()
    formato = str(dados.get("formato") or "").strip()
    if material not in DENSIDADES:
        raise ValueError("Selecione o material da chapa.")
    if formato not in FORMATOS:
        raise ValueError("Selecione o formato da chapa.")
    recebidas = dados.get("dimensoes") if isinstance(dados.get("dimensoes"), dict) else {}
    dimensoes = {campo: _numero_positivo(recebidas.get(campo), campo) for campo in FORMATOS[formato]}

    if formato in {"tubo_redondo", "tubo_quadrado"} and dimensoes["parede"] * 2 >= dimensoes.get("diametro", dimensoes.get("lado")):
        raise ValueError("A parede do tubo deve ser menor que a metade da medida externa.")
    if formato == "tubo_retangular" and (dimensoes["parede"] * 2 >= dimensoes["altura"] or dimensoes["parede"] * 2 >= dimensoes["largura"]):
        raise ValueError("A parede do tubo deve ser menor que a metade da altura e da largura.")
    if formato in {"perfil_l", "perfil_t"} and dimensoes["espessura"] >= dimensoes["aba"]:
        raise ValueError("A espessura deve ser menor que a aba.")
    if formato == "perfil_u" and (dimensoes["espessura"] * 2 >= dimensoes["altura"] or dimensoes["espessura"] >= dimensoes["aba"]):
        raise ValueError("Confira a altura, a aba e a espessura do perfil U.")

    d = dimensoes
    if formato in {"chapa", "bobina", "barra_retangular"}: area = d["espessura"] * d["largura"]
    elif formato == "barra_quadrada": area = d["lado"] ** 2
    elif formato == "barra_redonda": area = math.pi / 4 * d["diametro"] ** 2
    elif formato == "barra_sextavada": area = 0.8660254 * d["bitola"] ** 2
    elif formato == "tubo_redondo": area = math.pi / 4 * (d["diametro"] ** 2 - (d["diametro"] - 2 * d["parede"]) ** 2)
    elif formato == "tubo_quadrado": area = d["lado"] ** 2 - (d["lado"] - 2 * d["parede"]) ** 2
    elif formato == "tubo_retangular": area = d["altura"] * d["largura"] - (d["altura"] - 2 * d["parede"]) * (d["largura"] - 2 * d["parede"])
    elif formato in {"perfil_l", "perfil_t"}: area = d["espessura"] * (2 * d["aba"] - d["espessura"])
    else: area = d["espessura"] * (d["altura"] + 2 * d["aba"] - 2 * d["espessura"])
    peso = area * d["comprimento"] * DENSIDADES[material] / 1_000_000
    return material, formato, dimensoes, peso


def salvar_calculo_item(item, dados, usuario):
    from .chapa_auditoria_service import registrar
    material, formato, dimensoes, peso = validar_calculo(dados)
    calc = ChapaCalculo.query.filter_by(numero_nota=item.numero_nota, item_nota_id=item.id).first()
    anterior = None
    if calc:
        try: anterior_dims = json.loads(calc.dimensoes) if calc.dimensoes else {}
        except Exception: anterior_dims = {}
        anterior = {"material": calc.material, "formato": calc.formato, "dimensoes": anterior_dims, "peso_por_peca": calc.peso_por_peca}
    novo = {"material": material, "formato": formato, "dimensoes": dimensoes, "peso_por_peca": peso}
    if anterior == novo:
        return calc
    if not calc:
        calc = ChapaCalculo(numero_nota=item.numero_nota, item_nota_id=item.id,
                            codigo=item.codigo_grv or item.codigo, criado_por=usuario)
        db.session.add(calc)
    calc.material = material
    calc.formato = formato
    calc.dimensoes = json.dumps(dimensoes, ensure_ascii=False)
    calc.peso_por_peca = peso
    calc.atualizado_por = usuario
    calc.atualizado_em = datetime.now()
    db.session.flush()
    db.session.add(ChapaCalculoLog(
        chapa_calculo_id=calc.id, alterado_por=usuario,
        dados_anteriores=json.dumps(anterior, ensure_ascii=False) if anterior else "",
        dados_novos=json.dumps(novo, ensure_ascii=False),
    ))
    registrar(item, 'Cálculo criado' if anterior is None else 'Cálculo alterado', usuario, anterior, novo)
    return calc
