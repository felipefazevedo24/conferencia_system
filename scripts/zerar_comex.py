"""Zera TODOS os registros do modulo Comex (processos, OC/PO, cotacoes,
documentos, comentarios, follow-ups, lembretes e o cadastro de
fornecedores) - uso unico pra limpar dados de teste antes de comecar a
usar o modulo de verdade em producao.

Por seguranca, SO APAGA se rodar com --confirmar. Sem essa flag, e' um
dry-run: so mostra quantos registros existem em cada tabela, sem apagar
nada.

Uso (mesmo padrao de scripts/aplicar_migracao_comex.py):
    cd ~/conferencia_system
    python scripts/zerar_comex.py                # dry-run, so mostra contagens
    python scripts/zerar_comex.py --confirmar     # apaga de verdade

Se DATABASE_URL nao estiver disponivel no shell (comum no console do
PythonAnywhere - so fica definida dentro do arquivo WSGI), passe
explicitamente com ASPAS SIMPLES (o nome do banco tem "$", que aspas
duplas expandem errado no Bash):
    DATABASE_URL='mysql://usuario:senha@host/usuario$banco' python scripts/zerar_comex.py --confirmar

Isso NAO apaga nada de outro modulo (Recebimento, Expedicao, Compras,
etc.) - so as tabelas comex_*.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from conferencia_app import create_app
from conferencia_app.extensions import db
from conferencia_app.models import (
    ComexComentario,
    ComexCotacao,
    ComexCotacaoVolume,
    ComexDocumento,
    ComexEntregaFoto,
    ComexFollowUp,
    ComexFollowUpLog,
    ComexFornecedor,
    ComexLembrete,
    ComexPoItem,
    ComexProcesso,
)
from conferencia_app.services import comex_service as svc

# Ordem importa pra nao esbarrar em FK: filhos antes dos pais.
# ComexDocumento e' tratado a parte (precisa apagar o arquivo fisico/Drive
# de cada um antes de apagar a linha).
TABELAS_EM_ORDEM = [
    ComexCotacaoVolume,
    ComexFollowUpLog,
    ComexPoItem,
    ComexDocumento,
    ComexComentario,
    ComexEntregaFoto,
    ComexLembrete,
    ComexCotacao,
    ComexFollowUp,
    ComexProcesso,
    ComexFornecedor,
]

app = create_app()
with app.app_context():
    confirmar = "--confirmar" in sys.argv

    print("=== Contagem atual (modulo Comex) ===")
    contagens = {}
    for modelo in TABELAS_EM_ORDEM:
        total = modelo.query.count()
        contagens[modelo.__tablename__] = total
        print(f"  {modelo.__tablename__}: {total}")
    total_geral = sum(contagens.values())

    if total_geral == 0:
        print("\nNada pra apagar - todas as tabelas do Comex ja estao vazias.")
        sys.exit(0)

    if not confirmar:
        print(f"\nTotal: {total_geral} registro(s) em {len(TABELAS_EM_ORDEM)} tabelas.")
        print("Isso foi so uma contagem (dry-run) - NADA foi apagado.")
        print("Rode de novo com --confirmar pra apagar de verdade:")
        print("  python scripts/zerar_comex.py --confirmar")
        sys.exit(0)

    print(f"\nApagando {total_geral} registro(s)...")

    # Documentos: apaga o arquivo (Google Drive ou disco local) antes da
    # linha - melhor esforco, um arquivo que falhar nao trava o resto.
    documentos = ComexDocumento.query.all()
    for doc in documentos:
        try:
            svc.apagar_documento(doc)  # ja apaga arquivo + linha + commit
        except Exception as exc:
            print(f"  aviso: falha ao apagar arquivo do documento {doc.id} ({exc}) - removendo so o registro.")
            db.session.delete(doc)
            db.session.commit()
    print(f"  comex_documento: {len(documentos)} apagado(s)")

    # comex_processo.cotacao_vencedora_id referencia comex_cotacao (e
    # comex_cotacao.processo_id referencia comex_processo de volta) - essa
    # referencia circular precisa ser zerada antes de apagar qualquer uma
    # das duas tabelas, senao a FK trava o DELETE.
    zerados = ComexProcesso.query.filter(ComexProcesso.cotacao_vencedora_id.isnot(None)).update(
        {ComexProcesso.cotacao_vencedora_id: None}, synchronize_session=False
    )
    db.session.commit()
    if zerados:
        print(f"  comex_processo.cotacao_vencedora_id: {zerados} zerado(s) (quebra referencia circular com comex_cotacao)")

    for modelo in TABELAS_EM_ORDEM:
        if modelo is ComexDocumento:
            continue  # ja tratado acima
        apagados = modelo.query.delete(synchronize_session=False)
        db.session.commit()
        print(f"  {modelo.__tablename__}: {apagados} apagado(s)")

    print("\nComex zerado com sucesso. Nenhum outro modulo foi afetado.")
