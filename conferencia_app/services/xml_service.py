import xml.etree.ElementTree as et
from datetime import datetime

from ..extensions import db
from ..models import ItemNota


CFOPS_CONFERENCIA_PRINCIPAL = {"5124", "5125"}
CFOPS_EXCLUIR_SEM_CONFERENCIA = {"5902", "6902"}


def _txt(node, path, ns, default=""):
    target = node.find(path, ns) if node is not None else None
    return (target.text if target is not None and target.text is not None else default)


def _digits(value: str) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _local_name(tag: str) -> str:
    return str(tag or "").split("}")[-1].lower()


def _first_text_any(root, paths):
    for path in paths:
        node = root.find(path)
        if node is not None and node.text is not None and str(node.text).strip():
            return str(node.text).strip()
    return ""


def _first_text_by_local_names(root, local_names):
    wanted = {str(name or "").strip().lower() for name in (local_names or []) if str(name or "").strip()}
    if not wanted:
        return ""
    for node in root.iter():
        if _local_name(node.tag) in wanted and node.text is not None and str(node.text).strip():
            return str(node.text).strip()
    return ""


def _is_nfse_document(root) -> bool:
    alvo = {"compnfse", "nfse", "infnfse", "declaracaoprestacaoservico", "rps"}
    return any(_local_name(n.tag) in alvo for n in root.iter())


def _process_nfse_and_store(root, user: str, status_inicial: str = "Pendente") -> int:
    id_externo = ""
    for node in root.iter():
        local = _local_name(node.tag)
        if local in {"infnfse", "nfse"}:
            id_externo = str(node.attrib.get("Id") or node.attrib.get("id") or "").strip()
            if id_externo:
                break
        if not id_externo:
            id_externo = str(node.attrib.get("Id") or node.attrib.get("id") or "").strip()

    numero_nota = _first_text_any(
        root,
        [
            ".//{*}InfNfse/{*}Numero",
            ".//{*}Nfse/{*}InfNfse/{*}Numero",
            ".//{*}Numero",
            ".//{*}NumeroNfse",
            ".//{*}numero_nfse",
        ],
    )
    if not numero_nota:
        numero_nota = _first_text_by_local_names(root, ["numero", "numeronfse", "numero_nfse", "numnfse"])

    codigo_verificacao = _first_text_any(root, [".//{*}CodigoVerificacao", ".//{*}codigo_verificacao"])
    if not codigo_verificacao:
        codigo_verificacao = _first_text_by_local_names(root, ["codigoverificacao", "codigo_verificacao"])

    if not numero_nota:
        id_digits = _digits(id_externo)
        if id_digits:
            numero_nota = id_digits[-20:]
        elif codigo_verificacao:
            numero_nota = f"NFSE-{str(codigo_verificacao).strip()}"[:20]

    if not numero_nota:
        return 0

    chave = _first_text_any(root, [".//{*}ChaveNFe", ".//{*}chave", ".//{*}Chave"])
    if not chave:
        chave = _first_text_by_local_names(root, ["chavenfe", "chave"])
    chave = _digits(chave)

    fornecedor = _first_text_any(
        root,
        [
            ".//{*}PrestadorServico/{*}RazaoSocial",
            ".//{*}Prestador/{*}RazaoSocial",
            ".//{*}prestador_razao_social",
        ],
    )
    tomador = _first_text_any(
        root,
        [
            ".//{*}TomadorServico/{*}RazaoSocial",
            ".//{*}Tomador/{*}RazaoSocial",
            ".//{*}tomador_razao_social",
        ],
    )
    descricao = _first_text_any(
        root,
        [
            ".//{*}Discriminacao",
            ".//{*}DiscriminacaoServico",
            ".//{*}discriminacao",
        ],
    )
    if not descricao:
        descricao = "Serviço (NFS-e)"

    cnpj_emitente = _digits(
        _first_text_any(
            root,
            [
                ".//{*}PrestadorServico/{*}IdentificacaoPrestador/{*}Cnpj",
                ".//{*}Prestador/{*}CpfCnpj/{*}Cnpj",
                ".//{*}prestador_cpf_cnpj",
            ],
        )
    )[:14]
    cnpj_destinatario = _digits(
        _first_text_any(
            root,
            [
                ".//{*}TomadorServico/{*}IdentificacaoTomador/{*}CpfCnpj/{*}Cnpj",
                ".//{*}Tomador/{*}CpfCnpj/{*}Cnpj",
                ".//{*}tomador_cpf_cnpj",
            ],
        )
    )[:14]

    valor_raw = _first_text_any(
        root,
        [
            ".//{*}Servico/{*}Valores/{*}ValorServicos",
            ".//{*}Valores/{*}ValorLiquidoNfse",
            ".//{*}valor",
        ],
    )
    try:
        valor = float(str(valor_raw or "0").replace(",", "."))
    except Exception:
        valor = 0.0

    duplicado = (
        ItemNota.query.filter_by(numero_nota=numero_nota, tipo_documento="NFSE").first()
        or (ItemNota.query.filter_by(documento_externo_id=id_externo, tipo_documento="NFSE").first() if id_externo else None)
    )
    if duplicado:
        return 0

    db.session.add(
        ItemNota(
            tipo_documento="NFSE",
            documento_externo_id=id_externo or None,
            codigo_verificacao=codigo_verificacao[:40] if codigo_verificacao else None,
            numero_nota=numero_nota[:20],
            fornecedor=(fornecedor or tomador or "Prestador de Serviço")[:100],
            chave_acesso=chave[:44] if chave else None,
            cfop="",
            codigo="SERVICO",
            descricao=descricao[:200],
            qtd_real=1.0,
            unidade_comercial="UN",
            cnpj_emitente=cnpj_emitente,
            cnpj_destinatario=cnpj_destinatario,
            valor_produto=valor,
            status=status_inicial,
            usuario_importacao=user,
            valor_total=f"R$ {valor:.2f}",
            valor_imposto="---",
            sem_conferencia_logistica=True,
            auditor_status="SemInconsistencia",
            auditor_decisao="PendenteDecisao",
        )
    )

    return 1


