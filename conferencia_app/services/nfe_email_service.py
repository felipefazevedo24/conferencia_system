"""
Envio automatico de NF-e emitida por e-mail ao destinatario.

Fluxo:
- Dado um numero de NF (ou chave de acesso), busca a NF-e emitida no ERP via API bridge.
- Usa XML/PDF gravados no banco do ERP, sem aguardar a Consyste.
- Resolve destinatario na ordem: 1) override manual -> 2) cadastro AgendamentoCliente ->
  3) tag <dest><email> do XML. Em ultimo caso, fica pendente.
- Registra EmailNFEnviado e envia e-mail com XML + PDF anexados.
- Modo teste: redireciona para NFE_EMAIL_TESTE_DESTINO, mantendo original em metadados.
"""
from __future__ import annotations

import re
import smtplib
import threading
import os
from dataclasses import dataclass
from datetime import datetime
from email.mime.application import MIMEApplication
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any
from xml.etree import ElementTree as ET

from flask import current_app

from ..extensions import db
from ..models import EmailNFEnviado, AgendamentoCliente, ItemNota
from .erp_nfe_emitidas_service import buscar_nfe_emitida_erp
from .danfe_service import gerar_danfe
from .planilhas_cadastros import buscar_email_por_cnpj


# ---------- Utilidades ----------

_RE_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _somente_digitos(s: str | None) -> str:
    return re.sub(r"\D", "", str(s or ""))


def _valido_email(e: str | None) -> bool:
    return bool(e and _RE_EMAIL.match(e.strip()))


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


def _resolver_nota_erp(numero_nf: str, chave: str | None = None) -> NotaEmitida | None:
    app = current_app._get_current_object()
    try:
        doc = buscar_nfe_emitida_erp(numero_nf, chave or "")
    except Exception as exc:
        app.logger.warning("ERP NF-e lookup falhou para %s/%s: %s", numero_nf, chave or "", exc)
        return None

    if not doc:
        return None

    chave_doc = _somente_digitos(doc.get("chave") or chave or "")
    if len(chave_doc) != 44:
        return None

    if not doc.get("autorizada"):
        app.logger.info("NF-e %s encontrada no ERP mas ainda nao autorizada.", doc.get("numero") or numero_nf)
        return None

    nota = NotaEmitida(
        chave=chave_doc,
        numero=str(doc.get("numero") or numero_nf or "").strip(),
        dest_nome=str(doc.get("dest_nome") or "").strip(),
        dest_cnpj=_somente_digitos(doc.get("dest_cnpj")),
        dest_email_xml=str(doc.get("email_danfe") or "").strip(),
        emit_nome="COLUMBIA MACHINE BRASIL",
        emit_cnpj=_somente_digitos(app.config.get("EMPRESA_CNPJ")),
        xml_bytes=doc.get("xml_bytes"),
        pdf_bytes=doc.get("pdf_bytes"),
    )

    email_xml, nome_xml, cnpj_xml = _parse_dest_email_do_xml(nota.xml_bytes)
    if email_xml:
        nota.dest_email_xml = email_xml
    if not nota.dest_nome and nome_xml:
        nota.dest_nome = nome_xml
    if not nota.dest_cnpj and cnpj_xml:
        nota.dest_cnpj = cnpj_xml

    if nota.xml_bytes and not nota.pdf_bytes:
        app.logger.warning("NF-e %s encontrada no ERP sem PDF DANFE no banco; gerando localmente.", chave_doc)
        nota.pdf_bytes = _gerar_pdf_danfe_do_xml(nota.xml_bytes, nota.numero, nota.chave)
    return nota


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


def _parse_cfops_do_xml(xml_bytes: bytes | None) -> set[str]:
    """Retorna o conjunto de CFOPs (4 digitos) presentes nos itens da NF-e."""
    if not xml_bytes:
        return set()
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return set()
    ns = {"nfe": "http://www.portalfiscal.inf.br/nfe"}
    cfops: set[str] = set()
    for el in root.findall(".//nfe:det/nfe:prod/nfe:CFOP", ns):
        valor = (el.text or "").strip()
        if valor:
            cfops.add(valor[:4])
    return cfops


