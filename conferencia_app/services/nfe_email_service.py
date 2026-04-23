"""
Envio automatico de NF-e emitida por e-mail ao destinatario.

Fluxo:
- Dado um numero de NF (ou chave de acesso), busca a nota na Consyste (API ja integrada).
- Baixa XML e DANFE (PDF) via Consyste download endpoints.
- Resolve destinatario na ordem: 1) override manual -> 2) cadastro AgendamentoCliente ->
  3) tag <dest><email> do XML. Em ultimo caso, fica pendente.
- Registra EmailNFEnviado e envia e-mail com XML + PDF anexados.
- Modo teste: redireciona para NFE_EMAIL_TESTE_DESTINO, mantendo original em metadados.
"""
from __future__ import annotations

import os
import re
import smtplib
import threading
from dataclasses import dataclass
from datetime import datetime
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any
from xml.etree import ElementTree as ET

import requests
from flask import current_app

from ..extensions import db
from ..models import EmailNFEnviado, AgendamentoCliente, ItemNota
from .planilhas_cadastros import buscar_email_por_cnpj


# ---------- Utilidades ----------

_RE_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _somente_digitos(s: str | None) -> str:
    return re.sub(r"\D", "", str(s or ""))


def _valido_email(e: str | None) -> bool:
    return bool(e and _RE_EMAIL.match(e.strip()))


def _token_consyste(app) -> str:
    return app.config.get("CONSYSTE_TOKEN") or ""


def _base_consyste(app) -> str:
    return app.config.get("CONSYSTE_API_BASE", "https://portal.consyste.com.br/api/v1").rstrip("/")


# ---------- Dados resolvidos da NF ----------


@dataclass
class NotaEmitida:
    chave: str
    numero: str
    dest_nome: str = ""
    dest_cnpj: str = ""
    dest_email_xml: str = ""
    emit_nome: str = ""
    emit_cnpj: str = ""
    xml_bytes: bytes | None = None
    pdf_bytes: bytes | None = None


def _buscar_documento_consyste(app, numero: str, chave: str) -> dict[str, Any] | None:
    """Localiza doc na Consyste por chave (preferencial) ou numero."""
    token = _token_consyste(app)
    if not token:
        return None
    headers = {"X-Consyste-Auth-Token": token, "Accept": "application/json"}
    base = _base_consyste(app)

    chave_limpa = _somente_digitos(chave)
    numero_limpo = str(numero or "").strip()

    if chave_limpa and len(chave_limpa) == 44:
        try:
            resp = requests.get(f"{base}/nfe/{chave_limpa}", headers=headers, timeout=15)
            if resp.ok and resp.content:
                data = resp.json()
                if isinstance(data, dict):
                    return data
        except Exception as exc:  # pragma: no cover - rede
            app.logger.warning("Consyste lookup por chave falhou: %s", exc)

    if numero_limpo:
        # preferencia: caixa 'emitidos' (NF emitida pela empresa)
        for filtro in ("emitidos", "todos", "recebidos"):
            url = f"{base}/nfe/lista/{filtro}"
            params = {"q": f"numero:{numero_limpo}", "campos": "id,chave,numero,emit_nome,emit_cnpj,dest_nome,dest_cnpj"}
            try:
                resp = requests.get(url, headers=headers, params=params, timeout=15)
                if not resp.ok:
                    continue
                dados = resp.json() if resp.content else {}
                lista = dados.get("documentos", []) if isinstance(dados, dict) else []
                candidatos = [d for d in lista if str(d.get("numero", "")).strip() == numero_limpo]
                if candidatos:
                    return candidatos[0]
            except Exception as exc:  # pragma: no cover - rede
                app.logger.warning("Consyste lista %s falhou: %s", filtro, exc)

    return None


def _download_consyste(app, chave: str, formato: str) -> bytes | None:
    token = _token_consyste(app)
    if not token:
        return None
    base = _base_consyste(app)
    headers = {
        "X-Consyste-Auth-Token": token,
        "Accept": "application/xml" if formato == "xml" else "application/pdf",
    }
    url = f"{base}/nfe/{_somente_digitos(chave)}/download.{formato}"
    try:
        resp = requests.get(url, headers=headers, timeout=25)
        if resp.ok and resp.content:
            return resp.content
    except Exception as exc:  # pragma: no cover - rede
        app.logger.warning("Consyste download.%s falhou: %s", formato, exc)
    return None


