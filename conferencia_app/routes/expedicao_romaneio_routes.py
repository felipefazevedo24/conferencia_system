"""Rotas do Romaneio de Expedição."""

import base64
import json
import os
from datetime import datetime
from flask import Blueprint, request, jsonify, render_template, session, current_app
from ..extensions import db
from ..models import (
    ExpedicaoRomaneio,
    ExpedicaoRomaneioNF,
    ExpedicaoRomaneioExclusao,
    ExpedicaoRomaneioFotoCarregamento,
    ExpedicaoOrdemFat,
    ExpedicaoOrdemFatItem,
    ExpedicaoOrdemST,
    ExpedicaoConferenciaSimples,
)
from ..auth import permission_required, roles_required
from ..services import expedicao_fat_service as fat_svc
from ..services import expedicao_st_service as st_svc
from ..services import cadastro_workflow_service as cad_svc
from ..services import danfe_service
from ..services.erp_nfe_emitidas_service import buscar_nfe_emitida_erp
from werkzeug.utils import secure_filename
from ..services.expedicao_photo_storage import using_drive, upload_bytes_to_drive, upload_to_drive
from .api_routes import _resolver_foto_expedicao, _send_foto_expedicao
from ..services.nfe_email_service import enviar_aviso_coleta_fob

expedicao_romaneio_bp = Blueprint("expedicao_romaneio", __name__)

PERMISSION = "PAGE_EXPEDICAO_CONF_CEGA"
ROLES = ("Conferente", "Admin", "Fiscal", "Logística", "Comex")
ROLES_NAO_ADMIN = ("Conferente", "Fiscal", "Logística", "Comex")


def _parse_float(valor, default=0.0) -> float:
    try:
        texto = str(valor if valor is not None else "").strip().replace(",", ".")
        return float(texto) if texto else default
    except (TypeError, ValueError):
        return default


def _parse_int(valor, default=0) -> int:
    return int(_parse_float(valor, default))


def _gerar_solicitacao_entrega_cif(romaneio) -> None:
    """Gatilho imediato da Regra 2: gera a Solicitacao de Entrega de um
    romaneio CIF assim que ele fica Pronto ou Expedido. Best-effort: nunca
    interrompe o fluxo do romaneio (o scheduler cobre eventuais falhas)."""
    try:
        if not current_app.config.get("SOLICITACAO_CIF_AUTO_ENABLED", True):
            return
        if not current_app.config.get("SOLICITACAO_CIF_ENTREGA_ENABLED", True):
            return
        if str(getattr(romaneio, "tipo_frete", "") or "").strip().upper() != "CIF":
            return
        from ..services.solicitacao_logistica_cif_service import (
            gerar_solicitacao_entrega_para_romaneio,
        )

        solicitante = session.get("username", "sistema")
        gerar_solicitacao_entrega_para_romaneio(romaneio, solicitante=solicitante, commit=True)
    except Exception:
        current_app.logger.exception(
            "Falha ao gerar Solicitacao de Entrega CIF para o romaneio %s.",
            getattr(romaneio, "numero_romaneio", None),
        )


def _cancelar_solicitacao_entrega_cif(romaneio, motivo: str = "") -> None:
    """Estorna a Solicitacao de Entrega CIF gerada automaticamente quando o
    romaneio e estornado/excluido. Best-effort: nunca interrompe o fluxo."""
    try:
        if str(getattr(romaneio, "tipo_frete", "") or "").strip().upper() != "CIF":
            return
        from ..services.solicitacao_logistica_cif_service import (
            cancelar_solicitacao_entrega_para_romaneio,
        )

        usuario = session.get("username", "sistema")
        cancelar_solicitacao_entrega_para_romaneio(
            romaneio, usuario=usuario, motivo=motivo, commit=True
        )
    except Exception:
        current_app.logger.exception(
            "Falha ao estornar Solicitacao de Entrega CIF do romaneio %s.",
            getattr(romaneio, "numero_romaneio", None),
        )


def _calcular_totais_nfs(nfs: list) -> tuple[float, int]:
    peso_total = sum(_parse_float(getattr(nf, "peso_bruto", 0) or 0) for nf in (nfs or []))
    volumes_total = sum(_parse_int(getattr(nf, "qtde_volumes", 0) or 0) for nf in (nfs or []))
    return peso_total, volumes_total


def _recalcular_totais_romaneio(romaneio: ExpedicaoRomaneio) -> tuple[float, int]:
    peso_total, volumes_total = _calcular_totais_nfs(romaneio.nfs or [])
    romaneio.peso_bruto_total = peso_total
    romaneio.qtde_volumes_total = volumes_total
    return peso_total, volumes_total


def _dados_nf_do_bridge(numero_nf: str) -> dict | None:
    """Busca os dados reais de uma NF-e emitida direto na bridge Postgres do
    ERP (mesma fonte usada no envio de e-mail e no destinatário do romaneio),
    para permitir incluir manualmente no romaneio uma NF que NÃO passou pela
    Conferência de Expedição (sem ExpedicaoOrdemFat/ST correspondente).
    Retorna None se a NF não for encontrada/autorizada no ERP ou não tiver
    XML disponível - nesse caso o chamador cai no fallback manual (payload)."""
    try:
        nota = buscar_nfe_emitida_erp(numero_nf=numero_nf)
        xml_bytes = (nota or {}).get("xml_bytes")
        if not xml_bytes:
            return None
        nfe = danfe_service.parse_nfe_xml(xml_bytes)
        vol_peso_raw = str(nfe.get("vol_pesoB") or "").strip()
        vol_qtd_raw = str(nfe.get("vol_qtd") or "").strip()
        return {
            "cliente": nfe.get("dest_nome") or "",
            "peso_bruto": _parse_float(vol_peso_raw) if vol_peso_raw else None,
            "qtde_volumes": _parse_int(vol_qtd_raw) if vol_qtd_raw else None,
            "especie_volumes": nfe.get("vol_esp") or "",
            "modfrete": str(nfe.get("transp_modFrete") or "").strip(),
        }
    except Exception:
        current_app.logger.warning(
            "Falha ao buscar NF %s na bridge do ERP para inclusão manual no romaneio", numero_nf, exc_info=True
        )
        return None


_MODFRETE_LABEL = {
    "0": "Emitente (CIF)",
    "1": "Destinatário (FOB)",
    "2": "Terceiros",
    "3": "Próprio/Remetente (CIF)",
    "4": "Próprio/Destinatário (FOB)",
    "9": "Sem frete",
}

# Rótulo curto do grupo de frete, usado nas mensagens (CC-e, avisos).
_FRETE_GRUPO_LABEL = {
    "CIF": "CIF",
    "FOB": "FOB",
    "TERCEIROS": "Terceiros",
    "SEM_FRETE": "Sem frete",
}


def _modfrete_grupo(codigo) -> str:
    """Converte o código modFrete da NF-e no grupo CIF/FOB/TERCEIROS/SEM_FRETE.
    Retorna "" quando o código é desconhecido/ausente."""
    c = str(codigo or "").strip()
    if c in ("0", "3"):
        return "CIF"
    if c in ("1", "4"):
        return "FOB"
    if c == "2":
        return "TERCEIROS"
    if c == "9":
        return "SEM_FRETE"
    return ""


def _modfrete_da_nf(nf) -> str:
    """Retorna o código modFrete de uma NF do romaneio. Usa o valor já
    armazenado (preenchido na inclusão) e, se ausente, tenta uma leitura ao
    vivo na bridge do ERP — cacheando o resultado na própria linha."""
    codigo = str(getattr(nf, "modfrete_nf", "") or "").strip()
    if codigo:
        return codigo
    numero_nf = str(getattr(nf, "numero_nf", "") or "").strip()
    if not numero_nf:
        return ""
    dados = _dados_nf_do_bridge(numero_nf) or {}
    codigo = str(dados.get("modfrete") or "").strip()
    if codigo:
        try:
            nf.modfrete_nf = codigo
        except Exception:
            pass
    return codigo


def _analisar_divergencia_frete(romaneio) -> dict:
    """Compara a modalidade de frete de cada NF (modFrete da NF-e) com o
    tipo_frete do romaneio. Retorna:
      - divergentes: NFs cuja modalidade não bate com o romaneio (inclui
        Terceiros/Sem frete, que não correspondem a CIF nem FOB);
      - nao_validadas: NFs cuja modalidade não pôde ser lida no ERP."""
    frete_rom = str(getattr(romaneio, "tipo_frete", "") or "").strip().upper()
    divergentes = []
    nao_validadas = []
    for nf in romaneio.nfs or []:
        codigo = _modfrete_da_nf(nf)
        grupo = _modfrete_grupo(codigo)
        if not grupo:
            nao_validadas.append(nf.numero_nf)
            continue
        if grupo != frete_rom:
            divergentes.append({
                "numero_nf": nf.numero_nf,
                "frete_romaneio": frete_rom,
                "frete_nf": grupo,
                "frete_nf_label": _MODFRETE_LABEL.get(codigo, grupo),
            })
    return {"divergentes": divergentes, "nao_validadas": nao_validadas}


def _notificar_cce_modalidade_faturamento(romaneio, divergentes) -> None:
    """Avisa o Faturamento (canal do Teams) que é necessária uma carta de
    correção da modalidade de frete. Best-effort: nunca interrompe o fluxo."""
    try:
        from ..services import teams_service

        partes = []
        nfs_txt = ", ".join(str(d.get("numero_nf")) for d in (divergentes or []))
        partes.append(
            f"Corrigir a **modalidade de frete** da(s) NF **{nfs_txt or '—'}** por "
            "**carta de correção (CC-e)**."
        )
        partes.append(
            f"**Solicitado por:** {session.get('username', 'sistema')} · "
            f"{datetime.now().strftime('%d/%m/%Y %H:%M')}"
        )
        if getattr(romaneio, "orcamento", None):
            partes.append(f"**Orçamento:** {romaneio.orcamento}")
        if getattr(romaneio, "transportadora_nome", None):
            partes.append(f"**Transportadora:** {romaneio.transportadora_nome}")
        partes.append("**Correções (texto da CC-e):**")
        for d in (divergentes or []):
            correto = _FRETE_GRUPO_LABEL.get(
                d.get("frete_romaneio"), d.get("frete_romaneio") or "?"
            )
            partes.append(
                f"• NF {d['numero_nf']}: "
                f"**CONSIDERAR: MODALIDADE DO TRANSPORTE {correto}**"
            )
        subinfo = "\n\n".join(partes)

        teams_service.enviar_card(
            "📝 Solicitação de carta de correção (CC-e)",
            f"Romaneio {romaneio.numero_romaneio}",
            subinfo,
            mencionar_canal=True,
            env_var="TEAMS_WEBHOOK_EXPEDICAO_URL",
            config_key="webhook_expedicao",
        )
    except Exception:
        current_app.logger.exception(
            "Falha ao notificar o Faturamento sobre CC-e de modalidade do romaneio %s.",
            getattr(romaneio, "numero_romaneio", None),
        )


