"""Acerta os Registros de Expedição que ficaram "Pendente de expedição" sem NF.

Eles nasceram das fotos tiradas na Conferência de Expedição antes de a nota
sair. Até a correção de 19/10/2026, ao expedir o romaneio o sistema não
reconhecia esse rascunho, criava outro registro para a NF e o rascunho (com as
fotos) ficava pendente para sempre - 107 em produção nessa data.

Para cada rascunho sem NF o script acha a ordem dele (pelo log das fotos, que
guarda o nome do arquivo com o número do rascunho, ou por orçamento + cliente
quando só uma ordem bate) e:
- ordem já expedida com registro próprio: move as fotos para esse registro e
  apaga o rascunho, que fica vazio;
- ordem com NF e ainda não expedida: o rascunho só recebe a NF;
- ordem ainda sem NF, ou ordem não identificada: não mexe, só lista.

Sem --aplicar só lista. Uso (SEMPRE com o DATABASE_URL do arquivo WSGI):

    cd ~/conferencia_system && DATABASE_URL='...' <python do venv> scripts/corrigir_registros_sem_nf.py
    cd ~/conferencia_system && DATABASE_URL='...' <python do venv> scripts/corrigir_registros_sem_nf.py --aplicar
"""
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from conferencia_app import create_app
from conferencia_app.extensions import db
from conferencia_app.models import (
    ExpedicaoConferenciaLog,
    ExpedicaoConferenciaSimples as Registro,
    ExpedicaoConferenciaSimplesFoto as Foto,
    ExpedicaoOrdemFat,
    ExpedicaoOrdemST,
)

APLICAR = "--aplicar" in sys.argv
PENDENTE = ("Pendente de expedição", "Pendente de expedicao")


def nfs(valor):
    return [n.strip() for n in str(valor or "").replace(";", ",").replace("/", ",").split(",") if n.strip()]


def ordem_do_rascunho(r):
    """(ordem, como achou) ou (None, motivo)."""
    for modelo in (ExpedicaoOrdemFat, ExpedicaoOrdemST):
        ligada = modelo.query.filter_by(expedicao_registro_id=r.id).first()
        if ligada is not None:
            return ligada, "vínculo da ordem"
    log = (
        ExpedicaoConferenciaLog.query
        .filter(ExpedicaoConferenciaLog.acao == "fotos", ExpedicaoConferenciaLog.detalhes.like(f"%_reg{r.id}_%"))
        .order_by(ExpedicaoConferenciaLog.id.desc())
        .first()
    )
    if log is not None:
        modelo = ExpedicaoOrdemFat if log.origem == "fat" else ExpedicaoOrdemST
        ordem = db.session.get(modelo, log.ordem_id)
        if ordem is not None:
            return ordem, "log das fotos"
    # Rascunhos antigos, de antes do log das fotos: orçamento + cliente.
    if r.tipo_referencia == "OrdemCompra" and r.ordem_compra:
        candidatas = ExpedicaoOrdemST.query.filter_by(cod_ordem_compra=r.ordem_compra).all()
    elif r.orcamento:
        candidatas = ExpedicaoOrdemFat.query.filter_by(orcamento=r.orcamento, cliente=r.nome_cliente).all()
    else:
        candidatas = []
    if len(candidatas) == 1:
        return candidatas[0], "orçamento + cliente"
    return None, ("várias ordens com o mesmo orçamento/cliente" if candidatas else "ordem não encontrada")


app = create_app()
with app.app_context():
    rascunhos = [
        r for r in Registro.query.filter(Registro.status.in_(PENDENTE)).order_by(Registro.id).all()
        if not nfs(r.numero_nf)
    ]
    print(f"Rascunhos pendentes sem NF: {len(rascunhos)}")
    resumo = Counter()
    for r in rascunhos:
        fotos = Foto.query.filter_by(conferencia_id=r.id).all()
        tem_foto_cliente = bool(r.foto_cliente_file_name)
        ordem, como = ordem_do_rascunho(r)
        ref = f"reg {r.id} ({len(fotos)} foto(s){' + foto cliente' if tem_foto_cliente else ''})"
        if ordem is None:
            resumo["sem ordem: " + como] += 1
            print(f"  {ref}: não mexe - {como}")
            continue
        cod = getattr(ordem, "cod_ordem_fat", None) or getattr(ordem, "cod_ordem_compra", None)
        nfs_ordem = nfs(ordem.numero_nf)
        final = db.session.get(Registro, ordem.expedicao_registro_id) if ordem.expedicao_registro_id else None
        if final is not None and final.id != r.id and final.status not in PENDENTE:
            resumo["fotos movidas, rascunho apagado"] += 1
            print(f"  {ref}: ordem {cod} ({como}) já expedida -> fotos para o reg {final.id} ({final.status}), rascunho apagado")
            if APLICAR:
                for f in fotos:
                    f.conferencia_id = final.id
                if tem_foto_cliente and not final.foto_cliente_file_name:
                    final.foto_cliente_file_name = r.foto_cliente_file_name
                    final.foto_cliente_file_path = r.foto_cliente_file_path
                    final.foto_cliente_uploaded_at = r.foto_cliente_uploaded_at
                    final.foto_cliente_uploaded_by = r.foto_cliente_uploaded_by
                db.session.flush()
                db.session.delete(r)
        elif nfs_ordem:
            resumo["recebeu a NF"] += 1
            print(f"  {ref}: ordem {cod} ({como}) com NF {ordem.numero_nf} e ainda não expedida -> rascunho recebe a NF")
            if APLICAR:
                r.numero_nf = ordem.numero_nf
                ordem.expedicao_registro_id = r.id
        else:
            resumo["ordem ainda sem NF"] += 1
            print(f"  {ref}: ordem {cod} ({como}) ainda sem NF - pendente de verdade, não mexe")
    print("\nResumo:")
    for k, v in sorted(resumo.items()):
        print(f"  {k}: {v}")
    if APLICAR:
        db.session.commit()
        print("\nGravado.")
    else:
        print("\nNada foi alterado. Rode de novo com --aplicar.")
