"""Hora oficial do sistema: sempre horário de Brasília.

O PythonAnywhere roda em UTC. Antes desta correção, todo `datetime.now()`
do app devolvia a hora do servidor (UTC), não a de Brasília — todo prazo,
timestamp exibido e regra de horário comercial estava adiantado em relação
ao horário real do Brasil. `agora_br()` é o substituto único: o Brasil não
tem mais horário de verão desde 2019, então o deslocamento é sempre -3h,
sem ambiguidade sazonal a tratar.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

_BRASILIA = ZoneInfo("America/Sao_Paulo")


def agora_br() -> datetime:
    """Hora atual de Brasília, como datetime *naive* (sem tzinfo).

    Naive de propósito: toda coluna DateTime do banco já é naive, e comparar
    um valor aware com um naive lido do banco levanta TypeError. `agora_br()`
    substitui `datetime.now()` 1 para 1 em todo o app, sem exigir migrar
    nenhuma coluna para timezone-aware."""
    return datetime.now(_BRASILIA).replace(tzinfo=None)
