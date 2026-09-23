import os
import sys


if __name__ == "__main__" and "--habilitar-rpa" in sys.argv:
    os.environ["GRV_WEB_RPA_ENABLED"] = "1"
    os.environ.setdefault("SYNC_LAUNCHER", "serve_tablet.py --habilitar-rpa")
    sys.argv.remove("--habilitar-rpa")

from waitress import serve

from conferencia_app import create_app


app = create_app()

if __name__ == "__main__":
    host = os.environ.get("APP_HOST", "0.0.0.0")
    port = int(os.environ.get("APP_PORT", "5000"))
    threads = int(os.environ.get("APP_THREADS", "8"))
    serve(app, host=host, port=port, threads=threads)
