"""Servico do modulo Intralog > Picking Almoxarifado.

A lista de material a separar vem AO VIVO da API do ERP
(INTRALOG_PICKING_API_URL, hoje https://columbia.consultoriarf.net/
listamaterialseparar) - nada e' importado/copiado pro banco. O banco do
Sync guarda SO' a confirmacao de separacao do almoxarifado (ver
IntralogPickingSeparacao).

Estrutura que a API devolve (lista de linhas):
    servico_raiz      OS Pai (ex.: "7844")
    n_servico         OS da linha (pode ser != da raiz - ai' e' OS filha)
    cod_os_completo   "<n_servico>/<aux>" (ex.: "7847/001")
    os                descricao da OS filha
    cod_interno       codigo do material (ex.: "19-01-00558" = chapa)
    item              descricao do material
    unidade / qtde / qtde_reservada / qtde_utilizada
    processado        "sim" | "nao" (status do ERP)
    origem_diferente  "SIM" quando a linha vem de OS != raiz
    produto / orcamento_raiz

AGREGACAO: a API nao tem id de linha e repete o mesmo (cod_os_completo,
cod_interno) varias vezes (ate' 10x), uma por demanda. O almoxarifado
separa o TOTAL daquele material pra aquela OS, entao agregamos por esse
par - que tambem e' a unica chave estavel pra prender a confirmacao.
"""
from __future__ import annotations

from datetime import datetime

import requests
from flask import current_app

from ..extensions import db
from ..models import IntralogPickingSeparacao

_URL_PADRAO = "https://columbia.consultoriarf.net/listamaterialseparar"


def _config(nome: str, padrao):
    return current_app.config.get(nome, padrao) if current_app else padrao


def buscar_linhas_api(timeout: int | None = None) -> list[dict]:
    """Consulta o endpoint do ERP e devolve as linhas cruas (lista de dicts).
    Levanta ValueError com mensagem amigavel se a resposta nao vier no
    formato esperado."""
    url = str(_config("INTRALOG_PICKING_API_URL", _URL_PADRAO) or "").strip() or _URL_PADRAO
    timeout = timeout or int(_config("INTRALOG_PICKING_API_TIMEOUT", 60))
    headers = {
        "Accept": "application/json",
        "ngrok-skip-browser-warning": "true",
        "User-Agent": "ColumbiaSync/1.0 (intralog-picking)",
    }
    resp = requests.get(url, headers=headers, timeout=timeout)
    resp.raise_for_status()
    dados = resp.json()
    if not isinstance(dados, list):
        raise ValueError("Resposta inesperada da API de material a separar (esperado uma lista).")
    return [linha for linha in dados if isinstance(linha, dict)]


def _num(valor) -> float:
    try:
        return float(valor or 0)
    except (TypeError, ValueError):
        return 0.0


def _txt(valor) -> str:
    return str(valor if valor is not None else "").strip()


# Familia do codigo interno que corresponde a CHAPA. O picking do
# almoxarifado separa material de todo tipo - a chapa nao e' o foco, so'
# ganha uma tag na linha pra ser identificada de bate-pronto.
FAMILIA_CHAPA = "19-01"


def familia_do_material(cod_interno: str) -> str:
    """Familia = 2 primeiros blocos do codigo interno (ex.: "19-01" = chapa)."""
    partes = _txt(cod_interno).split("-")
    return "-".join(partes[:2]) if len(partes) >= 2 else _txt(cod_interno)


def eh_chapa(cod_interno: str) -> bool:
    return familia_do_material(cod_interno) == FAMILIA_CHAPA


def _chave(linha: dict) -> tuple[str, str]:
    return (_txt(linha.get("cod_os_completo")), _txt(linha.get("cod_interno")))