def _parse_dest_email_do_xml(xml_bytes: bytes | None) -> tuple[str, str, str]:
    """Retorna (dest_email, dest_nome, dest_cnpj) extraidos do XML da NF-e."""
    if not xml_bytes:
        return "", "", ""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return "", "", ""
    ns = {"nfe": "http://www.portalfiscal.inf.br/nfe"}
    # email pode estar em <dest><email> ou, em menor frequencia, em <infNFe><infAdic><email>
    email = (root.findtext(".//nfe:dest/nfe:email", default="", namespaces=ns) or "").strip()
    nome = (root.findtext(".//nfe:dest/nfe:xNome", default="", namespaces=ns) or "").strip()
    cnpj = (
        root.findtext(".//nfe:dest/nfe:CNPJ", default="", namespaces=ns)
        or root.findtext(".//nfe:dest/nfe:CPF", default="", namespaces=ns)
        or ""
    ).strip()
    return email, nome, cnpj


def _resolver_nota(numero_nf: str, chave: str | None = None) -> NotaEmitida | None:
    app = current_app._get_current_object()
    doc = _buscar_documento_consyste(app, numero_nf, chave or "")
    if not doc:
        return None
    chave_doc = _somente_digitos(doc.get("chave") or chave or "")
    if len(chave_doc) != 44:
        return None

    nota = NotaEmitida(
        chave=chave_doc,
        numero=str(doc.get("numero") or numero_nf or "").strip(),
        dest_nome=str(doc.get("dest_nome") or "").strip(),
        dest_cnpj=_somente_digitos(doc.get("dest_cnpj")),
        emit_nome=str(doc.get("emit_nome") or "").strip(),
        emit_cnpj=_somente_digitos(doc.get("emit_cnpj")),
    )
    nota.xml_bytes = _download_consyste(app, chave_doc, "xml")
    nota.pdf_bytes = _download_consyste(app, chave_doc, "pdf")

    email_xml, nome_xml, cnpj_xml = _parse_dest_email_do_xml(nota.xml_bytes)
    nota.dest_email_xml = email_xml
    if not nota.dest_nome and nome_xml:
        nota.dest_nome = nome_xml
    if not nota.dest_cnpj and cnpj_xml:
        nota.dest_cnpj = cnpj_xml
    return nota


# ---------- Resolucao do destinatario ----------


def _email_do_cadastro_cliente(cnpj: str, nome: str) -> str:
    if cnpj:
        cad = (
            AgendamentoCliente.query
            .filter(AgendamentoCliente.cnpj_cpf == cnpj)
            .order_by(AgendamentoCliente.ativo.desc(), AgendamentoCliente.id.asc())
            .first()
        )
        if cad and _valido_email(cad.email):
            return cad.email.strip()
    if nome:
        like = f"%{nome.strip()}%"
        cad = (
            AgendamentoCliente.query
            .filter(AgendamentoCliente.nome.ilike(like))
            .order_by(AgendamentoCliente.ativo.desc())
            .first()
        )
        if cad and _valido_email(cad.email):
            return cad.email.strip()
    return ""


