import json

import requests
from flask import current_app

from ..extensions import db
from ..models import DepositoWMS, ItemWMS, LocalizacaoArmazem, WMSIntegracaoEvento, WMSInventreeVinculo


class InventreeService:
    EVENTO_NOTA_LANCADA = "NotaLancada"
    EVENTO_ENDERECO_MANUAL = "EnderecoManual"
    EVENTO_ESTORNO_ENDERECO = "EstornoEnderecamento"
    EVENTO_TRANSFERENCIA_DEPOSITO = "TransferenciaDeposito"

    @staticmethod
    def is_enabled() -> bool:
        return bool(current_app.config.get("INVENTREE_WMS_ENABLED", False))

    @staticmethod
    def _api_base() -> str:
        return str(current_app.config.get("INVENTREE_API_BASE", "") or "").strip().rstrip("/")

    @staticmethod
    def _api_token() -> str:
        return str(current_app.config.get("INVENTREE_API_TOKEN", "") or "").strip()

    @staticmethod
    def _timeout() -> int:
        return int(current_app.config.get("INVENTREE_TIMEOUT_SECONDS", 20) or 20)

    @staticmethod
    def _root_location_id():
        return current_app.config.get("INVENTREE_ROOT_LOCATION_ID")

    @staticmethod
    def _pending_location_id():
        return current_app.config.get("INVENTREE_PENDING_LOCATION_ID")

    @staticmethod
    def _default_part_category_id():
        return current_app.config.get("INVENTREE_DEFAULT_PART_CATEGORY_ID")

    @staticmethod
    def _stock_note_prefix() -> str:
        return str(current_app.config.get("INVENTREE_STOCK_NOTE_PREFIX", "ERP/WMS") or "ERP/WMS").strip()

    @staticmethod
    def _assert_configured() -> None:
        if not InventreeService.is_enabled():
            raise RuntimeError("Integracao InvenTree desabilitada")
        if not InventreeService._api_base():
            raise RuntimeError("Configure INVENTREE_API_BASE")
        if not InventreeService._api_token():
            raise RuntimeError("Configure INVENTREE_API_TOKEN")

    @staticmethod
    def _headers() -> dict:
        return {
            "Authorization": f"Token {InventreeService._api_token()}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _normalize_response(resp):
        if resp.status_code == 204 or not resp.content:
            return {}
        try:
            return resp.json()
        except ValueError:
            return {}

    @staticmethod
    def _request(method: str, path: str, *, params=None, payload=None):
        InventreeService._assert_configured()
        url = f"{InventreeService._api_base()}{path}"
        try:
            response = requests.request(
                method=method.upper(),
                url=url,
                headers=InventreeService._headers(),
                params=params,
                json=payload,
                timeout=InventreeService._timeout(),
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"Falha ao acessar InvenTree: {exc}") from exc

        body = InventreeService._normalize_response(response)
        if response.status_code >= 400:
            detail = body.get("detail") if isinstance(body, dict) else None
            if not detail and isinstance(body, dict):
                detail = json.dumps(body, ensure_ascii=True)
            detail = detail or response.text or "erro_sem_detalhe"
            raise RuntimeError(f"InvenTree retornou {response.status_code}: {detail[:300]}")
        return body

    @staticmethod
    def _result_list(data):
        if isinstance(data, dict):
            return data.get("results") or []
        if isinstance(data, list):
            return data
        return []

    @staticmethod
    def _vinculo(entidade_tipo: str, entidade_chave: str):
        return WMSInventreeVinculo.query.filter_by(
            entidade_tipo=str(entidade_tipo).strip(),
            entidade_chave=str(entidade_chave).strip(),
        ).first()

    @staticmethod
    def mapear_vinculos(entidade_tipo: str, chaves):
        chaves_normalizadas = [str(chave).strip() for chave in (chaves or []) if str(chave).strip()]
        if not chaves_normalizadas:
            return {}
        registros = WMSInventreeVinculo.query.filter(
            WMSInventreeVinculo.entidade_tipo == str(entidade_tipo).strip(),
            WMSInventreeVinculo.entidade_chave.in_(chaves_normalizadas),
        ).all()
        return {registro.entidade_chave: registro for registro in registros}

    @staticmethod
    def _upsert_vinculo(
        entidade_tipo: str,
        entidade_chave: str,
        inventree_tipo: str,
        inventree_id: int,
        *,
        inventree_codigo=None,
        inventree_path=None,
        metadata=None,
    ):
        registro = InventreeService._vinculo(entidade_tipo, entidade_chave)
        if not registro:
            registro = WMSInventreeVinculo(
                entidade_tipo=str(entidade_tipo).strip(),
                entidade_chave=str(entidade_chave).strip(),
                inventree_tipo=str(inventree_tipo).strip(),
                inventree_id=int(inventree_id),
            )
            db.session.add(registro)

        registro.inventree_tipo = str(inventree_tipo).strip()
        registro.inventree_id = int(inventree_id)
        registro.inventree_codigo = str(inventree_codigo).strip() if inventree_codigo else None
        registro.inventree_path = str(inventree_path).strip() if inventree_path else None
        registro.metadata_json = json.dumps(metadata or {}, ensure_ascii=True)
        db.session.commit()
        return registro

    @staticmethod
    def enfileirar_evento(tipo_evento: str, referencia: str, payload: dict | None, *, origem="WMS", idempotency_key=None):
        referencia = str(referencia or "").strip()
        tipo_evento = str(tipo_evento or "").strip()
        origem = str(origem or "WMS").strip()
        if not referencia or not tipo_evento:
            return None, False

        payload = payload or {}
        idempotency_key = str(idempotency_key or f"{tipo_evento}:{referencia}").strip()[:120]
        evento = WMSIntegracaoEvento.query.filter_by(idempotency_key=idempotency_key).first()
        if evento:
            if evento.status in ("Falha", "DeadLetter"):
                evento.status = "Pendente"
                evento.proxima_tentativa_em = None
                evento.ultima_erro = None
                db.session.commit()
            return evento, False

        evento = WMSIntegracaoEvento(
            idempotency_key=idempotency_key,
            tipo_evento=tipo_evento,
            referencia=referencia,
            origem=origem,
            payload_json=json.dumps(payload, ensure_ascii=True),
            status="Pendente",
            tentativas=0,
        )
        db.session.add(evento)
        db.session.commit()
        return evento, True

    @staticmethod
    def status() -> dict:
        resultado = {
            "enabled": InventreeService.is_enabled(),
            "api_base": InventreeService._api_base(),
            "root_location_id": InventreeService._root_location_id(),
            "pending_location_id": InventreeService._pending_location_id(),
            "default_part_category_id": InventreeService._default_part_category_id(),
            "vinculos": WMSInventreeVinculo.query.count(),
        }

        if not InventreeService.is_enabled():
            resultado["mensagem"] = "Integracao InvenTree desabilitada"
            return resultado

        try:
            resposta = InventreeService._request("GET", "/api/stock/location/", params={"limit": 1})
            resultado["conectado"] = True
            resultado["location_probe_count"] = int(resposta.get("count", 0) or 0) if isinstance(resposta, dict) else None
        except Exception as exc:
            resultado["conectado"] = False
            resultado["erro"] = str(exc)
        return resultado

    @staticmethod
    def resumo_operacional_remoto() -> dict:
        if not InventreeService.is_enabled():
            return {"enabled": False, "mensagem": "Integracao InvenTree desabilitada"}

        try:
            stock = InventreeService._request("GET", "/api/stock/", params={"limit": 1})
            locations = InventreeService._request("GET", "/api/stock/location/", params={"limit": 1})
            return {
                "enabled": True,
                "conectado": True,
                "stock_items": int(stock.get("count", 0) or 0) if isinstance(stock, dict) else None,
                "localizacoes": int(locations.get("count", 0) or 0) if isinstance(locations, dict) else None,
                "vinculos_locais": WMSInventreeVinculo.query.count(),
            }
        except Exception as exc:
            return {"enabled": True, "conectado": False, "erro": str(exc)}

    @staticmethod
    def _format_quantity(quantity) -> str:
        valor = float(quantity or 0)
        if valor.is_integer():
            return str(int(valor))
        return f"{valor:.6f}".rstrip("0").rstrip(".")

    @staticmethod
    def _extrair_nome_localizacao(registro: dict) -> str | None:
        detalhe = registro.get("location_detail")
        if isinstance(detalhe, dict):
            return detalhe.get("name") or detalhe.get("pathstring")
        caminho = registro.get("location_path")
        if isinstance(caminho, list) and caminho:
            ultimo = caminho[-1]
            if isinstance(ultimo, dict):
                return ultimo.get("name") or ultimo.get("pathstring")
            return str(ultimo)
        return None

    @staticmethod
    def _find_remote_part_by_sku(codigo_item: str):
        resposta = InventreeService._request("GET", "/api/part/", params={"IPN": codigo_item, "limit": 10})
        for registro in InventreeService._result_list(resposta):
            if str(registro.get("IPN") or "").strip() == str(codigo_item or "").strip():
                return registro
        return None

    @staticmethod
    def _find_remote_location(name: str, parent_id=None):
        resposta = InventreeService._request("GET", "/api/stock/location/", params={"search": name, "limit": 50})
        for registro in InventreeService._result_list(resposta):
            if str(registro.get("name") or "").strip() != str(name or "").strip():
                continue
            registro_parent = registro.get("parent")
            if parent_id is None and registro_parent in (None, ""):
                return registro
            if parent_id is not None and int(registro_parent or 0) == int(parent_id):
                return registro
        return None

    @staticmethod
    def obter_estoque_remoto_por_sku(codigo_item: str) -> dict:
        codigo_item = str(codigo_item or "").strip()
        if not codigo_item:
            return {"codigo_item": codigo_item, "itens": [], "qtd_total": 0.0}

        resposta = InventreeService._request(
            "GET",
            "/api/stock/",
            params={"IPN": codigo_item, "limit": 100, "location_detail": True},
        )
        itens = []
        qtd_total = 0.0
        for registro in InventreeService._result_list(resposta):
            quantidade = float(registro.get("quantity") or 0)
            qtd_total += quantidade
            itens.append(
                {
                    "stock_item_id": registro.get("pk"),
                    "part_id": registro.get("part"),
                    "location_id": registro.get("location"),
                    "location_nome": InventreeService._extrair_nome_localizacao(registro),
                    "quantity": quantidade,
                    "status": registro.get("status_text"),
                    "batch": registro.get("batch"),
                    "serial": registro.get("serial"),
                    "notes": registro.get("notes"),
                }
            )
        return {
            "codigo_item": codigo_item,
            "itens": itens,
            "qtd_total": round(qtd_total, 6),
            "count": len(itens),
        }

    @staticmethod
    def _build_stock_notes(item_wms: ItemWMS) -> str:
        partes = [
            InventreeService._stock_note_prefix(),
            f"item_wms={item_wms.id}",
            f"nf={item_wms.numero_nota}",
            f"sku={item_wms.codigo_item}",
        ]
        if item_wms.codigo_grv:
            partes.append(f"grv={item_wms.codigo_grv}")
        if item_wms.ordem_servico:
            partes.append(f"os={item_wms.ordem_servico}")
        if item_wms.ordem_compra:
            partes.append(f"oc={item_wms.ordem_compra}")
        return " | ".join(partes)[:500]

    @staticmethod
    def _ensure_deposito_location(deposito_id):
        deposito = DepositoWMS.query.get(deposito_id) if deposito_id else None
        if not deposito or not deposito.ativo:
            return None

        chave = str(deposito.id)
        vinculo = InventreeService._vinculo("deposito", chave)
        if vinculo:
            return int(vinculo.inventree_id)

        parent_id = InventreeService._root_location_id()
        nome = str(deposito.codigo or deposito.nome or f"DEP-{deposito.id}").strip()
        remoto = InventreeService._find_remote_location(nome, parent_id=parent_id)
        if not remoto:
            payload = {
                "name": nome,
                "description": (deposito.nome or deposito.descricao or nome)[:200],
                "parent": parent_id,
                "structural": False,
                "external": False,
            }
            if parent_id is None:
                payload.pop("parent")
            remoto = InventreeService._request("POST", "/api/stock/location/", payload=payload)

        InventreeService._upsert_vinculo(
            "deposito",
            chave,
            "location",
            remoto["pk"],
            inventree_codigo=remoto.get("name"),
            inventree_path=remoto.get("pathstring"),
            metadata={"local_codigo": deposito.codigo, "local_nome": deposito.nome},
        )
        return int(remoto["pk"])

    @staticmethod
    def _ensure_localizacao(localizacao_id):
        localizacao = LocalizacaoArmazem.query.get(localizacao_id) if localizacao_id else None
        if not localizacao or not localizacao.ativo:
            raise RuntimeError("Localizacao WMS nao encontrada para sincronizacao")

        chave = str(localizacao.id)
        vinculo = InventreeService._vinculo("localizacao", chave)
        if vinculo:
            return int(vinculo.inventree_id)

        parent_id = InventreeService._ensure_deposito_location(localizacao.deposito_id) if localizacao.deposito_id else InventreeService._root_location_id()
        nome = str(localizacao.codigo or f"LOC-{localizacao.id}").strip()
        remoto = InventreeService._find_remote_location(nome, parent_id=parent_id)
        if not remoto:
            descricao = (
                f"Deposito {localizacao.deposito_id or '-'} | "
                f"Rua {localizacao.rua or '-'} | Predio {localizacao.predio or '-'} | "
                f"Nivel {localizacao.nivel or '-'}"
            )[:200]
            payload = {
                "name": nome,
                "description": descricao,
                "parent": parent_id,
                "structural": False,
                "external": False,
            }
            if parent_id is None:
                payload.pop("parent")
            remoto = InventreeService._request("POST", "/api/stock/location/", payload=payload)

        InventreeService._upsert_vinculo(
            "localizacao",
            chave,
            "location",
            remoto["pk"],
            inventree_codigo=remoto.get("name"),
            inventree_path=remoto.get("pathstring"),
            metadata={"local_codigo": localizacao.codigo, "deposito_id": localizacao.deposito_id},
        )
        return int(remoto["pk"])

    @staticmethod
    def _resolve_pending_destination(item_wms: ItemWMS):
        pending_id = InventreeService._pending_location_id()
        if pending_id:
            return int(pending_id)
        if item_wms.deposito_id:
            deposito_location = InventreeService._ensure_deposito_location(item_wms.deposito_id)
            if deposito_location:
                return int(deposito_location)
        root_id = InventreeService._root_location_id()
        if root_id:
            return int(root_id)
        raise RuntimeError("Configure INVENTREE_PENDING_LOCATION_ID ou INVENTREE_ROOT_LOCATION_ID")

    @staticmethod
    def ensure_part_for_item(item_wms: ItemWMS) -> int:
        chave = str(item_wms.codigo_item or "").strip()
        if not chave:
            raise RuntimeError("ItemWMS sem codigo_item para sincronizar com InvenTree")

        vinculo = InventreeService._vinculo("sku", chave)
        if vinculo:
            return int(vinculo.inventree_id)

        remoto = InventreeService._find_remote_part_by_sku(chave)
        if not remoto:
            payload = {
                "name": (item_wms.descricao or chave)[:100],
                "description": (item_wms.descricao or chave)[:200],
                "IPN": chave,
                "units": (item_wms.unidade or "UN")[:20],
                "active": True,
                "component": True,
                "purchaseable": True,
                "salable": False,
                "trackable": False,
                "virtual": False,
                "minimum_stock": 0,
            }
            categoria = InventreeService._default_part_category_id()
            if categoria:
                payload["category"] = int(categoria)
            remoto = InventreeService._request("POST", "/api/part/", payload=payload)

        InventreeService._upsert_vinculo(
            "sku",
            chave,
            "part",
            remoto["pk"],
            inventree_codigo=remoto.get("IPN") or remoto.get("name"),
            metadata={"descricao": item_wms.descricao, "unidade": item_wms.unidade},
        )
        return int(remoto["pk"])

    @staticmethod
    def ensure_stock_item_for_item(item_wms: ItemWMS) -> int:
        chave = str(item_wms.id)
        vinculo = InventreeService._vinculo("item_wms", chave)
        if vinculo:
            return int(vinculo.inventree_id)

        part_id = InventreeService.ensure_part_for_item(item_wms)
        location_id = (
            InventreeService._ensure_localizacao(item_wms.localizacao_id)
            if item_wms.localizacao_id
            else InventreeService._resolve_pending_destination(item_wms)
        )

        payload = {
            "part": int(part_id),
            "quantity": float(item_wms.qtd_atual or 0),
            "location": int(location_id),
            "notes": InventreeService._build_stock_notes(item_wms),
        }
        if item_wms.lote:
            payload["batch"] = str(item_wms.lote)[:100]
        if item_wms.data_validade:
            payload["expiry_date"] = item_wms.data_validade.isoformat()

        remoto = InventreeService._request("POST", "/api/stock/", payload=payload)
        InventreeService._upsert_vinculo(
            "item_wms",
            chave,
            "stock_item",
            remoto["pk"],
            inventree_codigo=remoto.get("SKU") or item_wms.codigo_item,
            inventree_path=remoto.get("location_path"),
            metadata={"part": part_id, "location": payload["location"]},
        )
        return int(remoto["pk"])

    @staticmethod
    def _transfer_stock_item(item_wms: ItemWMS, destination_location_id: int, notes: str):
        stock_item_id = InventreeService.ensure_stock_item_for_item(item_wms)
        payload = {
            "location": int(destination_location_id),
            "items": [
                {
                    "pk": int(stock_item_id),
                    "quantity": InventreeService._format_quantity(item_wms.qtd_atual),
                }
            ],
            "notes": notes[:500],
        }
        if item_wms.lote:
            payload["items"][0]["batch"] = str(item_wms.lote)[:100]
        InventreeService._request("POST", "/api/stock/transfer/", payload=payload)
        InventreeService._upsert_vinculo(
            "item_wms",
            str(item_wms.id),
            "stock_item",
            int(stock_item_id),
            inventree_codigo=item_wms.codigo_item,
            metadata={"location": int(destination_location_id)},
        )
        return int(stock_item_id)

    @staticmethod
    def sincronizar_nota_lancada(numero_nota: str, usuario: str):
        numero_nota = str(numero_nota or "").strip()
        itens = ItemWMS.query.filter_by(numero_nota=numero_nota, ativo=True).all()
        sincronizados = 0
        for item in itens:
            InventreeService.ensure_stock_item_for_item(item)
            sincronizados += 1
        return {
            "sucesso": True,
            "nota": numero_nota,
            "itens_sincronizados": sincronizados,
            "usuario": usuario,
        }

    @staticmethod
    def sincronizar_item_enderecado(item_wms_id: int, usuario: str):
        item = ItemWMS.query.get(item_wms_id)
        if not item or not item.ativo or not item.localizacao_id:
            raise RuntimeError("Item WMS invalido para sincronizacao de enderecamento")

        destino = InventreeService._ensure_localizacao(item.localizacao_id)
        stock_item_id = InventreeService._transfer_stock_item(
            item,
            destino,
            notes=f"Enderecamento ERP por {usuario}: localizacao {item.localizacao_id}",
        )
        return {"sucesso": True, "item_wms_id": item.id, "stock_item_id": stock_item_id, "location_id": destino}

    @staticmethod
    def sincronizar_estorno_enderecamento(item_wms_id: int, usuario: str):
        item = ItemWMS.query.get(item_wms_id)
        if not item or not item.ativo:
            raise RuntimeError("Item WMS invalido para sincronizacao de estorno")

        destino = InventreeService._resolve_pending_destination(item)
        stock_item_id = InventreeService._transfer_stock_item(
            item,
            destino,
            notes=f"Estorno de enderecamento ERP por {usuario}",
        )
        return {"sucesso": True, "item_wms_id": item.id, "stock_item_id": stock_item_id, "location_id": destino}

    @staticmethod
    def sincronizar_transferencia_deposito(item_wms_id: int, usuario: str):
        item = ItemWMS.query.get(item_wms_id)
        if not item or not item.ativo:
            raise RuntimeError("Item WMS invalido para sincronizacao de transferencia")

        if item.localizacao_id:
            destino = InventreeService._ensure_localizacao(item.localizacao_id)
        else:
            destino = InventreeService._resolve_pending_destination(item)

        stock_item_id = InventreeService._transfer_stock_item(
            item,
            destino,
            notes=f"Transferencia entre depositos ERP por {usuario}",
        )
        return {"sucesso": True, "item_wms_id": item.id, "stock_item_id": stock_item_id, "location_id": destino}

    @staticmethod
    def sincronizar_estrutura_local():
        if not InventreeService.is_enabled():
            return {"sucesso": True, "mensagem": "Integracao InvenTree desabilitada", "depositos": 0, "localizacoes": 0}

        depositos = 0
        localizacoes = 0
        for deposito in DepositoWMS.query.filter_by(ativo=True).order_by(DepositoWMS.id.asc()).all():
            InventreeService._ensure_deposito_location(deposito.id)
            depositos += 1

        for localizacao in LocalizacaoArmazem.query.filter_by(ativo=True).order_by(LocalizacaoArmazem.id.asc()).all():
            InventreeService._ensure_localizacao(localizacao.id)
            localizacoes += 1

        return {"sucesso": True, "depositos": depositos, "localizacoes": localizacoes}

    @staticmethod
    def processar_evento(evento: WMSIntegracaoEvento):
        if not InventreeService.is_enabled():
            return {"sucesso": True, "mensagem": "InvenTree desabilitado"}

        payload = json.loads(evento.payload_json or "{}")
        usuario = str(payload.get("usuario") or "Integrador").strip()

        if evento.tipo_evento == InventreeService.EVENTO_NOTA_LANCADA:
            return InventreeService.sincronizar_nota_lancada(payload.get("numero_nota") or evento.referencia, usuario)
        if evento.tipo_evento == InventreeService.EVENTO_ENDERECO_MANUAL:
            return InventreeService.sincronizar_item_enderecado(int(payload.get("item_wms_id") or 0), usuario)
        if evento.tipo_evento == InventreeService.EVENTO_ESTORNO_ENDERECO:
            return InventreeService.sincronizar_estorno_enderecamento(int(payload.get("item_wms_id") or 0), usuario)
        if evento.tipo_evento == InventreeService.EVENTO_TRANSFERENCIA_DEPOSITO:
            return InventreeService.sincronizar_transferencia_deposito(int(payload.get("item_wms_id") or 0), usuario)

        return {"sucesso": False, "mensagem": f"Tipo de evento nao suportado: {evento.tipo_evento}"}