def agregar_materiais(linhas: list[dict]) -> dict[tuple[str, str], dict]:
    """Agrega as linhas da API por (cod_os_completo, cod_interno), somando
    as quantidades e consolidando o 'processado' do ERP em contagem."""
    agregado: dict[tuple[str, str], dict] = {}
    for linha in linhas:
        chave = _chave(linha)
        if not chave[0] or not chave[1]:
            continue
        item = agregado.get(chave)
        if item is None:
            item = agregado[chave] = {
                "cod_os_completo": chave[0],
                "cod_interno": chave[1],
                "servico_raiz": _txt(linha.get("servico_raiz")),
                "n_servico": _txt(linha.get("n_servico")),
                "os": _txt(linha.get("os")),
                "item": _txt(linha.get("item")),
                "unidade": _txt(linha.get("unidade")),
                "produto": _txt(linha.get("produto")),
                "orcamento_raiz": _txt(linha.get("orcamento_raiz")),
                "familia": familia_do_material(chave[1]),
                "eh_chapa": eh_chapa(chave[1]),
                "origem_diferente": _txt(linha.get("origem_diferente")).upper() == "SIM",
                "qtde": 0.0,
                "qtde_reservada": 0.0,
                "qtde_utilizada": 0.0,
                "linhas_total": 0,
                "linhas_processadas": 0,
            }
        item["qtde"] += _num(linha.get("qtde"))
        item["qtde_reservada"] += _num(linha.get("qtde_reservada"))
        item["qtde_utilizada"] += _num(linha.get("qtde_utilizada"))
        item["linhas_total"] += 1
        if _txt(linha.get("processado")).lower() == "sim":
            item["linhas_processadas"] += 1

    for item in agregado.values():
        item["qtde"] = round(item["qtde"], 4)
        item["qtde_reservada"] = round(item["qtde_reservada"], 4)
        item["qtde_utilizada"] = round(item["qtde_utilizada"], 4)
        # Status do ERP consolidado: todas processadas / parcial / nenhuma.
        if item["linhas_processadas"] == 0:
            item["erp_status"] = "pendente"
        elif item["linhas_processadas"] == item["linhas_total"]:
            item["erp_status"] = "processado"
        else:
            item["erp_status"] = "parcial"
    return agregado


def _confirmacoes_por_chave() -> dict[tuple[str, str], IntralogPickingSeparacao]:
    return {
        (registro.cod_os_completo, registro.cod_interno): registro
        for registro in IntralogPickingSeparacao.query.all()
    }


def montar_arvore(linhas: list[dict] | None = None, busca: str = "", status: str = "", familia: str = "") -> dict:
    """Monta a LISTA PAI: uma entrada por OS Pai (servico_raiz), com todas
    as OS filhas que a compoem e os materiais agregados de cada uma, ja
    cruzado com a confirmacao de separacao do almoxarifado.

    Filtros (todos opcionais):
      busca   - OS Pai, OS filha, codigo/descricao do material, produto, orcamento
      status  - "pendente" | "separado" (pela confirmacao do ALMOXARIFADO)
      familia - ex.: "19-01" (chapa)
    """
    if linhas is None:
        linhas = buscar_linhas_api()

    agregado = agregar_materiais(linhas)
    confirmacoes = _confirmacoes_por_chave()

    busca_norm = _txt(busca).lower()
    familia_norm = _txt(familia)
    status_norm = _txt(status).lower()

    raizes: dict[str, dict] = {}
    for chave, material in agregado.items():
        registro = confirmacoes.get(chave)
        material = dict(material)
        material["separado"] = bool(registro and registro.separado)
        material["separado_em"] = registro.separado_em.strftime("%d/%m/%Y %H:%M") if registro and registro.separado_em else None
        material["separado_por"] = registro.separado_por if registro else None
        material["observacao"] = registro.observacao if registro else None
        # Divergencia: a demanda mudou na API depois da separacao confirmada.
        material["qtde_separada_snapshot"] = registro.qtde_snapshot if registro else None
        material["qtde_mudou"] = bool(
            registro and registro.separado and registro.qtde_snapshot is not None
            and abs(_num(registro.qtde_snapshot) - material["qtde"]) > 0.01
        )

        if familia_norm and material["familia"] != familia_norm:
            continue
        if status_norm == "separado" and not material["separado"]:
            continue
        if status_norm == "pendente" and material["separado"]:
            continue
        if busca_norm:
            alvo = " ".join([
                material["servico_raiz"], material["n_servico"], material["cod_os_completo"],
                material["os"], material["cod_interno"], material["item"],
                material["produto"], material["orcamento_raiz"],
            ]).lower()
            if busca_norm not in alvo:
                continue

        raiz_id = material["servico_raiz"] or material["n_servico"]
        raiz = raizes.get(raiz_id)
        if raiz is None:
            raiz = raizes[raiz_id] = {
                "servico_raiz": raiz_id,
                "produto": material["produto"],
                "orcamento_raiz": material["orcamento_raiz"],
                "os_filhas": {},
            }
        if not raiz["produto"]:
            raiz["produto"] = material["produto"]
        if not raiz["orcamento_raiz"]:
            raiz["orcamento_raiz"] = material["orcamento_raiz"]

        filha = raiz["os_filhas"].get(material["cod_os_completo"])
        if filha is None:
            filha = raiz["os_filhas"][material["cod_os_completo"]] = {
                "cod_os_completo": material["cod_os_completo"],
                "n_servico": material["n_servico"],
                "os": material["os"],
                # OS filha de servico diferente da raiz (origem_diferente=SIM)
                "outra_origem": material["origem_diferente"],
                "materiais": [],
            }
        filha["materiais"].append(material)

    # Converte pra listas ordenadas e calcula os totais de cada nivel.
    resultado = []
    for raiz in raizes.values():
        filhas = []
        for filha in raiz["os_filhas"].values():
            filha["materiais"].sort(key=lambda m: (m["cod_interno"], m["item"]))
            filha["qtd_materiais"] = len(filha["materiais"])
            filha["qtd_separados"] = sum(1 for m in filha["materiais"] if m["separado"])
            filhas.append(filha)
        filhas.sort(key=lambda f: f["cod_os_completo"])

        materiais_raiz = [m for f in filhas for m in f["materiais"]]
        resultado.append({
            "servico_raiz": raiz["servico_raiz"],
            "produto": raiz["produto"],
            "orcamento_raiz": raiz["orcamento_raiz"],
            "os_filhas": filhas,
            "qtd_os_filhas": len(filhas),
            "qtd_materiais": len(materiais_raiz),
            "qtd_separados": sum(1 for m in materiais_raiz if m["separado"]),
            "qtd_erp_pendentes": sum(1 for m in materiais_raiz if m["erp_status"] != "processado"),
            "familias": sorted({m["familia"] for m in materiais_raiz}),
        })
    resultado.sort(key=lambda r: r["servico_raiz"])

    total_materiais = sum(r["qtd_materiais"] for r in resultado)
    total_separados = sum(r["qtd_separados"] for r in resultado)
    return {
        "os_pais": resultado,
        "metricas": {
            "os_pais": len(resultado),
            "os_filhas": sum(r["qtd_os_filhas"] for r in resultado),
            "materiais": total_materiais,
            "separados": total_separados,
            "pendentes": total_materiais - total_separados,
        },
        "familias_disponiveis": sorted({m["familia"] for m in agregado.values()}),
    }


