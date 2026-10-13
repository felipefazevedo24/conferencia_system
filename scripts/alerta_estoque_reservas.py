"""Aviso por e-mail de reserva sem saldo para atender (mais reservado do que
saldo físico no GRV, sem OC aberta que cubra a falta). Fica rodando e confere
a cada 5 minutos.

Rodar como **Always-on task** na aba "Tasks" do PythonAnywhere (não como
scheduled task: ela só roda de hora em hora):

    cd ~/conferencia_system && source ~/estoque_alerta_env.sh && <python do venv> scripts/alerta_estoque_reservas.py

O script roda FORA do WSGI, então não enxerga as variáveis de lá. O
~/estoque_alerta_env.sh é um arquivo só com `export`, copiando do arquivo WSGI:
DATABASE_URL, ERP_LANCAMENTO_API_URL, ERP_LANCAMENTO_API_TOKEN, MAIL_PASSWORD e
os MAIL_SMTP_*/MAIL_SENDER, mais PUBLIC_BASE_URL (pro botão do e-mail). Os
destinatários têm padrão no config (ESTOQUE_ALERTA_EMAIL_DESTINATARIOS). Sem o
DATABASE_URL ele roda contra um SQLite local vazio e avisa tudo de novo a
cada reinício.

Pra testar uma rodada só: acrescente --uma-vez.
Pra só ver o e-mail (manda tudo que está sem saldo agora pra um endereço, sem
gravar nada): acrescente --teste fulano@colmac.com.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from conferencia_app import create_app
from conferencia_app.extensions import db
from conferencia_app.services.estoque_alerta_service import verificar_reservas

INTERVALO_SEGUNDOS = 300

TESTE_PARA = sys.argv[sys.argv.index("--teste") + 1] if "--teste" in sys.argv else None

app = create_app()
while True:
    with app.app_context():
        try:
            resultado = verificar_reservas(teste_para=TESTE_PARA)
            print(
                f"Reservas sem saldo: {resultado['sem_saldo']} (+{resultado['com_oc']} com OC cobrindo) "
                f"| avisados agora: {resultado['avisados']}",
                flush=True,
            )
        except Exception as exc:
            # GRV ou SMTP fora: nada foi gravado, o próximo ciclo tenta de novo.
            db.session.rollback()
            print(f"Falha na verificação de reservas: {exc}", flush=True)
        finally:
            # Devolve a conexão: parada 5 min, o MySQL derruba a sessão ociosa.
            db.session.remove()
    if TESTE_PARA or "--uma-vez" in sys.argv:
        break
    time.sleep(INTERVALO_SEGUNDOS)
