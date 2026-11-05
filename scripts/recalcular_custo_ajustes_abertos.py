"""Troca o custo dos ajustes de inventário ainda abertos (Validação de
Suprimentos e Emissão do Formulário) pelo custo médio do Cardex. Até
04/11/2026 o ajuste nascia com tproduto.preco_custo, que é o custo fiscal e
vem zerado em muitos itens. Os que já entraram num FORM-08.52 (Finance em
diante) não mudam: o documento já saiu.

Sem custo no Cardex, o ajuste fica como está. Sem --aplicar só lista. Uso:

    cd ~/conferencia_system && DATABASE_URL='...' python scripts/recalcular_custo_ajustes_abertos.py
    cd ~/conferencia_system && DATABASE_URL='...' python scripts/recalcular_custo_ajustes_abertos.py --aplicar
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from conferencia_app import create_app
from conferencia_app.extensions import db
from conferencia_app.models import LogisticaInventarioAjuste
from conferencia_app.services import logistica_cardex_service as cardex


def brl(valor):
    if valor is None:
        return "sem custo"
    return f"R$ {valor:,.4f}".replace(",", "X").replace(".", ",").replace("X", ".")


app = create_app()
with app.app_context():
    ajustes = (
        LogisticaInventarioAjuste.query
        .filter(LogisticaInventarioAjuste.status_modulo.in_(("Validacao", "Relatorio")))
        .order_by(LogisticaInventarioAjuste.id)
        .all()
    )
    print(f"Ajustes abertos: {len(ajustes)}. Consultando o Cardex...")
    cardex.precarregar_custos_inventario([a.codigo_produto for a in ajustes])
    mudar, sem_cardex = [], 0
    for a in ajustes:
        novo = cardex.custo_medio_inventario(a.codigo_produto)
        if novo is None:
            sem_cardex += 1
            continue
        if a.custo_medio is None or abs(float(a.custo_medio) - novo) > 1e-6:
            mudar.append((a, novo))

    for a, novo in mudar:
        impacto = (a.diferenca or 0) * novo
        print(f"  {a.id}: {a.codigo_produto} {a.local_codigo or '-'} ({a.status_modulo}) "
              f"{brl(a.custo_medio)} -> {brl(novo)} | impacto {brl(impacto)}")
    print(f"Mudam: {len(mudar)} | já corretos: {len(ajustes) - len(mudar) - sem_cardex} | sem custo no Cardex: {sem_cardex}")
    if "--aplicar" not in sys.argv:
        print("Nada foi alterado. Rode de novo com --aplicar.")
        sys.exit(0)
    for a, novo in mudar:
        a.custo_medio = novo
    db.session.commit()
    print(f"Atualizados: {len(mudar)}")
