"""Chapa passa a ser endereçada em UND (14/10/2026). Este script, de uma vez só:

1. Zera o saldo por endereço das chapas que ficou em KG no Sync. O Sync não
   deixa misturar KG e UND no mesmo código; o saldo em UND volta pela
   recontagem do inventário. O GRV não é tocado e os endereços continuam
   cadastrados. Cada saldo zerado fica registrado nas movimentações.
2. Passa para UND as chapas que ainda estão na fila de endereçamento em KG.

Sem --aplicar só lista o que faria. Rodar logo depois do git pull + Reload:

    cd ~/conferencia_system && DATABASE_URL='...' <python do venv> scripts/enderecamento_chapas_und.py
    cd ~/conferencia_system && DATABASE_URL='...' <python do venv> scripts/enderecamento_chapas_und.py --aplicar

(SEMPRE com DATABASE_URL='...' copiado do arquivo WSGI - sem ele roda contra
um SQLite local vazio e não muda nada na produção.)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from conferencia_app import create_app
from conferencia_app.extensions import db
from conferencia_app.models import (
    EnderecoMovimento,
    EnderecoSaldo,
    ItemNota,
    RecebimentoEnderecamento,
    RecebimentoEnderecamentoEvento,
)
from conferencia_app.services.enderecamento_service import unidade_canonica
from conferencia_app.services.recebimento_enderecamento_service import UNIDADE_CHAPA
from conferencia_app.tempo import agora_br

APLICAR = "--aplicar" in sys.argv
USUARIO = "sistema"

app = create_app()
with app.app_context():
    # Chapa = item recebido com a contagem de chapas (UND) informada.
    itens_chapa = ItemNota.query.filter(ItemNota.qtd_chapas_und > 0).all()
    skus = {str(i.codigo_grv or "").strip() for i in itens_chapa} - {""}

    saldos = [s for s in EnderecoSaldo.query.filter(EnderecoSaldo.sku.in_(skus)).all()
              if unidade_canonica(s.unidade) == "KG"] if skus else []
    print(f"Saldos de chapa em KG: {len(saldos)}")
    for s in sorted(saldos, key=lambda s: (s.sku, s.endereco)):
        print(f"  {s.sku:<16} {s.endereco:<16} {s.quantidade} {s.unidade}")

    pendentes = []
    por_id = {i.id: i for i in itens_chapa}
    if por_id:
        for tarefa in RecebimentoEnderecamento.query.filter(
            RecebimentoEnderecamento.status == "Pendente",
            RecebimentoEnderecamento.item_nota_id.in_(list(por_id)),
        ).all():
            if unidade_canonica(tarefa.unidade) != unidade_canonica(UNIDADE_CHAPA):
                pendentes.append(tarefa)
    print(f"Chapas na fila de endereçamento em KG: {len(pendentes)}")
    for t in pendentes:
        print(f"  tarefa {t.id}: {t.sku} NF {por_id[t.item_nota_id].numero_nota} "
              f"{t.quantidade} {t.unidade} -> {por_id[t.item_nota_id].qtd_chapas_und:g} {UNIDADE_CHAPA}")

    if not APLICAR:
        print("\nNada foi alterado. Rode de novo com --aplicar para gravar.")
        sys.exit(0)

    agora = agora_br()
    for s in saldos:
        db.session.add(EnderecoMovimento(
            chave=f"chapa-und:{s.id}:{agora.isoformat()}", sku=s.sku, unidade=s.unidade,
            tipo="Saldo zerado", origem=s.endereco, quantidade=s.quantidade, usuario=USUARIO,
            motivo="Chapa passou a ser endereçada em UND; recontar no inventário.",
            detalhes={"endereco": s.endereco, "quantidade_kg": str(s.quantidade), "conferido": bool(s.conferido)},
            # Não muda endereço no GRV: nasce sincronizado, senão a fila de
            # envio tentaria mandar uma lista de locais que ela não tem.
            sincronizado_em=agora,
        ))
        db.session.delete(s)
    for t in pendentes:
        item = por_id[t.item_nota_id]
        db.session.add(RecebimentoEnderecamentoEvento(
            tarefa_id=t.id, tipo="Unidade alterada", usuario=USUARIO,
            detalhes={"de": f"{t.quantidade} {t.unidade}", "para": f"{item.qtd_chapas_und:g} {UNIDADE_CHAPA}"},
        ))
        t.quantidade = float(item.qtd_chapas_und)
        t.unidade = UNIDADE_CHAPA
    db.session.commit()
    print(f"\nFeito: {len(saldos)} saldo(s) zerado(s), {len(pendentes)} tarefa(s) passada(s) para UND.")