def _finalizar_registro_expedicao_para_nf(nf, romaneio, usuario):
    """Ao expedir o romaneio, garante um Registro de Expedicao em "Expedido"
    para a NF. Se ja existir um rascunho (fotos do material/cliente tiradas na
    etapa Faturado), promove-o; senao cria um novo. So vira "Finalizado"
    quando o comprovante de entrega (canhoto) for anexado - ver
    salvar_comprovante_entrega_romaneio (FOB, um por romaneio) e o endpoint de
    canhoto por registro em api_routes.py (CIF, um por NF).
    Retorna o registro (ou None se a NF for vazia)."""
    numero_nf = str(getattr(nf, "numero_nf", "") or "").strip()
    if not numero_nf:
        return None
    agora = datetime.now()

    ordem = ExpedicaoOrdemFat.query.filter_by(numero_nf=numero_nf).first()
    if ordem is None:
        ordem = ExpedicaoOrdemST.query.filter_by(numero_nf=numero_nf).first()

    def _nfs_do_registro(reg) -> list:
        bruto = str(getattr(reg, "numero_nf", "") or "").replace(";", ",").replace("/", ",")
        return [n.strip() for n in bruto.split(",") if n.strip()]

    registro = None
    # 1) Rascunho ja dedicado a ESTA NF (fotos tiradas na etapa Faturado).
    registro = (
        ExpedicaoConferenciaSimples.query
        .filter_by(numero_nf=numero_nf, origem="Romaneio")
        .filter(ExpedicaoConferenciaSimples.status.in_(
            ["Pendente de expedição", "Pendente de expedicao"]))
        .order_by(ExpedicaoConferenciaSimples.id.desc())
        .first()
    )
    # 2) Registro vinculado pela ordem — SO reutiliza se cobrir exatamente esta
    #    unica NF. Registros compartilhados por varias NFs do mesmo orcamento
    #    (conf. cega faturada em varias notas) NAO sao reaproveitados: cada NF
    #    precisa do seu proprio Registro de Expedicao.
    if registro is None and ordem is not None and ordem.expedicao_registro_id:
        candidato = ExpedicaoConferenciaSimples.query.get(ordem.expedicao_registro_id)
        if candidato is not None and _nfs_do_registro(candidato) == [numero_nf]:
            registro = candidato

    numero_os = str(getattr(nf, "numeros_os", "") or "").strip() or None
    ordem_compra = str(getattr(nf, "ordem_compra", "") or "").strip()
    if ordem_compra:
        # NF de Servico de Terceiro (ST): referencia e Ordem de Compra.
        orcamento = ""
        tipo_referencia = "OrdemCompra"
    else:
        orcamento = str(getattr(nf, "orcamento", "") or romaneio.orcamento or "").strip()
        tipo_referencia = "Orcamento"
    cliente = str(getattr(nf, "cliente", "") or romaneio.cliente or "").strip()
    transportadora = str(getattr(romaneio, "transportadora", "") or "").strip() or None
    placa = str(getattr(romaneio, "placa", "") or "").strip() or None
    motorista = str(getattr(romaneio, "motorista", "") or "").strip() or None

    if registro is None:
        registro = ExpedicaoConferenciaSimples(
            orcamento=orcamento,
            ordem_compra=ordem_compra or None,
            tipo_referencia=tipo_referencia,
            numero_os=numero_os,
            conferente=usuario,
            numero_nf=numero_nf,
            nome_cliente=cliente,
            cliente_origem="Consyste",
            nf_origem="Consyste",
            origem="Romaneio",
            transportadora=transportadora,
            placa=placa,
            motorista=motorista,
            status="Expedido",
        )
        db.session.add(registro)
    else:
        registro.origem = "Romaneio"
        if not registro.numero_os and numero_os:
            registro.numero_os = numero_os
        if ordem_compra and not registro.ordem_compra:
            registro.ordem_compra = ordem_compra
            registro.tipo_referencia = "OrdemCompra"
        if not registro.nome_cliente and cliente:
            registro.nome_cliente = cliente
        if transportadora:
            registro.transportadora = transportadora
        if placa:
            registro.placa = placa
        if motorista:
            registro.motorista = motorista
        registro.status = "Expedido"

    # O registro fica em "Expedido" ate o comprovante de entrega (canhoto) ser
    # anexado - FOB anexa um unico comprovante pro romaneio inteiro (ver
    # salvar_comprovante_entrega_romaneio), CIF anexa um por NF reaproveitando
    # o endpoint de canhoto ja existente.
    if not registro.expedido_at:
        registro.expedido_at = agora
        registro.expedido_by = usuario
    registro.updated_at = agora
    db.session.flush()

    if ordem is not None:
        ordem.expedicao_registro_id = registro.id
    return registro


def _registro_conferencia_da_nf(numero_nf: str) -> ExpedicaoConferenciaSimples | None:
    """Localiza o Registro de Expedicao ja vinculado a uma NF (apos o romaneio
    ter sido expedido) - via ExpedicaoOrdemFat/ST.expedicao_registro_id, com
    fallback pela propria NF+origem Romaneio."""
    numero_nf = str(numero_nf or "").strip()
    if not numero_nf:
        return None
    ordem = ExpedicaoOrdemFat.query.filter_by(numero_nf=numero_nf).first()
    if ordem is None:
        ordem = ExpedicaoOrdemST.query.filter_by(numero_nf=numero_nf).first()
    if ordem is not None and ordem.expedicao_registro_id:
        registro = ExpedicaoConferenciaSimples.query.get(ordem.expedicao_registro_id)
        if registro is not None:
            return registro
    return (
        ExpedicaoConferenciaSimples.query
        .filter_by(numero_nf=numero_nf, origem="Romaneio")
        .order_by(ExpedicaoConferenciaSimples.id.desc())
        .first()
    )


def _info_comprovantes_romaneio(romaneio) -> tuple[dict, bool]:
    """Para romaneios Expedidos, retorna {numero_nf: {registro_id, canhoto_pendente}}
    e se ha qualquer NF ainda sem comprovante anexado. Para os demais status
    nao faz nenhuma consulta extra."""
    info: dict = {}
    if romaneio.status != "Expedido":
        return info, False
    tem_pendencia = False
    for nf in romaneio.nfs or []:
        registro = _registro_conferencia_da_nf(nf.numero_nf)
        if registro is None:
            continue
        # So e pendente enquanto o registro segue Expedido sem canhoto. Registros
        # ja Finalizados (inclusive os dispensados por serem antigos) nao pendem.
        canhoto_pendente = registro.status == "Expedido" and not bool(registro.canhoto_file_name)
        info[nf.numero_nf] = {"registro_id": registro.id, "canhoto_pendente": canhoto_pendente}
        if canhoto_pendente:
            tem_pendencia = True
    return info, tem_pendencia


@expedicao_romaneio_bp.route("/api/expedicao/romaneio-fat/<int:romaneio_id>/comprovante-entrega", methods=["POST"])
@roles_required(*ROLES)
def salvar_comprovante_entrega_romaneio(romaneio_id):
    """Comprovante de entrega unico para romaneios FOB: uma unica foto
    finaliza de uma vez o Registro de Expedicao de todas as NFs do romaneio
    (mesmo arquivo fisico referenciado em cada registro, reaproveitando os
    campos de canhoto ja existentes)."""
    romaneio = ExpedicaoRomaneio.query.get(romaneio_id)
    if not romaneio:
        return jsonify({"error": "Romaneio não encontrado."}), 404

    if romaneio.tipo_frete != "FOB":
        return jsonify({"error": "Este romaneio e CIF - anexe o comprovante por NF."}), 400
    if romaneio.status != "Expedido":
        return jsonify({"error": "O romaneio precisa estar Expedido para anexar o comprovante."}), 400

    comprovante = request.files.get("comprovante")
    if not comprovante or not comprovante.filename:
        return jsonify({"error": "Arquivo do comprovante é obrigatório."}), 400

    fotos_dir = current_app.config.get("EXPEDICAO_CONFERENCIA_FOTOS_DIR", "")
    if not fotos_dir:
        fotos_dir = os.path.join(current_app.instance_path, "expedicao_conferencia_simples")
    os.makedirs(fotos_dir, exist_ok=True)

    ext = os.path.splitext(secure_filename(comprovante.filename))[1] or ".jpg"
    nome_arquivo = f"canhoto_romaneio{romaneio_id}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}{ext}"
    if using_drive():
        try:
            stored = upload_to_drive(comprovante, nome_arquivo)
            caminho = stored.file_path
        except Exception as exc:
            # Nunca bloqueia o anexo do comprovante por falha do Drive: cai para
            # o disco local (servido depois por _resolver_foto_expedicao).
            current_app.logger.warning(
                "Falha ao enviar comprovante de entrega para o Drive (%s); salvando localmente.",
                exc,
            )
            try:
                comprovante.stream.seek(0)
            except Exception:
                pass
            caminho = os.path.join(fotos_dir, nome_arquivo)
            comprovante.save(caminho)
    else:
        caminho = os.path.join(fotos_dir, nome_arquivo)
        comprovante.save(caminho)

    agora = datetime.now()
    usuario = session["username"]
    total_finalizados = 0
    for nf in romaneio.nfs or []:
        registro = _registro_conferencia_da_nf(nf.numero_nf)
        if not registro or registro.status != "Expedido":
            continue
        registro.canhoto_file_name = nome_arquivo
        registro.canhoto_file_path = caminho
        registro.canhoto_uploaded_at = agora
        registro.canhoto_uploaded_by = usuario
        registro.status = "Finalizado"
        registro.finalizado_at = agora
        registro.finalizado_by = usuario
        registro.updated_at = agora
        total_finalizados += 1
    db.session.commit()

    return jsonify({
        "message": "Comprovante de entrega salvo com sucesso.",
        "nfs_finalizadas": total_finalizados,
    })


