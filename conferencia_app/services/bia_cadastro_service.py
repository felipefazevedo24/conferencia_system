from __future__ import annotations

import re
import unicodedata

from .cadastro_workflow_service import (
    CAMPO_OPCOES,
    TIPOS_CADASTRO,
    cnpj_valido,
    consultar_cartao_cnpj,
    criar_solicitacao,
)


def _normalizar(texto: str) -> str:
    txt = (texto or "").strip().lower()
    txt = unicodedata.normalize("NFKD", txt)
    return "".join(c for c in txt if not unicodedata.combining(c))


def _intencao_cadastro(q: str) -> bool:
    return any(
        termo in q
        for termo in (
            "solicitacao de cadastro",
            "solicitacao cadastro",
            "solicitar cadastro",
            "abrir cadastro",
            "novo cadastro",
            "criar cadastro",
            "cadastro de ",
        )
    )


def _tipo_from_texto(q: str) -> str | None:
    mapa = {
        "material": "material",
        "cliente": "cliente",
        "fornecedor": "fornecedor",
        "transportadora": "transportadora",
    }
    for termo, tipo in mapa.items():
        if termo in q:
            return tipo
    return None


def _campos_fluxo(tipo: str) -> list[str]:
    if tipo in {"cliente", "fornecedor", "transportadora"}:
        return ["documento"]
    campos = []
    for campo, _label, obrigatorio in TIPOS_CADASTRO[tipo].get("solicitante_fields") or []:
        if obrigatorio:
            campos.append(campo)
    return campos


def _lote_campos(estado: dict, max_por_lote: int = 3) -> list[str]:
    pendentes = [c for c in estado.get("campos", []) if not str(estado.get("dados", {}).get(c) or "").strip()]
    return pendentes[:max_por_lote]


def _rotulo_campo(tipo: str, campo: str) -> str:
    for c, label, _ob in TIPOS_CADASTRO[tipo]["fields"]:
        if c == campo:
            return label
    return campo.replace("_", " ").title()


def _opcoes_resumo(campo: str) -> str:
    opcoes = CAMPO_OPCOES.get(campo) or []
    if not opcoes:
        return ""
    preview = ", ".join(f"{v} ({lbl.split(' - ')[-1]})" for v, lbl in opcoes[:6])
    if len(opcoes) > 6:
        preview += ", ..."
    return preview


def _pergunta_lote(tipo: str, lote: list[str]) -> str:
    if not lote:
        return ""
    if len(lote) == 1:
        campo = lote[0]
        label = _rotulo_campo(tipo, campo)
        extra = ""
        if campo == "documento":
            extra = " (somente CNPJ)"
        opcoes = _opcoes_resumo(campo)
        opcoes_txt = f"\nOpcoes aceitas: {opcoes}." if opcoes else ""
        return f"Perfeito. Me informe {label}{extra}.{opcoes_txt}"

    linhas = ["Show. Para abrir a solicitacao, me mande estes campos:"]
    for idx, campo in enumerate(lote, start=1):
        label = _rotulo_campo(tipo, campo)
        linhas.append(f"{idx}) {label}")
        opcoes = _opcoes_resumo(campo)
        if opcoes:
            linhas.append(f"   Opcoes: {opcoes}")
    linhas.append("Pode responder em uma mensagem so, no formato campo: valor (uma linha por campo).")
    return "\n".join(linhas)


def _extrair_cnpj(texto: str) -> str:
    return re.sub(r"\D+", "", texto or "")


def _extrair_valores_lote(tipo: str, lote: list[str], texto: str) -> dict:
    bruto = (texto or "").strip()
    if not bruto:
        return {}

    aliases = {
        "descricao": ["descricao", "desc"],
        "unidade_medida": ["unidade", "unidade_medida", "um"],
        "utilizacao": ["utilizacao", "uso"],
        "fornecedor_sugerido": ["fornecedor", "fornecedor_sugerido"],
        "documento": ["cnpj", "documento"],
    }

    encontrados: dict[str, str] = {}
    linhas = [ln.strip() for ln in re.split(r"[\n;]", bruto) if ln.strip()]
    for ln in linhas:
        if ":" not in ln:
            continue
        chave, valor = ln.split(":", 1)
        chave_n = _normalizar(chave)
        valor = valor.strip()
        for campo in lote:
            possiveis = aliases.get(campo, [campo])
            if any(alias in chave_n for alias in possiveis) and valor:
                encontrados[campo] = valor

    if len(encontrados) == len(lote):
        return encontrados

    if len(lote) == 1 and lote[0] == "documento":
        cnpj = _extrair_cnpj(bruto)
        return {"documento": cnpj} if cnpj else {}

    if not encontrados:
        partes = [p.strip() for p in re.split(r"[\n;]", bruto) if p.strip()]
        if len(partes) >= len(lote):
            return {campo: partes[idx] for idx, campo in enumerate(lote)}

    return encontrados