def _gerar_pdf_danfe_do_xml(xml_bytes: bytes | None, numero_nf: str, chave: str) -> bytes | None:
    """Gera DANFE localmente quando o ERP ainda nao gravou pdf_danfe."""
    if not xml_bytes:
        return None
    app = current_app._get_current_object()
    try:
        logo_path = os.path.normpath(os.path.join(app.root_path, "..", "static", "columbia_logo.png"))
        logo_url = app.config.get("EMPRESA_LOGO_URL", "")
        pdf_bytes = gerar_danfe(xml_bytes, logo_path=logo_path, logo_url=logo_url)
        if pdf_bytes:
            app.logger.info("DANFE da NF-e %s/%s gerada localmente a partir do XML.", numero_nf, chave)
            return pdf_bytes
    except Exception as exc:
        app.logger.exception("Falha ao gerar DANFE local da NF-e %s/%s: %s", numero_nf, chave, exc)
    return None


def _resolver_nota(numero_nf: str, chave: str | None = None) -> NotaEmitida | None:
    nota_erp = _resolver_nota_erp(numero_nf, chave)
    if nota_erp:
        return nota_erp

    return None


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
    if not nota:
        return {
            "email": (override_email or "").strip(),
            "fonte_email": "Manual" if _valido_email(override_email) else "",
            "dest_nome": "",
            "dest_cnpj": "",
            "numero": numero_nf,
            "chave": chave or "",
            "avisos": ["NF nao encontrada/autorizada no ERP a partir de 13/05/2026."],
        }

    return _resolver_destinatario_da_nota(nota, override_email)