@expedicao_romaneio_bp.route("/expedicao/romaneio")
@permission_required(PERMISSION)
def lista_romaneios():
    """Página principal de gerenciamento de romaneios."""
    return render_template(
        "expedicao_romaneio.html",
        user=session["username"],
        user_role=session.get("role", ""),
        is_admin=session.get("role") == "Admin",
    )


def _exclusao_pendente_dict(romaneio_id: int) -> dict | None:
    exclusao = ExpedicaoRomaneioExclusao.query.filter_by(
        romaneio_id=romaneio_id, status="Pendente"
    ).order_by(ExpedicaoRomaneioExclusao.created_at.desc()).first()
    if not exclusao:
        return None
    return {
        "id": exclusao.id,
        "solicitante": exclusao.solicitante,
        "motivo": exclusao.motivo,
        "created_at": exclusao.created_at.isoformat() if exclusao.created_at else None,
    }


@expedicao_romaneio_bp.route("/api/expedicao/romaneio-fat", methods=["GET"])
@permission_required(PERMISSION)
def listar_romaneios():
    """Lista todos os romaneios com filtros opcionais."""
    # Puxa automaticamente os canhotos ja tirados pelos motoristas no app para
    # os romaneios CIF pendentes (best-effort, com throttle interno).
    try:
        from ..services.comprovante_entrega_motorista_service import (
            sincronizar_automatico,
        )

        sincronizar_automatico()
    except Exception:
        current_app.logger.exception(
            "Falha ao sincronizar canhotos do motorista na listagem de romaneios."
        )

    status = (request.args.get("status") or "").strip()
    busca = (request.args.get("q") or "").strip().lower()

    query = ExpedicaoRomaneio.query.order_by(ExpedicaoRomaneio.criado_em.desc())
    
    if status:
        query = query.filter_by(status=status)
    
    romaneios = query.all()
    
    # Filtro por busca (número, orçamento, cliente ou qualquer NF incluída)
    if busca:
        romaneios = [
            r for r in romaneios
            if busca in str(r.numero_romaneio or "").lower()
            or busca in str(r.orcamento or "").lower()
            or busca in str(r.cliente or "").lower()
            or any(busca in str(nf.numero_nf or "").lower() for nf in (r.nfs or []))
            or any(busca in str(nf.cliente or "").lower() for nf in (r.nfs or []))
        ]
    
    def _clientes_do_romaneio(r):
        # Um romaneio pode reunir NFs de clientes/fornecedores diferentes -
        # lista todos os nomes distintos (na ordem em que aparecem), com
        # fallback para o cliente do cabecalho se nenhuma NF tiver o campo.
        nomes = []
        for nf in (r.nfs or []):
            nome = str(nf.cliente or "").strip()
            if nome and nome not in nomes:
                nomes.append(nome)
        if not nomes and r.cliente:
            nomes.append(r.cliente)
        return nomes

    data = []
    for r in romaneios:
        peso_total, volumes_total = _calcular_totais_nfs(r.nfs or [])
        comprovantes_info, tem_pendencia_comprovante = _info_comprovantes_romaneio(r)
        data.append({
            "id": r.id,
            "numero_romaneio": r.numero_romaneio,
            "data_romaneio": r.data_romaneio.isoformat() if r.data_romaneio else None,
            "orcamento": r.orcamento,
            "cliente": r.cliente,
            "clientes": _clientes_do_romaneio(r),
            "tipo_frete": r.tipo_frete,
            "peso_bruto_total": peso_total,
            "qtde_volumes_total": volumes_total,
            "qtde_nfs": len(r.nfs) if r.nfs else 0,
            "tem_pendencia_comprovante": tem_pendencia_comprovante,
            "nfs": [
                {
                    "id": nf.id,
                    "numero_nf": nf.numero_nf,
                    "orcamento": nf.orcamento,
                    "ordem_compra": nf.ordem_compra,
                    "cliente": nf.cliente,
                    "peso_bruto": nf.peso_bruto,
                    "qtde_volumes": nf.qtde_volumes,
                    "registro_id": comprovantes_info.get(nf.numero_nf, {}).get("registro_id"),
                    "canhoto_pendente": comprovantes_info.get(nf.numero_nf, {}).get("canhoto_pendente"),
                }
                for nf in (r.nfs or [])
            ],
            "status": r.status,
            "criado_por": r.criado_por,
            "criado_em": r.criado_em.isoformat() if r.criado_em else None,
            "cce_modalidade_pendente": bool(r.cce_modalidade_pendente),
            "cce_modalidade_detalhe": r.cce_modalidade_detalhe,
            "exclusao_pendente": _exclusao_pendente_dict(r.id) if r.status == "Rascunho" else None,
        })
    
    return jsonify({"romaneios": data})


@expedicao_romaneio_bp.route("/api/expedicao/romaneio-fat", methods=["POST"])
@roles_required(*ROLES)
def criar_romaneio():
    """Cria um novo romaneio em branco."""
    payload = request.get_json(silent=True) or {}
    
    # Gera número único para o romaneio (p.ex: ROM-2026-0001). Usa o MAIOR
    # sufixo numérico já existente no ano (não a contagem de linhas) — contar
    # linhas gera número duplicado sempre que um romaneio anterior é excluído
    # (o total cai, mas o número "vago" já pode pertencer a um romaneio que
    # ainda existe), disparando IntegrityError na coluna única.
    hoje = datetime.now()
    prefixo = f"ROM-{hoje.year}-"
    existentes = (
        ExpedicaoRomaneio.query
        .filter(ExpedicaoRomaneio.numero_romaneio.like(f"{prefixo}%"))
        .with_entities(ExpedicaoRomaneio.numero_romaneio)
        .all()
    )
    maior_sequencia = 0
    for (numero,) in existentes:
        sufixo = str(numero or "")[len(prefixo):]
        if sufixo.isdigit():
            maior_sequencia = max(maior_sequencia, int(sufixo))
    numero_romaneio = f"{prefixo}{maior_sequencia + 1:04d}"
    
    romaneio = ExpedicaoRomaneio(
        numero_romaneio=numero_romaneio,
        data_romaneio=datetime.now().date(),
        criado_por=session["username"],
    )
    
    db.session.add(romaneio)
    db.session.commit()
    
    return jsonify({
        "id": romaneio.id,
        "numero_romaneio": romaneio.numero_romaneio,
        "status": "Rascunho",
    }), 201


@expedicao_romaneio_bp.route("/api/expedicao/romaneio-fat/<int:romaneio_id>", methods=["GET"])
@permission_required(PERMISSION)
def obter_romaneio(romaneio_id):
    """Obtém detalhes de um romaneio específico."""
    romaneio = ExpedicaoRomaneio.query.get(romaneio_id)
    if not romaneio:
        return jsonify({"error": "Romaneio não encontrado."}), 404
    
    peso_total, volumes_total = _calcular_totais_nfs(romaneio.nfs or [])

    return jsonify({
        "id": romaneio.id,
        "numero_romaneio": romaneio.numero_romaneio,
        "data_romaneio": romaneio.data_romaneio.isoformat() if romaneio.data_romaneio else None,
        "orcamento": romaneio.orcamento,
        "cliente": romaneio.cliente,
        "tipo_frete": romaneio.tipo_frete,
        "transportadora_nome": romaneio.transportadora,
        "placa": romaneio.placa,
        "motorista_nome": romaneio.motorista,
        "motorista_documento": romaneio.motorista_documento,
        "transportadora_documento": romaneio.transportadora_documento,
        "transportadora_dados": _transportadora_snapshot(romaneio),
        "peso_bruto_total": peso_total,
        "qtde_volumes_total": volumes_total,
        "observacao_1": romaneio.observacao_1,
        "observacao_2": romaneio.observacao_2,
        "observacao_3": romaneio.observacao_3,
        "status": romaneio.status,
        "criado_por": romaneio.criado_por,
        "criado_em": romaneio.criado_em.isoformat() if romaneio.criado_em else None,
        "cce_modalidade_pendente": bool(romaneio.cce_modalidade_pendente),
        "cce_modalidade_detalhe": romaneio.cce_modalidade_detalhe,
        "exclusao_pendente": _exclusao_pendente_dict(romaneio.id) if romaneio.status == "Rascunho" else None,
        "assinatura_conferente_url": (
            f"/api/expedicao/romaneio-fat/{romaneio.id}/assinatura/conferente"
            if romaneio.assinatura_conferente_file_name else None
        ),
        "assinatura_transportador_url": (
            f"/api/expedicao/romaneio-fat/{romaneio.id}/assinatura/transportador"
            if romaneio.assinatura_transportador_file_name else None
        ),
        "nfs": [
            {
                "id": nf.id,
                "numero_nf": nf.numero_nf,
                "orcamento": nf.orcamento,
                "ordem_compra": nf.ordem_compra,
                "cliente": nf.cliente,
                "peso_bruto": nf.peso_bruto,
                "qtde_volumes": nf.qtde_volumes,
                "especie_volumes": nf.especie_volumes,
                "numeros_os": nf.numeros_os,
                "adicionado_em": nf.adicionado_em.isoformat() if nf.adicionado_em else None,
                "adicionado_por": nf.adicionado_por,
            }
            for nf in romaneio.nfs or []
        ],
    })


