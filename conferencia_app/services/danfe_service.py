"""
DANFE – Documento Auxiliar da Nota Fiscal Eletrônica
Layout oficial NF-e 4.0 (Ajuste SINIEF 07/2005 e alterações).
Identidade visual Columbia Machine Brasil.
"""

import io
import os
import re
import textwrap
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Optional

import requests as _req
from reportlab.graphics.barcode import code128
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

try:
    from brazilfiscalreport.danfe import Danfe as _FiscalDanfe
    from brazilfiscalreport.danfe import DanfeConfig as _FiscalDanfeConfig
except Exception:  # pragma: no cover - optional dependency
    _FiscalDanfe = None
    _FiscalDanfeConfig = None

# ─── Design Tokens ────────────────────────────────────────────────────────────
CB   = colors.HexColor("#1e3a5f")   # Columbia Navy
CB2  = colors.HexColor("#2563eb")   # Columbia Blue accent
CLG  = colors.HexColor("#f1f5f9")   # Row alt background
CGR  = colors.HexColor("#64748b")   # Label gray
BDR  = colors.HexColor("#94a3b8")   # Border color
CW   = colors.white
CK   = colors.black
CBDR = colors.HexColor("#cbd5e1")   # light border

F   = "Helvetica"
FB  = "Helvetica-Bold"

PW, PH = A4          # 595.28 × 841.89 pt
LM  = 8 * mm         # left margin
RM  = PW - 8 * mm    # right boundary
TM  = PH - 8 * mm    # top boundary (pt from bottom)
BM  = 8 * mm         # bottom boundary
CWT = RM - LM        # total content width


# ─── Format Helpers ───────────────────────────────────────────────────────────
def _v(s):
    return str(s or "").strip()


def _fmt_cnpj(v):
    v = re.sub(r"\D", "", _v(v))
    if len(v) == 14:
        return f"{v[:2]}.{v[2:5]}.{v[5:8]}/{v[8:12]}-{v[12:]}"
    if len(v) == 11:
        return f"{v[:3]}.{v[3:6]}.{v[6:9]}-{v[9:]}"
    return v


def _fmt_cep(v):
    v = re.sub(r"\D", "", _v(v))
    return f"{v[:5]}-{v[5:]}" if len(v) == 8 else v


def _fmt_fone(v):
    v = re.sub(r"\D", "", _v(v))
    if len(v) == 11:
        return f"({v[:2]}) {v[2:7]}-{v[7:]}"
    if len(v) == 10:
        return f"({v[:2]}) {v[2:6]}-{v[6:]}"
    return v


def _fmt_date(v):
    if not v:
        return ""
    try:
        return datetime.strptime(_v(v)[:10], "%Y-%m-%d").strftime("%d/%m/%Y")
    except Exception:
        return _v(v)[:10]


def _fmt_time(v):
    if not v or "T" not in _v(v):
        return ""
    try:
        return _v(v).split("T")[1][:8]
    except Exception:
        return ""


def _fmt_num(v, dec=2):
    try:
        return f"{float(v):,.{dec}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return _v(v)


def _fmt_chave(v):
    v = re.sub(r"\D", "", _v(v))
    return " ".join(v[i : i + 4] for i in range(0, len(v), 4))


def _mod_frete(v):
    return {
        "0": "0 - Emitente", "1": "1 - Dest/Rem",
        "2": "2 - Terceiros", "3": "3 - Próprio/R",
        "4": "4 - Próprio/D", "9": "9 - Sem frete",
    }.get(_v(v), _v(v))


# ─── XML Parsing ──────────────────────────────────────────────────────────────
_NS = {"nfe": "http://www.portalfiscal.inf.br/nfe"}


def _strip_xpath_prefix(path: str) -> str:
    clean = re.sub(r"\{[^}]+\}", "", path).lstrip("./")
    return re.sub(r"\b\w+:", "", clean)


def _t(node, *paths):
    if node is None:
        return ""
    for path in paths:
        r = node.find(path, _NS)
        if r is not None and r.text:
            return r.text.strip()
        # try without namespace
        clean = _strip_xpath_prefix(path)
        r = node.find(f".//{clean}")
        if r is not None and r.text:
            return r.text.strip()
    return ""


def _find(node, path):
    if node is None:
        return None
    r = node.find(path, _NS)
    if r is not None:
        return r
    clean = _strip_xpath_prefix(path)
    return node.find(f".//{clean}")


def _findall(node, path):
    if node is None:
        return []
    r = node.findall(path, _NS)
    if r:
        return r
    clean = _strip_xpath_prefix(path)
    return node.findall(f".//{clean}")


