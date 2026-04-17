from conferencia_app import create_app
import os

app = create_app()

if __name__ == '__main__':
    host = os.environ.get("APP_HOST", "0.0.0.0")
    port = int(os.environ.get("APP_PORT", "5000"))
    debug = os.environ.get("APP_DEBUG", "false").lower() == "true"
    app.run(host=host, port=port, debug=debug)