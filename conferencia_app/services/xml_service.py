import xml.etree.ElementTree as et

from ..extensions import db
from ..models import ItemNota


CFOPS_CONFERENCIA_PRINCIPAL = {"5124", "5125"}
CFOPS_EXCLUIR_SEM_CONFERENCIA = {"5902", "6902"}


def process_xml_and_store(xml_bytes: bytes, user: str, status_inicial: str = "Pendente") -> int:
    ns = {"nfe": "http://www.portalfiscal.inf.br/nfe"}
    try:
        root = et.fromstring(xml_bytes)

        numero_nota = root.find(".//nfe:ide/nfe:nNF", ns).text
        inf_nfe = root.find(".//nfe:infNFe", ns)
        chave = inf_nfe.attrib.get("Id", "")[3:] if inf_nfe is not None else ""
        fornecedor = root.find(".//nfe:emit/nfe:xNome", ns).text

        v_nf = root.find(".//nfe:total/nfe:ICMSTot/nfe:vNF", ns)
        v_icms = root.find(".//nfe:total/nfe:ICMSTot/nfe:vICMS", ns)
        txt_total = f"R$ {v_nf.text}" if v_nf is not None else "---"
        txt_imposto = f"R$ {v_icms.text}" if v_icms is not None else "---"

        if ItemNota.query.filter_by(numero_nota=numero_nota).first():
            return 0

        itens_xml = []
        for item in root.findall(".//nfe:det", ns):
            prod = item.find("nfe:prod", ns)
            cfop = (prod.find("nfe:CFOP", ns).text if prod is not None and prod.find("nfe:CFOP", ns) is not None else "")
            itens_xml.append(
                {
                    "cfop": str(cfop or "").strip(),
                    "codigo": str(prod.find("nfe:cProd", ns).text).strip(),
                    "descricao": prod.find("nfe:xProd", ns).text,
                    "qtd_real": float(prod.find("nfe:qCom", ns).text),
                    "unidade_comercial": (prod.find("nfe:uCom", ns).text if prod.find("nfe:uCom", ns) is not None else "UN"),
                }
            )

        tem_cfop_principal = any(item["cfop"] in CFOPS_CONFERENCIA_PRINCIPAL for item in itens_xml)
        itens_filtrados = [
            item
            for item in itens_xml
            if not (tem_cfop_principal and item["cfop"] in CFOPS_EXCLUIR_SEM_CONFERENCIA)
        ]

        for item in itens_filtrados:
            db.session.add(
                ItemNota(
                    numero_nota=numero_nota,
                    fornecedor=fornecedor,
                    chave_acesso=chave,
                    cfop=item["cfop"][:4],
                    codigo=item["codigo"],
                    descricao=item["descricao"],
                    qtd_real=item["qtd_real"],
                    unidade_comercial=item["unidade_comercial"],
                    status=status_inicial,
                    usuario_importacao=user,
                    valor_total=txt_total,
                    valor_imposto=txt_imposto,
                )
            )

        return 1 if itens_filtrados else 0
    except Exception:
        return 0