def parse_nfe_xml(xml_bytes: bytes) -> dict:
    root = ET.fromstring(xml_bytes)

    # Find infNFe
    infNFe = None
    for elem in root.iter():
        if elem.tag.split("}")[-1] == "infNFe":
            infNFe = elem
            break
    if infNFe is None:
        raise ValueError("Elemento infNFe não encontrado no XML")

    # Protocol
    nProt = dhRecbto = ""
    for elem in root.iter():
        if elem.tag.split("}")[-1] == "infProt":
            for ch in elem:
                loc = ch.tag.split("}")[-1]
                if loc == "nProt" and ch.text:
                    nProt = ch.text.strip()
                elif loc == "dhRecbto" and ch.text:
                    dhRecbto = ch.text.strip()
            break

    ide        = _find(infNFe, "nfe:ide")
    emit       = _find(infNFe, "nfe:emit")
    dest       = _find(infNFe, "nfe:dest")
    total      = _find(infNFe, "nfe:total")
    transp     = _find(infNFe, "nfe:transp")
    cobr       = _find(infNFe, "nfe:cobr")
    infAdic    = _find(infNFe, "nfe:infAdic")
    icmsTot    = _find(total, "nfe:ICMSTot") if total else None
    enderEmit  = _find(emit, "nfe:enderEmit")
    enderDest  = _find(dest, "nfe:enderDest") if dest else None
    transporta = _find(transp, "nfe:transporta") if transp else None
    veicTransp = _find(transp, "nfe:veicTransp") if transp else None
    fat_node   = _find(cobr, "nfe:fat") if cobr else None

    chave = re.sub(r"\D", "", infNFe.get("Id", "").replace("NFe", ""))

    # Volumes
    vols = _findall(transp, "nfe:vol") if transp else []
    try:
        vol_qtd = sum(int(_t(v, "nfe:qVol") or "0") for v in vols)
    except Exception:
        vol_qtd = 0
    vol_esp   = ", ".join({_t(v, "nfe:esp")   for v in vols} - {""})
    vol_marca = ", ".join({_t(v, "nfe:marca") for v in vols} - {""})
    vol_nVol  = ", ".join(_t(v, "nfe:nVol") for v in vols if _t(v, "nfe:nVol"))
    try:
        vol_pesoL = sum(float(_t(v, "nfe:pesoL") or "0") for v in vols)
    except Exception:
        vol_pesoL = 0.0
    try:
        vol_pesoB = sum(float(_t(v, "nfe:pesoB") or "0") for v in vols)
    except Exception:
        vol_pesoB = 0.0

    # Duplicatas
    dups = []
    if cobr:
        for dup in _findall(cobr, "nfe:dup"):
            dups.append({
                "nDup": _t(dup, "nfe:nDup"),
                "dVenc": _fmt_date(_t(dup, "nfe:dVenc")),
                "vDup": _fmt_num(_t(dup, "nfe:vDup")),
            })

    # Items
    itens = []
    for det in _findall(infNFe, "nfe:det"):
        prod    = _find(det, "nfe:prod")
        imposto = _find(det, "nfe:imposto")
        CST = vBC = vICMS = pICMS = vIPI = pIPI = ""
        if imposto:
            icms_grp = _find(imposto, "nfe:ICMS")
            if icms_grp is not None:
                for sub in icms_grp:
                    _cst = (sub.find("{http://www.portalfiscal.inf.br/nfe}CST") or
                            sub.find("{http://www.portalfiscal.inf.br/nfe}CSOSN") or
                            sub.find("CST") or sub.find("CSOSN"))
                    if _cst is not None and _cst.text:
                        CST = _cst.text.strip()
                    for tag in ("vBC", "vICMS", "pICMS"):
                        el = (sub.find(f"{{http://www.portalfiscal.inf.br/nfe}}{tag}") or
                              sub.find(tag))
                        if el is not None and el.text:
                            if tag == "vBC":   vBC   = el.text.strip()
                            if tag == "vICMS": vICMS = el.text.strip()
                            if tag == "pICMS": pICMS = el.text.strip()
            ipi_grp = _find(imposto, "nfe:IPI")
            if ipi_grp is not None:
                for sub in ipi_grp:
                    for tag in ("vIPI", "pIPI"):
                        el = (sub.find(f"{{http://www.portalfiscal.inf.br/nfe}}{tag}") or
                              sub.find(tag))
                        if el is not None and el.text:
                            if tag == "vIPI": vIPI = el.text.strip()
                            if tag == "pIPI": pIPI = el.text.strip()

        itens.append({
            "nItem":  det.get("nItem", ""),
            "cProd":  _t(prod, "nfe:cProd"),
            "xProd":  _t(prod, "nfe:xProd"),
            "NCM":    _t(prod, "nfe:NCM"),
            "CFOP":   _t(prod, "nfe:CFOP"),
            "uCom":   _t(prod, "nfe:uCom"),
            "qCom":   _t(prod, "nfe:qCom"),
            "vUnCom": _t(prod, "nfe:vUnCom"),
            "vProd":  _t(prod, "nfe:vProd"),
            "CST":    CST,
            "vBC":    vBC,
            "vICMS":  vICMS,
            "pICMS":  pICMS,
            "vIPI":   vIPI,
            "pIPI":   pIPI,
        })

    return {
        "chave": chave,
        "nNF": _t(ide, "nfe:nNF"),
        "serie": _t(ide, "nfe:serie"),
        "dhEmi": _t(ide, "nfe:dhEmi"),
        "dhSaiEnt": _t(ide, "nfe:dhSaiEnt"),
        "tpNF": _t(ide, "nfe:tpNF"),
        "natOp": _t(ide, "nfe:natOp"),
        "nProt": nProt,
        "dhRecbto": dhRecbto,
        # Emitente
        "emit_nome":   _t(emit, "nfe:xNome"),
        "emit_fant":   _t(emit, "nfe:xFant"),
        "emit_cnpj":   _fmt_cnpj(_t(emit, "nfe:CNPJ")),
        "emit_ie":     _t(emit, "nfe:IE"),
        "emit_iest":   _t(emit, "nfe:IEST"),
        "emit_logr":   _t(enderEmit, "nfe:xLgr"),
        "emit_nro":    _t(enderEmit, "nfe:nro"),
        "emit_cpl":    _t(enderEmit, "nfe:xCpl"),
        "emit_bairro": _t(enderEmit, "nfe:xBairro"),
        "emit_mun":    _t(enderEmit, "nfe:xMun"),
        "emit_uf":     _t(enderEmit, "nfe:UF"),
        "emit_cep":    _fmt_cep(_t(enderEmit, "nfe:CEP")),
        "emit_fone":   _fmt_fone(_t(enderEmit, "nfe:fone")),
        # Destinatário
        "dest_nome":   _t(dest, "nfe:xNome"),
        "dest_cnpj":   _fmt_cnpj(_t(dest, "nfe:CNPJ") or _t(dest, "nfe:CPF")),
        "dest_ie":     _t(dest, "nfe:IE"),
        "dest_logr":   _t(enderDest, "nfe:xLgr"),
        "dest_nro":    _t(enderDest, "nfe:nro"),
        "dest_cpl":    _t(enderDest, "nfe:xCpl"),
        "dest_bairro": _t(enderDest, "nfe:xBairro"),
        "dest_mun":    _t(enderDest, "nfe:xMun"),
        "dest_uf":     _t(enderDest, "nfe:UF"),
        "dest_cep":    _fmt_cep(_t(enderDest, "nfe:CEP")),
        "dest_fone":   _fmt_fone(_t(enderDest, "nfe:fone")),
        # Totais
        "vBC":    _t(icmsTot, "nfe:vBC"),
        "vICMS":  _t(icmsTot, "nfe:vICMS"),
        "vBCST":  _t(icmsTot, "nfe:vBCST"),
        "vST":    _t(icmsTot, "nfe:vST"),
        "vProd":  _t(icmsTot, "nfe:vProd"),
        "vFrete": _t(icmsTot, "nfe:vFrete"),
        "vSeg":   _t(icmsTot, "nfe:vSeg"),
        "vDesc":  _t(icmsTot, "nfe:vDesc"),
        "vOutro": _t(icmsTot, "nfe:vOutro"),
        "vIPI":   _t(icmsTot, "nfe:vIPI"),
        "vNF":    _t(icmsTot, "nfe:vNF"),
        "vTotTrib": _t(icmsTot, "nfe:vTotTrib"),
        # Transportador
        "transp_modFrete": _t(transp, "nfe:modFrete") if transp else "",
        "transp_nome":     _t(transporta, "nfe:xNome"),
        "transp_cnpj":     _fmt_cnpj(_t(transporta, "nfe:CNPJ")),
        "transp_ie":       _t(transporta, "nfe:IE"),
        "transp_ender":    _t(transporta, "nfe:xEnder"),
        "transp_mun":      _t(transporta, "nfe:xMun"),
        "transp_uf":       _t(transporta, "nfe:UF"),
        "transp_placa":    _t(veicTransp, "nfe:placa"),
        "transp_uf_veic":  _t(veicTransp, "nfe:UF"),
        "transp_rntc":     _t(veicTransp, "nfe:RNTC"),
        "vol_qtd":   str(vol_qtd) if vol_qtd else "",
        "vol_esp":   vol_esp,
        "vol_marca": vol_marca,
        "vol_nVol":  vol_nVol,
        "vol_pesoL": f"{vol_pesoL:.3f}" if vol_pesoL else "",
        "vol_pesoB": f"{vol_pesoB:.3f}" if vol_pesoB else "",
        # Fatura
        "fat_nFat":  _t(fat_node, "nfe:nFat"),
        "fat_vOrig": _t(fat_node, "nfe:vOrig"),
        "fat_vDesc": _t(fat_node, "nfe:vDesc"),
        "fat_vLiq":  _t(fat_node, "nfe:vLiq"),
        "duplicatas": dups,
        # Dados adicionais
        "infCpl":   _t(infAdic, "nfe:infCpl"),
        "infFisco": _t(infAdic, "nfe:infAdFisco"),
        "itens": itens,
    }