@expedicao_romaneio_bp.route("/api/expedicao/romaneio-fat/<int:romaneio_id>", methods=["PUT"])
@roles_required(*ROLES)
def atualizar_romaneio(romaneio_id):
    """Atualiza informações do romaneio."""
    romaneio = ExpedicaoRomaneio.query.get(romaneio_id)
    if not romaneio:
        return jsonify({"error": "Romaneio não encontrado."}), 404
    
    if romaneio.status not in ("Rascunho", "Pronto"):
        return jsonify({"error": "Apenas romaneios em Rascunho ou Pronto podem ser editados."}), 400

    # Com o romaneio Pronto, so os dados do transportador (transportadora,
    # placa, motorista/conferente e CPF/CNPJ) podem ser corrigidos - os demais
    # campos exigem voltar para Rascunho (estornar finalizacao).
    somente_dados_transportador = romaneio.status == "Pronto"

    payload = request.get_json(silent=True) or {}

    disparar_aviso_fob = False
    if not somente_dados_transportador:
        if "orcamento" in payload:
            romaneio.orcamento = str(payload["orcamento"]).strip()
        if "cliente" in payload:
            romaneio.cliente = str(payload["cliente"]).strip()
        if "tipo_frete" in payload:
            frete = str(payload["tipo_frete"]).strip().upper()
            if frete not in ("FOB", "CIF"):
                return jsonify({"error": "Tipo de frete deve ser FOB ou CIF."}), 400
            disparar_aviso_fob = (frete == "FOB")
            romaneio.tipo_frete = frete
    if "transportadora" in payload:
        romaneio.transportadora = str(payload["transportadora"]).strip()
    if "placa" in payload:
        romaneio.placa = str(payload["placa"]).strip()
    if "motorista" in payload:
        romaneio.motorista = str(payload["motorista"]).strip()
    if "motorista_documento" in payload:
        romaneio.motorista_documento = str(payload["motorista_documento"]).strip()
    # Transportadora do frete FOB: CNPJ + snapshot do cartao CNPJ (BrasilAPI).
    if "transportadora_documento" in payload:
        doc = cad_svc.normalizar_documento(str(payload.get("transportadora_documento") or ""))
        romaneio.transportadora_documento = cad_svc.formatar_cnpj(doc) if doc else ""
    if "transportadora_dados" in payload:
        dados = payload.get("transportadora_dados")
        if isinstance(dados, dict) and dados:
            romaneio.transportadora_dados_json = json.dumps(dados, ensure_ascii=False)
            # Mantem o nome exibido em "Dados Transportador" sincronizado.
            razao = str(dados.get("razao_social") or "").strip()
            if razao:
                romaneio.transportadora = razao
        elif dados in (None, "", {}):
            romaneio.transportadora_dados_json = None
    if not somente_dados_transportador:
        if "observacao_1" in payload:
            romaneio.observacao_1 = str(payload["observacao_1"]).strip()
        if "observacao_2" in payload:
            romaneio.observacao_2 = str(payload["observacao_2"]).strip()
        if "observacao_3" in payload:
            romaneio.observacao_3 = str(payload["observacao_3"]).strip()
    
    romaneio.atualizado_por = session["username"]
    romaneio.atualizado_em = datetime.now()
    db.session.commit()

    # Ao montar/atualizar romaneio com frete FOB, dispara aviso de coleta para
    # cada NF usando o mesmo resolvedor de e-mails do envio da NF-e.
    if disparar_aviso_fob and (romaneio.nfs or []):
        usuario = session.get("username", "sistema")
        for nf in romaneio.nfs:
            try:
                enviar_aviso_coleta_fob(
                    numero_nf=nf.numero_nf,
                    nome_cliente=nf.cliente or romaneio.cliente or "",
                    qtde_volumes=int(nf.qtde_volumes or 0),
                    peso=float(nf.peso_bruto or 0),
                    disparado_por=usuario,
                    origem="RomaneioFOB",
                    envio_assincrono=True,
                )
            except Exception:
                current_app.logger.exception(
                    "Falha ao disparar aviso FOB para NF %s (romaneio %s).",
                    nf.numero_nf,
                    romaneio.numero_romaneio,
                )
    
    return jsonify({"message": "Romaneio atualizado com sucesso."})


@expedicao_romaneio_bp.route("/api/expedicao/romaneio-fat/<int:romaneio_id>/nf", methods=["POST"])
@roles_required(*ROLES)
def adicionar_nf_ao_romaneio(romaneio_id):
    """Adiciona uma NF ao romaneio."""
    romaneio = ExpedicaoRomaneio.query.get(romaneio_id)
    if not romaneio:
        return jsonify({"error": "Romaneio não encontrado."}), 404

    if romaneio.status != "Rascunho":
        return jsonify({"error": "Apenas romaneios em Rascunho podem receber NFes."}), 400

    payload = request.get_json(silent=True) or {}
    numero_nf = (payload.get("numero_nf") or "").strip()

    if not numero_nf:
        return jsonify({"error": "Número da NF é obrigatório."}), 400

    nf, erro = incluir_nf_no_romaneio(romaneio, numero_nf, session["username"], payload)
    if erro:
        return jsonify({"error": erro}), 400

    return jsonify({
        "id": nf.id,
        "numero_nf": nf.numero_nf,
        "message": "NF adicionada com sucesso.",
    }), 201


def incluir_nf_no_romaneio(romaneio, numero_nf, autor, payload=None):
    """Núcleo reutilizável de inclusão de NF em um romaneio (usado pela rota
    HTTP e pela Bia). Retorna (nf, None) em sucesso ou (None, mensagem_erro).
    Só opera em romaneios em Rascunho."""
    payload = payload or {}
    numero_nf = str(numero_nf or "").strip()
    if not numero_nf:
        return None, "Número da NF é obrigatório."
    if romaneio.status != "Rascunho":
        return None, "Apenas romaneios em Rascunho podem receber NFes."
    romaneio_id = romaneio.id

    # Verifica se NF já foi adicionada
    existe = ExpedicaoRomaneioNF.query.filter_by(
        romaneio_id=romaneio_id,
        numero_nf=numero_nf,
    ).first()
    if existe:
        return None, f"NF {numero_nf} já foi adicionada a este romaneio."

    # Busca os dados reais da NF na ordem de faturamento (preenchidos pelo
    # conferente na Conferência de Expedição) — peso/volume devem ser
    # identicos ao que consta na NF, nao digitados de novo aqui. So cai no
    # que veio no payload se a NF nao estiver rastreada em ExpedicaoOrdemFat
    # (ex.: NF fora do fluxo normal de faturamento).
    ordem_fat = ExpedicaoOrdemFat.query.filter_by(numero_nf=numero_nf).first()
    ordem_st = None if ordem_fat else ExpedicaoOrdemST.query.filter_by(numero_nf=numero_nf).first()

    ordem_com_conferencia = ordem_fat or ordem_st
    if ordem_com_conferencia:
        registro_conferencia = None
        if ordem_com_conferencia.expedicao_registro_id:
            registro_conferencia = ExpedicaoConferenciaSimples.query.get(
                ordem_com_conferencia.expedicao_registro_id
            )
        # Fallback: a foto do cliente pode ter sido anexada por outro fluxo
        # (ex.: Conferencia de Expedicao manual, origem "Manual"), gerando um
        # registro com foto_cliente que nunca foi vinculado em
        # expedicao_registro_id. Nesse caso a ordem aponta para None (ou para
        # um registro sem foto), mas a foto EXISTE para a mesma NF. Procura um
        # registro da NF que ja tenha a foto e vincula a ordem a ele,
        # auto-corrigindo o elo para os proximos passos do romaneio. Prefere um
        # registro ainda "Pendente de expedicao" (rascunho) para nao repropor
        # um registro ja Finalizado; caindo nele so se nao houver pendente.
        if not registro_conferencia or not registro_conferencia.foto_cliente_file_name:
            base_foto = (
                ExpedicaoConferenciaSimples.query
                .filter_by(numero_nf=numero_nf)
                .filter(ExpedicaoConferenciaSimples.foto_cliente_file_name.isnot(None))
            )
            registro_com_foto = (
                base_foto
                .filter(ExpedicaoConferenciaSimples.status.in_(
                    ["Pendente de expedição", "Pendente de expedicao"]))
                .order_by(ExpedicaoConferenciaSimples.id.desc())
                .first()
                or base_foto.order_by(ExpedicaoConferenciaSimples.id.desc()).first()
            )
            if registro_com_foto is not None:
                registro_conferencia = registro_com_foto
                ordem_com_conferencia.expedicao_registro_id = registro_com_foto.id
        if not registro_conferencia or not registro_conferencia.foto_cliente_file_name:
            return None, (
                "É necessário anexar a foto do cliente antes de incluir esta "
                "NF no romaneio."
            )

    if ordem_fat:
        orcamento = ordem_fat.orcamento or payload.get("orcamento") or romaneio.orcamento
        ordem_compra = ""
        cliente = ordem_fat.cliente or payload.get("cliente") or romaneio.cliente
        peso_bruto = _parse_float(ordem_fat.peso_bruto, _parse_float(payload.get("peso_bruto")))
        qtde_volumes = _parse_int(ordem_fat.qtde_volumes, _parse_int(payload.get("qtde_volumes")))
        especie_volumes = ordem_fat.especie_volumes or payload.get("especie_volumes") or ""
        # Numeros de OS vem dos itens da propria ordem de faturamento (mesma
        # fonte usada pelo pre-fill do Registro de Expedicao a partir da
        # Conferencia de Expedicao) — nao digitados manualmente aqui.
        oss_itens = (
            ExpedicaoOrdemFatItem.query
            .filter_by(ordem_id=ordem_fat.id)
            .with_entities(ExpedicaoOrdemFatItem.n_os)
            .distinct()
            .all()
        )
        oss_unicas = [str(v[0]).strip() for v in oss_itens if v[0] and str(v[0]).strip()]
        numeros_os = ", ".join(oss_unicas) if oss_unicas else (payload.get("numeros_os") or "")
    elif ordem_st:
        # Ordem de Servico de Terceiro (ST): o documento de referencia e a
        # Ordem de Compra (nao ha orcamento). Grava a OC em ordem_compra para
        # o romaneio/registro exibirem "OC" em vez de "Orcamento".
        orcamento = ""
        ordem_compra = ordem_st.cod_ordem_compra or payload.get("ordem_compra") or ""
        cliente = ordem_st.fornecedor or payload.get("cliente") or romaneio.cliente
        peso_bruto = _parse_float(ordem_st.peso_bruto, _parse_float(payload.get("peso_bruto")))
        qtde_volumes = _parse_int(ordem_st.qtde_volumes, _parse_int(payload.get("qtde_volumes")))
        especie_volumes = ordem_st.especie_volumes or payload.get("especie_volumes") or ""
        numeros_os = ordem_st.n_os or payload.get("numeros_os") or ""
    else:
        # NF que nao passou pela Conferencia de Expedicao (sem ExpedicaoOrdemFat/
        # ST correspondente) - busca os dados reais direto na bridge Postgres do
        # ERP em vez de confiar apenas no que foi digitado manualmente.
        dados_bridge = _dados_nf_do_bridge(numero_nf)
        dados_bridge = dados_bridge or {}
        orcamento = payload.get("orcamento") or romaneio.orcamento
        ordem_compra = payload.get("ordem_compra") or ""
        cliente = dados_bridge.get("cliente") or payload.get("cliente") or romaneio.cliente
        peso_bruto = dados_bridge.get("peso_bruto")
        if peso_bruto is None:
            peso_bruto = _parse_float(payload.get("peso_bruto"))
        qtde_volumes = dados_bridge.get("qtde_volumes")
        if qtde_volumes is None:
            qtde_volumes = _parse_int(payload.get("qtde_volumes"))
        especie_volumes = dados_bridge.get("especie_volumes") or payload.get("especie_volumes") or ""
        numeros_os = payload.get("numeros_os") or ""

    # Modalidade de frete declarada na propria NF-e (modFrete do XML) — usada
    # para detectar divergencia com o tipo_frete do romaneio na finalizacao.
    # Best-effort: se a bridge do ERP nao responder, fica vazia e sera lida ao
    # vivo na finalizacao.
    if ordem_fat or ordem_st:
        _bridge_frete = _dados_nf_do_bridge(numero_nf) or {}
        modfrete_codigo = str(_bridge_frete.get("modfrete") or "").strip() or None
    else:
        modfrete_codigo = str((dados_bridge or {}).get("modfrete") or "").strip() or None

    nf = ExpedicaoRomaneioNF(
        romaneio_id=romaneio_id,
        numero_nf=numero_nf,
        orcamento=orcamento,
        ordem_compra=ordem_compra,
        cliente=cliente,
        peso_bruto=peso_bruto,
        qtde_volumes=qtde_volumes,
        especie_volumes=especie_volumes,
        numeros_os=numeros_os,
        modfrete_nf=modfrete_codigo,
        adicionado_por=autor,
    )

    db.session.add(nf)

    # Atualiza totais sempre pela lista real de NFs, sem duplicar a NF recém-adicionada.
    _recalcular_totais_romaneio(romaneio)

    # Se for a primeira NF, usa seus dados como padrão
    if len(romaneio.nfs or []) == 1:
        if not romaneio.orcamento:
            romaneio.orcamento = orcamento
        if not romaneio.cliente:
            romaneio.cliente = cliente

    romaneio.atualizado_em = datetime.now()
    db.session.commit()

    # Fluxo progressivo: a ordem (FAT ou ST — o numero_nf so existe em uma
    # delas) cuja NF entrou no romaneio sai da etapa "Faturado" e passa para
    # "Em Romaneio".
    fat_svc.marcar_em_romaneio_por_nf(numero_nf)
    st_svc.marcar_em_romaneio_por_nf(numero_nf)

    return nf, None


