"""Módulo Assistência Técnica: gestão das Solicitações de NF.

A solicitação é aberta no formulário público em /solicitacao-nf (sem login) e
cai aqui para a equipe separar, faturar e controlar o retorno do material.

Esta tela veio da aba "Faturamento avulso", que vivia dentro da Conferência de
Expedição (cega). As rotas de ação continuam em expedicao_avulso_routes.py,
com os mesmos papéis: separação de Logística/Comex/Fiscal/Admin, faturamento e
retorno só de Fiscal/Admin. Quem emite a nota continua sendo o Fiscal.
"""

from flask import Blueprint, render_template, session

from ..auth import permission_required

assistencia_tecnica_bp = Blueprint("assistencia_tecnica", __name__)

PERMISSION = "PAGE_ASSISTENCIA_TECNICA"


@assistencia_tecnica_bp.route("/assistencia-tecnica")
@permission_required(PERMISSION)
def page_assistencia_tecnica():
    return render_template("assistencia_tecnica.html", user=session.get("username"))
