"""Deixa so' a provisao mais recente de cada processo do Comex.

Ate' 27/11/2026 cada "Gravar provisao" criava uma versao nova; agora a
provisao e' uma por processo e e' atualizada no lugar (pedido do usuario).
Este script apaga as versoes antigas - a mais recente e' a que ja' valia nos
numeros (provisao x realizado, relatorio consolidado). Os PDFs ja' mandados
por e-mail continuam no e-mail da Controladoria.

Roda primeiro SEM --aplicar (so' mostra o que faria):

    cd ~/conferencia_system && DATABASE_URL='...' python scripts/limpar_versoes_provisao_comex.py
    cd ~/conferencia_system && DATABASE_URL='...' python scripts/limpar_versoes_provisao_comex.py --aplicar

(SEMPRE com DATABASE_URL='...' copiado do arquivo WSGI - sem ele o script
roda contra um SQLite local e nao mexe na producao.)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from conferencia_app import create_app
from conferencia_app.extensions import db
from conferencia_app.models import ComexProcesso, ComexProvisao

aplicar = "--aplicar" in sys.argv
app = create_app()
with app.app_context():
    print("Banco:", db.engine.url.render_as_string(hide_password=True))
    total = 0
    processos = (ComexProcesso.query.join(ComexProvisao, ComexProvisao.processo_id == ComexProcesso.id)
                 .group_by(ComexProcesso.id).having(db.func.count(ComexProvisao.id) > 1).all())
    for proc in processos:
        versoes = sorted(proc.provisoes, key=lambda p: (p.versao, p.id))
        manter, apagar = versoes[-1], versoes[:-1]
        total += len(apagar)
        print(f"{proc.id_op}: mantém v{manter.versao} ({manter.criado_em:%d/%m/%Y}), apaga {', '.join(f'v{p.versao}' for p in apagar)}")
        if aplicar:
            for p in apagar:
                db.session.delete(p)
    if aplicar:
        db.session.commit()
        print(f"Apagadas {total} versão(ões) antiga(s) em {len(processos)} processo(s).")
    else:
        print(f"{total} versão(ões) antiga(s) em {len(processos)} processo(s). Nada apagado - rode com --aplicar.")