# ─── Drawing Helpers ──────────────────────────────────────────────────────────

def _rect(c, x, y, w, h, fill=None, stroke=None, lw=0.3):
    c.saveState()
    if fill:
        c.setFillColor(fill)
    if stroke:
        c.setStrokeColor(stroke)
        c.setLineWidth(lw)
    c.rect(x, y, w, h, fill=1 if fill else 0, stroke=1 if stroke else 0)
    c.restoreState()


def _cell(c, x, y, w, h, label, value, lsz=5.5, vsz=7.5, bold=False,
          bg=None, align="left", border=True, max_lines=1):
    """Labeled data cell: small gray label top-left, value bottom."""
    c.saveState()
    if bg:
        c.setFillColor(bg)
        c.rect(x, y, w, h, fill=1, stroke=0)
    if border:
        c.setStrokeColor(BDR)
        c.setLineWidth(0.3)
        c.rect(x, y, w, h, fill=0, stroke=1)
    pad = 1.5 * mm
    # Label
    c.setFillColor(CGR)
    c.setFont(F, lsz)
    c.drawString(x + pad, y + h - lsz * 0.4 * mm - pad * 0.6, label.upper())
    # Value
    c.setFillColor(CK)
    c.setFont(FB if bold else F, vsz)
    val = _v(value)
    max_chars = max(1, int((w - 2 * pad) / (vsz * 0.55 * mm)))
    if max_lines <= 1:
        val = val if len(val) <= max_chars else val[: max_chars - 1] + "…"
        if align == "right":
            c.drawRightString(x + w - pad, y + pad * 0.7, val)
        elif align == "center":
            c.drawCentredString(x + w / 2, y + pad * 0.7, val)
        else:
            c.drawString(x + pad, y + pad * 0.7, val)
    else:
        # Wrap in 2+ lines when critical fields are long (natureza, razao social, endereco).
        lines = textwrap.wrap(val, max_chars) or [""]
        lines = lines[:max_lines]
        line_h = 3.1 * mm
        start_y = y + pad * 0.7 + (len(lines) - 1) * line_h
        for i, line in enumerate(reversed(lines)):
            c.drawString(x + pad, start_y - i * line_h, line)
    c.restoreState()


def _sec(c, x, y, w, h, title):
    """Dark navy section header bar."""
    _rect(c, x, y, w, h, fill=CB)
    c.saveState()
    c.setFillColor(CW)
    c.setFont(FB, 6.5)
    c.drawString(x + 2 * mm, y + (h - 6.5 * 0.35 * mm) / 2, title.upper())
    c.restoreState()


def _line(c, x1, y1, x2, y2, color=BDR, lw=0.3):
    c.saveState()
    c.setStrokeColor(color)
    c.setLineWidth(lw)
    c.line(x1, y1, x2, y2)
    c.restoreState()


def _multiline(c, x, y, w, h, text, font=F, size=6.5, line_h=None, pad=1.5):
    """Draw wrapped text inside a box."""
    if not text:
        return
    pad_pt = pad * mm
    line_h = line_h or (size * 0.4 * mm + 1.5 * mm)
    chars_per_line = max(1, int((w - 2 * pad_pt) / (size * 0.55 * mm)))
    lines = []
    for para in text.split("\n"):
        lines.extend(textwrap.wrap(para, chars_per_line) or [""])
    max_lines = max(1, int((h - 2 * pad_pt) / line_h))
    c.saveState()
    c.setFont(font, size)
    c.setFillColor(CK)
    cur_y = y + h - pad_pt - size * 0.35 * mm
    for line in lines[:max_lines]:
        if cur_y < y + pad_pt:
            break
        c.drawString(x + pad_pt, cur_y, line)
        cur_y -= line_h
    c.restoreState()


# ─── Barcode ─────────────────────────────────────────────────────────────────

def _draw_barcode(c, chave, x, y, w, h):
    """Draw Code 128 barcode for chave de acesso."""
    if not chave or len(chave) != 44:
        return
    try:
        bc = code128.Code128(chave, barHeight=h * 0.6, barWidth=0.72, humanReadable=False)
        bc_w = bc.width
        # Center in the available width
        bc_x = x + (w - bc_w) / 2
        bc_y = y + h * 0.2
        bc.drawOn(c, bc_x, bc_y)
    except Exception:
        pass


# ─── Section Drawing Functions ────────────────────────────────────────────────