def _validar_valor_campo(campo: str, valor: str) -> str | None:
    valor = (valor or "").strip()
    if campo == "documento":
        if not cnpj_valido(valor):
            return "O CNPJ informado parece invalido. Me envie um CNPJ com 14 digitos."
        return None

    opcoes = CAMPO_OPCOES.get(campo) or []
    if opcoes and valor:
        validos = {v for v, _lbl in opcoes}
        if valor not in validos:
            resumo = _opcoes_resumo(campo)
            return f"Valor invalido para {campo.replace('_', ' ')}. Use um destes codigos: {resumo}."
    return None


def _montar_dados_final(tipo: str, dados: dict) -> dict:
    if tipo not in {"cliente", "fornecedor", "transportadora"}:
        return dados

    documento = dados.get("documento") or ""
    contribuinte_icms = "9"
    try:
        consulta = consultar_cartao_cnpj(documento)
        if str(consulta.get("inscricao_estadual") or "").strip():
            contribuinte_icms = "1"
    except Exception:
        pass

    return {
        "documento": documento,
        "contribuinte_icms": contribuinte_icms,
    }


def interpretar(pergunta: str, ctx: dict, estado: dict | None) -> dict | None:
    q = _normalizar(pergunta)
    estado = estado or {}

    if any(t in q for t in ("cancelar", "parar", "deixa pra la", "esquece")) and estado.get("ativo"):
        return {
            "consumiu": True,
            "limpar_estado": True,
            "resposta": "Fluxo de solicitacao de cadastro cancelado. Quando quiser, me peça para abrir de novo.",
        }

    if not estado.get("ativo") and not _intencao_cadastro(q):
        return None

    if not estado.get("ativo"):
        tipo = _tipo_from_texto(q)
        if not tipo:
            return {
                "consumiu": True,
                "limpar_estado": True,
                "resposta": (
                    "Consigo abrir solicitacao de cadastro para material, cliente, fornecedor ou transportadora. "
                    "Me diga qual tipo voce quer."
                ),
            }
        estado = {
            "ativo": True,
            "tipo": tipo,
            "campos": _campos_fluxo(tipo),
            "dados": {},
        }
        lote = _lote_campos(estado)
        return {
            "consumiu": True,
            "estado": estado,
            "resposta": f"Beleza, vamos abrir uma solicitacao de cadastro de {tipo}.\n\n" + _pergunta_lote(tipo, lote),
        }

    tipo = estado.get("tipo")
    if tipo not in TIPOS_CADASTRO:
        return {
            "consumiu": True,
            "limpar_estado": True,
            "resposta": "Perdi o contexto do fluxo. Pode me pedir novamente a solicitacao de cadastro?",
        }

    lote = _lote_campos(estado)
    if not lote:
        return {
            "consumiu": True,
            "limpar_estado": True,
            "resposta": "Nao encontrei campos pendentes desse fluxo. Vamos reiniciar se precisar.",
        }

    extraidos = _extrair_valores_lote(tipo, lote, pergunta)
    faltantes = [campo for campo in lote if not str(extraidos.get(campo) or "").strip()]
    if faltantes:
        return {
            "consumiu": True,
            "estado": estado,
            "resposta": "Ainda faltou preencher alguns campos deste lote.\n\n" + _pergunta_lote(tipo, lote),
        }

    for campo in lote:
        valor = str(extraidos.get(campo) or "").strip()
        erro = _validar_valor_campo(campo, valor)
        if erro:
            return {
                "consumiu": True,
                "estado": estado,
                "resposta": erro + "\n\n" + _pergunta_lote(tipo, [campo]),
            }
        estado.setdefault("dados", {})[campo] = valor

    proximo_lote = _lote_campos(estado)
    if proximo_lote:
        return {
            "consumiu": True,
            "estado": estado,
            "resposta": "Perfeito, seguimos.\n\n" + _pergunta_lote(tipo, proximo_lote),
        }

    try:
        dados = _montar_dados_final(tipo, estado.get("dados") or {})
        solicitacao = criar_solicitacao(
            tipo=tipo,
            dados=dados,
            solicitante=str(ctx.get("username") or "usuario"),
            anexos="",
        )
    except ValueError as exc:
        return {
            "consumiu": True,
            "estado": estado,
            "resposta": f"Nao consegui abrir a solicitacao: {exc}",
        }
    except Exception:
        return {
            "consumiu": True,
            "estado": estado,
            "resposta": "Tive um erro ao abrir a solicitacao agora. Tente novamente em instantes.",
        }

    return {
        "consumiu": True,
        "limpar_estado": True,
        "resposta": (
            f"Solicitacao de cadastro criada com sucesso: #{solicitacao.numero} ({TIPOS_CADASTRO[tipo]['label']}).\n"
            "A validacao continua manual no workflow de Cadastro (Compras/Fiscal), como combinado."
        ),
    }