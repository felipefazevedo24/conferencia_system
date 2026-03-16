import os

from waitress import serve

from conferencia_app import create_app


app = create_app()

if __name__ == "__main__":
    host = os.environ.get("APP_HOST", "0.0.0.0")
    port = int(os.environ.get("APP_PORT", "5000"))
    threads = int(os.environ.get("APP_THREADS", "8"))
    serve(app, host=host, port=port, threads=threads)
