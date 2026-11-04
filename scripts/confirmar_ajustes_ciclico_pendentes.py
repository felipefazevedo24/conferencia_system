"""Leva para Emissão do Formulário os ajustes que o inventário cíclico abriu
antes de 04/11/2026 e ficaram parados em Validação de Suprimentos. Desde
então o ajuste já sai confirmado do cíclico (ver
inventario_ciclico_service._fechar); estes são os de antes disso.

Confirma no nome do gestor que decidiu no cíclico: quem fez a 3ª contagem,
ou quem dispensou a recontagem (gravado na observação da contagem). Igual à
regra nova, só confirma se esse gestor tem a permissão de Validação de
Suprimentos; os outros ficam na Validação.

Sem --aplicar só lista. Uso:

    cd ~/conferencia_system && DATABASE_URL='...' python scripts/confirmar_ajustes_ciclico_pendentes.py
    cd ~/conferencia_system && DATABASE_URL='...' python scripts/confirmar_ajustes_ciclico_pendentes.py --aplicar
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from conferencia_app import create_app
from conferencia_app.auth import has_permission
from conferencia_app.extensions import db
from conferencia_app.models import InventarioCiclicoItem, LogisticaInventarioAjuste, LogisticaInventarioInicial
from conferencia_app.services import inventario_ciclico_service as ciclico
from conferencia_app.services import logistica_inventario_ajuste_service as svc


def validacao_do_item(item):
    """(gestor, texto da justificativa) de quem decidiu no ciclico."""
    if item.contagem3 is not None and item.contagem3_por:
        return item.contagem3_por, "3ª contagem confirmou a diferença"
    contagem = db.session.get(LogisticaInventarioInicial, item.contagem_id) if item.contagem_id else None
    achado = re.search(r"recontagem dispensada por (.+?)\)", (contagem.observacao or "") if contagem else "")
    if achado:
        rodada = 2 if item.contagem2 is not None else 1
        return achado.group(1).strip(), f"{rodada}ª contagem divergente, recontagem dispensada"
    return None, None


app = create_app()
# has_permission olha a sessao quando falta o cargo: precisa de um request.
with app.app_context(), app.test_request_context():
    itens = InventarioCiclicoItem.query.filter(InventarioCiclicoItem.ajuste_id.isnot(None)).all()
    confirmar, ficam = [], []
    for item in itens:
        ajuste = db.session.get(LogisticaInventarioAjuste, item.ajuste_id)
        if not ajuste or ajuste.status_modulo != "Validacao":
            continue
        gestor, texto = validacao_do_item(item)
        if gestor and has_permission(ciclico.PERM_VALIDACAO_SUPRIMENTOS, username=gestor):
            confirmar.append((ajuste, item, gestor, texto))
        else:
            ficam.append((ajuste, gestor))

    print(f"Ajustes do cíclico parados em Validação: {len(confirmar) + len(ficam)}")
    for ajuste, item, gestor, texto in confirmar:
        print(f"  {ajuste.id}: {ajuste.codigo_produto} {ajuste.local_codigo or '-'} dif {ajuste.diferenca:+g}"
              f" -> Emissão do Formulário, por {gestor} ({ciclico.numero(item.distribuicao)}: {texto})")
    for ajuste, gestor in ficam:
        motivo = f"{gestor} sem permissão de Validação" if gestor else "não deu para saber quem decidiu"
        print(f"  {ajuste.id}: {ajuste.codigo_produto} {ajuste.local_codigo or '-'} -> fica na Validação ({motivo})")
    if "--aplicar" not in sys.argv:
        print("Nada foi alterado. Rode de novo com --aplicar.")
        sys.exit(0)
    for ajuste, item, gestor, texto in confirmar:
        svc.confirmar_pelo_inventario_ciclico(
            ajuste, gestor, f"Validado no inventário cíclico {ciclico.numero(item.distribuicao)}: {texto}")
    print(f"Levados para Emissão do Formulário: {len(confirmar)}")
