"""Corrige o código GRV de linhas de NF lançada que ficaram com o código errado.

Por quê: até 25/09/2026 o pareamento NF x pedido de compra sobrescrevia o
codigo_grv mesmo de nota já lançada; com bitolas parecidas no mesmo pedido,
uma chapa 3/16" (19-01-00549) ficou como 3/8" (19-01-00564) no Controle de Chapas.
O pareamento não mexe mais em nota lançada; este script arruma o que ficou.

Só grava se o código novo existir nas linhas do GRV daquela NF (a bridge
precisa responder). Registra na auditoria de chapas. Idempotente.
Sem --aplicar só mostra o que faria.

    DATABASE_URL='<do WSGI>' python scripts/corrigir_codigo_grv_chapa.py --item 5173 --item 5208 --codigo 19-01-00549 --usuario SEU_LOGIN
"""
import argparse
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--item', required=True, type=int, action='append', help='id do ItemNota (pode repetir)')
    parser.add_argument('--codigo', required=True, help='código GRV correto')
    parser.add_argument('--usuario', required=True, help='login gravado na auditoria')
    parser.add_argument('--aplicar', action='store_true', help='grava; sem isso é só simulação')
    args = parser.parse_args()
    # Sem isso o script roda num SQLite local vazio e "dá certo" sem tocar a produção.
    if not (os.environ.get('DATABASE_URL') or os.environ.get('DB_PATH')):
        parser.error('Defina DATABASE_URL (servidor) ou DB_PATH (SQLite) explicitamente.')

    from conferencia_app import create_app
    from conferencia_app.extensions import db
    from conferencia_app.models import ItemNota
    from conferencia_app.services.chapa_auditoria_service import registrar
    from conferencia_app.services.erp_lancamento_service import buscar_entradas_chapa_lote

    codigo = args.codigo.strip()
    app = create_app()
    with app.app_context():
        itens = ItemNota.query.filter(ItemNota.id.in_(args.item)).order_by(ItemNota.id).all()
        faltando = set(args.item) - {i.id for i in itens}
        if faltando:
            sys.exit(f'ItemNota não encontrado: {sorted(faltando)}')
        entradas = buscar_entradas_chapa_lote(itens)
        if not entradas:
            sys.exit('A bridge do GRV não respondeu; sem ela não dá pra conferir o código. Nada foi alterado.')
        codigos_grv = {}
        for entrada in entradas:
            nota = str(entrada.get('numero_nota') or '').strip()
            codigos_grv.setdefault(nota, set()).update(
                str(it.get('cod_interno') or '').strip() for it in entrada.get('itens') or [])

        alterar = []
        for item in itens:
            nota = str(item.numero_nota or '').strip()
            if codigo not in codigos_grv.get(nota, set()):
                sys.exit(f'{codigo} não está nas linhas do GRV da NF {nota} (item {item.id}). Nada foi alterado.')
            estado = 'já correto' if item.codigo_grv == codigo else f'{item.codigo_grv} -> {codigo}'
            print(f'  {item.id} NF {nota} {item.descricao!r} {item.qtd_real} {item.unidade_comercial}: {estado}')
            if item.codigo_grv != codigo:
                alterar.append(item)

        if not args.aplicar:
            print('\nSimulação. Rode com --aplicar para gravar.')
            return
        for item in alterar:
            anterior = item.codigo_grv
            item.codigo_grv = codigo[:80]
            registrar(item, 'Código GRV corrigido', args.usuario, {'codigo': anterior}, {'codigo': codigo})
        db.session.commit()
        print(f'\nAplicado em {len(alterar)} item(ns).')


if __name__ == '__main__':
    main()
