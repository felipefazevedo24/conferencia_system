"""Persistencia simples de rastreamento de veiculos em JSON dentro de instance/.

Armazena cadastro dos veiculos + ultima posicao conhecida + credenciais do
portal Locartrack (caso o usuario queira salvar login/senha para abrir sessao
em um so clique).

Sem modelo de banco: JSON e suficiente porque sao poucos veiculos e nao ha
historico longo de posicoes (usamos apenas a ultima posicao por veiculo).
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from typing import Any

from flask import current_app

_LOCK = threading.Lock()

BASE_PADRAO = {
    "latitude": -22.8531,
    "longitude": -47.2158,
    "endereco": "Estr. Carlos Roberto Prataviera, 600 - Jardim Novo Angulo, Hortolandia - SP, 13185-155",
    "nome": "Base Columbia Machine",
}

VEICULOS_PADRAO = [
    {
        "id": "savero_tjj1i14",
        "apelido": "Saveiro",
        "marca_modelo": "VW / Saveiro CS RB MF",
        "placa": "TJJ-1I14",
        "motorista_padrao": "",
        "latitude": None,
        "longitude": None,
        "atualizado_em": None,
        "velocidade_kmh": None,
        "observacao": "",
        "ativo": True,
    },
    {
        "id": "daily_rhw3d87",
        "apelido": "Iveco Daily",
        "marca_modelo": "IVECO / Daily 35-150CS",
        "placa": "RHW-3D87",
        "motorista_padrao": "",
        "latitude": None,
        "longitude": None,
        "atualizado_em": None,
        "velocidade_kmh": None,
        "observacao": "",
        "ativo": True,
    },
]


def _caminho_arquivo() -> str:
    return os.path.join(current_app.instance_path, "rastreamento_config.json")


def _default_config() -> dict[str, Any]:
    return {
        "portal_url": "https://locartrack.plataforma.app.br/web/franqueado",
        "portal_login": "",
        "portal_senha": "",
        "base": dict(BASE_PADRAO),
        "veiculos": [dict(v) for v in VEICULOS_PADRAO],
    }


def carregar() -> dict[str, Any]:
    path = _caminho_arquivo()
    if not os.path.exists(path):
        salvar(_default_config())
        return _default_config()
    try:
        with open(path, "r", encoding="utf-8") as f:
            dados = json.load(f)
    except Exception:
        return _default_config()
    # Garante chaves minimas
    base_default = _default_config()
    for k, v in base_default.items():
        dados.setdefault(k, v)
    # Garante veiculos padrao existem (primeiro boot pode sobrescrever, depois so acrescenta)
    existentes = {str(v.get("id")) for v in (dados.get("veiculos") or [])}
    for v_pad in VEICULOS_PADRAO:
        if v_pad["id"] not in existentes:
            dados["veiculos"].append(dict(v_pad))
    return dados


def salvar(dados: dict[str, Any]) -> None:
    path = _caminho_arquivo()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with _LOCK:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(dados, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)


def atualizar_posicao(
    veiculo_id: str,
    latitude: float,
    longitude: float,
    velocidade_kmh: float | None = None,
    observacao: str = "",
) -> dict[str, Any] | None:
    dados = carregar()
    agora = datetime.now().isoformat(timespec="seconds")
    alvo = None
    for v in dados.get("veiculos", []):
        if str(v.get("id")) == str(veiculo_id):
            v["latitude"] = float(latitude)
            v["longitude"] = float(longitude)
            v["velocidade_kmh"] = float(velocidade_kmh) if velocidade_kmh is not None else None
            v["observacao"] = str(observacao or "")[:300]
            v["atualizado_em"] = agora
            alvo = v
            break
    if alvo is None:
        return None
    salvar(dados)
    return alvo


def atualizar_credenciais(portal_url: str | None, login: str | None, senha: str | None) -> dict[str, Any]:
    dados = carregar()
    if portal_url is not None:
        dados["portal_url"] = str(portal_url).strip() or dados.get("portal_url", "")
    if login is not None:
        dados["portal_login"] = str(login).strip()
    if senha is not None:
        # Senha em branco = nao alterar
        if str(senha).strip():
            dados["portal_senha"] = str(senha)
    salvar(dados)
    # Nao retorna senha
    sanitized = {k: v for k, v in dados.items() if k != "portal_senha"}
    sanitized["portal_senha_cadastrada"] = bool(dados.get("portal_senha"))
    return sanitized


def atualizar_veiculo(veiculo_id: str, campos: dict[str, Any]) -> dict[str, Any] | None:
    dados = carregar()
    for v in dados.get("veiculos", []):
        if str(v.get("id")) == str(veiculo_id):
            for campo in ("apelido", "marca_modelo", "placa", "motorista_padrao"):
                if campo in campos:
                    v[campo] = str(campos.get(campo) or "").strip()
            if "ativo" in campos:
                v["ativo"] = bool(campos.get("ativo"))
            salvar(dados)
            return v
    return None


def estado_publico() -> dict[str, Any]:
    """Estado seguro para enviar ao front (sem senha)."""
    dados = carregar()
    return {
        "portal_url": dados.get("portal_url", ""),
        "portal_login": dados.get("portal_login", ""),
        "portal_senha_cadastrada": bool(dados.get("portal_senha")),
        "base": dados.get("base", dict(BASE_PADRAO)),
        "veiculos": dados.get("veiculos", []),
    }
