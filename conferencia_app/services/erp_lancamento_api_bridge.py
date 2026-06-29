"""Compat wrapper: usa a bridge canonical em scripts/erp_lancamento_api_bridge.py.

Este modulo existe apenas para manter imports legados sem duplicar codigo.
A implementacao oficial da bridge vive em scripts/erp_lancamento_api_bridge.py.
"""
from __future__ import annotations

from scripts.erp_lancamento_api_bridge import app, create_app

__all__ = ["app", "create_app"]


if __name__ == "__main__":
    import os

    host = str(os.environ.get("ERP_BRIDGE_HOST", "0.0.0.0") or "0.0.0.0")
    try:
        port = int(str(os.environ.get("ERP_BRIDGE_PORT", "8088") or "8088"))
    except ValueError:
        port = 8088
    app.run(host=host, port=port)