@expedicao_romaneio_bp.route("/api/expedicao/romaneio-fat/<int:romaneio_id>/nf/<int:nf_id>", methods=["DELETE"])
@roles_required(*ROLES)
def remover_nf_do_romaneio(romaneio_id, nf_id):
    """Remove uma NF do romaneio."""
    romaneio = ExpedicaoRomaneio.query.get(romaneio_id)
    if not romaneio:
        return jsonify({"error": "Romaneio não encontrado."}), 404

    if romaneio.status != "Rascunho":
        dica = (
            "Realize primeiro o estorno da Expedição e depois da Finalização."
            if romaneio.status == "Expedido"
            else "Realize primeiro o estorno da Finalização (Pronto → Rascunho)."
        )
        return jsonify({"error": f"Apenas romaneios em Rascunho podem ter NFes removidas. {dica}"}), 400

    nf = ExpedicaoRomaneioNF.query.get(nf_id)
    if not nf or nf.romaneio_id != romaneio_id:
        return jsonify({"error": "NF não encontrada neste romaneio."}), 404

    ok, erro = remover_nf_core(romaneio, nf)
    if erro:
        return jsonify({"error": erro}), 400
    return jsonify({"message": "NF removida com sucesso."})


def remover_nf_core(romaneio, nf):
    """Núcleo reutilizável de remoção de NF do romaneio (rota HTTP e Bia).
    Retorna (True, None) em sucesso ou (False, mensagem_erro). Só opera em
    romaneios em Rascunho."""
    if romaneio.status != "Rascunho":
        return False, "Apenas romaneios em Rascunho podem ter NFes removidas."
    if nf is None or nf.romaneio_id != romaneio.id:
        return False, "NF não encontrada neste romaneio."

    numero_nf_removido = nf.numero_nf

    db.session.delete(nf)
    db.session.flush()

    # Atualiza totais pela lista remanescente para manter consistência.
    _recalcular_totais_romaneio(romaneio)
    romaneio.atualizado_em = datetime.now()

    db.session.commit()

    # Estorno da etapa atual: a ordem (FAT ou ST) volta de "Em Romaneio" para
    # "Faturado".
    fat_svc.reverter_romaneio_por_nf(numero_nf_removido)
    st_svc.reverter_romaneio_por_nf(numero_nf_removido)

    return True, None


@expedicao_romaneio_bp.route("/api/expedicao/romaneio-fat/<int:romaneio_id>/finalizar", methods=["POST"])
@roles_required(*ROLES)
def finalizar_romaneio(romaneio_id):
    """Finaliza o romaneio (muda status de Rascunho para Pronto).

    Antes de finalizar, compara a modalidade de frete declarada em cada NF
    (modFrete da NF-e) com o tipo_frete do romaneio. Se houver divergência ou
    NFs cuja modalidade não pôde ser validada, NÃO finaliza e devolve
    ``requer_confirmacao`` para o operador decidir. Com ``aprovar=true`` no
    corpo, finaliza mesmo assim: divergências registram carta de correção
    pendente e avisam o Faturamento no Teams."""
    romaneio = ExpedicaoRomaneio.query.get(romaneio_id)
    if not romaneio:
        return jsonify({"error": "Romaneio não encontrado."}), 404
    
    if romaneio.status != "Rascunho":
        return jsonify({"error": "Apenas romaneios em Rascunho podem ser finalizados."}), 400
    
    if not romaneio.nfs or len(romaneio.nfs) == 0:
        return jsonify({"error": "Adicione ao menos uma NF antes de finalizar o romaneio."}), 400

    payload = request.get_json(silent=True) or {}
    aprovar = bool(payload.get("aprovar"))

    analise = _analisar_divergencia_frete(romaneio)
    divergentes = analise["divergentes"]
    nao_validadas = analise["nao_validadas"]

    # Cacheia eventuais modFretes lidos ao vivo durante a analise.
    db.session.flush()

    if not aprovar and (divergentes or nao_validadas):
        db.session.commit()
        return jsonify({
            "requer_confirmacao": True,
            "tipo_frete_romaneio": romaneio.tipo_frete,
            "divergentes": divergentes,
            "nao_validadas": nao_validadas,
        })

    if divergentes and aprovar:
        detalhe = "; ".join(
            f"NF {d['numero_nf']}: romaneio {d['frete_romaneio']} × nota {d['frete_nf_label']}"
            for d in divergentes
        )
        romaneio.cce_modalidade_pendente = True
        romaneio.cce_modalidade_aprovado_por = session["username"]
        romaneio.cce_modalidade_aprovado_em = datetime.now()
        romaneio.cce_modalidade_detalhe = detalhe[:1000]

    romaneio.status = "Pronto"
    romaneio.atualizado_por = session["username"]
    romaneio.atualizado_em = datetime.now()
    db.session.commit()

    if divergentes and aprovar:
        _notificar_cce_modalidade_faturamento(romaneio, divergentes)

    _gerar_solicitacao_entrega_cif(romaneio)

    if divergentes and aprovar:
        mensagem = (
            "Romaneio finalizado. Solicitada carta de correção da modalidade "
            "de transporte ao Faturamento."
        )
    else:
        mensagem = "Romaneio finalizado com sucesso."

    return jsonify({
        "message": mensagem,
        "cce_modalidade_pendente": bool(romaneio.cce_modalidade_pendente),
    })


@expedicao_romaneio_bp.route("/api/expedicao/romaneio-fat/<int:romaneio_id>/expedir", methods=["POST"])
@roles_required(*ROLES)
def expedir_romaneio(romaneio_id):
    """Marca o romaneio como expedido."""
    romaneio = ExpedicaoRomaneio.query.get(romaneio_id)
    if not romaneio:
        return jsonify({"error": "Romaneio não encontrado."}), 404
    
    if romaneio.status != "Pronto":
        return jsonify({"error": "Apenas romaneios em Pronto podem ser expedidos."}), 400
    
    romaneio.status = "Expedido"
    romaneio.expedido_por = session["username"]
    romaneio.expedido_em = datetime.now()
    romaneio.atualizado_em = datetime.now()
    db.session.commit()
    
    # Fluxo progressivo: cada ordem cuja NF esta neste romaneio avanca de
    # "Em Romaneio" para "Expedido". O Registro de Expedicao nasce ja
    # Finalizado (o romaneio faz as vezes de canhoto), reaproveitando as fotos
    # tiradas na etapa Faturado quando existirem.
    for nf in romaneio.nfs or []:
        registro = _finalizar_registro_expedicao_para_nf(nf, romaneio, session["username"])
        fat_svc.marcar_expedido_por_nf(
            nf.numero_nf,
            registro_id=(registro.id if registro else None),
            usuario=session["username"],
        )
        st_svc.marcar_expedido_por_nf(
            nf.numero_nf,
            registro_id=(registro.id if registro else None),
            usuario=session["username"],
        )
    db.session.commit()

    _gerar_solicitacao_entrega_cif(romaneio)

    return jsonify({"message": "Romaneio expedido com sucesso."})


@expedicao_romaneio_bp.route("/api/expedicao/romaneio-fat/<int:romaneio_id>/estornar-finalizacao", methods=["POST"])
@roles_required("Admin")
def estornar_finalizacao_romaneio(romaneio_id):
    """Estorna a finalização do romaneio (Pronto -> Rascunho). As ordens
    associadas permanecem em "Em Romaneio" (nao ha mudanca de etapa das
    ordens aqui, apenas do romaneio)."""
    romaneio = ExpedicaoRomaneio.query.get(romaneio_id)
    if not romaneio:
        return jsonify({"error": "Romaneio não encontrado."}), 404

    if romaneio.status != "Pronto":
        return jsonify({"error": "Apenas romaneios em Pronto podem ter a finalização estornada."}), 400

    estornar_finalizacao_core(romaneio, session["username"])
    return jsonify({"message": "Finalização estornada. Romaneio voltou para Rascunho."})


def estornar_finalizacao_core(romaneio, autor):
    """Núcleo do estorno de finalização (Pronto -> Rascunho). Reutilizado pela
    rota HTTP e pela Bia."""
    romaneio.status = "Rascunho"
    romaneio.atualizado_por = autor
    romaneio.atualizado_em = datetime.now()
    db.session.commit()

    _cancelar_solicitacao_entrega_cif(
        romaneio, motivo=f"Finalizacao do romaneio {romaneio.numero_romaneio} estornada (voltou para Rascunho)."
    )