def _draw_canhoto(c, d, x, y, w, h):
    """Canhoto strip at top of first page."""
    _rect(c, x, y, w, h, fill=colors.HexColor("#eff6ff"), stroke=BDR, lw=0.5)
    # Dashed top border
    c.saveState()
    c.setStrokeColor(CB2)
    c.setLineWidth(0.8)
    c.setDash([3, 3])
    c.line(x, y + h, x + w, y + h)
    c.restoreState()
    # Text
    c.saveState()
    c.setFont(FB, 6.5)
    c.setFillColor(CB)
    c.drawString(x + 2 * mm, y + h - 5 * mm, "RECEBEMOS DE")
    c.setFont(FB, 8)
    emit = _v(d.get("emit_fant") or d.get("emit_nome"))
    c.drawString(x + 28 * mm, y + h - 5 * mm, emit)
    c.setFont(F, 6.5)
    c.setFillColor(CK)
    c.drawString(x + 2 * mm, y + h - 9 * mm,
                 "OS PRODUTOS/SERVIÇOS CONSTANTES NA NOTA FISCAL INDICADA AO LADO")
    # Right side: NF info
    nf_txt = f"NF-e   Nº: {int(_v(d.get('nNF')) or 0):09,}".replace(",", ".")
    c.setFont(FB, 8)
    c.setFillColor(CB)
    c.drawRightString(x + w - 2 * mm, y + h - 5 * mm, nf_txt)
    c.setFont(F, 6.5)
    c.setFillColor(CK)
    c.drawRightString(x + w - 2 * mm, y + h - 9 * mm,
                      f"SÉRIE: {_v(d.get('serie'))}")
    # Signature line
    sig_x = x + w * 0.35
    c.setFont(F, 5.5)
    c.setFillColor(CGR)
    c.drawString(x + 2 * mm, y + 4 * mm, "DATA DE RECEBIMENTO")
    c.drawString(x + 2 * mm, y + 1.5 * mm, "____/____/____")
    c.drawString(sig_x, y + 4 * mm, "IDENTIFICAÇÃO E ASSINATURA DO RECEBEDOR")
    _line(c, sig_x, y + 3 * mm, x + w - 2 * mm, y + 3 * mm)
    c.restoreState()


def _draw_header(c, d, logo_img, x, y, w, h):
    """Main header block: logo | emitente info | DANFE title | barcode | NF info."""
    _rect(c, x, y, w, h, stroke=BDR, lw=0.5)

    LOGO_W = 38 * mm
    NF_W   = 40 * mm
    MID_W  = w - LOGO_W - NF_W

    # ── Logo column ──
    logo_col_x = x
    logo_col_y = y
    logo_col_w = LOGO_W
    _rect(c, logo_col_x, logo_col_y, logo_col_w, h, stroke=BDR, lw=0.3)

    # Try to draw logo image
    if logo_img:
        try:
            iw, ih = logo_img.getSize()
            if iw and ih:
                box_w = logo_col_w - 4 * mm
                box_h = h * 0.45
                scale = min(box_w / float(iw), box_h / float(ih))
                dw = float(iw) * scale
                dh = float(ih) * scale
                dx = logo_col_x + (logo_col_w - dw) / 2
                dy = logo_col_y + h * 0.52 + (box_h - dh) / 2
                c.drawImage(logo_img,
                            dx,
                            dy,
                            width=dw,
                            height=dh,
                            preserveAspectRatio=True,
                            mask="auto")
        except Exception:
            pass

    # Emitente name + address in logo column
    c.saveState()
    emit_name = _v(d.get("emit_fant") or d.get("emit_nome"))
    c.setFont(FB, 8)
    c.setFillColor(CB)
    # Wrap name
    chars = max(1, int((logo_col_w - 4 * mm) / (8 * 0.55 * mm)))
    lines = textwrap.wrap(emit_name, chars) or [""]
    text_y = logo_col_y + h * 0.48
    for ln in lines[:2]:
        c.drawCentredString(logo_col_x + logo_col_w / 2, text_y, ln)
        text_y -= 3.5 * mm

    c.setFont(F, 5.5)
    c.setFillColor(CK)
    addr_parts = [
        f"{_v(d.get('emit_logr'))}, {_v(d.get('emit_nro'))}",
        _v(d.get("emit_bairro")),
        f"{_v(d.get('emit_mun'))} – {_v(d.get('emit_uf'))}  CEP: {_v(d.get('emit_cep'))}",
        f"FONE: {_v(d.get('emit_fone'))}",
    ]
    text_y -= 0.5 * mm
    for part in addr_parts:
        if part.strip(" –,") and text_y > logo_col_y + 1.5 * mm:
            c.drawCentredString(logo_col_x + logo_col_w / 2, text_y, part[:40])
            text_y -= 3 * mm
    c.restoreState()

    # ── Center column: DANFE title + barcode ──
    mid_x = x + LOGO_W
    mid_y = y
    _rect(c, mid_x, mid_y, MID_W, h, stroke=BDR, lw=0.3)

    c.saveState()
    c.setFont(FB, 11)
    c.setFillColor(CB)
    c.drawCentredString(mid_x + MID_W / 2, mid_y + h - 7 * mm, "DANFE")
    c.setFont(F, 6.5)
    c.setFillColor(CK)
    c.drawCentredString(mid_x + MID_W / 2, mid_y + h - 11 * mm,
                        "DOCUMENTO AUXILIAR DA NOTA FISCAL ELETRÔNICA")

    # Tipo (entrada/saída box)
    tipo_x = mid_x + MID_W / 2 - 12 * mm
    tipo_y = mid_y + h - 20 * mm
    tipo_w = 24 * mm
    tipo_h = 9 * mm
    _rect(c, tipo_x, tipo_y, tipo_w, tipo_h, stroke=BDR, lw=0.5)
    tpNF = _v(d.get("tpNF"))
    c.setFont(F, 6)
    c.setFillColor(CGR)
    c.drawString(tipo_x + 2 * mm, tipo_y + tipo_h - 3.5 * mm, "0 - ENTRADA")
    c.drawString(tipo_x + 2 * mm, tipo_y + 1.5 * mm, "1 - SAÍDA")
    # Box for selected
    sel_x = tipo_x + tipo_w - 5 * mm
    sel_y = tipo_y + (tipo_h / 2 - 2.5 * mm) if tpNF == "0" else tipo_y + 1 * mm
    _rect(c, sel_x, sel_y, 4 * mm, 4 * mm, stroke=CB, lw=1.0)
    c.setFont(FB, 8)
    c.setFillColor(CB)
    c.drawCentredString(sel_x + 2 * mm, sel_y + 1 * mm, tpNF or "")
    c.restoreState()

    # Nº / Série / Folha in center column
    c.saveState()
    c.setFont(F, 6.5)
    c.setFillColor(CGR)
    c.drawCentredString(mid_x + MID_W / 2, mid_y + h - 26 * mm, "Nº / SÉRIE / FOLHA")
    c.setFont(FB, 8)
    c.setFillColor(CK)
    nf_num = f"{int(_v(d.get('nNF')) or 0):09,}".replace(",", ".")
    c.drawCentredString(mid_x + MID_W / 2, mid_y + h - 30 * mm,
                        f"{nf_num}  /  {_v(d.get('serie'))}  /  1")

    # Barcode
    barcode_h = h * 0.28
    _draw_barcode(c, _v(d.get("chave")), mid_x + 2 * mm, mid_y + 1.5 * mm,
                  MID_W - 4 * mm, barcode_h)
    c.restoreState()

    # ── Right column: chave + consulta + protocolo ──
    nf_col_x = x + LOGO_W + MID_W
    nf_col_y = y
    _rect(c, nf_col_x, nf_col_y, NF_W, h, stroke=BDR, lw=0.3)

    c.saveState()
    c.setFont(F, 5.5)
    c.setFillColor(CGR)
    c.drawCentredString(nf_col_x + NF_W / 2, nf_col_y + h - 4 * mm, "CHAVE DE ACESSO")
    c.setFont(F, 5.8)
    c.setFillColor(CK)
    chave_fmt = _fmt_chave(_v(d.get("chave")))
    # Split chave into 2 lines of ~22 chars
    parts = chave_fmt.split()
    line1 = " ".join(parts[:6])
    line2 = " ".join(parts[6:])
    c.drawCentredString(nf_col_x + NF_W / 2, nf_col_y + h - 8 * mm, line1)
    c.drawCentredString(nf_col_x + NF_W / 2, nf_col_y + h - 11 * mm, line2)

    c.setFont(F, 5.5)
    c.setFillColor(CGR)
    c.drawCentredString(nf_col_x + NF_W / 2, nf_col_y + h - 16 * mm,
                        "Consulta de autenticidade:")
    c.setFont(F, 5.5)
    c.setFillColor(CB2)
    c.drawCentredString(nf_col_x + NF_W / 2, nf_col_y + h - 19 * mm,
                        "www.nfe.fazenda.gov.br/portal")

    # Protocolo
    nProt = _v(d.get("nProt"))
    dhRecbto = _v(d.get("dhRecbto"))
    proto_str = f"{nProt} – {_fmt_date(dhRecbto)} {_fmt_time(dhRecbto)}" if nProt else ""
    c.setFont(F, 5.5)
    c.setFillColor(CGR)
    c.drawCentredString(nf_col_x + NF_W / 2, nf_col_y + h - 24 * mm,
                        "PROTOCOLO DE AUTORIZAÇÃO")
    c.setFont(F, 5.8)
    c.setFillColor(CK)
    c.drawCentredString(nf_col_x + NF_W / 2, nf_col_y + h - 28 * mm, proto_str[:40])
    c.restoreState()


