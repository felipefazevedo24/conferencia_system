"""Consolida no Controle de Chapas uma NF cujas N linhas viraram 1 linha no GRV.

Por quê: o casamento de códigos GRV (_aplicar_codigos_grv) é 1:1. Quando a NF
tem N linhas de chapa e o GRV recebe tudo numa linha só, apenas a primeira
linha ganha o código GRV (e o peso TOTAL do GRV); as outras ficam com o código
do fornecedor, sem saldo nem lote, como se fossem outro material.

O que faz, espelhando o GRV (1 código, 1 lote):
- a linha principal (a que tem codigo_grv) fica com a UND somada de todas;
- as demais saem da visualização via ChapaControleExclusao (mesmo mecanismo do
  botão de excluir do admin). O recebimento em si não é alterado.
Tudo registrado na auditoria de chapas. Idempotente: rodar de novo não muda nada.

Sem --aplicar só mostra o que faria.

    DATABASE_URL='<do WSGI>' python scripts/consolidar_chapas_nf.py --nota 21728 --principal 5194 --usuario SEU_LOGIN
"""
import argparse
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--nota', required=True)
    parser.add_argument('--principal', required=True, type=int, help='id do ItemNota que já tem o código GRV')
    parser.add_argument('--usuario', required=True, help='login gravado na auditoria')
    parser.add_argument('--aplicar', action='store_true', help='grava; sem isso é só simulação')
    args = parser.parse_args()
    # Sem isso o script roda num SQLite local vazio e "dá certo" sem tocar a produção.
    if not (os.environ.get('DATABASE_URL') or os.environ.get('DB_PATH')):
        parser.error('Defina DATABASE_URL (servidor) ou DB_PATH (SQLite) explicitamente.')

    from conferencia_app import create_app
    from conferencia_app.extensions import db
    from conferencia_app.models import ChapaCalculo, ChapaControleExclusao, ItemNota
    from conferencia_app.routes.logistica_inventario_routes import _chapa_kg_do_item
    from conferencia_app.services.chapa_auditoria_service import alterar_unidades, registrar

    app = create_app()
    with app.app_context():
        itens = ItemNota.query.filter_by(numero_nota=args.nota).order_by(ItemNota.id).all()
        principal = next((i for i in itens if i.id == args.principal), None)
        if not principal:
            sys.exit(f'ItemNota {args.principal} não pertence à NF {args.nota}.')
        if not principal.codigo_grv:
            sys.exit(f'ItemNota {principal.id} não tem código GRV; escolha a linha que tem.')
        chapas = [i for i in itens if i.qtd_chapas_und and i.qtd_chapas_und > 0]
        outros = [i for i in chapas if i.id != principal.id]
        if not outros:
            sys.exit('Nada a consolidar: só a linha principal está no controle de chapas.')

        excluidos = {e.item_nota_id for e in ChapaControleExclusao.query.filter(
            ChapaControleExclusao.item_nota_id.in_([i.id for i in outros])).all()}
        # Só soma quem ainda não foi consolidado: a principal já carrega a UND dos
        # excluídos, e somar de novo inflaria a cada execução.
        pendentes = [i for i in outros if i.id not in excluidos]
        und_total = float(principal.qtd_chapas_und) + sum(float(i.qtd_chapas_und) for i in pendentes)
        kg_principal = _chapa_kg_do_item(principal)
        calc = ChapaCalculo.query.filter_by(item_nota_id=principal.id).first()

        print(f'NF {args.nota} - principal {principal.id} ({principal.codigo_grv})')
        print(f'  UND: {principal.qtd_chapas_und} -> {und_total}')
        print(f'  peso: {kg_principal:.2f} kg -> {kg_principal / und_total:.2f} kg/chapa'
              + (f' (cálculo salvo: {calc.peso_por_peca} kg/peça)' if calc and calc.peso_por_peca else ''))
        for i in outros:
            estado = 'já fora do controle' if i.id in excluidos else 'sai do controle'
            print(f'  {i.id} {i.codigo} {_chapa_kg_do_item(i):.2f} kg {i.qtd_chapas_und} und: {estado}')

        if not args.aplicar:
            print('\nSimulação. Rode com --aplicar para gravar.')
            return

        alterar_unidades(principal, und_total, args.usuario)
        for i in pendentes:
            db.session.add(ChapaControleExclusao(item_nota_id=i.id, usuario=args.usuario))
            registrar(i, 'Exclusão do controle', args.usuario,
                      {'no_controle': True, 'und': i.qtd_chapas_und, 'kg_nf': _chapa_kg_do_item(i)},
                      {'no_controle': False, 'consolidado_em': principal.id})
        db.session.commit()
        print('\nAplicado.')


if __name__ == '__main__':
    main()