def process_xml_and_store(xml_bytes: bytes, user: str, status_inicial: str = "Pendente") -> int:
    ns = {"nfe": "http://www.portalfiscal.inf.br/nfe"}
    try:
        root = et.fromstring(xml_bytes)

        if _is_nfse_document(root):
            return _process_nfse_and_store(root, user, status_inicial=status_inicial)

        numero_nota = _txt(root, ".//nfe:ide/nfe:nNF", ns, "").strip()
        inf_nfe = root.find(".//nfe:infNFe", ns)
        chave = inf_nfe.attrib.get("Id", "")[3:] if inf_nfe is not None else ""
        fornecedor = _txt(root, ".//nfe:emit/nfe:xNome", ns, "").strip()
        cnpj_emitente = _digits(_txt(root, ".//nfe:emit/nfe:CNPJ", ns, "") or _txt(root, ".//nfe:emit/nfe:CPF", ns, ""))[:14]
        cnpj_destinatario = _digits(_txt(root, ".//nfe:dest/nfe:CNPJ", ns, "") or _txt(root, ".//nfe:dest/nfe:CPF", ns, ""))[:14]

        v_nf = root.find(".//nfe:total/nfe:ICMSTot/nfe:vNF", ns)
        v_icms = root.find(".//nfe:total/nfe:ICMSTot/nfe:vICMS", ns)
        txt_total = f"R$ {v_nf.text}" if v_nf is not None else "---"
        txt_imposto = f"R$ {v_icms.text}" if v_icms is not None else "---"

        det_pag_list = root.findall(".//nfe:pag/nfe:detPag", ns)
        pagamento_xml = len(det_pag_list) > 0
        tipos_pagamento = []
        valor_pagamento_xml = 0.0
        for det_pag in det_pag_list:
            tipo = _txt(det_pag, "nfe:tPag", ns, "").strip()
            if tipo and tipo not in tipos_pagamento:
                tipos_pagamento.append(tipo)
            valor_pagamento_xml += float(_txt(det_pag, "nfe:vPag", ns, "0") or 0)

        tipo_pagamento_xml = ",".join(tipos_pagamento)
        vencimento_pagamento_xml = None
        vencimento_raw = _txt(root, ".//nfe:cobr/nfe:dup/nfe:dVenc", ns, "").strip()
        if vencimento_raw:
            try:
                vencimento_pagamento_xml = datetime.strptime(vencimento_raw[:10], "%Y-%m-%d")
            except Exception:
                vencimento_pagamento_xml = None

        if ItemNota.query.filter_by(numero_nota=numero_nota, tipo_documento="NFE").first():
            return 0

        itens_xml = []
        for item in root.findall(".//nfe:det", ns):
            prod = item.find("nfe:prod", ns)
            cfop = (prod.find("nfe:CFOP", ns).text if prod is not None and prod.find("nfe:CFOP", ns) is not None else "")
            imposto = item.find("nfe:imposto", ns)
            cst_icms = ""
            if imposto is not None:
                for icms_tag in [
                    "nfe:ICMS00",
                    "nfe:ICMS10",
                    "nfe:ICMS20",
                    "nfe:ICMS30",
                    "nfe:ICMS40",
                    "nfe:ICMS41",
                    "nfe:ICMS50",
                    "nfe:ICMS51",
                    "nfe:ICMS60",
                    "nfe:ICMS70",
                    "nfe:ICMS90",
                    "nfe:ICMSSN101",
                    "nfe:ICMSSN102",
                    "nfe:ICMSSN201",
                    "nfe:ICMSSN202",
                    "nfe:ICMSSN500",
                    "nfe:ICMSSN900",
                ]:
                    bloco = imposto.find(f"nfe:ICMS/{icms_tag}", ns)
                    if bloco is not None:
                        cst_icms = _txt(bloco, "nfe:CST", ns, "").strip() or _txt(bloco, "nfe:CSOSN", ns, "").strip()
                        break

            cst_pis = _txt(imposto, "nfe:PIS//nfe:CST", ns, "").strip() if imposto is not None else ""
            cst_cofins = _txt(imposto, "nfe:COFINS//nfe:CST", ns, "").strip() if imposto is not None else ""

            itens_xml.append(
                {
                    "cfop": str(cfop or "").strip(),
                    "codigo": _txt(prod, "nfe:cProd", ns, "").strip(),
                    "descricao": _txt(prod, "nfe:xProd", ns, "").strip(),
                    "qtd_real": float(_txt(prod, "nfe:qCom", ns, "0") or 0),
                    "unidade_comercial": _txt(prod, "nfe:uCom", ns, "UN") or "UN",
                    "ncm": _txt(prod, "nfe:NCM", ns, "").strip()[:8],
                    "valor_produto": float(_txt(prod, "nfe:vProd", ns, "0") or 0),
                    "cst_icms": str(cst_icms or "").strip()[:3],
                    "cst_pis": str(cst_pis or "").strip()[:2],
                    "cst_cofins": str(cst_cofins or "").strip()[:2],
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
                    tipo_documento="NFE",
                    numero_nota=numero_nota,
                    fornecedor=fornecedor,
                    chave_acesso=chave,
                    cfop=item["cfop"][:4],
                    codigo=item["codigo"],
                    descricao=item["descricao"],
                    qtd_real=item["qtd_real"],
                    unidade_comercial=item["unidade_comercial"],
                    cnpj_emitente=cnpj_emitente,
                    cnpj_destinatario=cnpj_destinatario,
                    ncm=item["ncm"],
                    cst_icms=item["cst_icms"],
                    cst_pis=item["cst_pis"],
                    cst_cofins=item["cst_cofins"],
                    valor_produto=item["valor_produto"],
                    pagamento_xml=pagamento_xml,
                    tipo_pagamento_xml=tipo_pagamento_xml,
                    valor_pagamento_xml=valor_pagamento_xml,
                    vencimento_pagamento_xml=vencimento_pagamento_xml,
                    status=status_inicial,
                    usuario_importacao=user,
                    valor_total=txt_total,
                    valor_imposto=txt_imposto,
                )
            )

        return 1 if itens_filtrados else 0
    except Exception:
        return 0