def resolver_destinatario(numero_nf: str, chave: str | None = None, override_email: str | None = None) -> dict:
    """Sugere e-mail para envio da NF-e. Ordem: manual -> cadastro -> XML.

    Retorna dict com: email, fonte_email (Manual|Cadastro|XML|""), dest_nome, dest_cnpj,
    numero, chave e avisos[]. Se nao encontrar nada, email="".
    """
    nota = _resolver_nota(numero_nf, chave)
    avisos: list[str] = []
    if not nota:
        return {
            "email": (override_email or "").strip(),
            "fonte_email": "Manual" if _valido_email(override_email) else "",
            "dest_nome": "",
            "dest_cnpj": "",
            "numero": numero_nf,
            "chave": chave or "",
            "avisos": ["NF nao encontrada na Consyste."],
        }

    email_manual = (override_email or "").strip()
    if email_manual and _valido_email(email_manual):
        return {
            "email": email_manual,
            "fonte_email": "Manual",
            "dest_nome": nota.dest_nome,
            "dest_cnpj": nota.dest_cnpj,
            "numero": nota.numero,
            "chave": nota.chave,
            "avisos": avisos,
        }

    # 1) Planilhas locais clientes.xlsx / fornecedores.xlsx
    if nota.dest_cnpj:
        hit = buscar_email_por_cnpj(nota.dest_cnpj)
        if hit and _valido_email(hit.get("email")):
            return {
                "email": hit["email"],
                "fonte_email": "Planilha",
                "dest_nome": nota.dest_nome or hit.get("nome", ""),
                "dest_cnpj": nota.dest_cnpj,
                "numero": nota.numero,
                "chave": nota.chave,
                "avisos": avisos,
            }

    # 2) Cadastro interno (AgendamentoCliente)
    email_cad = _email_do_cadastro_cliente(nota.dest_cnpj, nota.dest_nome)
    if email_cad:
        return {
            "email": email_cad,
            "fonte_email": "Cadastro",
            "dest_nome": nota.dest_nome,
            "dest_cnpj": nota.dest_cnpj,
            "numero": nota.numero,
            "chave": nota.chave,
            "avisos": avisos,
        }

    if _valido_email(nota.dest_email_xml):
        return {
            "email": nota.dest_email_xml,
            "fonte_email": "XML",
            "dest_nome": nota.dest_nome,
            "dest_cnpj": nota.dest_cnpj,
            "numero": nota.numero,
            "chave": nota.chave,
            "avisos": avisos,
        }

    avisos.append("Nenhum e-mail valido encontrado no cadastro nem no XML.")
    return {
        "email": "",
        "fonte_email": "",
        "dest_nome": nota.dest_nome,
        "dest_cnpj": nota.dest_cnpj,
        "numero": nota.numero,
        "chave": nota.chave,
        "avisos": avisos,
    }


# ---------- Envio propriamente dito ----------


def _montar_corpo_html(numero_nf: str, chave: str, dest_nome: str, emit_nome: str, modo_teste: bool, destino_real: str) -> str:
    aviso_teste = ""
    if modo_teste:
        aviso_teste = f"""
        <div style="margin-top:16px;padding:12px 16px;border-radius:10px;background:#fff7ed;border:1px solid #fdba74;color:#9a3412;font-size:13px">
          <strong>[MODO TESTE]</strong> Este e-mail seria enviado para <strong>{destino_real or '(sem destinatario)'}</strong>.
          Em producao, envie ao destinatario real desligando <code>NFE_EMAIL_MODO_TESTE</code>.
        </div>"""

    chave_fmt = " ".join([chave[i:i + 4] for i in range(0, len(chave), 4)]) if chave else ""
    consulta_url = f"https://www.nfe.fazenda.gov.br/portal/consultaRecaptcha.aspx?tipoConsulta=resumo&tipoConteudo=XbSeqxE8pl8=&nfe={chave}"

    return f"""\
    <html><body style="font-family:Arial,Helvetica,sans-serif;background:#f5f7fb;padding:24px;margin:0">
      <div style="max-width:620px;margin:auto;background:#fff;border-radius:14px;padding:28px;box-shadow:0 8px 28px rgba(15,23,42,0.08)">
        <div style="border-left:4px solid #155eef;padding-left:14px;margin-bottom:18px">
          <h2 style="margin:0 0 4px;color:#0f172a;font-size:20px">NF-e emitida &mdash; {emit_nome or 'Columbia'}</h2>
          <p style="margin:0;color:#64748b;font-size:13px">Segue em anexo a NF-e e o DANFE para seus registros.</p>
        </div>
        <table style="width:100%;font-size:14px;color:#1e293b;border-collapse:collapse">
          <tr><td style="padding:6px 0;color:#64748b;width:120px">Destinatario</td><td><strong>{dest_nome or '&mdash;'}</strong></td></tr>
          <tr><td style="padding:6px 0;color:#64748b">Numero da NF</td><td><strong>{numero_nf}</strong></td></tr>
          <tr><td style="padding:6px 0;color:#64748b">Chave de acesso</td><td style="font-family:Consolas,monospace;font-size:12px">{chave_fmt}</td></tr>
        </table>
        <div style="margin-top:22px">
          <a href="{consulta_url}" style="display:inline-block;padding:10px 18px;background:#155eef;color:#fff;border-radius:10px;text-decoration:none;font-size:13px;font-weight:700">
            Consultar NF-e na SEFAZ
          </a>
        </div>
        {aviso_teste}
        <p style="margin-top:26px;font-size:11.5px;color:#94a3b8">
          E-mail automatico enviado pelo sistema Columbia Sync. Em caso de divergencia, responda para seu contato comercial.
        </p>
      </div>
    </body></html>"""