def _obter_ou_criar(cod_os_completo: str, cod_interno: str) -> IntralogPickingSeparacao:
    cod_os_completo = _txt(cod_os_completo)
    cod_interno = _txt(cod_interno)
    if not cod_os_completo or not cod_interno:
        raise ValueError("Informe a OS e o código do material.")
    registro = IntralogPickingSeparacao.query.filter_by(
        cod_os_completo=cod_os_completo, cod_interno=cod_interno
    ).first()
    if registro is None:
        registro = IntralogPickingSeparacao(
            cod_os_completo=cod_os_completo, cod_interno=cod_interno
        )
        db.session.add(registro)
    return registro


def confirmar_separacao(
    cod_os_completo: str, cod_interno: str, usuario: str,
    servico_raiz: str = "", qtde: float | None = None, unidade: str = "",
) -> IntralogPickingSeparacao:
    registro = _obter_ou_criar(cod_os_completo, cod_interno)
    if registro.separado:
        raise ValueError("Esse material já está separado.")
    registro.separado = True
    registro.separado_em = datetime.now()
    registro.separado_por = usuario
    if servico_raiz:
        registro.servico_raiz = _txt(servico_raiz)[:30]
    if qtde is not None:
        registro.qtde_snapshot = _num(qtde)
    if unidade:
        registro.unidade_snapshot = _txt(unidade)[:10]
    db.session.commit()
    return registro


def estornar_separacao(cod_os_completo: str, cod_interno: str) -> IntralogPickingSeparacao:
    registro = _obter_ou_criar(cod_os_completo, cod_interno)
    if not registro.separado:
        raise ValueError("Esse material ainda não foi separado.")
    registro.separado = False
    registro.separado_em = None
    registro.separado_por = None
    registro.qtde_snapshot = None
    db.session.commit()
    return registro


def salvar_observacao(cod_os_completo: str, cod_interno: str, observacao: str | None) -> IntralogPickingSeparacao:
    registro = _obter_ou_criar(cod_os_completo, cod_interno)
    registro.observacao = _txt(observacao)[:2000] or None
    db.session.commit()
    return registro