def _draw_nat_ie_cnpj(c, d, x, y, w, h):
    """Row: Natureza da Operação | IE Emitente | IE Sub. Tributária | CNPJ."""
    w1 = w * 0.42
    w2 = w * 0.18
    w3 = w * 0.13
    w4 = w - w1 - w2 - w3
    _cell(c, x,          y, w1, h, "NATUREZA DA OPERAÇÃO", _v(d.get("natOp")), max_lines=2)
    _cell(c, x + w1,     y, w2, h, "INSCRIÇÃO ESTADUAL",   _v(d.get("emit_ie")))
    _cell(c, x + w1 + w2, y, w3, h, "IE SUB. TRIBUTÁRIA",  _v(d.get("emit_iest")))
    _cell(c, x + w1 + w2 + w3, y, w4, h, "CNPJ",           _v(d.get("emit_cnpj")),
          bold=True)


def _draw_dest(c, d, x, y, w, row_h):
    """Destinatário block: 3 rows."""
    total_h = row_h * 3
    _sec(c, x, y + total_h - 5 * mm, w, 5 * mm, "DESTINATÁRIO / REMETENTE")

    # Row 1: nome | cnpj | data emissão
    r1y = y + row_h * 2
    w1  = w * 0.60
    w2  = w * 0.25
    w3  = w - w1 - w2
    _cell(c, x,       r1y, w1, row_h, "NOME / RAZÃO SOCIAL",   _v(d.get("dest_nome")), max_lines=2)
    _cell(c, x + w1,  r1y, w2, row_h, "CNPJ / CPF",             _v(d.get("dest_cnpj")), bold=True)
    _cell(c, x + w1 + w2, r1y, w3, row_h, "DATA DE EMISSÃO",   _fmt_date(_v(d.get("dhEmi"))))

    # Row 2: endereço | bairro | CEP | data saída
    r2y = y + row_h
    logr = f"{_v(d.get('dest_logr'))}, {_v(d.get('dest_nro'))}"
    if _v(d.get("dest_cpl")):
        logr += f" – {_v(d.get('dest_cpl'))}"
    w2a = w * 0.42
    w2b = w * 0.20
    w2c = w * 0.18
    w2d = w - w2a - w2b - w2c
    _cell(c, x,              r2y, w2a, row_h, "ENDEREÇO",        logr, max_lines=2)
    _cell(c, x + w2a,        r2y, w2b, row_h, "BAIRRO / DIST.",  _v(d.get("dest_bairro")))
    _cell(c, x + w2a + w2b,  r2y, w2c, row_h, "CEP",             _v(d.get("dest_cep")))
    _cell(c, x + w2a + w2b + w2c, r2y, w2d, row_h, "DATA SAÍDA/ENTRADA",
          _fmt_date(_v(d.get("dhSaiEnt"))))

    # Row 3: município | fone | UF | IE | hora saída
    r3y = y
    w3a = w * 0.30
    w3b = w * 0.18
    w3c = w * 0.06
    w3d = w * 0.28
    w3e = w - w3a - w3b - w3c - w3d
    _cell(c, x,               r3y, w3a, row_h, "MUNICÍPIO",          _v(d.get("dest_mun")))
    _cell(c, x + w3a,         r3y, w3b, row_h, "FONE / FAX",         _v(d.get("dest_fone")))
    _cell(c, x + w3a + w3b,   r3y, w3c, row_h, "UF",                 _v(d.get("dest_uf")))
    _cell(c, x + w3a + w3b + w3c, r3y, w3d, row_h, "INSCRIÇÃO ESTADUAL", _v(d.get("dest_ie")))
    _cell(c, x + w3a + w3b + w3c + w3d, r3y, w3e, row_h, "HORA SAÍDA",
          _fmt_time(_v(d.get("dhSaiEnt"))))

    return total_h


def _draw_fatura(c, d, x, y, w, h):
    """Fatura / Duplicatas (opcional)."""
    _sec(c, x, y + h - 5 * mm, w, 5 * mm, "FATURA")
    row_h = h - 5 * mm
    dups  = d.get("duplicatas") or []
    if not dups:
        _cell(c, x, y, w, row_h, "", "Sem duplicatas.")
        return
    # Draw up to N duplicatas
    ncols = min(len(dups), 8)
    col_w = w / ncols
    for i, dup in enumerate(dups[:ncols]):
        cx = x + i * col_w
        # Split the cell vertically: nDup / dVenc / vDup
        sub_h = row_h / 3
        _cell(c, cx, y + 2 * sub_h, col_w, sub_h, "Nº", _v(dup.get("nDup")), vsz=6.5)
        _cell(c, cx, y + sub_h,     col_w, sub_h, "VENCIMENTO", _v(dup.get("dVenc")), vsz=6.5)
        _cell(c, cx, y,             col_w, sub_h, "VALOR", _v(dup.get("vDup")), vsz=6.5, align="right")