def _send_async(app, msg, smtp_server, smtp_port, sender, password, log_id):
    with app.app_context():
        try:
            with smtplib.SMTP(smtp_server, smtp_port, timeout=30) as server:
                server.starttls()
                server.login(sender, password)
                server.send_message(msg)
            row = db.session.get(EmailNFEnviado, log_id)
            if row:
                row.status = "Enviado"
                row.enviado_em = datetime.now()
                row.tentativas = (row.tentativas or 0) + 1
                db.session.commit()
            app.logger.info("NF-e %s enviada por e-mail (log %s).", msg["Subject"], log_id)
        except Exception as exc:
            app.logger.error("Falha ao enviar NF-e (log %s): %s", log_id, exc)
            try:
                row = db.session.get(EmailNFEnviado, log_id)
                if row:
                    row.status = "Falha"
                    row.tentativas = (row.tentativas or 0) + 1
                    row.erro_mensagem = str(exc)[:780]
                    db.session.commit()
            except Exception:  # pragma: no cover - defensivo
                db.session.rollback()


def enviar_nfe_por_email(
    numero_nf: str,
    *,
    chave: str | None = None,
    override_email: str | None = None,
    cc_emails: list[str] | None = None,
    disparado_por: str = "sistema",
    origem: str = "Manual",
    conferencia_id: int | None = None,
    faturamento_id: int | None = None,
) -> dict:
    """Orquestra resolucao + download + envio. Retorna dict com resultado."""
    app = current_app._get_current_object()

    nota = _resolver_nota(numero_nf, chave)
    if not nota:
        return {"sucesso": False, "erro": "NF nao encontrada na Consyste.", "numero_nf": numero_nf}

    # Destinatario
    resolvido = resolver_destinatario(nota.numero, nota.chave, override_email)
    destino_real = resolvido["email"]
    fonte = resolvido["fonte_email"]

    if not destino_real:
        # Sem e-mail: em modo Auto aguarda intervencao manual; caso contrario, falha.
        status_pend = "AguardandoManual" if origem in ("Auto", "Scheduler") else "Falha"
        log = EmailNFEnviado(
            numero_nf=nota.numero,
            chave_acesso=nota.chave,
            destinatario_email="",
            destinatario_nome=nota.dest_nome,
            destinatario_cnpj=nota.dest_cnpj,
            fonte_email="",
            origem=origem,
            status=status_pend,
            erro_mensagem="Sem e-mail no XML, planilha ou cadastro. Informe manualmente.",
            conferencia_id=conferencia_id,
            faturamento_id=faturamento_id,
            disparado_por=disparado_por,
        )
        db.session.add(log)
        db.session.commit()
        return {"sucesso": False, "erro": "Nenhum e-mail disponivel.", "log_id": log.id,
                "numero_nf": nota.numero, "status": status_pend}

    # Modo teste: redireciona
    modo_teste = bool(app.config.get("NFE_EMAIL_MODO_TESTE", True))
    destino_teste = str(app.config.get("NFE_EMAIL_TESTE_DESTINO") or "").strip()
    destino_efetivo = destino_teste if (modo_teste and _valido_email(destino_teste)) else destino_real

    # Config SMTP
    smtp_server = app.config.get("MAIL_SMTP_SERVER")
    smtp_port = int(app.config.get("MAIL_SMTP_PORT", 587))
    sender = app.config.get("MAIL_SENDER") or ""
    password = app.config.get("MAIL_PASSWORD") or ""
    sender_name = app.config.get("MAIL_SENDER_NAME", "Columbia Sync")
    if not sender or not password:
        return {"sucesso": False, "erro": "SMTP nao configurado (MAIL_SENDER/MAIL_PASSWORD)."}

    # Monta mensagem
    assunto_base = f"NF-e {nota.numero} - {(nota.emit_nome or sender_name)}"
    assunto = f"[TESTE] {assunto_base}" if modo_teste else assunto_base

    # CC: combina lista explicita + config global. Em modo teste, ignora CC
    # para evitar que varios e-mails de cobranca caiam pra outras pessoas durante testes.
    cc_final: list[str] = []
    if not modo_teste:
        if cc_emails:
            cc_final.extend([e.strip() for e in cc_emails if _valido_email(str(e).strip())])
        cc_config_raw = str(app.config.get("NFE_EMAIL_CC") or "")
        for e in re.split(r"[,;\s]+", cc_config_raw):
            e = e.strip()
            if _valido_email(e) and e not in cc_final:
                cc_final.append(e)

    msg = MIMEMultipart("mixed")
    msg["Subject"] = assunto
    msg["From"] = f"{sender_name} <{sender}>"
    msg["To"] = destino_efetivo
    if cc_final:
        msg["Cc"] = ", ".join(cc_final)

    corpo_html = _montar_corpo_html(
        nota.numero, nota.chave, nota.dest_nome, nota.emit_nome, modo_teste, destino_real,
    )
    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(f"NF-e {nota.numero} em anexo. Destinatario: {nota.dest_nome}", "plain"))
    alt.attach(MIMEText(corpo_html, "html"))
    msg.attach(alt)

    anexou_xml = False
    anexou_pdf = False
    if nota.xml_bytes:
        part = MIMEApplication(nota.xml_bytes, _subtype="xml")
        part.add_header("Content-Disposition", "attachment", filename=f"NFe-{nota.chave}.xml")
        msg.attach(part)
        anexou_xml = True
    if nota.pdf_bytes:
        part = MIMEApplication(nota.pdf_bytes, _subtype="pdf")
        part.add_header("Content-Disposition", "attachment", filename=f"DANFE-{nota.numero}.pdf")
        msg.attach(part)
        anexou_pdf = True

    # Log ANTES do envio (para idempotencia + rastreio de falha)
    log = EmailNFEnviado(
        numero_nf=nota.numero,
        chave_acesso=nota.chave,
        destinatario_email=destino_efetivo,
        destinatario_nome=nota.dest_nome,
        destinatario_cnpj=nota.dest_cnpj,
        cc_emails=(", ".join(cc_final) if cc_final else None),
        assunto=assunto,
        fonte_email=fonte,
        origem=origem,
        status="Pendente",
        anexou_xml=anexou_xml,
        anexou_pdf=anexou_pdf,
        conferencia_id=conferencia_id,
        faturamento_id=faturamento_id,
        disparado_por=disparado_por,
    )
    db.session.add(log)
    db.session.commit()

    # Envio em background
    destinatarios_smtp = [destino_efetivo]
    if cc_final:
        destinatarios_smtp += cc_final

    thread = threading.Thread(
        target=_send_async,
        args=(app, msg, smtp_server, smtp_port, sender, password, log.id),
        daemon=True,
    )
    thread.start()

    return {
        "sucesso": True,
        "log_id": log.id,
        "numero_nf": nota.numero,
        "chave": nota.chave,
        "destinatario": destino_efetivo,
        "destinatario_real": destino_real,
        "modo_teste": modo_teste,
        "fonte_email": fonte,
        "anexou_xml": anexou_xml,
        "anexou_pdf": anexou_pdf,
    }
