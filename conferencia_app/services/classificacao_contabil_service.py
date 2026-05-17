from __future__ import annotations

import re
import unicodedata
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import openpyxl
from sqlalchemy import func

from ..extensions import db
from ..models import ClassificacaoContabilItem, ClassificacaoContabilPadrao, ItemNota


ARQUIVOS_PADRAO_2026 = [
    Path(r"z:\CUSTOS\ESTOQUE\2026\04-Abril 2026\Classificação entradas\Entradas 01 a 29 - FINAL PARA CONTABIL.xlsx"),
    Path(r"z:\CUSTOS\ESTOQUE\2026\03-Março 2026\Classificações entradas\Entradas 30 _ CHECk _ FINAL.xlsx"),
    Path(r"z:\CUSTOS\ESTOQUE\2026\02-Fevereiro 2026\Classificações entradas\Entradas Fevereiro  01 A 26.xlsx"),
]


HEADER_ALIASES = {
    "fornecedor": ["Entrada:Fornecedor", "fornecedor", "FORNECEDOR"],
    "cfop": ["Itens da Entrada de NF:CFOP", "cfop", "CFOP"],
    "codigo": ["Itens da Entrada de NF:Cód. interno /Cód. fabricante", "cod_interno", "Material"],
    "descricao": ["Itens da Entrada de NF:Descrição", "produto"],
    "conta": ["Conta"],
    "nome_conta": ["Nome conta"],
    "comentario": ["comentario", "comentário"],
}


def normalizar_texto(value: Any) -> str:
    text = str(value or "").strip().upper()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^A-Z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _normalizar_codigo(value: Any) -> str:
    return normalizar_texto(value).replace(" ", "")


def _valor(row: tuple, index: dict[str, int], campo: str) -> Any:
    for header in HEADER_ALIASES[campo]:
        pos = index.get(header)
        if pos is not None and pos < len(row):
            return row[pos]
    return None


def _linha_cabecalho(sheet) -> tuple[int, list[str]] | None:
    for row_number, row in enumerate(sheet.iter_rows(min_row=1, max_row=6, values_only=True), start=1):
        headers = [str(value).strip() if value is not None else "" for value in row]
        if "Conta" in headers and ("Nome conta" in headers) and any(h in headers for h in HEADER_ALIASES["fornecedor"]):
            return row_number, headers
    return None


def _iter_linhas_classificadas(workbook_source, origem_nome: str):
    workbook = openpyxl.load_workbook(workbook_source, read_only=True, data_only=True)
    for sheet in workbook.worksheets:
        header_info = _linha_cabecalho(sheet)
        if not header_info:
            continue
        header_row, headers = header_info
        index = {header: pos for pos, header in enumerate(headers) if header}

        for row in sheet.iter_rows(min_row=header_row + 1, values_only=True):
            conta = str(_valor(row, index, "conta") or "").strip()
            nome_conta = str(_valor(row, index, "nome_conta") or "").strip()
            if not conta or not nome_conta:
                continue
            fornecedor = _valor(row, index, "fornecedor")
            cfop = _valor(row, index, "cfop")
            codigo = _valor(row, index, "codigo")
            descricao = _valor(row, index, "descricao")
            if not any([fornecedor, cfop, codigo, descricao]):
                continue
            yield {
                "fornecedor_norm": normalizar_texto(fornecedor),
                "cfop": str(cfop or "").strip(),
                "codigo_norm": _normalizar_codigo(codigo),
                "descricao_norm": normalizar_texto(descricao),
                "conta": conta,
                "nome_conta": nome_conta,
                "comentario": str(_valor(row, index, "comentario") or "").strip(),
                "origem": f"{origem_nome} / {sheet.title}",
            }


def importar_padroes_excel(paths: list[Path] | None = None) -> dict:
    paths = paths or ARQUIVOS_PADRAO_2026
    agregadas: Counter[tuple] = Counter()
    exemplos: dict[tuple, dict] = {}
    arquivos_lidos = []

    for path in paths:
        if not path.exists():
            continue
        arquivos_lidos.append(str(path))
        for row in _iter_linhas_classificadas(path, path.name):
            key = (
                row["fornecedor_norm"],
                row["cfop"],
                row["codigo_norm"],
                row["descricao_norm"],
                row["conta"],
            )
            agregadas[key] += 1
            exemplos.setdefault(key, row)

    agora = datetime.now()
    criados = 0
    atualizados = 0
    for key, ocorrencias in agregadas.items():
        row = exemplos[key]
        padrao = ClassificacaoContabilPadrao.query.filter_by(
            fornecedor_norm=row["fornecedor_norm"],
            cfop=row["cfop"],
            codigo_norm=row["codigo_norm"],
            descricao_norm=row["descricao_norm"],
            conta=row["conta"],
        ).first()
        if padrao is None:
            padrao = ClassificacaoContabilPadrao(
                fornecedor_norm=row["fornecedor_norm"],
                cfop=row["cfop"],
                codigo_norm=row["codigo_norm"],
                descricao_norm=row["descricao_norm"],
                conta=row["conta"],
            )
            db.session.add(padrao)
            criados += 1
        else:
            atualizados += 1
        padrao.nome_conta = row["nome_conta"]
        padrao.comentario = row["comentario"][:500]
        padrao.ocorrencias = int(ocorrencias)
        padrao.origem = row["origem"][:120]
        padrao.atualizado_em = agora

    db.session.commit()
    return {
        "arquivos_lidos": len(arquivos_lidos),
        "linhas_agregadas": sum(agregadas.values()),
        "padroes_criados": criados,
        "padroes_atualizados": atualizados,
        "padroes_total": ClassificacaoContabilPadrao.query.count(),
    }