def _draw_calc_imposto(c, d, x, y, w, row_h):
    """Cálculo do Imposto: 2 rows."""
    total_h = 5 * mm + row_h * 2
    _sec(c, x, y + total_h - 5 * mm, w, 5 * mm, "CÁLCULO DO IMPOSTO")

    # Row 1
    r1y = y + row_h
    cols1 = [
        ("BASE DE CÁLCULO DO ICMS",         w * 0.14, _fmt_num(_v(d.get("vBC")))),
        ("VALOR DO ICMS",                    w * 0.12, _fmt_num(_v(d.get("vICMS")))),
        ("BASE CÁLCULO ICMS ST",             w * 0.14, _fmt_num(_v(d.get("vBCST")))),
        ("VALOR ICMS ST",                    w * 0.12, _fmt_num(_v(d.get("vST")))),
        ("VALOR TOTAL DOS PRODUTOS",         w * 0.24, _fmt_num(_v(d.get("vProd")))),
        ("VALOR APROX. TRIBUTOS",            w - w * 0.14 - w * 0.12 - w * 0.14 - w * 0.12 - w * 0.24,
         _fmt_num(_v(d.get("vTotTrib")))),
    ]
    cx = x
    for label, cw, val in cols1:
        _cell(c, cx, r1y, cw, row_h, label, val, align="right")
        cx += cw

    # Row 2
    r2y = y
    w_each = w / 6
    cols2 = [
        ("VALOR DO FRETE",             w_each, _fmt_num(_v(d.get("vFrete")))),
        ("VALOR DO SEGURO",            w_each, _fmt_num(_v(d.get("vSeg")))),
        ("DESCONTO",                   w_each, _fmt_num(_v(d.get("vDesc")))),
        ("OUTRAS DESPESAS ACESSÓRIAS", w_each, _fmt_num(_v(d.get("vOutro")))),
        ("VALOR DO IPI",               w_each, _fmt_num(_v(d.get("vIPI")))),
        ("VALOR TOTAL DA NOTA",        w_each, _fmt_num(_v(d.get("vNF")))),
    ]
    cx = x
    for label, cw, val in cols2:
        _cell(c, cx, r2y, cw, row_h, label, val,
              align="right",
              bold=(label == "VALOR TOTAL DA NOTA"),
              bg=colors.HexColor("#eff6ff") if label == "VALOR TOTAL DA NOTA" else None)
        cx += cw

    return total_h


def _draw_transp(c, d, x, y, w, row_h):
    """Transportador block: 2 rows + volumes row."""
    total_h = 5 * mm + row_h * 3
    _sec(c, x, y + total_h - 5 * mm, w, 5 * mm, "TRANSPORTADOR / VOLUMES TRANSPORTADOS")

    # Row 1: nome | frete | CNPJ | IE
    r1y = y + row_h * 2
    w1a = w * 0.36
    w1b = w * 0.18
    w1c = w * 0.25
    w1d = w - w1a - w1b - w1c
    _cell(c, x,          r1y, w1a, row_h, "RAZÃO SOCIAL",    _v(d.get("transp_nome")), max_lines=2)
    _cell(c, x + w1a,    r1y, w1b, row_h, "FRETE POR CONTA", _mod_frete(_v(d.get("transp_modFrete"))))
    _cell(c, x + w1a + w1b, r1y, w1c, row_h, "CNPJ / CPF",   _v(d.get("transp_cnpj")))
    _cell(c, x + w1a + w1b + w1c, r1y, w1d, row_h, "INSCRIÇÃO ESTADUAL", _v(d.get("transp_ie")))

    # Row 2: endereço | município | UF | placa | UF veíc | ANTT
    r2y = y + row_h
    w2a = w * 0.40
    w2b = w * 0.22
    w2c = w * 0.06
    w2d = w * 0.13
    w2e = w * 0.08
    w2f = w - w2a - w2b - w2c - w2d - w2e
    _cell(c, x,                         r2y, w2a, row_h, "ENDEREÇO",     _v(d.get("transp_ender")), max_lines=2)
    _cell(c, x + w2a,                   r2y, w2b, row_h, "MUNICÍPIO",    _v(d.get("transp_mun")))
    _cell(c, x + w2a + w2b,             r2y, w2c, row_h, "UF",           _v(d.get("transp_uf")))
    _cell(c, x + w2a + w2b + w2c,       r2y, w2d, row_h, "PLACA DO VEÍC.", _v(d.get("transp_placa")))
    _cell(c, x + w2a + w2b + w2c + w2d, r2y, w2e, row_h, "UF",          _v(d.get("transp_uf_veic")))
    _cell(c, x + w2a + w2b + w2c + w2d + w2e, r2y, w2f, row_h, "ANTT/RNTC", _v(d.get("transp_rntc")))

    # Row 3: volumes
    r3y = y
    w3a = w * 0.10
    w3b = w * 0.14
    w3c = w * 0.18
    w3d = w * 0.18
    w3e = w * 0.20
    w3f = w - w3a - w3b - w3c - w3d - w3e
    _cell(c, x,                           r3y, w3a, row_h, "QUANTIDADE",    _v(d.get("vol_qtd")))
    _cell(c, x + w3a,                     r3y, w3b, row_h, "ESPÉCIE",       _v(d.get("vol_esp")))
    _cell(c, x + w3a + w3b,               r3y, w3c, row_h, "MARCA",         _v(d.get("vol_marca")))
    _cell(c, x + w3a + w3b + w3c,         r3y, w3d, row_h, "NUMERAÇÃO",     _v(d.get("vol_nVol")))
    _cell(c, x + w3a + w3b + w3c + w3d,   r3y, w3e, row_h, "PESO BRUTO",   _v(d.get("vol_pesoB")), align="right")
    _cell(c, x + w3a + w3b + w3c + w3d + w3e, r3y, w3f, row_h, "PESO LÍQUIDO",
          _v(d.get("vol_pesoL")), align="right")

    return total_h


# Items table column definitions (label, fraction of width, align)
_ITEM_COLS = [
    ("CÓD. PROD.",      0.070, "left"),
    ("DESCRIÇÃO",       0.270, "left"),
    ("NCM / SH",        0.065, "center"),
    ("CST",             0.035, "center"),
    ("CFOP",            0.040, "center"),
    ("UNID.",           0.040, "center"),
    ("QTDE.",           0.065, "right"),
    ("VLR. UNIT.",      0.085, "right"),
    ("VLR. TOTAL",      0.085, "right"),
    ("B.C. ICMS",       0.065, "right"),
    ("VLR. ICMS",       0.065, "right"),
    ("VLR. IPI",        0.065, "right"),
    ("ALÍQ. ICMS",      0.040, "right"),
    ("ALÍQ. IPI",       0.040, "right"),
]


