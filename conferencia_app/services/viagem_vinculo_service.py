"""Distingue a alocação válida das tentativas mantidas no histórico da viagem."""
from sqlalchemy import and_, or_

from ..models import Viagem, ViagemParada


def filtro_vinculo_solicitacao():
    """Aplicar em consultas de ViagemParada com JOIN de Viagem.

    Uma tentativa não realizada/cancelada não prende a solicitação à viagem.
    Viagens encerradas só mantêm o vínculo operacional de paradas concluídas.
    """
    return and_(
        Viagem.status != "Cancelada",
        ViagemParada.status.notin_(("Nao_realizada", "Cancelada")),
        or_(Viagem.status != "Concluida", ViagemParada.status == "Concluida"),
    )
