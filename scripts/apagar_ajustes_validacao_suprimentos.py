"""Apaga os ajustes de inventário que estão em "Validação de Suprimentos"
(antigo "Aguardando gestor"). Limpeza pedida pela logística em 03/11/2026,
junto com a saída do motivo da contagem.

Apaga de verdade: o ajuste some da aba Ajuste de Estoque. A contagem que
abriu o ajuste continua em Realizados. Se o item for contado de novo e
divergir, abre um ajuste novo normalmente.

Sem --aplicar só lista. Uso:

    cd ~/conferencia_system && DATABASE_URL='...' python scripts/apagar_ajustes_validacao_suprimentos.py
    cd ~/conferencia_system && DATABASE_URL='...' python scripts/apagar_ajustes_validacao_suprimentos.py --aplicar
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from conferencia_app import create_app
from conferencia_app.extensions import db
from conferencia_app.models import LogisticaInventarioAjuste, LogisticaInventarioAnaliseCausa

app = create_app()
with app.app_context():
    print(f"Banco: {db.engine.url.render_as_string(hide_password=True)}")
    ajustes = LogisticaInventarioAjuste.query.filter_by(status_modulo="Validacao").order_by(LogisticaInventarioAjuste.id).all()
    print(f"Em Validação de Suprimentos: {len(ajustes)}")
    for a in ajustes:
        print(f"  {a.id}: {a.codigo_produto} {a.local_codigo or '-'} contado {a.qtde_contada:g} x GRV {a.qtde_estoque_no_momento:g} ({a.criado_em:%d/%m/%Y})")
    if "--aplicar" not in sys.argv:
        print("Nada foi alterado. Rode de novo com --aplicar.")
        sys.exit(0)
    ids = [a.id for a in ajustes]
    # Analise de causa raiz so' nasce na confirmacao, mas se alguma tiver
    # sido aberta (Pular/Estornar), sai junto: a FK nao aceita orfa.
    analises = LogisticaInventarioAnaliseCausa.query.filter(LogisticaInventarioAnaliseCausa.ajuste_id.in_(ids)).delete(synchronize_session=False) if ids else 0
    for a in ajustes:
        db.session.delete(a)
    db.session.commit()
    print(f"Apagados: {len(ids)} ajuste(s) e {analises} análise(s) de causa raiz.")