@expedicao_romaneio_bp.route("/api/expedicao/romaneio-fat/<int:romaneio_id>/estornar-expedicao", methods=["POST"])
@roles_required("Admin")
def estornar_expedicao_romaneio(romaneio_id):
    """Estorna a expedição do romaneio (Expedido -> Pronto). Cada ordem
    associada volta de "Expedido" para "Em Romaneio" — unica forma de
    retroceder a partir da etapa final."""
    romaneio = ExpedicaoRomaneio.query.get(romaneio_id)
    if not romaneio:
        return jsonify({"error": "Romaneio não encontrado."}), 404

    if romaneio.status != "Expedido":
        return jsonify({"error": "Apenas romaneios Expedidos podem ter a expedição estornada."}), 400

    estornar_expedicao_core(romaneio, session["username"])
    return jsonify({"message": "Expedição estornada. Romaneio voltou para Pronto."})


def estornar_expedicao_core(romaneio, autor):
    """Núcleo do estorno de expedição (Expedido -> Pronto). Reutilizado pela
    rota HTTP e pela Bia."""
    romaneio.status = "Pronto"
    romaneio.expedido_por = None
    romaneio.expedido_em = None
    romaneio.atualizado_por = autor
    romaneio.atualizado_em = datetime.now()
    db.session.commit()

    for nf in romaneio.nfs or []:
        fat_svc.reverter_expedicao_por_nf(nf.numero_nf)
        st_svc.reverter_expedicao_por_nf(nf.numero_nf)
        # Reverte o Registro de Expedicao auto-finalizado de volta para
        # rascunho (mantem as fotos ja tiradas), permitindo re-expedir depois.
        numero_nf = str(nf.numero_nf or "").strip()
        if numero_nf:
            registros = (
                ExpedicaoConferenciaSimples.query
                .filter_by(numero_nf=numero_nf, origem="Romaneio")
                .filter(ExpedicaoConferenciaSimples.status.in_(["Expedido", "Finalizado"]))
                .all()
            )
            for reg in registros:
                reg.status = "Pendente de expedição"
                reg.expedido_at = None
                reg.expedido_by = None
                reg.finalizado_at = None
                reg.finalizado_by = None
                # Zera o comprovante de entrega - ao re-expedir, precisa ser
                # anexado de novo (FOB ou CIF).
                reg.canhoto_file_name = None
                reg.canhoto_file_path = None
                reg.canhoto_uploaded_at = None
                reg.canhoto_uploaded_by = None
                reg.updated_at = datetime.now()
    db.session.commit()


def estornar_para_rascunho(romaneio, autor):
    """Leva o romaneio até Rascunho a partir do estado atual (Expedido -> Pronto
    -> Rascunho, ou Pronto -> Rascunho). Usado pela Bia após aprovação. Retorna
    (True, None) se chegou em Rascunho, ou (False, mensagem) se não era possível."""
    if romaneio.status == "Rascunho":
        return True, None
    if romaneio.status == "Expedido":
        estornar_expedicao_core(romaneio, autor)
    if romaneio.status == "Pronto":
        estornar_finalizacao_core(romaneio, autor)
    if romaneio.status == "Rascunho":
        return True, None
    return False, f"Não é possível estornar um romaneio no estado '{romaneio.status}'."


def editar_romaneio_campos(romaneio, alteracoes, autor):
    """Aplica o subconjunto de campos editáveis pela Bia (transportadora, placa,
    motorista, motorista_documento, tipo_frete). Só opera em Rascunho. Retorna
    (True, None) em sucesso ou (False, mensagem_erro)."""
    if romaneio.status != "Rascunho":
        return False, "Só é possível editar romaneios em Rascunho."
    if not alteracoes:
        return False, "Nada para alterar."

    disparar_aviso_fob = False
    if "transportadora" in alteracoes:
        romaneio.transportadora = str(alteracoes["transportadora"] or "").strip()
    if "placa" in alteracoes:
        romaneio.placa = str(alteracoes["placa"] or "").strip()
    if "motorista" in alteracoes:
        romaneio.motorista = str(alteracoes["motorista"] or "").strip()
    if "motorista_documento" in alteracoes:
        romaneio.motorista_documento = str(alteracoes["motorista_documento"] or "").strip()
    if "tipo_frete" in alteracoes:
        frete = str(alteracoes["tipo_frete"] or "").strip().upper()
        if frete not in ("FOB", "CIF"):
            return False, "Tipo de frete deve ser FOB ou CIF."
        disparar_aviso_fob = (frete == "FOB")
        romaneio.tipo_frete = frete

    romaneio.atualizado_por = autor
    romaneio.atualizado_em = datetime.now()
    db.session.commit()

    if disparar_aviso_fob and (romaneio.nfs or []):
        for nf in romaneio.nfs:
            try:
                enviar_aviso_coleta_fob(
                    numero_nf=nf.numero_nf,
                    nome_cliente=nf.cliente or romaneio.cliente or "",
                    qtde_volumes=int(nf.qtde_volumes or 0),
                    peso=float(nf.peso_bruto or 0),
                    disparado_por=autor,
                    origem="RomaneioFOB",
                    envio_assincrono=True,
                )
            except Exception:
                current_app.logger.exception(
                    "Falha ao disparar aviso FOB para NF %s (romaneio %s).",
                    nf.numero_nf,
                    romaneio.numero_romaneio,
                )
    return True, None


def _excluir_romaneio(romaneio: ExpedicaoRomaneio) -> None:
    """Exclui o romaneio (so chamar com status ja validado como Rascunho) e
    devolve as ordens cujas NFs estavam nele para 'Faturado'. Reutilizada
    tanto pela exclusao direta do Admin quanto pela aprovacao de uma
    solicitacao de exclusao."""
    # Estorna a Solicitacao de Entrega CIF (se houver) antes de apagar o
    # romaneio, pois a busca depende do romaneio.id.
    _cancelar_solicitacao_entrega_cif(
        romaneio, motivo=f"Romaneio {romaneio.numero_romaneio} excluido."
    )
    numeros_nf = [nf.numero_nf for nf in (romaneio.nfs or [])]
    # Remove as solicitacoes de exclusao (inclusive a que acabou de ser
    # aprovada) antes do romaneio: sem isso, a linha "Aprovado" fica com
    # romaneio_id apontando para um romaneio inexistente, violando a FK em
    # bancos que a validam (Postgres/MySQL) e derrubando a aprovacao com
    # Erro interno.
    ExpedicaoRomaneioExclusao.query.filter_by(romaneio_id=romaneio.id).delete()
    db.session.delete(romaneio)
    db.session.commit()
    for numero_nf in numeros_nf:
        fat_svc.reverter_romaneio_por_nf(numero_nf)
        st_svc.reverter_romaneio_por_nf(numero_nf)


@expedicao_romaneio_bp.route("/api/expedicao/romaneio-fat/<int:romaneio_id>/deletar", methods=["DELETE"])
@roles_required("Admin")
def deletar_romaneio(romaneio_id):
    """Deleta um romaneio (apenas Admin, apenas Rascunho)."""
    romaneio = ExpedicaoRomaneio.query.get(romaneio_id)
    if not romaneio:
        return jsonify({"error": "Romaneio não encontrado."}), 404

    if romaneio.status != "Rascunho":
        dica = (
            " Realize primeiro o estorno da Expedição e depois da Finalização."
            if romaneio.status == "Expedido"
            else " Realize primeiro o estorno da Finalização (Pronto → Rascunho)."
        )
        return jsonify({"error": f"Apenas romaneios em Rascunho podem ser deletados.{dica}"}), 400

    _excluir_romaneio(romaneio)
    return jsonify({"message": "Romaneio deletado com sucesso."})


@expedicao_romaneio_bp.route("/api/expedicao/romaneio-fat/<int:romaneio_id>/solicitar-exclusao", methods=["POST"])
@roles_required(*ROLES_NAO_ADMIN)
def solicitar_exclusao_romaneio(romaneio_id):
    """Quem nao e Admin solicita a exclusao de um romaneio em Rascunho —
    fica Pendente ate um Admin aprovar ou rejeitar."""
    romaneio = ExpedicaoRomaneio.query.get(romaneio_id)
    if not romaneio:
        return jsonify({"error": "Romaneio não encontrado."}), 404
    if romaneio.status != "Rascunho":
        return jsonify({"error": "Apenas romaneios em Rascunho podem ter exclusão solicitada."}), 400

    ja_pendente = ExpedicaoRomaneioExclusao.query.filter_by(
        romaneio_id=romaneio_id, status="Pendente"
    ).first()
    if ja_pendente:
        return jsonify({"error": "Já existe uma solicitação de exclusão pendente para este romaneio."}), 400

    payload = request.get_json(silent=True) or {}
    motivo = str(payload.get("motivo") or "").strip()
    if not motivo:
        return jsonify({"error": "Informe o motivo da exclusão."}), 400

    exclusao = ExpedicaoRomaneioExclusao(
        romaneio_id=romaneio_id,
        solicitante=session["username"],
        motivo=motivo,
        status="Pendente",
    )
    db.session.add(exclusao)
    db.session.commit()
    return jsonify({"message": "Solicitação de exclusão enviada. Aguarde aprovação de um Admin."}), 201


@expedicao_romaneio_bp.route("/api/expedicao/romaneio-fat/exclusao/<int:exclusao_id>/<acao>", methods=["POST"])
@roles_required("Admin")
def decidir_exclusao_romaneio(exclusao_id, acao):
    """Admin aprova (exclui de fato) ou rejeita uma solicitação de exclusão."""
    if acao not in ("aprovar", "rejeitar"):
        return jsonify({"error": "Ação inválida."}), 400

    exclusao = ExpedicaoRomaneioExclusao.query.get(exclusao_id)
    if not exclusao:
        return jsonify({"error": "Solicitação de exclusão não encontrada."}), 404
    if exclusao.status != "Pendente":
        return jsonify({"error": "Esta solicitação já foi decidida."}), 400

    payload = request.get_json(silent=True) or {}
    exclusao.admin_usuario = session["username"]
    exclusao.admin_observacao = str(payload.get("observacao") or "").strip() or None
    exclusao.resolvido_at = datetime.now()

    if acao == "aprovar":
        romaneio = ExpedicaoRomaneio.query.get(exclusao.romaneio_id)
        if not romaneio:
            exclusao.status = "Rejeitado"
            exclusao.admin_observacao = "Romaneio não existe mais."
            db.session.commit()
            return jsonify({"error": "Romaneio não encontrado (já excluído?)."}), 404
        if romaneio.status != "Rascunho":
            return jsonify({"error": "Romaneio não está mais em Rascunho — não é possível excluir."}), 400
        exclusao.status = "Aprovado"
        db.session.commit()
        _excluir_romaneio(romaneio)
        return jsonify({"message": "Exclusão aprovada. Romaneio removido."})

    exclusao.status = "Rejeitado"
    db.session.commit()
    return jsonify({"message": "Solicitação de exclusão rejeitada."})


