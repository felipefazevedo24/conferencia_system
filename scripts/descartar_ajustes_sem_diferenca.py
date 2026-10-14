"""Descarta os ajustes de inventário que estão aguardando o gestor com
diferença zero. Foram abertos entre 08/10 e 14/10/2026, quando contagem que
batia com o GRV ainda ia pro gestor. Não apaga: o ajuste fica "Descartado"
com o motivo, como se o gestor tivesse descartado na tela.

Sem --aplicar só lista. Uso:

    cd ~/conferencia_system && DATABASE_URL='...' python scripts/descartar_ajustes_sem_diferenca.py
    cd ~/conferencia_system && DATABASE_URL='...' python scripts/descartar_ajustes_sem_diferenca.py --aplicar
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from conferencia_app import create_app
from conferencia_app.models import LogisticaInventarioAjuste
from conferencia_app.services import logistica_inventario_ajuste_service as svc

MOTIVO = "Sem diferença entre contado e GRV: não precisa de ajuste."

app = create_app()
with app.app_context():
    ajustes = [
        a for a in LogisticaInventarioAjuste.query.filter_by(status_modulo="Validacao").all()
        if abs(float(a.diferenca or 0)) <= svc.TOLERANCIA_DIVERGENCIA
    ]
    print(f"Aguardando gestor com diferença zero: {len(ajustes)}")
    for a in ajustes:
        print(f"  {a.id}: {a.codigo_produto} {a.local_codigo or '-'} contado {a.qtde_contada:g} x GRV {a.qtde_estoque_no_momento:g}")
    if "--aplicar" not in sys.argv:
        print("Nada foi alterado. Rode de novo com --aplicar.")
        sys.exit(0)
    for a in ajustes:
        svc.descartar_divergencia(a, "sistema", MOTIVO)
    print(f"Descartados: {len(ajustes)}")
