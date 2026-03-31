from collections import defaultdict
from datetime import datetime
from xml.etree import ElementTree as et

from flask import current_app

from ..extensions import db
from ..models import ConsertoAuditoria, ConsertoBaixa, ConsertoEstoque, ItemNota
from .consyste_service import download_documento_consyste, listar_nfes_consyste_por_caixa


class ConsertoService:
    CFOPS_REMESSA = {"5915", "6915"}
    CFOPS_RETORNO = {"5916", "6916"}
    XML_NS = {"nfe": "http://www.portalfiscal.inf.br/nfe"}
    CNPJ_EMPRESA_PADRAO = "30482274000125"

    @staticmethod
    def _normalizar_texto(valor):
        if valor is None:
            return ""
        return str(valor).strip()

    @staticmethod
    def _normalizar_quantidade(valor):
        try:
            quantidade = float(valor)
        except (TypeError, ValueError) as exc:
            raise ValueError("Informe uma quantidade valida.") from exc
        if quantidade <= 0:
            raise ValueError("A quantidade deve ser maior que zero.")
        return quantidade

    @staticmethod
    def _parse_data_qualquer(valor):
        texto = ConsertoService._normalizar_texto(valor)
        if not texto:
            return None
        try:
            return datetime.fromisoformat(texto.replace("Z", "").replace("-03:00", ""))
        except Exception:
            try:
                return datetime.strptime(texto[:10], "%Y-%m-%d")
            except Exception:
                return None

    @staticmethod
    def _cnpj_empresa():
        return ConsertoService._normalizar_texto(
            current_app.config.get("EMPRESA_CNPJ") or ConsertoService.CNPJ_EMPRESA_PADRAO
        )

    @staticmethod
    def _obter_estoque(conserto_estoque_id):
        estoque = db.session.get(ConsertoEstoque, conserto_estoque_id)
        if not estoque:
            raise ValueError("Saldo de conserto nao encontrado.")
        return estoque

    @staticmethod
    def _sugerir_estoque_por_heuristica(retorno, codigo_item, quantidade):
        quantidade = float(quantidade or 0)
        if quantidade <= 0:
            return None, "quantidade_invalida"

        query = (
            ConsertoEstoque.query.filter(ConsertoEstoque.quantidade_saldo >= quantidade)
            .filter(ConsertoEstoque.produto_codigo == codigo_item)
            .filter(ConsertoEstoque.fornecedor_cnpj == (retorno.get("fornecedor_cnpj") or ""))
            .order_by(ConsertoEstoque.data_emissao.asc(), ConsertoEstoque.id.asc())
        )

        candidatos = query.all()
        if not candidatos:
            return None, "sem_candidato"

        fechamento_exato = [c for c in candidatos if float(c.quantidade_saldo or 0) == quantidade]
        if len(fechamento_exato) == 1:
            return fechamento_exato[0], "fechamento_exato_por_fornecedor_produto"

        if len(candidatos) == 1:
            return candidatos[0], "unico_candidato_por_fornecedor_produto"

        return None, "multiplos_candidatos"

    @staticmethod
    def _papel_documento_valido(item, tipo_fluxo):
        cnpj_empresa = ConsertoService._cnpj_empresa()
        cnpj_emitente = ConsertoService._normalizar_texto(getattr(item, "cnpj_emitente", ""))
        cnpj_destinatario = ConsertoService._normalizar_texto(getattr(item, "cnpj_destinatario", ""))

        if tipo_fluxo == "retorno":
            return cnpj_destinatario == cnpj_empresa and cnpj_emitente != cnpj_empresa
        if tipo_fluxo == "remessa":
            return cnpj_emitente == cnpj_empresa and cnpj_destinatario != cnpj_empresa
        return False

    @staticmethod
    def _agrupar_itens_retorno_item_nota(numero_nota=None, status=None):
        query = (
            ItemNota.query.filter(ItemNota.cfop.in_(list(ConsertoService.CFOPS_RETORNO)))
            .filter(ItemNota.chave_acesso.isnot(None))
        )
        if numero_nota:
            query = query.filter(ItemNota.numero_nota == str(numero_nota).strip())
        if status:
            if isinstance(status, (list, tuple, set)):
                query = query.filter(ItemNota.status.in_(list(status)))
            else:
                query = query.filter(ItemNota.status == str(status))

        itens = query.order_by(ItemNota.id.asc()).all()
        grupos = {}
        for item in itens:
            if not ConsertoService._papel_documento_valido(item, "retorno"):
                continue

            chave = ConsertoService._normalizar_texto(item.chave_acesso)
            if not chave:
                continue

            grupo = grupos.setdefault(
                chave,
                {
                    "numero_nota": ConsertoService._normalizar_texto(item.numero_nota),
                    "chave_acesso": chave,
                    "data_emissao": item.data_importacao or datetime.now(),
                    "fornecedor_nome": ConsertoService._normalizar_texto(item.fornecedor) or "Fornecedor nao informado",
                    "fornecedor_cnpj": ConsertoService._normalizar_texto(item.cnpj_emitente),
                    "itens": defaultdict(lambda: {"descricao": "", "quantidade": 0.0}),
                },
            )

            codigo = ConsertoService._normalizar_texto(item.codigo)
            if not codigo:
                continue

            item_grupo = grupo["itens"][codigo]
            item_grupo["descricao"] = ConsertoService._normalizar_texto(item.descricao) or item_grupo["descricao"] or codigo
            item_grupo["quantidade"] += float(item.qtd_real or 0)

        return grupos

    @staticmethod
    def _processar_retornos(retornos, usuario, resumo, debug_callback=None):
        def log(msg):
            if debug_callback:
                debug_callback(msg)

        log(f"Encontrados {len(retornos)} retornos candidatos para conciliacao.")
        for retorno in retornos.values():
            log(f"Analisando retorno {retorno['numero_nota'] or retorno['chave_acesso']}.")
            referencias = ConsertoService._obter_referencias_remessa_por_chave_retorno(retorno["chave_acesso"])

            for codigo, dados_item in retorno["itens"].items():
                quantidade = float(dados_item["quantidade"] or 0)
                estoque = None
                motivo_sugestao = None

                if referencias:
                    chave_origem = referencias[0]
                    estoque = (
                        ConsertoEstoque.query.filter_by(
                            chave_nf_remessa=chave_origem,
                            produto_codigo=codigo,
                        )
                        .order_by(ConsertoEstoque.id.desc())
                        .first()
                    )
                    motivo_sugestao = "referencia_xml"
                else:
                    estoque, motivo_sugestao = ConsertoService._sugerir_estoque_por_heuristica(retorno, codigo, quantidade)

                if not estoque:
                    if referencias:
                        resumo["retornos_sem_saldo"] += 1
                        log(f"Retorno {retorno['numero_nota']} item {codigo} sem saldo correspondente para a remessa {referencias[0]}.")
                    else:
                        resumo["retornos_sem_vinculo"] += 1
                        log(
                            f"Retorno {retorno['numero_nota'] or retorno['chave_acesso']} item {codigo} sem sugestao automatica "
                            f"({motivo_sugestao})."
                        )
                    continue

                existente = ConsertoBaixa.query.filter_by(
                    conserto_estoque_id=estoque.id,
                    chave_nf_retorno=retorno["chave_acesso"],
                ).first()
                if existente:
                    if not existente.numero_nf_retorno and retorno["numero_nota"]:
                        existente.numero_nf_retorno = retorno["numero_nota"]
                        db.session.commit()
                    resumo["baixas_existentes"] += 1
                    log(f"Baixa ja existente para retorno {retorno['numero_nota']} item {codigo}.")
                    continue

                if quantidade <= 0:
                    continue
                if quantidade > float(estoque.quantidade_saldo or 0):
                    resumo["retornos_com_saldo_insuficiente"] += 1
                    log(f"Retorno {retorno['numero_nota']} item {codigo} excede saldo atual.")
                    continue

                baixa = ConsertoService.sugerir_baixa_automatica(
                    conserto_estoque_id=estoque.id,
                    numero_nf_retorno=retorno["numero_nota"],
                    chave_nf_retorno=retorno["chave_acesso"],
                    data_nf_retorno=retorno["data_emissao"],
                    quantidade=quantidade,
                    usuario=usuario,
                )
                if motivo_sugestao and motivo_sugestao != "referencia_xml":
                    baixa.observacoes = f"Sugestao de conciliacao: {motivo_sugestao}."
                    db.session.commit()
                if baixa.tipo_vinculo == "automatico" and baixa.status_baixa == "Pendente de confirmacao":
                    resumo["baixas_sugeridas"] += 1
                    log(
                        f"Baixa sugerida para retorno {retorno['numero_nota']} item {codigo} qtd {quantidade} "
                        f"usando {motivo_sugestao or 'referencia_xml'}."
                    )

    @staticmethod
    def processar_retorno_no_lancamento(numero_nota, usuario, debug_callback=None):
        usuario = ConsertoService._normalizar_texto(usuario) or "sistema"
        numero_nota = ConsertoService._normalizar_texto(numero_nota)
        resumo = {
            "nota": numero_nota,
            "baixas_sugeridas": 0,
            "baixas_existentes": 0,
            "retornos_sem_vinculo": 0,
            "retornos_sem_saldo": 0,
            "retornos_com_saldo_insuficiente": 0,
        }
        retornos = ConsertoService._agrupar_itens_retorno_item_nota(numero_nota=numero_nota)
        ConsertoService._processar_retornos(retornos, usuario, resumo, debug_callback=debug_callback)
        return resumo

    @staticmethod
    def _parse_referencias_xml(xml_bytes):
        root = et.fromstring(xml_bytes)
        refs = []
        for ref in root.findall(".//nfe:NFref/nfe:refNFe", ConsertoService.XML_NS):
            valor = ConsertoService._normalizar_texto(ref.text)
            if valor:
                refs.append(valor)
        return refs

    @staticmethod
    def _parse_xml_nfe(xml_bytes):
        root = et.fromstring(xml_bytes)
        ns = ConsertoService.XML_NS

        inf_nfe = root.find(".//nfe:infNFe", ns)
        chave = ""
        if inf_nfe is not None:
            chave = ConsertoService._normalizar_texto(inf_nfe.attrib.get("Id", ""))
            if chave.startswith("NFe"):
                chave = chave[3:]

        emit_nome = ConsertoService._normalizar_texto(root.findtext(".//nfe:emit/nfe:xNome", default="", namespaces=ns))
        emit_cnpj = ConsertoService._normalizar_texto(
            root.findtext(".//nfe:emit/nfe:CNPJ", default="", namespaces=ns)
            or root.findtext(".//nfe:emit/nfe:CPF", default="", namespaces=ns)
        )[:14]
        dest_nome = ConsertoService._normalizar_texto(root.findtext(".//nfe:dest/nfe:xNome", default="", namespaces=ns))
        dest_cnpj = ConsertoService._normalizar_texto(
            root.findtext(".//nfe:dest/nfe:CNPJ", default="", namespaces=ns)
            or root.findtext(".//nfe:dest/nfe:CPF", default="", namespaces=ns)
        )[:14]
        numero_nota = ConsertoService._normalizar_texto(root.findtext(".//nfe:ide/nfe:nNF", default="", namespaces=ns))
        data_emissao_raw = ConsertoService._normalizar_texto(
            root.findtext(".//nfe:ide/nfe:dhEmi", default="", namespaces=ns)
            or root.findtext(".//nfe:ide/nfe:dEmi", default="", namespaces=ns)
        )
        data_emissao = datetime.now()
        if data_emissao_raw:
            try:
                data_emissao = datetime.fromisoformat(data_emissao_raw.replace("Z", "").replace("-03:00", ""))
            except Exception:
                try:
                    data_emissao = datetime.strptime(data_emissao_raw[:10], "%Y-%m-%d")
                except Exception:
                    data_emissao = datetime.now()

        itens = defaultdict(lambda: {"descricao": "", "quantidade": 0.0, "cfops": set()})
        for det in root.findall(".//nfe:det", ns):
            prod = det.find("nfe:prod", ns)
            if prod is None:
                continue
            codigo = ConsertoService._normalizar_texto(prod.findtext("nfe:cProd", default="", namespaces=ns))
            if not codigo:
                continue
            descricao = ConsertoService._normalizar_texto(prod.findtext("nfe:xProd", default="", namespaces=ns))
            qtd_raw = ConsertoService._normalizar_texto(prod.findtext("nfe:qCom", default="0", namespaces=ns))
            cfop = ConsertoService._normalizar_texto(prod.findtext("nfe:CFOP", default="", namespaces=ns))[:4]
            try:
                qtd = float(qtd_raw or 0)
            except Exception:
                qtd = 0.0
            item = itens[codigo]
            item["descricao"] = descricao or item["descricao"] or codigo
            item["quantidade"] += qtd
            if cfop:
                item["cfops"].add(cfop)

        return {
            "numero_nota": numero_nota,
            "chave_acesso": chave,
            "data_emissao": data_emissao,
            "emit_nome": emit_nome,
            "emit_cnpj": emit_cnpj,
            "dest_nome": dest_nome,
            "dest_cnpj": dest_cnpj,
            "itens": dict(itens),
            "referencias": ConsertoService._parse_referencias_xml(xml_bytes),
        }

    @staticmethod
    def _listar_documentos_emitidos_consyste(data_inicial=None, debug_callback=None):
        query_periodo = None
        if data_inicial:
            query_periodo = f"emitido_em:>={data_inicial.strftime('%Y-%m-%d')}"
            if debug_callback:
                debug_callback(f"Consultando Consyste com filtro de emissao desde {data_inicial.strftime('%Y-%m-%d')}.")

        ok, status_code, payload = listar_nfes_consyste_por_caixa(
            caixa="emitidos",
            q=query_periodo,
            campos="id,chave,emitido_em,numero,emit_nome,dest_nome",
            timeout=20,
        )
        if not ok:
            raise RuntimeError(f"Falha ao consultar Consyste: status {status_code}.")

        documentos = payload.get("documentos", []) if isinstance(payload, dict) else []
        unicos = {}
        for doc in documentos:
            chave = ConsertoService._normalizar_texto(doc.get("chave"))
            numero = ConsertoService._normalizar_texto(doc.get("numero"))
            if not chave:
                continue
            emitido_em = ConsertoService._parse_data_qualquer(doc.get("emitido_em"))
            if data_inicial and not emitido_em:
                if debug_callback:
                    debug_callback(f"NF {numero or chave} ignorada: Consyste nao informou a data de emissao.")
                continue
            if data_inicial and emitido_em < data_inicial:
                if debug_callback:
                    debug_callback(
                        f"NF {numero or chave} ignorada: emissao em {emitido_em.strftime('%Y-%m-%d')} antes do periodo."
                    )
                continue
            unicos[chave] = {
                "chave": chave,
                "numero": numero,
                "emitido_em": doc.get("emitido_em"),
                "emit_nome": doc.get("emit_nome"),
                "dest_nome": doc.get("dest_nome"),
            }
        return list(unicos.values())

    @staticmethod
    def criar_saldo_remessa(
        chave_nf_remessa,
        data_emissao,
        fornecedor_cnpj,
        fornecedor_nome,
        produto_codigo,
        produto_descricao,
        quantidade,
        usuario,
        numero_nf_remessa=None,
    ):
        chave_nf_remessa = ConsertoService._normalizar_texto(chave_nf_remessa)
        numero_nf_remessa = ConsertoService._normalizar_texto(numero_nf_remessa) or None
        fornecedor_cnpj = ConsertoService._normalizar_texto(fornecedor_cnpj)
        fornecedor_nome = ConsertoService._normalizar_texto(fornecedor_nome)
        produto_codigo = ConsertoService._normalizar_texto(produto_codigo)
        produto_descricao = ConsertoService._normalizar_texto(produto_descricao)
        usuario = ConsertoService._normalizar_texto(usuario) or "sistema"
        quantidade = ConsertoService._normalizar_quantidade(quantidade)

        if not chave_nf_remessa:
            raise ValueError("Informe a chave da NF de remessa.")
        if not data_emissao:
            raise ValueError("Informe a data de emissao da remessa.")
        if not fornecedor_cnpj:
            raise ValueError("Informe o CNPJ do fornecedor.")
        if not fornecedor_nome:
            raise ValueError("Informe o nome do fornecedor.")
        if not produto_codigo:
            raise ValueError("Informe o codigo do produto.")
        if not produto_descricao:
            raise ValueError("Informe a descricao do produto.")

        saldo_existente = ConsertoEstoque.query.filter_by(
            chave_nf_remessa=chave_nf_remessa,
            produto_codigo=produto_codigo,
        ).first()
        if saldo_existente:
            raise ValueError("Ja existe um saldo aberto para esta remessa e produto.")

        try:
            saldo = ConsertoEstoque(
                numero_nf_remessa=numero_nf_remessa,
                chave_nf_remessa=chave_nf_remessa,
                data_emissao=data_emissao,
                fornecedor_cnpj=fornecedor_cnpj,
                fornecedor_nome=fornecedor_nome,
                produto_codigo=produto_codigo,
                produto_descricao=produto_descricao,
                quantidade_enviada=quantidade,
                quantidade_saldo=quantidade,
                status="Em conserto",
                usuario_criacao=usuario,
            )
            db.session.add(saldo)
            db.session.flush()
            ConsertoService.registrar_auditoria(
                "criar_saldo",
                saldo.id,
                "estoque",
                usuario,
                f"Saldo criado para remessa {numero_nf_remessa or chave_nf_remessa}, produto {produto_codigo}, qtd {quantidade}",
                commit=False,
            )
            db.session.commit()
            return saldo
        except Exception:
            db.session.rollback()
            raise

    @staticmethod
    def sugerir_baixa_automatica(
        conserto_estoque_id,
        chave_nf_retorno,
        data_nf_retorno,
        quantidade,
        usuario,
        numero_nf_retorno=None,
    ):
        estoque = ConsertoService._obter_estoque(conserto_estoque_id)
        quantidade = ConsertoService._normalizar_quantidade(quantidade)
        usuario = ConsertoService._normalizar_texto(usuario) or "sistema"
        chave_nf_retorno = ConsertoService._normalizar_texto(chave_nf_retorno) or None
        numero_nf_retorno = ConsertoService._normalizar_texto(numero_nf_retorno) or None

        if quantidade > estoque.quantidade_saldo:
            raise ValueError("A quantidade sugerida nao pode ser maior que o saldo em aberto.")

        if chave_nf_retorno:
            existente = ConsertoBaixa.query.filter_by(
                conserto_estoque_id=conserto_estoque_id,
                chave_nf_retorno=chave_nf_retorno,
            ).first()
            if existente:
                return existente

        try:
            baixa = ConsertoBaixa(
                conserto_estoque_id=conserto_estoque_id,
                numero_nf_retorno=numero_nf_retorno,
                chave_nf_retorno=chave_nf_retorno,
                data_nf_retorno=data_nf_retorno,
                quantidade_baixada=quantidade,
                tipo_vinculo="automatico",
                status_baixa="Pendente de confirmacao",
            )
            db.session.add(baixa)
            db.session.flush()
            ConsertoService.registrar_auditoria(
                "sugerir_baixa",
                baixa.id,
                "baixa",
                usuario,
                f"Sugestao automatica de baixa para retorno {numero_nf_retorno or baixa.chave_nf_retorno or 'sem chave'}, qtd {quantidade}",
                commit=False,
            )
            db.session.commit()
            return baixa
        except Exception:
            db.session.rollback()
            raise

    @staticmethod
    def vinculo_manual(conserto_estoque_id, chave_nf_retorno, data_nf_retorno, quantidade, usuario, observacoes=None):
        estoque = ConsertoService._obter_estoque(conserto_estoque_id)
        chave_nf_retorno = ConsertoService._normalizar_texto(chave_nf_retorno)
        usuario = ConsertoService._normalizar_texto(usuario) or "sistema"
        observacoes = ConsertoService._normalizar_texto(observacoes) or None
        quantidade = ConsertoService._normalizar_quantidade(quantidade)

        if not chave_nf_retorno:
            raise ValueError("Informe a chave da NF de retorno.")
        if not data_nf_retorno:
            raise ValueError("Informe a data da NF de retorno.")
        if quantidade > estoque.quantidade_saldo:
            raise ValueError("A quantidade vinculada nao pode ser maior que o saldo em aberto.")

        try:
            baixa = ConsertoBaixa(
                conserto_estoque_id=conserto_estoque_id,
                chave_nf_retorno=chave_nf_retorno,
                data_nf_retorno=data_nf_retorno,
                quantidade_baixada=quantidade,
                tipo_vinculo="manual",
                status_baixa="Pendente de confirmacao",
                observacoes=observacoes,
            )
            db.session.add(baixa)
            db.session.flush()
            ConsertoService.registrar_auditoria(
                "vinculo_manual",
                baixa.id,
                "baixa",
                usuario,
                f"Vinculo manual para retorno {chave_nf_retorno}, qtd {quantidade}",
                commit=False,
            )
            db.session.commit()
            return baixa
        except Exception:
            db.session.rollback()
            raise

    @staticmethod
    def confirmar_baixa(baixa_id, usuario, observacoes=None):
        baixa = db.session.get(ConsertoBaixa, baixa_id)
        if not baixa or baixa.status_baixa == "Confirmado":
            return None

        estoque = db.session.get(ConsertoEstoque, baixa.conserto_estoque_id)
        if not estoque:
            return None
        if baixa.quantidade_baixada > estoque.quantidade_saldo:
            raise ValueError("Quantidade retornada maior que o saldo em aberto.")

        usuario = ConsertoService._normalizar_texto(usuario) or "sistema"
        observacoes = ConsertoService._normalizar_texto(observacoes) or None

        try:
            estoque.quantidade_saldo = max(
                float(estoque.quantidade_saldo or 0) - float(baixa.quantidade_baixada or 0),
                0,
            )
            estoque.status = "Conserto finalizado" if estoque.quantidade_saldo == 0 else "Em conserto"
            baixa.status_baixa = "Confirmado"
            baixa.usuario_confirmacao = usuario
            baixa.data_confirmacao = datetime.now()
            if observacoes:
                baixa.observacoes = observacoes
            ConsertoService.registrar_auditoria(
                "confirmar_baixa",
                baixa.id,
                "baixa",
                usuario,
                f"Confirmacao de baixa, qtd {baixa.quantidade_baixada}",
                commit=False,
            )
            db.session.commit()
            return baixa
        except Exception:
            db.session.rollback()
            raise

    @staticmethod
    def sincronizar_notas_fiscais(usuario, data_inicial=None, debug_callback=None):
        usuario = ConsertoService._normalizar_texto(usuario) or "sistema"
        data_inicial = data_inicial or datetime(datetime.now().year, 3, 1)
        resumo = {
            "data_inicial": data_inicial.strftime("%Y-%m-%d"),
            "remessas_emitidas_consyste": 0,
            "saldos_criados": 0,
            "saldos_existentes": 0,
            "baixas_sugeridas": 0,
            "baixas_existentes": 0,
            "retornos_sem_vinculo": 0,
            "retornos_sem_saldo": 0,
            "retornos_com_saldo_insuficiente": 0,
        }

        def log(msg):
            if debug_callback:
                debug_callback(msg)

        log(f"Iniciando sincronizacao a partir de {resumo['data_inicial']}.")
        documentos_emitidos = ConsertoService._listar_documentos_emitidos_consyste(
            data_inicial=data_inicial,
            debug_callback=debug_callback,
        )
        log(f"Consyste retornou {len(documentos_emitidos)} NF-es emitidas no periodo.")

        for doc in documentos_emitidos:
            log(f"Lendo XML da remessa {doc['numero'] or doc['chave']}.")
            ok, status_code, payload = download_documento_consyste(
                modelo="nfe",
                formato="xml",
                chave=doc["chave"],
                timeout=20,
            )
            if not ok or status_code != 200 or not payload:
                log(f"Falha ao baixar XML da remessa {doc['numero'] or doc['chave']} (status {status_code}).")
                continue

            nota = ConsertoService._parse_xml_nfe(payload)
            if not ConsertoService._papel_documento_valido(
                type("Doc", (), {"cnpj_emitente": nota["emit_cnpj"], "cnpj_destinatario": nota["dest_cnpj"]})(),
                "remessa",
            ):
                log(f"Ignorada {nota['numero_nota'] or doc['chave']}: nao e remessa emitida pela empresa.")
                continue

            possui_cfop_remessa = any(
                cfop in ConsertoService.CFOPS_REMESSA
                for item in nota["itens"].values()
                for cfop in item["cfops"]
            )
            if not possui_cfop_remessa:
                log(f"Ignorada {nota['numero_nota'] or doc['chave']}: sem CFOP 5915/6915.")
                continue

            resumo["remessas_emitidas_consyste"] += 1
            log(f"Processando remessa {nota['numero_nota'] or doc['chave']} para {nota['dest_nome'] or 'fornecedor nao informado'}.")
            for codigo, dados_item in nota["itens"].items():
                if not any(cfop in ConsertoService.CFOPS_REMESSA for cfop in dados_item["cfops"]):
                    continue

                existente = ConsertoEstoque.query.filter_by(
                    chave_nf_remessa=nota["chave_acesso"],
                    produto_codigo=codigo,
                ).first()
                if existente:
                    if not existente.numero_nf_remessa and nota["numero_nota"]:
                        existente.numero_nf_remessa = nota["numero_nota"]
                        db.session.commit()
                    resumo["saldos_existentes"] += 1
                    log(f"Saldo ja existente para NF {nota['numero_nota']} item {codigo}.")
                    continue

                try:
                    ConsertoService.criar_saldo_remessa(
                        numero_nf_remessa=nota["numero_nota"],
                        chave_nf_remessa=nota["chave_acesso"],
                        data_emissao=nota["data_emissao"],
                        fornecedor_cnpj=nota["dest_cnpj"] or "00000000000000",
                        fornecedor_nome=nota["dest_nome"] or "Fornecedor nao informado",
                        produto_codigo=codigo,
                        produto_descricao=dados_item["descricao"] or codigo,
                        quantidade=dados_item["quantidade"],
                        usuario=usuario,
                    )
                    resumo["saldos_criados"] += 1
                    log(f"Saldo criado para NF {nota['numero_nota']} item {codigo} qtd {dados_item['quantidade']}.")
                except ValueError:
                    resumo["saldos_existentes"] += 1
                    log(f"Saldo duplicado ignorado para NF {nota['numero_nota']} item {codigo}.")

        ConsertoService.registrar_auditoria(
            "sincronizar_notas",
            0,
            "lote",
            usuario,
            (
                f"Sincronizacao concluida. "
                f"Remessas emitidas lidas na Consyste: {resumo['remessas_emitidas_consyste']}, "
                f"saldos criados: {resumo['saldos_criados']}."
            ),
        )
        log("Sincronizacao finalizada.")
        return resumo

    @staticmethod
    def _obter_referencias_remessa_por_chave_retorno(chave_nf_retorno):
        chave_nf_retorno = ConsertoService._normalizar_texto(chave_nf_retorno)
        if len(chave_nf_retorno) != 44:
            return []

        token = str(current_app.config.get("CONSYSTE_TOKEN") or "").strip()
        if not token:
            return []

        try:
            ok, status_code, payload = download_documento_consyste(
                modelo="nfe",
                formato="xml",
                chave=chave_nf_retorno,
                timeout=20,
            )
            if not ok or status_code != 200 or not payload:
                return []
            return ConsertoService._parse_referencias_xml(payload)
        except Exception:
            return []

    @staticmethod
    def listar_historico_estoque():
        registros = (
            ConsertoEstoque.query.order_by(ConsertoEstoque.data_emissao.desc(), ConsertoEstoque.id.desc()).all()
        )
        return [
            {
                "id": estoque.id,
                "numero_nf_remessa": estoque.numero_nf_remessa,
                "chave_nf_remessa": estoque.chave_nf_remessa,
                "data_emissao": estoque.data_emissao.isoformat() if estoque.data_emissao else None,
                "fornecedor_cnpj": estoque.fornecedor_cnpj,
                "fornecedor_nome": estoque.fornecedor_nome,
                "produto_codigo": estoque.produto_codigo,
                "produto_descricao": estoque.produto_descricao,
                "quantidade_enviada": float(estoque.quantidade_enviada or 0),
                "quantidade_saldo": float(estoque.quantidade_saldo or 0),
                "quantidade_baixada": max(
                    float(estoque.quantidade_enviada or 0) - float(estoque.quantidade_saldo or 0),
                    0,
                ),
                "status": estoque.status,
                "usuario_criacao": estoque.usuario_criacao,
            }
            for estoque in registros
        ]

    @staticmethod
    def registrar_auditoria(acao, referencia_id, referencia_tipo, usuario, detalhes=None, commit=True):
        audit = ConsertoAuditoria(
            acao=acao,
            referencia_id=referencia_id,
            referencia_tipo=referencia_tipo,
            usuario=usuario,
            detalhes=detalhes,
        )
        db.session.add(audit)
        if commit:
            db.session.commit()