def importar_padroes_uploads(arquivos) -> dict:
    agregadas: Counter[tuple] = Counter()
    exemplos: dict[tuple, dict] = {}
    arquivos_lidos = []

    for arquivo in arquivos:
        filename = str(getattr(arquivo, "filename", "") or "upload.xlsx").strip()
        if not filename.lower().endswith((".xlsx", ".xlsm")):
            continue
        try:
            arquivo.stream.seek(0)
        except Exception:
            pass
        arquivos_lidos.append(filename)
        for row in _iter_linhas_classificadas(arquivo.stream, filename):
            key = (
                row["fornecedor_norm"],
                row["cfop"],
                row["codigo_norm"],
                row["descricao_norm"],
                row["conta"],
            )
            agregadas[key] += 1
            exemplos.setdefault(key, row)

    agora = datetime.now()
    criados = 0
    atualizados = 0
    for key, ocorrencias in agregadas.items():
        row = exemplos[key]
        padrao = ClassificacaoContabilPadrao.query.filter_by(
            fornecedor_norm=row["fornecedor_norm"],
            cfop=row["cfop"],
            codigo_norm=row["codigo_norm"],
            descricao_norm=row["descricao_norm"],
            conta=row["conta"],
        ).first()
        if padrao is None:
            padrao = ClassificacaoContabilPadrao(
                fornecedor_norm=row["fornecedor_norm"],
                cfop=row["cfop"],
                codigo_norm=row["codigo_norm"],
                descricao_norm=row["descricao_norm"],
                conta=row["conta"],
            )
            db.session.add(padrao)
            criados += 1
        else:
            atualizados += 1
        padrao.nome_conta = row["nome_conta"]
        padrao.comentario = row["comentario"][:500]
        padrao.ocorrencias = int(padrao.ocorrencias or 0) + int(ocorrencias)
        padrao.origem = row["origem"][:120]
        padrao.atualizado_em = agora

    db.session.commit()
    return {
        "arquivos_lidos": len(arquivos_lidos),
        "linhas_agregadas": sum(agregadas.values()),
        "padroes_criados": criados,
        "padroes_atualizados": atualizados,
        "padroes_total": ClassificacaoContabilPadrao.query.count(),
    }


def _conta_dominante(query):
    rows = query.all()
    if not rows:
        return None
    total = sum(int(r.ocorrencias or 1) for r in rows)
    por_conta: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = (row.conta, row.nome_conta)
        slot = por_conta.setdefault(key, {"ocorrencias": 0, "row": row})
        slot["ocorrencias"] += int(row.ocorrencias or 1)
    melhor = max(por_conta.values(), key=lambda entry: entry["ocorrencias"])
    return melhor["row"], melhor["ocorrencias"], total