def _resolver_destinatario_da_nota(nota: NotaEmitida, override_email: str | None = None) -> dict:
    avisos: list[str] = []

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
        <tr><td style="padding:0 32px">
          <div style="margin-top:8px;padding:12px 16px;border-radius:8px;background:#fff7ed;border:1px solid #fdba74;color:#9a3412;font-size:13px;font-family:Arial,Helvetica,sans-serif">
            <strong>[MODO TESTE]</strong> Este e-mail seria enviado para <strong>{destino_real or '(sem destinatário)'}</strong>.
          </div>
        </td></tr>"""

    dest_nome_exib = (dest_nome or "").upper() or "—"
    emit_nome_exib = (emit_nome or "COLUMBIA MACHINE BRASIL").upper()
    chave_fmt = " ".join([chave[i:i + 4] for i in range(0, len(chave), 4)]) if chave else ""
    consulta_url = f"https://www.nfe.fazenda.gov.br/portal/consultaRecaptcha.aspx?tipoConsulta=resumo&tipoConteudo=XbSeqxE8pl8=&nfe={chave}"

    return f"""\
    <!DOCTYPE html>
    <html lang="pt-BR">
    <head>
      <meta charset="UTF-8"/>
      <meta name="viewport" content="width=device-width,initial-scale=1"/>
      <meta name="color-scheme" content="light only"/>
      <meta name="supported-color-schemes" content="light"/>
      <title>Nota Fiscal Eletrônica</title>
      <style>
        :root {{ color-scheme: light only; supported-color-schemes: light; }}
      </style>
    </head>
    <body style="margin:0;padding:0;background:#f1f5f9;font-family:Arial,Helvetica,sans-serif;color:#0f172a">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" bgcolor="#f1f5f9" style="background:#f1f5f9;padding:28px 12px">
        <tr><td align="center">
          <table role="presentation" width="640" cellpadding="0" cellspacing="0" bgcolor="#ffffff" style="max-width:640px;width:100%;background:#ffffff;border:1px solid #e2e8f0;border-radius:10px">

            <!-- Header azul solido -->
            <tr>
              <td bgcolor="#1e3a8a" style="background-color:#1e3a8a;padding:20px 28px;border-top-left-radius:10px;border-top-right-radius:10px">
                <table width="100%" cellpadding="0" cellspacing="0">
                  <tr>
                    <td width="64" valign="middle" style="padding-right:14px">
                      <img src="https://www.columbiamachine.com.br/img/columbia_logo.png" alt="Columbia" width="56" height="56" style="display:block;width:56px;height:56px;border:0;outline:none;background:#ffffff;border-radius:8px;padding:6px">
                    </td>
                    <td valign="middle" style="font-family:Arial,Helvetica,sans-serif;font-size:20px;font-weight:700;color:#ffffff;line-height:1.3">
                      Nota Fiscal Eletrônica
                    </td>
                    <td align="right" valign="middle" style="font-family:Arial,Helvetica,sans-serif;font-size:13px;color:#ffffff;font-weight:700">
                      Série/Número 1/{numero_nf}
                    </td>
                  </tr>
                  <tr>
                    <td colspan="3" style="padding-top:4px;font-family:Arial,Helvetica,sans-serif;font-size:12px;color:#bfdbfe">
                      Emitida por {emit_nome_exib}
                    </td>
                  </tr>
                </table>
              </td>
            </tr>

            <!-- Intro -->
            <tr>
              <td style="padding:24px 28px 10px;font-family:Arial,Helvetica,sans-serif;font-size:14px;line-height:1.6;color:#1e293b">
                Esta mensagem refere-se à Nota Fiscal Eletrônica Nacional
                <strong style="color:#0f172a">Série/Número 1/{numero_nf}</strong>, emitida para:
              </td>
            </tr>

            <!-- Destinatario -->
            <tr>
              <td style="padding:6px 28px 0">
                <table width="100%" cellpadding="0" cellspacing="0" bgcolor="#f8fafc" style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px">
                  <tr>
                    <td style="padding:14px 16px;font-family:Arial,Helvetica,sans-serif">
                      <div style="font-size:11px;letter-spacing:1.5px;text-transform:uppercase;color:#475569;font-weight:700">Razão Social</div>
                      <div style="margin-top:4px;font-size:15px;font-weight:700;color:#0f172a;line-height:1.4">{dest_nome_exib}</div>
                    </td>
                  </tr>
                </table>
              </td>
            </tr>

            <!-- Chave -->
            <tr>
              <td style="padding:14px 28px 0;font-family:Arial,Helvetica,sans-serif">
                <div style="font-size:11px;letter-spacing:1.5px;text-transform:uppercase;color:#475569;font-weight:700">Chave de acesso</div>
                <div style="margin-top:6px;padding:11px 14px;background:#f1f5f9;color:#0f172a;border:1px solid #e2e8f0;border-radius:6px;font-family:Consolas,'Courier New',monospace;font-size:12.5px;word-break:break-all;letter-spacing:0.5px">
                  NFe {chave_fmt}
                </div>
              </td>
            </tr>

            <!-- CTA -->
            <tr>
              <td style="padding:18px 28px 0;font-family:Arial,Helvetica,sans-serif">
                <table cellpadding="0" cellspacing="0">
                  <tr><td bgcolor="#1e3a8a" style="border-radius:6px;background-color:#1e3a8a">
                    <a href="{consulta_url}" style="display:inline-block;padding:10px 18px;font-size:13px;font-weight:700;color:#ffffff;text-decoration:none;font-family:Arial,Helvetica,sans-serif">
                      Consultar NF-e na SEFAZ
                    </a>
                  </td></tr>
                </table>
                <div style="margin-top:10px;font-size:12px;color:#475569">
                  Ou acesse diretamente:
                  <a href="http://www.nfe.fazenda.gov.br/portal" style="color:#1d4ed8;text-decoration:underline">www.nfe.fazenda.gov.br/portal</a>
                </div>
              </td>
            </tr>

            <!-- Anexos -->
            <tr>
              <td style="padding:22px 28px 0;font-family:Arial,Helvetica,sans-serif">
                <table width="100%" cellpadding="0" cellspacing="0" style="border-top:1px solid #e2e8f0">
                  <tr>
                    <td style="padding-top:14px;font-size:13px;color:#334155;line-height:1.6">
                      <strong style="color:#0f172a">Anexos:</strong> XML da NF-e e DANFE (PDF) seguem anexados para seus registros.
                    </td>
                  </tr>
                </table>
              </td>
            </tr>

            <!-- Mensagem institucional -->
            <tr>
              <td style="padding:16px 28px 0;font-family:Arial,Helvetica,sans-serif">
                <p style="margin:0;font-size:13px;line-height:1.65;color:#475569;font-style:italic">
                  Agradecemos a confiança em nossos serviços e reiteramos nossa satisfação com esta parceria.
                  Permanecemos à disposição para eventuais dúvidas.
                </p>
              </td>
            </tr>

            {aviso_teste}

            <!-- Footer -->
            <tr>
              <td style="padding:20px 28px 24px;font-family:Arial,Helvetica,sans-serif">
                <div style="border-top:1px solid #e2e8f0;padding-top:14px;font-size:12px;color:#475569;line-height:1.6">
                  Este e-mail foi enviado automaticamente pelo Sistema de Nota Fiscal Eletrônica (NF-e) da
                  <strong style="color:#334155">{emit_nome_exib}</strong>. Em caso de divergência, responda a este e-mail ou contate seu representante comercial.
                </div>
                <div style="margin-top:10px;font-size:11px;color:#64748b;font-style:italic">
                  powered by <strong style="color:#1e3a8a;font-style:normal">Columbia Sync</strong>
                </div>
              </td>
            </tr>

          </table>
        </td></tr>
      </table>
    </body>
    </html>"""


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
    envio_assincrono: bool = True,
) -> dict:
    """Orquestra resolucao + download + envio. Retorna dict com resultado."""
    app = current_app._get_current_object()

    nota = _resolver_nota(numero_nf, chave)
    if not nota:
        return {"sucesso": False, "erro": "NF nao encontrada/autorizada no ERP a partir de 13/05/2026.", "numero_nf": numero_nf}

    # Roteamento por CFOP: se algum item da NF tiver CFOP na lista de CFOPs
    # especiais configurada, o destinatario do XML/cadastro e ignorado e o
    # e-mail vai exclusivamente para a lista NFE_EMAIL_DESTINATARIOS_ESPECIAIS.
    cfops_cfg_raw = str(app.config.get("NFE_EMAIL_CFOPS_ESPECIAIS") or "")
    cfops_especiais = {
        c.strip() for c in re.split(r"[,;\s]+", cfops_cfg_raw) if c.strip().isdigit()
    }
    cfops_da_nota: set[str] = set()
    cfop_match: list[str] = []
    if cfops_especiais:
        cfops_da_nota = _parse_cfops_do_xml(nota.xml_bytes)
        cfop_match = sorted(cfops_da_nota & cfops_especiais)

    rota_especial_emails: list[str] = []
    if cfop_match:
        destinos_raw = str(app.config.get("NFE_EMAIL_DESTINATARIOS_ESPECIAIS") or "")
        for e in re.split(r"[,;\s]+", destinos_raw):
            e = e.strip()
            if _valido_email(e) and e not in rota_especial_emails:
                rota_especial_emails.append(e)
        if not rota_especial_emails:
            app.logger.warning(
                "NF-e %s casou CFOP especial %s mas NFE_EMAIL_DESTINATARIOS_ESPECIAIS esta vazio; envio segue fluxo normal.",
                nota.numero, cfop_match,
            )

    # Destinatario
    resolvido = _resolver_destinatario_da_nota(nota, override_email)
    destino_real = resolvido["email"]
    fonte = resolvido["fonte_email"]

    # Modo teste: redireciona
    modo_teste = bool(app.config.get("NFE_EMAIL_MODO_TESTE", True))
    destino_teste = str(app.config.get("NFE_EMAIL_TESTE_DESTINO") or "").strip()

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

    # Fallback: sem destinatario principal, mas ha CC -> usa o primeiro CC como principal.
    # O restante continua em copia. Mantem a nota como "enviada" em vez de ficar pendente.
    if not destino_real and cc_final:
        destino_real = cc_final.pop(0)
        fonte = "CC"

    # Override por CFOP: substitui completamente destinatario e CC pela lista
    # configurada (primeiro vira TO, demais ficam em CC). O cliente NAO recebe.
    if rota_especial_emails:
        destino_real = rota_especial_emails[0]
        cc_final = list(rota_especial_emails[1:])
        fonte = "CFOP"
        app.logger.info(
            "NF-e %s roteada por CFOP %s -> %s (CC=%s)",
            nota.numero, cfop_match, destino_real, cc_final,
        )

    if not destino_real:
        # Sem e-mail e sem CC: em modo Auto aguarda intervencao manual; caso contrario, falha.
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
    alt.attach(MIMEText(f"NF-e {nota.numero} em anexo. Destinatário: {nota.dest_nome}", "plain", "utf-8"))
    alt.attach(MIMEText(corpo_html, "html", "utf-8"))
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

    if envio_assincrono:
        thread = threading.Thread(
            target=_send_async,
            args=(app, msg, smtp_server, smtp_port, sender, password, log.id),
            daemon=True,
        )
        thread.start()
    else:
        _send_async(app, msg, smtp_server, smtp_port, sender, password, log.id)
        db.session.refresh(log)

    return {
        "sucesso": bool(log.status == "Enviado") if not envio_assincrono else True,
        "log_id": log.id,
        "numero_nf": nota.numero,
        "chave": nota.chave,
        "destinatario": destino_efetivo,
        "destinatario_real": destino_real,
        "modo_teste": modo_teste,
        "fonte_email": fonte,
        "anexou_xml": anexou_xml,
        "anexou_pdf": anexou_pdf,
        "status": log.status,
        "erro": log.erro_mensagem if log.status == "Falha" else None,
    }
