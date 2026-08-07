#!/usr/bin/env python3
"""Dispensa o comprovante (canhoto) dos registros de expedicao ANTIGOS.

Regra pedida pela operacao:
    - Registros de expedicao "Expedido" que JA TEM canhoto -> nao mexe.
    - Registros "Expedido" SEM canhoto e ANTERIORES A ONTEM -> podem ficar sem
      comprovante: sao marcados como "Finalizado" (dispensados), saindo da
      cobranca da Bia e da pendencia de comprovante do romaneio.
    - Registros de ontem em diante continuam exigindo o canhoto normalmente.

E uma correcao PONTUAL do backlog: rode UMA vez. Registros novos seguem a
regra normal (continuam pedindo o comprovante).

Uso no Bash do PythonAnywhere:
    cd ~/conferencia_system
    python scripts/dispensar_comprovante_antigo.py

Simulacao (nao grava nada, so mostra o que faria):
    python scripts/dispensar_comprovante_antigo.py --dry-run
"""
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from conferencia_app import create_app
from conferencia_app.extensions import db
from conferencia_app.models import ExpedicaoConferenciaSimples


def _data_do_registro(reg):
    """Data usada para decidir se e antigo: expedido_at -> data_conferencia ->
    created_at (primeira nao nula)."""
    return reg.expedido_at or reg.data_conferencia or reg.created_at


def main():
    dry_run = "--dry-run" in sys.argv or "-n" in sys.argv
    app = create_app()
    with app.app_context():
        # Corte = inicio de ONTEM. Tudo ANTERIOR a ontem entra na dispensa;
        # ontem e hoje continuam exigindo o comprovante.
        hoje0 = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        corte = hoje0 - timedelta(days=1)

        candidatos = (
            ExpedicaoConferenciaSimples.query
            .filter(ExpedicaoConferenciaSimples.status == "Expedido")
            .filter(
                db.or_(
                    ExpedicaoConferenciaSimples.canhoto_file_name.is_(None),
                    ExpedicaoConferenciaSimples.canhoto_file_name == "",
                )
            )
            .all()
        )

        antigos = [r for r in candidatos if (_data_do_registro(r) or hoje0) < corte]

        print(f"Corte (anterior a ontem): {corte:%d/%m/%Y %H:%M}")
        print(f"Registros 'Expedido' sem canhoto: {len(candidatos)}")
        print(f"  -> anteriores ao corte (serao dispensados): {len(antigos)}")
        print(f"  -> de ontem/hoje (mantidos, seguem exigindo): {len(candidatos) - len(antigos)}")

        if not antigos:
            print("Nada a fazer.")
            return

        if dry_run:
            print("\n[DRY-RUN] Nenhuma alteracao gravada. Amostra:")
            for r in antigos[:20]:
                d = _data_do_registro(r)
                print(f"  #{r.id} NF {r.numero_nf or '-'} {r.nome_cliente or '-'} ({d:%d/%m/%Y})")
            return

        agora = datetime.now()
        for r in antigos:
            r.status = "Finalizado"
            r.finalizado_at = agora
            r.finalizado_by = "sistema (comprovante dispensado - anterior a ontem)"
            r.updated_at = agora
        db.session.commit()
        print(f"\nOK: {len(antigos)} registro(s) marcados como Finalizado (comprovante dispensado).")


if __name__ == "__main__":
    main()
