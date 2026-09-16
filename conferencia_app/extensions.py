from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
import os

from sqlalchemy import event, exc


db = SQLAlchemy()
migrate = Migrate()


def proteger_pool_entre_processos(engine):
    """Uma conexão aberta antes do fork não pode atender dois workers WSGI."""
    @event.listens_for(engine, "connect")
    def registrar_processo(connection, record):
        record.info["pid"] = os.getpid()

    @event.listens_for(engine, "checkout")
    def verificar_processo(connection, record, proxy):
        if record.info.get("pid") != os.getpid():
            record.dbapi_connection = proxy.dbapi_connection = None
            raise exc.DisconnectionError("Conexão herdada de outro processo; reconectando.")
