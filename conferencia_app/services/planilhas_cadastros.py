"""Lookup de e-mails em planilhas locais clientes.xlsx e fornecedores.xlsx.

Carregamento em cache por (path, mtime) - recarrega automaticamente se o arquivo
for atualizado no disco. Retorna apenas o e-mail, normalizando CNPJ.
"""
from __future__ import annotations

import os
import re
import threading
from typing import Optional

from flask import current_app

try:
    import openpyxl
except Exception:  # pragma: no cover
    openpyxl = None  # type: ignore


_LOCK = threading.Lock()
_CACHE: dict[str, tuple[float, dict[str, dict[str, str]]]] = {}
# _CACHE[path] = (mtime, {cnpj_digits: {"email": "...", "nome": "...", "origem": "..."}})

_RE_EMAIL = re.compile(r"[^@\s;,]+@[^@\s;,]+\.[^@\s;,]+")


def _somente_digitos(s) -> str:
    return re.sub(r"\D", "", str(s or ""))


def _primeiro_email_valido(*valores: object) -> str:
    """Dada uma sequencia de valores, retorna o primeiro que contem um e-mail valido."""
    for v in valores:
        if not v:
            continue
        txt = str(v).strip()
        if not txt:
            continue
        m = _RE_EMAIL.search(txt)
        if m:
            return m.group(0).strip().strip(".").strip(",").strip(";")
    return ""


def _localizar_col(headers: list, *candidatos: str) -> Optional[int]:
    """Acha o indice de coluna cujo header contem qualquer candidato (case-insensitive)."""
    normalizados = [
        (idx, str(h or "").strip().lower().replace("  ", " "))
        for idx, h in enumerate(headers)
    ]
    for cand in candidatos:
        alvo = cand.strip().lower()
        for idx, h in normalizados:
            if alvo == h:
                return idx
        for idx, h in normalizados:
            if alvo in h:
                return idx
    return None


def _carregar_planilha(path: str, tipo: str) -> dict[str, dict[str, str]]:
    """Le planilha e retorna {cnpj_digits: {email, nome, origem}}."""
    if openpyxl is None:
        return {}
    if not os.path.exists(path):
        return {}
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        ws = wb.active
        linhas = ws.iter_rows(values_only=True)
        headers = list(next(linhas, ()) or [])
        if not headers:
            return {}

        if tipo == "clientes":
            col_cnpj = _localizar_col(headers, "CNPJ / CPF", "CNPJ/CPF", "*C.N.P.J/CPF", "CNPJ")
            col_nome = _localizar_col(headers, "*Nome", "Nome", "R. Social")
            col_email_1 = _localizar_col(headers, "E-mail de Entrega")
            col_email_2 = _localizar_col(headers, "E-mail para Envio DANFE")
            col_email_3 = _localizar_col(headers, "E-mail")
        else:  # fornecedores
            col_cnpj = _localizar_col(headers, "*C.N.P.J/CPF", "CNPJ/CPF", "CNPJ / CPF", "CNPJ")
            col_nome = _localizar_col(headers, "*Nome", "Nome", "*Razão Social", "Razão Social")
            col_email_1 = _localizar_col(headers, "E-mail para Envio DANFE")
            col_email_2 = _localizar_col(headers, "E-mail")
            col_email_3 = None

        if col_cnpj is None:
            return {}

        resultado: dict[str, dict[str, str]] = {}
        for row in linhas:
            if not row:
                continue
            cnpj = _somente_digitos(row[col_cnpj] if col_cnpj < len(row) else "")
            if not cnpj:
                continue
            nome = str(row[col_nome]).strip() if (col_nome is not None and col_nome < len(row) and row[col_nome]) else ""
            emails = []
            for c in (col_email_1, col_email_2, col_email_3):
                if c is not None and c < len(row):
                    emails.append(row[c])
            email = _primeiro_email_valido(*emails)
            if not email:
                continue
            # primeiro registro com email prevalece (evita substituir por duplicata sem e-mail)
            if cnpj not in resultado:
                resultado[cnpj] = {"email": email, "nome": nome, "origem": tipo}
        return resultado
    finally:
        wb.close()


def _cache_get(path: str, tipo: str) -> dict[str, dict[str, str]]:
    """Retorna dicionario em cache, recarregando se mtime mudou."""
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return {}
    with _LOCK:
        cached = _CACHE.get(path)
        if cached and cached[0] == mtime:
            return cached[1]
    dados = _carregar_planilha(path, tipo)
    with _LOCK:
        _CACHE[path] = (mtime, dados)
    return dados


def _raiz_workspace() -> str:
    """Tenta usar config NFE_EMAIL_PLANILHAS_DIR; senao, dir acima de instance_path."""
    try:
        dir_cfg = current_app.config.get("NFE_EMAIL_PLANILHAS_DIR")
        if dir_cfg and os.path.isdir(dir_cfg):
            return dir_cfg
        instance = current_app.instance_path
        return os.path.dirname(instance)
    except Exception:
        return os.getcwd()


def buscar_email_por_cnpj(cnpj: str) -> dict[str, str]:
    """Procura em clientes.xlsx e fornecedores.xlsx (nesta ordem). Retorna {}."""
    cnpj = _somente_digitos(cnpj)
    if not cnpj:
        return {}
    raiz = _raiz_workspace()
    for arquivo, tipo in (
        ("clientes.xlsx", "clientes"),
        ("fornecedores.xlsx", "fornecedores"),
    ):
        dados = _cache_get(os.path.join(raiz, arquivo), tipo)
        hit = dados.get(cnpj)
        if hit:
            return {**hit, "fonte": "Planilha"}
    return {}


def estatisticas_planilhas() -> dict:
    """Util para debug/tela de configuracao."""
    raiz = _raiz_workspace()
    out = {"raiz": raiz, "arquivos": []}
    for arquivo, tipo in (("clientes.xlsx", "clientes"), ("fornecedores.xlsx", "fornecedores")):
        path = os.path.join(raiz, arquivo)
        existe = os.path.exists(path)
        dados = _cache_get(path, tipo) if existe else {}
        out["arquivos"].append({
            "arquivo": arquivo,
            "tipo": tipo,
            "existe": existe,
            "total_com_email": len(dados),
        })
    return out