def _item_col_widths(w):
    cols = []
    used = 0.0
    for i, (label, frac, align) in enumerate(_ITEM_COLS):
        if i == len(_ITEM_COLS) - 1:
            cw = w - used
        else:
            cw = w * frac
            used += cw
        cols.append((label, cw, align))
    return cols


def _item_values(item):
    return [
        _v(item.get("cProd")),
        _v(item.get("xProd")),
        _v(item.get("NCM")),
        _v(item.get("CST")),
        _v(item.get("CFOP")),
        _v(item.get("uCom")),
        _fmt_num(_v(item.get("qCom")), 4),
        _fmt_num(_v(item.get("vUnCom")), 4),
        _fmt_num(_v(item.get("vProd"))),
        _fmt_num(_v(item.get("vBC"))),
        _fmt_num(_v(item.get("vICMS"))),
        _fmt_num(_v(item.get("vIPI"))),
        _fmt_num(_v(item.get("pICMS"))),
        _fmt_num(_v(item.get("pIPI"))),
    ]


def _draw_items_header(c, x, y, w, col_defs, sec_h=5 * mm, hdr_h=7 * mm):
    """Draw DADOS DO PRODUTO/SERVIÇO section header + column headers."""
    _sec(c, x, y + hdr_h, w, sec_h, "DADOS DO PRODUTO / SERVIÇO")
    # Column headers
    _rect(c, x, y, w, hdr_h, fill=colors.HexColor("#1e3a5f"))
    c.saveState()
    c.setFillColor(CW)
    c.setFont(FB, 5.0)
    cx = x
    for label, cw, align in col_defs:
        if align == "right":
            c.drawRightString(cx + cw - 1 * mm, y + 1.8 * mm, label)
        elif align == "center":
            c.drawCentredString(cx + cw / 2, y + 1.8 * mm, label)
        else:
            c.drawString(cx + 1 * mm, y + 1.8 * mm, label)
        # Separator
        c.setStrokeColor(colors.HexColor("#3b5f8a"))
        c.setLineWidth(0.3)
        c.line(cx + cw, y, cx + cw, y + hdr_h)
        cx += cw
    c.restoreState()
    return sec_h + hdr_h


def _draw_one_item(c, x, y, w, col_defs, item_data, alt=False, row_h=8 * mm):
    """Draw one product row. Returns actual row height."""
    # Estimate description lines (wrapped)
    desc_col_w = col_defs[1][1]
    desc_text   = item_data[1]
    chars_per   = max(1, int((desc_col_w - 2 * mm) / (6.5 * 0.55 * mm)))
    desc_lines  = textwrap.wrap(desc_text, chars_per) or [""]
    actual_h    = max(row_h, len(desc_lines) * 3.8 * mm + 2 * mm)

    if alt:
        _rect(c, x, y, w, actual_h, fill=CLG)
    _rect(c, x, y, w, actual_h, stroke=CBDR, lw=0.25)

    c.saveState()
    cx = x
    for i, (label, cw, align) in enumerate(col_defs):
        val = item_data[i] if i < len(item_data) else ""
        c.setFont(F, 6.5)
        c.setFillColor(CK)
        # Vertical separator
        c.setStrokeColor(CBDR)
        c.setLineWidth(0.25)
        c.line(cx + cw, y, cx + cw, y + actual_h)

        if i == 1:  # Description: multi-line
            text_y = y + actual_h - 3.8 * mm
            for ln in desc_lines:
                if text_y > y + 0.5 * mm:
                    c.drawString(cx + 1 * mm, text_y, ln)
                    text_y -= 3.8 * mm
        else:
            mid_y = y + (actual_h - 6.5 * 0.35 * mm) / 2
            if align == "right":
                c.drawRightString(cx + cw - 1 * mm, mid_y, val)
            elif align == "center":
                c.drawCentredString(cx + cw / 2, mid_y, val)
            else:
                max_c = max(1, int((cw - 2 * mm) / (6.5 * 0.55 * mm)))
                c.drawString(cx + 1 * mm, mid_y, val[:max_c])
        cx += cw
    c.restoreState()
    return actual_h


def _draw_dados_adicionais(c, d, x, y, w, h):
    """Dados Adicionais block."""
    total_h = 5 * mm + h
    _sec(c, x, y + total_h - 5 * mm, w, 5 * mm, "DADOS ADICIONAIS")
    mid = w * 0.72
    # Informações complementares
    _rect(c, x, y, mid, h, stroke=BDR, lw=0.3)
    c.saveState()
    c.setFont(F, 5.5)
    c.setFillColor(CGR)
    c.drawString(x + 1.5 * mm, y + h - 4 * mm, "INFORMAÇÕES COMPLEMENTARES")
    c.restoreState()
    _multiline(c, x, y, mid, h - 5 * mm, _v(d.get("infCpl")), size=6.5)
    # Reservado ao fisco
    _rect(c, x + mid, y, w - mid, h, stroke=BDR, lw=0.3)
    c.saveState()
    c.setFont(F, 5.5)
    c.setFillColor(CGR)
    c.drawString(x + mid + 1.5 * mm, y + h - 4 * mm, "RESERVADO AO FISCO")
    c.restoreState()
    _multiline(c, x + mid, y, w - mid, h - 5 * mm, _v(d.get("infFisco")), size=6.5)
    return total_h


# ─── Page Rendering ──────────────────────────────────────────────────────────

def _draw_page_border(c):
    c.saveState()
    c.setStrokeColor(BDR)
    c.setLineWidth(0.5)
    c.rect(LM - 1 * mm, BM - 1 * mm, CWT + 2 * mm, PH - BM - (PH - TM) + 2 * mm, fill=0, stroke=1)
    c.restoreState()