def _remetente_padrao() -> dict:
    cnpj_bruto = current_app.config.get("EMPRESA_CNPJ", "")
    return {
        "razao_social": current_app.config.get("EMPRESA_NOME", "COLUMBIA MACHINE BRASIL"),
        "nome_fantasia": "",
        "documento": cad_svc.formatar_cnpj(cnpj_bruto) if cnpj_bruto else "",
        "endereco": current_app.config.get("EMPRESA_ENDERECO", "RUA CARLOS ROBERTO PRATAVIEIRA, 600 - JD. NOVA EUROPA - HORTOLÂNDIA BRAZIL - SP"),
        "municipio": "",
        "uf": "",
        "cep": "",
        "inscricao_estadual": "",
        "telefone": "",
        "situacao_cadastral": "",
        # E-mails fixos do romaneio — nunca vem da consulta de CNPJ.
        "email_logistica": "logistica@colmac.com",
        "email_fiscal": "fiscal@colmac.com",
    }


def _remetente_completo() -> dict:
    """Dados do remetente (nossa empresa) para o documento do romaneio —
    tenta enriquecer com o cartao CNPJ (BrasilAPI); cai no fallback fixo se
    a consulta externa falhar."""
    dados = _remetente_padrao()
    cnpj = current_app.config.get("EMPRESA_CNPJ", "")
    if not cnpj:
        return dados
    try:
        cartao = cad_svc.consultar_cartao_cnpj(cnpj)
        dados.update({
            "razao_social": cartao.get("razao_social") or dados["razao_social"],
            "nome_fantasia": cartao.get("nome_fantasia") or "",
            "documento": cartao.get("documento") or dados["documento"],
            "endereco": cartao.get("endereco") or dados["endereco"],
            "municipio": cartao.get("municipio") or "",
            "uf": cartao.get("uf") or "",
            "cep": cartao.get("cep") or "",
            "inscricao_estadual": cartao.get("inscricao_estadual") or "",
            "telefone": cartao.get("telefone") or "",
            "situacao_cadastral": cartao.get("situacao_cadastral") or "",
        })
    except Exception:
        current_app.logger.warning("Falha ao consultar cartão CNPJ do remetente para romaneio", exc_info=True)
    return dados


def _destinatario_padrao(romaneio) -> dict:
    return {
        "razao_social": romaneio.cliente or "—",
        "documento": "",
        "inscricao_estadual": "",
        "endereco": "",
        "municipio": "",
        "uf": "",
        "cep": "",
        "telefone": "",
    }


def _destinatario_da_nf(nf, romaneio) -> dict:
    """Dados do destinatário de UMA NF específica — busca CNPJ/endereço direto
    na NF-e emitida (mesmo XML autorizado usado para gerar o DANFE). Cai no
    fallback (nome do cliente) se a NF não for encontrada no ERP ou não houver
    XML disponível."""
    dados = _destinatario_padrao(romaneio)
    if nf is not None and str(getattr(nf, "cliente", "") or "").strip():
        dados["razao_social"] = nf.cliente
    numero_nf = str(getattr(nf, "numero_nf", "") or "").strip()
    if not numero_nf:
        return dados
    try:
        nota = buscar_nfe_emitida_erp(numero_nf=numero_nf)
        xml_bytes = (nota or {}).get("xml_bytes")
        if not xml_bytes:
            return dados
        nfe = danfe_service.parse_nfe_xml(xml_bytes)
        endereco = ", ".join(
            parte.strip() for parte in [
                nfe.get("dest_logr"),
                nfe.get("dest_nro"),
                nfe.get("dest_cpl"),
                nfe.get("dest_bairro"),
            ] if str(parte or "").strip()
        )
        dados.update({
            "razao_social": nfe.get("dest_nome") or dados["razao_social"],
            "documento": nfe.get("dest_cnpj") or dados["documento"],
            "inscricao_estadual": nfe.get("dest_ie") or "",
            "endereco": endereco,
            "municipio": nfe.get("dest_mun") or "",
            "uf": nfe.get("dest_uf") or "",
            "cep": nfe.get("dest_cep") or "",
            "telefone": nfe.get("dest_fone") or "",
        })
    except Exception:
        current_app.logger.warning(
            "Falha ao obter dados do destinatário via NF-e emitida para o romaneio %s", romaneio.id, exc_info=True
        )
    return dados


def _destinatario_completo(romaneio) -> dict:
    """Destinatário do documento a partir da primeira NF (compatibilidade)."""
    primeira_nf = romaneio.nfs[0] if romaneio.nfs else None
    if not primeira_nf:
        return _destinatario_padrao(romaneio)
    return _destinatario_da_nf(primeira_nf, romaneio)


def _grupos_destinatarios(romaneio) -> list:
    """Agrupa as NFs do romaneio por destinatário distinto (CNPJ, ou nome
    quando não há CNPJ), para gerar UMA página do documento por destinatário —
    todas com o mesmo número de romaneio. Cada grupo traz o destinatário
    completo, as NFs daquele destinatário e os totais do grupo."""
    nfs = list(romaneio.nfs or [])
    if not nfs:
        return [{
            "destinatario": _destinatario_padrao(romaneio),
            "nfs": [],
            "peso_total": 0.0,
            "volumes_total": 0,
        }]
    grupos: list = []
    indice: dict = {}
    for nf in nfs:
        dados = _destinatario_da_nf(nf, romaneio)
        chave = (
            str(dados.get("documento") or "").strip()
            or str(dados.get("razao_social") or "").strip().upper()
            or f"__nf_{nf.numero_nf}"
        )
        grupo = indice.get(chave)
        if grupo is None:
            grupo = {"destinatario": dados, "nfs": [], "peso_total": 0.0, "volumes_total": 0}
            indice[chave] = grupo
            grupos.append(grupo)
        grupo["nfs"].append(nf)
        grupo["peso_total"] += float(getattr(nf, "peso_bruto", 0) or 0)
        grupo["volumes_total"] += int(getattr(nf, "qtde_volumes", 0) or 0)
    return grupos


def _transportadora_snapshot(romaneio) -> dict:
    """Snapshot do cartao CNPJ da transportadora (frete FOB), gravado no
    momento da digitacao. Retorna {} se nao houver."""
    bruto = getattr(romaneio, "transportadora_dados_json", None)
    if not bruto:
        return {}
    try:
        dados = json.loads(bruto)
        return dados if isinstance(dados, dict) else {}
    except (ValueError, TypeError):
        return {}


def _transportadora_card(romaneio) -> dict:
    """Dados da transportadora para o card do documento (frete FOB)."""
    snap = _transportadora_snapshot(romaneio)
    return {
        "razao_social": snap.get("razao_social") or romaneio.transportadora or "—",
        "nome_fantasia": snap.get("nome_fantasia") or "",
        "documento": snap.get("documento") or romaneio.transportadora_documento or "",
        "inscricao_estadual": snap.get("inscricao_estadual") or "",
        "endereco": snap.get("endereco") or "",
        "municipio": snap.get("municipio") or "",
        "uf": snap.get("uf") or "",
        "cep": snap.get("cep") or "",
        "telefone": snap.get("telefone") or "",
    }


def _consolidado_romaneio(romaneio) -> list:
    """Um unico documento com TODAS as NFs do romaneio (sem separar por
    destinatario). Usado tanto para CIF quanto para FOB."""
    nfs = list(romaneio.nfs or [])
    peso_total = sum(float(getattr(nf, "peso_bruto", 0) or 0) for nf in nfs)
    volumes_total = sum(int(getattr(nf, "qtde_volumes", 0) or 0) for nf in nfs)
    return [{
        "nfs": nfs,
        "peso_total": peso_total,
        "volumes_total": volumes_total,
    }]


@expedicao_romaneio_bp.route("/api/expedicao/romaneio-fat/consultar-cnpj", methods=["POST"])
@roles_required(*ROLES)
def consultar_cnpj_transportadora():
    """Consulta o cartao CNPJ (BrasilAPI) da transportadora para o frete FOB."""
    payload = request.get_json(silent=True) or {}
    cnpj = str(payload.get("cnpj") or "").strip()
    try:
        dados = cad_svc.consultar_cartao_cnpj(cnpj)
    except ValueError as exc:
        return jsonify({"ok": False, "erro": str(exc)}), 400
    except Exception:  # noqa: BLE001
        return jsonify({"ok": False, "erro": "Não foi possível consultar o CNPJ agora. Tente novamente em instantes."}), 502
    return jsonify({"ok": True, "dados": dados})


@expedicao_romaneio_bp.route("/expedicao/romaneio/<int:romaneio_id>/visualizar")
@permission_required(PERMISSION)
def visualizar_romaneio(romaneio_id):
    """Visualiza/imprime o romaneio: um unico documento com todas as NFs.
    CIF mostra so o remetente (centralizado); FOB mostra remetente +
    transportadora (dados puxados do cartao CNPJ)."""
    romaneio = ExpedicaoRomaneio.query.get(romaneio_id)
    if not romaneio:
        return "Romaneio não encontrado.", 404

    return render_template(
        "expedicao_romaneio_visualizar.html",
        romaneio=romaneio,
        remetente=_remetente_completo(),
        transportadora=_transportadora_card(romaneio),
        grupos=_consolidado_romaneio(romaneio),
        fotos_carregamento=_fotos_carregamento_payload(romaneio),
    )