def sugerir_classificacao_item(item: ItemNota) -> dict:
    fornecedor = normalizar_texto(item.fornecedor)
    codigo = _normalizar_codigo(item.codigo)
    descricao = normalizar_texto(item.descricao)
    cfop = str(item.cfop or "").strip()

    tentativas = [
        (
            "Fornecedor + codigo + CFOP",
            98,
            ClassificacaoContabilPadrao.query.filter_by(fornecedor_norm=fornecedor, codigo_norm=codigo, cfop=cfop),
        ),
        (
            "Fornecedor + codigo",
            92,
            ClassificacaoContabilPadrao.query.filter_by(fornecedor_norm=fornecedor, codigo_norm=codigo),
        ),
        (
            "Codigo + CFOP",
            86,
            ClassificacaoContabilPadrao.query.filter_by(codigo_norm=codigo, cfop=cfop),
        ),
        (
            "Fornecedor + descricao + CFOP",
            82,
            ClassificacaoContabilPadrao.query.filter_by(fornecedor_norm=fornecedor, descricao_norm=descricao, cfop=cfop),
        ),
        (
            "CFOP + descricao",
            70,
            ClassificacaoContabilPadrao.query.filter_by(cfop=cfop, descricao_norm=descricao),
        ),
        (
            "CFOP dominante",
            55,
            ClassificacaoContabilPadrao.query.filter_by(cfop=cfop),
        ),
    ]

    for metodo, confianca_base, query in tentativas:
        if "codigo" in metodo.lower() and not codigo:
            continue
        if "descricao" in metodo.lower() and not descricao:
            continue
        if "cfop" in metodo.lower() and not cfop:
            continue
        dominante = _conta_dominante(query)
        if not dominante:
            continue
        regra, ocorrencias, total = dominante
        consenso = ocorrencias / max(total, 1)
        confianca = min(99, round(confianca_base * consenso))
        return {
            "regra": regra,
            "conta": regra.conta,
            "nome_conta": regra.nome_conta,
            "comentario": regra.comentario or "",
            "confianca": int(confianca),
            "metodo": metodo,
            "status": "Classificado" if confianca >= 80 else "Revisar",
        }

    return {
        "regra": None,
        "conta": "",
        "nome_conta": "",
        "comentario": "",
        "confianca": 0,
        "metodo": "Sem padrão",
        "status": "Pendente",
    }


def classificar_item(item: ItemNota, sobrescrever_manual: bool = False) -> ClassificacaoContabilItem:
    existente = ClassificacaoContabilItem.query.filter_by(item_nota_id=item.id).first()
    if existente and existente.status == "Revisado" and not sobrescrever_manual:
        return existente

    sugestao = sugerir_classificacao_item(item)
    agora = datetime.now()
    registro = existente or ClassificacaoContabilItem(item_nota_id=item.id, numero_nota=str(item.numero_nota or ""))
    registro.numero_nota = str(item.numero_nota or "")
    registro.fornecedor = item.fornecedor
    registro.codigo_item = item.codigo
    registro.descricao_item = item.descricao
    registro.cfop = item.cfop
    registro.conta = sugestao["conta"]
    registro.nome_conta = sugestao["nome_conta"]
    registro.comentario = sugestao["comentario"][:500]
    registro.confianca = sugestao["confianca"]
    registro.metodo = sugestao["metodo"]
    registro.status = sugestao["status"]
    registro.regra_id = sugestao["regra"].id if sugestao["regra"] else None
    registro.atualizado_em = agora
    if existente is None:
        registro.criado_em = agora
        db.session.add(registro)
    return registro


def classificar_nota(numero_nota: str, sobrescrever_manual: bool = False) -> int:
    itens = ItemNota.query.filter_by(numero_nota=str(numero_nota), status="Lançado").all()
    total = 0
    for item in itens:
        classificar_item(item, sobrescrever_manual=sobrescrever_manual)
        total += 1
    db.session.commit()
    return total


def classificar_lancadas_desde_2026(limite: int = 500) -> int:
    query = (
        ItemNota.query.outerjoin(ClassificacaoContabilItem, ClassificacaoContabilItem.item_nota_id == ItemNota.id)
        .filter(ItemNota.status == "Lançado")
        .filter(ItemNota.data_lancamento >= datetime(2026, 1, 1))
        .filter(ClassificacaoContabilItem.id.is_(None))
        .order_by(ItemNota.data_lancamento.desc())
        .limit(limite)
    )
    total = 0
    for item in query.all():
        classificar_item(item)
        total += 1
    db.session.commit()
    return total


def resumo_classificacoes(data_inicio: datetime | None = None, data_fim: datetime | None = None) -> dict:
    query = ClassificacaoContabilItem.query.join(ItemNota, ItemNota.id == ClassificacaoContabilItem.item_nota_id)
    query = query.filter(ItemNota.data_lancamento >= datetime(2026, 1, 1))
    if data_inicio:
        query = query.filter(ItemNota.data_lancamento >= data_inicio)
    if data_fim:
        query = query.filter(ItemNota.data_lancamento <= data_fim)

    total = query.count()
    revisao = query.filter(ClassificacaoContabilItem.status.in_(["Revisar", "Pendente"])).count()
    revisados = query.filter(ClassificacaoContabilItem.status == "Revisado").count()
    auto = query.filter(ClassificacaoContabilItem.status == "Classificado").count()
    confianca_media = query.with_entities(func.avg(ClassificacaoContabilItem.confianca)).scalar() or 0
    return {
        "total": total,
        "classificados": auto,
        "revisao": revisao,
        "revisados": revisados,
        "confianca_media": round(float(confianca_media), 1),
        "padroes": ClassificacaoContabilPadrao.query.count(),
    }
