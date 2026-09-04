"""Força IPv4 nas chamadas à bridge do ERP quando ela é um Tailscale Funnel.

Contexto: o PythonAnywhere é IPv4-only e o resolver interno dele nem sempre
devolve o registro A (IPv4) do Funnel — muitas vezes só o AAAA (IPv6), que o
PythonAnywhere não alcança ("Network is unreachable"). Aqui a gente, SÓ para o
host do Funnel (`*.ts.net`), resolve normalmente e usa os IPv4 que vierem; se
não vier nenhum IPv4, cai nos IPs IPv4 fixos do relay do Funnel (configuráveis
por env). Em qualquer outra URL o patch fica inerte.
"""
from __future__ import annotations

import os
import socket
from urllib.parse import urlparse

_PATCHED = False


def _funnel_host() -> str:
    url = (os.environ.get("ERP_LANCAMENTO_API_URL") or "").strip()
    host = urlparse(url).hostname or "" if url else ""
    return host if host.endswith(".ts.net") else ""


def _ipv4_fallback() -> list[str]:
    raw = os.environ.get("ERP_BRIDGE_FUNNEL_IPV4", "199.38.181.54,209.177.145.137")
    return [ip.strip() for ip in raw.split(",") if ip.strip()]


def forcar_ipv4_bridge() -> None:
    """Idempotente. Só tem efeito se a URL da bridge for um Funnel `.ts.net`."""
    global _PATCHED
    if _PATCHED:
        return
    host = _funnel_host()
    fallback = _ipv4_fallback()
    if not host or not fallback:
        return

    _orig_getaddrinfo = socket.getaddrinfo

    def _patched(hostname, port, *args, **kwargs):
        if hostname == host:
            try:
                somente_ipv4 = [
                    r for r in _orig_getaddrinfo(hostname, port, *args, **kwargs)
                    if r[0] == socket.AF_INET
                ]
                if somente_ipv4:
                    return somente_ipv4
            except OSError:
                pass
            try:
                porta = int(port)
            except (TypeError, ValueError):
                porta = 443
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, porta)) for ip in fallback]
        return _orig_getaddrinfo(hostname, port, *args, **kwargs)

    socket.getaddrinfo = _patched
    _PATCHED = True