@expedicao_romaneio_bp.route("/api/expedicao/romaneio-fat/<int:romaneio_id>/assinatura/<tipo>", methods=["POST"])
@roles_required(*ROLES)
def salvar_assinatura_romaneio(romaneio_id, tipo):
    """Salva a assinatura digital (PNG em base64) do conferente ou do
    transportador, capturada num canvas (ex.: tablet do motorista)."""
    if tipo not in ("conferente", "transportador"):
        return jsonify({"error": "Tipo de assinatura inválido."}), 400

    romaneio = ExpedicaoRomaneio.query.get(romaneio_id)
    if not romaneio:
        return jsonify({"error": "Romaneio não encontrado."}), 404

    payload = request.get_json(silent=True) or {}
    imagem = str(payload.get("imagem_base64") or "").strip()
    if not imagem:
        return jsonify({"error": "Nenhuma assinatura recebida."}), 400
    if "," in imagem:
        imagem = imagem.split(",", 1)[1]

    try:
        dados_png = base64.b64decode(imagem)
    except Exception:
        return jsonify({"error": "Assinatura inválida."}), 400

    nome_arquivo = f"romaneio{romaneio_id}_{tipo}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.png"

    if using_drive():
        try:
            stored = upload_bytes_to_drive(dados_png, nome_arquivo, mimetype="image/png")
            caminho = stored.file_path
        except Exception as exc:
            current_app.logger.exception("Falha ao enviar assinatura do romaneio para o Drive")
            return jsonify({"error": f"Falha ao enviar assinatura para o Drive: {exc}"}), 502
    else:
        fotos_dir = current_app.config.get("EXPEDICAO_CONFERENCIA_FOTOS_DIR", "") or os.path.join(
            current_app.instance_path, "expedicao_romaneio_assinaturas"
        )
        os.makedirs(fotos_dir, exist_ok=True)
        caminho = os.path.join(fotos_dir, nome_arquivo)
        with open(caminho, "wb") as f:
            f.write(dados_png)

    agora = datetime.now()
    usuario = session["username"]
    if tipo == "conferente":
        romaneio.assinatura_conferente_file_name = nome_arquivo
        romaneio.assinatura_conferente_file_path = caminho
        romaneio.assinatura_conferente_uploadado_em = agora
        romaneio.assinatura_conferente_uploadado_por = usuario
    else:
        romaneio.assinatura_transportador_file_name = nome_arquivo
        romaneio.assinatura_transportador_file_path = caminho
        romaneio.assinatura_uploadado_em = agora
        romaneio.assinatura_uploadado_por = usuario
    romaneio.atualizado_em = agora
    db.session.commit()

    return jsonify({
        "message": "Assinatura salva com sucesso.",
        "url": f"/api/expedicao/romaneio-fat/{romaneio_id}/assinatura/{tipo}",
    })


@expedicao_romaneio_bp.route("/api/expedicao/romaneio-fat/<int:romaneio_id>/assinatura/<tipo>", methods=["GET"])
@permission_required(PERMISSION)
def obter_assinatura_romaneio(romaneio_id, tipo):
    if tipo not in ("conferente", "transportador"):
        return jsonify({"error": "Tipo de assinatura inválido."}), 400

    romaneio = ExpedicaoRomaneio.query.get(romaneio_id)
    if not romaneio:
        return jsonify({"error": "Romaneio não encontrado."}), 404

    if tipo == "conferente":
        file_name = romaneio.assinatura_conferente_file_name
        file_path = romaneio.assinatura_conferente_file_path
    else:
        file_name = romaneio.assinatura_transportador_file_name
        file_path = romaneio.assinatura_transportador_file_path

    if not file_name:
        return jsonify({"error": "Assinatura não encontrada."}), 404

    fotos_dir = current_app.config.get("EXPEDICAO_CONFERENCIA_FOTOS_DIR", "") or os.path.join(
        current_app.instance_path, "expedicao_romaneio_assinaturas"
    )
    caminho = _resolver_foto_expedicao(fotos_dir, file_name, file_path)
    if not caminho:
        return jsonify({"error": "Arquivo da assinatura não encontrado."}), 404

    try:
        return _send_foto_expedicao(caminho, file_name)
    except Exception as exc:
        current_app.logger.exception("Falha ao baixar assinatura do romaneio")
        return jsonify({"error": f"Falha ao baixar assinatura: {exc}"}), 502


def _fotos_dir_carregamento() -> str:
    return current_app.config.get("EXPEDICAO_CONFERENCIA_FOTOS_DIR", "") or os.path.join(
        current_app.instance_path, "expedicao_romaneio_carregamento"
    )


def _fotos_carregamento_payload(romaneio) -> list:
    """Lista as fotos de carregamento do romaneio: as multiplas fotos novas
    (tabela ExpedicaoRomaneioFotoCarregamento) + a foto unica legada (colunas
    foto_carregamento_* de ExpedicaoRomaneio), quando existir, para nao
    esconder fotos ja tiradas antes desta mudanca."""
    fotos = (
        ExpedicaoRomaneioFotoCarregamento.query
        .filter_by(romaneio_id=romaneio.id)
        .order_by(ExpedicaoRomaneioFotoCarregamento.id.asc())
        .all()
    )
    itens = [
        {
            "id": f.id,
            "url": f"/api/expedicao/romaneio-fat/{romaneio.id}/foto-carregamento/{f.id}",
            "uploaded_at": f.uploaded_at.strftime("%d/%m/%Y %H:%M") if f.uploaded_at else None,
            "uploaded_by": f.uploaded_by,
        }
        for f in fotos
    ]
    if romaneio.foto_carregamento_file_name:
        itens.insert(0, {
            "id": "legacy",
            "url": f"/api/expedicao/romaneio-fat/{romaneio.id}/foto-carregamento",
            "uploaded_at": (
                romaneio.foto_carregamento_uploadado_em.strftime("%d/%m/%Y %H:%M")
                if romaneio.foto_carregamento_uploadado_em else None
            ),
            "uploaded_by": romaneio.foto_carregamento_uploadado_por,
        })
    return itens


@expedicao_romaneio_bp.route("/api/expedicao/romaneio-fat/<int:romaneio_id>/foto-carregamento", methods=["POST"])
@roles_required(*ROLES)
def salvar_foto_carregamento_romaneio(romaneio_id):
    """Salva uma foto do carregamento (câmera ou galeria do celular/tablet),
    tirada com o romaneio já Pronto para expedir. Cada chamada adiciona uma
    nova foto; o romaneio pode ter varias."""
    romaneio = ExpedicaoRomaneio.query.get(romaneio_id)
    if not romaneio:
        return jsonify({"error": "Romaneio não encontrado."}), 404

    if romaneio.status not in ("Pronto", "Expedido"):
        return jsonify({"error": "A foto de carregamento só pode ser tirada com o romaneio Pronto."}), 400

    payload = request.get_json(silent=True) or {}
    imagem = str(payload.get("imagem_base64") or "").strip()
    if not imagem:
        return jsonify({"error": "Nenhuma foto recebida."}), 400
    if "," in imagem:
        imagem = imagem.split(",", 1)[1]

    try:
        dados_png = base64.b64decode(imagem)
    except Exception:
        return jsonify({"error": "Foto inválida."}), 400

    nome_arquivo = f"romaneio{romaneio_id}_carregamento_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.png"

    if using_drive():
        try:
            stored = upload_bytes_to_drive(dados_png, nome_arquivo, mimetype="image/png")
            caminho = stored.file_path
        except Exception as exc:
            current_app.logger.exception("Falha ao enviar foto de carregamento do romaneio para o Drive")
            return jsonify({"error": f"Falha ao enviar foto para o Drive: {exc}"}), 502
    else:
        fotos_dir = _fotos_dir_carregamento()
        os.makedirs(fotos_dir, exist_ok=True)
        caminho = os.path.join(fotos_dir, nome_arquivo)
        with open(caminho, "wb") as f:
            f.write(dados_png)

    agora = datetime.now()
    foto = ExpedicaoRomaneioFotoCarregamento(
        romaneio_id=romaneio.id,
        file_name=nome_arquivo,
        file_path=caminho,
        uploaded_at=agora,
        uploaded_by=session["username"],
    )
    db.session.add(foto)
    romaneio.atualizado_em = agora
    db.session.commit()

    return jsonify({
        "message": "Foto de carregamento salva com sucesso.",
        "id": foto.id,
        "url": f"/api/expedicao/romaneio-fat/{romaneio_id}/foto-carregamento/{foto.id}",
    })


@expedicao_romaneio_bp.route("/api/expedicao/romaneio-fat/<int:romaneio_id>/fotos-carregamento", methods=["GET"])
@permission_required(PERMISSION)
def listar_fotos_carregamento_romaneio(romaneio_id):
    romaneio = ExpedicaoRomaneio.query.get(romaneio_id)
    if not romaneio:
        return jsonify({"error": "Romaneio não encontrado."}), 404
    return jsonify({"fotos": _fotos_carregamento_payload(romaneio)})


@expedicao_romaneio_bp.route(
    "/api/expedicao/romaneio-fat/<int:romaneio_id>/foto-carregamento/<int:foto_id>", methods=["GET"]
)
@permission_required(PERMISSION)
def obter_foto_carregamento_individual_romaneio(romaneio_id, foto_id):
    foto = ExpedicaoRomaneioFotoCarregamento.query.filter_by(id=foto_id, romaneio_id=romaneio_id).first()
    if not foto:
        return jsonify({"error": "Foto não encontrada."}), 404

    caminho = _resolver_foto_expedicao(_fotos_dir_carregamento(), foto.file_name, foto.file_path)
    if not caminho:
        return jsonify({"error": "Arquivo da foto não encontrado."}), 404

    try:
        return _send_foto_expedicao(caminho, foto.file_name)
    except Exception as exc:
        current_app.logger.exception("Falha ao baixar foto de carregamento do romaneio")
        return jsonify({"error": f"Falha ao baixar foto: {exc}"}), 502


@expedicao_romaneio_bp.route("/api/expedicao/romaneio-fat/<int:romaneio_id>/foto-carregamento", methods=["GET"])
@permission_required(PERMISSION)
def obter_foto_carregamento_romaneio(romaneio_id):
    """Mantido para compatibilidade com a foto unica legada (tirada antes de
    este romaneio suportar multiplas fotos de carregamento)."""
    romaneio = ExpedicaoRomaneio.query.get(romaneio_id)
    if not romaneio:
        return jsonify({"error": "Romaneio não encontrado."}), 404

    if not romaneio.foto_carregamento_file_name:
        return jsonify({"error": "Foto de carregamento não encontrada."}), 404

    caminho = _resolver_foto_expedicao(
        _fotos_dir_carregamento(), romaneio.foto_carregamento_file_name, romaneio.foto_carregamento_file_path
    )
    if not caminho:
        return jsonify({"error": "Arquivo da foto não encontrado."}), 404

    try:
        return _send_foto_expedicao(caminho, romaneio.foto_carregamento_file_name)
    except Exception as exc:
        current_app.logger.exception("Falha ao baixar foto de carregamento do romaneio")
        return jsonify({"error": f"Falha ao baixar foto: {exc}"}), 502