def _gerar_danfe_manual(xml_bytes: bytes,
                        logo_path: Optional[str] = None,
                        logo_url:  Optional[str] = None) -> bytes:
    """Gera DANFE em PDF a partir de bytes do XML NF-e. Retorna bytes do PDF."""
    d   = parse_nfe_xml(xml_bytes)
    buf = io.BytesIO()
    cv  = canvas.Canvas(buf, pagesize=A4)
    cv.setTitle(f"DANFE NF-e {_v(d.get('nNF'))}")
    cv.setAuthor("Columbia Machine Brasil")
    cv.setSubject("Documento Auxiliar da Nota Fiscal Eletrônica")

    # Load logo
    logo_img = None
    logo_candidates = []
    if logo_path:
        logo_candidates.append(logo_path)
    logo_candidates.append(os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "static", "columbia_logo.png")))
    logo_candidates.append(os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "static", "logo.png")))
    for candidate in logo_candidates:
        if candidate and os.path.isfile(candidate):
            try:
                logo_img = ImageReader(candidate)
                break
            except Exception:
                continue
    if logo_img is None and logo_url:
        try:
            resp = _req.get(logo_url, timeout=6)
            if resp.ok:
                logo_img = ImageReader(io.BytesIO(resp.content))
        except Exception:
            pass

    # ── Layout constants ──
    x   = LM
    w   = CWT
    ROW = 11 * mm   # standard cell row height
    DUP_H = 12 * mm  # fatura height (body only)

    # Fixed section heights
    canhoto_h     = 19 * mm
    header_h      = 48 * mm
    nat_h         = ROW
    dest_h        = ROW * 3 + 5 * mm  # 3 rows + section header
    calc_h        = ROW * 2 + 5 * mm
    transp_h      = ROW * 3 + 5 * mm
    items_hdr_h   = 5 * mm + 7 * mm   # section + col headers
    dados_adic_h  = 22 * mm + 5 * mm  # body + section header

    has_fatura = bool(d.get("duplicatas"))
    fatura_h   = DUP_H + 5 * mm if has_fatura else 0

    # Estimate items height
    col_defs = _item_col_widths(w)
    itens    = d.get("itens") or []

    def estimate_row_h(item):
        desc_col_w = col_defs[1][1]
        chars_per  = max(1, int((desc_col_w - 2 * mm) / (6.5 * 0.55 * mm)))
        lines      = textwrap.wrap(_v(item.get("xProd")), chars_per) or [""]
        return max(ROW, len(lines) * 3.8 * mm + 2 * mm)

    row_heights = [estimate_row_h(item) for item in itens]

    # Fixed content height (page 1, excluding items and dados adicionais)
    fixed_h = (canhoto_h + header_h + nat_h + dest_h +
               fatura_h + calc_h + transp_h + items_hdr_h)

    # Available for items on page 1
    page_content_h = TM - BM
    avail_p1 = page_content_h - fixed_h - dados_adic_h

    # Split items into pages
    pages_items = []  # list of lists of item indices
    cur_page_items = []
    cur_h = 0.0
    avail = avail_p1

    for i, rh in enumerate(row_heights):
        if cur_h + rh > avail and cur_page_items:
            pages_items.append(cur_page_items)
            cur_page_items = [i]
            cur_h = rh
            avail = page_content_h - items_hdr_h - dados_adic_h  # page 2+
        else:
            cur_page_items.append(i)
            cur_h += rh

    if cur_page_items:
        pages_items.append(cur_page_items)
    if not pages_items:
        pages_items = [[]]

    total_pages = len(pages_items)

    # ── Draw each page ──
    for page_idx, page_item_indices in enumerate(pages_items):
        is_first = page_idx == 0
        is_last  = page_idx == total_pages - 1

        # Start Y from top
        y_cur = TM

        if is_first:
            # Canhoto
            y_cur -= canhoto_h
            _draw_canhoto(cv, d, x, y_cur, w, canhoto_h)

            # Separator (scissors / fold line)
            cv.saveState()
            cv.setStrokeColor(colors.HexColor("#3b82f6"))
            cv.setLineWidth(0.6)
            cv.setDash([5, 4])
            cv.line(LM, y_cur, RM, y_cur)
            cv.restoreState()

            # Header
            y_cur -= header_h
            _draw_header(cv, d, logo_img, x, y_cur, w, header_h)

            # Natureza / IE / CNPJ
            y_cur -= nat_h
            _draw_nat_ie_cnpj(cv, d, x, y_cur, w, nat_h)

            # Destinatário
            y_cur -= dest_h
            _draw_dest(cv, d, x, y_cur, w, ROW)

            # Fatura
            if has_fatura:
                y_cur -= fatura_h
                _draw_fatura(cv, d, x, y_cur, w, fatura_h)

            # Cálculo do Imposto
            y_cur -= calc_h
            _draw_calc_imposto(cv, d, x, y_cur, w, ROW)

            # Transportador
            y_cur -= transp_h
            _draw_transp(cv, d, x, y_cur, w, ROW)

        # Items header
        y_cur -= items_hdr_h
        _draw_items_header(cv, x, y_cur, w, col_defs)

        # Item rows
        for alt_idx, item_idx in enumerate(page_item_indices):
            item = itens[item_idx]
            rh   = row_heights[item_idx]
            y_cur -= rh
            _draw_one_item(cv, x, y_cur, w, col_defs, _item_values(item),
                           alt=(alt_idx % 2 == 1), row_h=rh)

        # Dados Adicionais on last page
        if is_last:
            # If there's remaining space, use it for dados adicionais (min 20mm body)
            remaining = y_cur - BM - 5 * mm
            body_h    = max(20 * mm, min(remaining, dados_adic_h))
            y_cur -= body_h + 5 * mm
            _draw_dados_adicionais(cv, d, x, y_cur, w, body_h)

        # Page number
        cv.saveState()
        cv.setFont(F, 7)
        cv.setFillColor(CGR)
        cv.drawRightString(RM, BM - 4 * mm,
                           f"Folha {page_idx + 1} de {total_pages}")
        cv.restoreState()

        if page_idx < total_pages - 1:
            cv.showPage()

    cv.save()
    return buf.getvalue()


def _resolve_logo_for_fiscal_engine(logo_path: Optional[str] = None) -> Optional[str]:
    candidates = []
    if logo_path:
        candidates.append(logo_path)
    candidates.append(os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "static", "columbia_logo.png")))
    candidates.append(os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "static", "logo.png")))
    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            return candidate
    return None


def gerar_danfe(xml_bytes: bytes,
                logo_path: Optional[str] = None,
                logo_url: Optional[str] = None) -> bytes:
    """Gera DANFE em PDF priorizando engine fiscal padrão, com fallback manual."""
    if _FiscalDanfe is not None and _FiscalDanfeConfig is not None:
        try:
            logo = _resolve_logo_for_fiscal_engine(logo_path)
            cfg = _FiscalDanfeConfig(logo=logo) if logo else _FiscalDanfeConfig()
            pdf_data = _FiscalDanfe(xml_bytes, cfg).output()
            if pdf_data:
                return bytes(pdf_data)
        except Exception:
            # fallback para o renderer manual já existente
            pass
    return _gerar_danfe_manual(xml_bytes, logo_path=logo_path, logo_url=logo_url)
