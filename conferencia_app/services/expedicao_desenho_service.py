"""Desenho da peca para a Conferencia de Expedicao, a partir do Nº OS do item.

O arquivo nao fica em pasta: o GRV guarda o PDF dentro do banco (coluna
`anexo` de tos_aux_desenhos / tos_aux_anexos). A localizacao reaproveita o
que o modulo de Producao ja' faz (inclusive o desenho herdado da estrutura de
origem) e as mesmas consultas ja' cadastradas na bridge do ERP - nao exige
atualizar a bridge.

Buscar no ERP a cada Nº OS da tela demorava: o resultado vira uma foto no
banco do Sync (ExpedicaoDesenhoFoto) que vale VALIDADE_FOTO. O PDF entra na
foto no primeiro clique e dali em diante abre direto do Sync.
"""

import logging
from datetime import timedelta

from sqlalchemy.exc import IntegrityError

from . import producao_service
from ..extensions import db
from ..models import ExpedicaoDesenhoFoto, ExpedicaoDesenhoFotoArquivo
from ..tempo import agora_br

logger = logging.getLogger(__name__)

# Revisao nova da Engenharia aparece em ate' 1h.
VALIDADE_FOTO = timedelta(hours=1)
# Vencida ha mais disso, a foto so' ocupava espaco (os PDFs ficam no MySQL).
DESCARTE_FOTO = timedelta(days=2)


def _numero_os(n_os) -> str:
    return str(n_os or "").strip()


def _itens_da_os(ordem: dict) -> list[tuple[int, str]]:
    """Subdivisoes da OS (11101/001, 11101/002...), na ordem. Olha todas: e'
    comum a /001 vir vazia e o desenho estar so' na /002."""
    itens = producao_service._metadata_read(
        producao_service.fetch_all,
        producao_service.queries.SQL_PRODUCAO_ESTRUTURA_OS,
        {"cod_empresa": 1, "cod_os": ordem["codigo"]},
    )
    return sorted(
        (int(item["aux_code"]), str(item.get("cod_os_completo") or "").strip())
        for item in itens if item.get("aux_code") is not None
    )


def _subdivisao(cod_os_completo: str) -> str:
    return "/" + cod_os_completo.split("/", 1)[1] if "/" in cod_os_completo else cod_os_completo


def _desenhos(documentos: list[dict]) -> list[dict]:
    """O que estiver em tos_aux_desenhos manda, o principal da Producao na
    frente. Antes o principal (que em OS CMS e' anexo) passava na frente do
    desenho e a expedicao abria arquivo errado."""
    desenhos = [d for d in documentos if d.get("kind") == "drawing"]
    return sorted(desenhos, key=lambda d: not d.get("is_primary"))


def _reserva(documentos: list[dict]) -> dict | None:
    """Sem desenho em subdivisao nenhuma: anexo principal ou outro PDF."""
    return (
        next((d for d in documentos if d.get("is_primary")), None)
        or next((d for d in documentos if str(d.get("filename") or "").lower().endswith(".pdf")), None)
    )


def _localizar_erp(numero_os: str) -> list[dict]:
    """Documentos que servem de desenho da OS, direto do ERP. Junta os
    desenhos de todas as subdivisoes; cada um leva a sua em "subdivisao"."""
    try:
        ordem = producao_service._obter_ordem(numero_os)
    except LookupError:
        return []
    itens = _itens_da_os(ordem)
    por_item: list[tuple[str, list[dict]]] = []
    for aux_code, completo in itens:
        try:
            documentos = producao_service.obter_documentos(numero_os, aux_code, ordem=ordem)
        except LookupError:
            continue
        rotulo = _subdivisao(completo) if len(itens) > 1 else ""
        por_item.append((rotulo, [{**d, "subdivisao": rotulo} for d in documentos]))

    escolhidos: list[dict] = []
    # A Producao pode herdar o mesmo desenho de outra subdivisao com o mesmo
    # nº de desenho: o arquivo entra uma vez so'.
    vistos = set()
    for _rotulo, documentos in por_item:
        for d in _desenhos(documentos):
            chave = (d.get("source_kind"), d.get("id"), d.get("source_cod_os"), d.get("source_aux_code"))
            if chave not in vistos:
                vistos.add(chave)
                escolhidos.append(d)
    if not escolhidos:
        reserva = next((r for _rotulo, docs in por_item if (r := _reserva(docs))), None)
        escolhidos = [reserva] if reserva else []
    return escolhidos


def _chave(tipo, documento_id, origem_cod_os, origem_aux_code, revisao) -> tuple:
    return (str(tipo), int(documento_id), int(origem_cod_os), int(origem_aux_code), str(revisao or ""))


def _gravar_foto(numero_os: str, foto: ExpedicaoDesenhoFoto | None, documentos: list[dict]) -> ExpedicaoDesenhoFoto:
    novos = [
        (d, _chave(d["source_kind"], d["id"], d["source_cod_os"], d["source_aux_code"], d.get("content_revision")))
        for d in documentos
    ]
    aproveitar: dict[tuple, bytes] = {}
    if foto is None:
        foto = ExpedicaoDesenhoFoto(n_os=numero_os)
        db.session.add(foto)
    else:
        # Mesmo arquivo na mesma revisao: o PDF ja' baixado continua valendo.
        chaves_novas = {chave for _d, chave in novos}
        for a in foto.arquivos:
            chave = _chave(a.tipo, a.documento_id, a.origem_cod_os, a.origem_aux_code, a.revisao_conteudo)
            if a.revisao_conteudo and chave in chaves_novas and a.conteudo is not None:
                aproveitar[chave] = a.conteudo
        foto.arquivos.clear()
    foto.foto_em = agora_br()
    for posicao, (d, chave) in enumerate(novos):
        foto.arquivos.append(ExpedicaoDesenhoFotoArquivo(
            posicao=posicao,
            nome_arquivo=str(d.get("filename") or "documento")[:260],
            subdivisao=d.get("subdivisao") or "",
            tipo=str(d["source_kind"]),
            documento_id=int(d["id"]),
            aux_code=int(d["aux_code"]),
            origem_cod_os=int(d["source_cod_os"]),
            origem_aux_code=int(d["source_aux_code"]),
            revisao_conteudo=str(d.get("content_revision") or "") or None,
            conteudo=aproveitar.get(chave),
        ))
    try:
        db.session.commit()
    except IntegrityError:
        # Outra aba tirou a foto da mesma OS ao mesmo tempo: vale a dela.
        db.session.rollback()
        return ExpedicaoDesenhoFoto.query.filter_by(n_os=numero_os).one()
    _descartar_fotos_velhas()
    return foto


def _descartar_fotos_velhas() -> None:
    try:
        limite = agora_br() - DESCARTE_FOTO
        for velha in ExpedicaoDesenhoFoto.query.filter(ExpedicaoDesenhoFoto.foto_em < limite).limit(50):
            db.session.delete(velha)
        db.session.commit()
    except Exception:  # noqa: BLE001 - limpeza nao pode derrubar a consulta
        db.session.rollback()
        logger.warning("Falha ao descartar fotos velhas de desenho", exc_info=True)


def localizar(n_os) -> dict | None:
    """Desenhos da OS ({"documentos": [...]}), ou None se ela nao tiver. Sai
    da foto enquanto ela vale; vencida, refaz pelo ERP. ERP fora com foto
    vencida: usa a vencida. ERP fora sem foto: levanta excecao - quem chama
    decide o que mostrar."""
    numero_os = _numero_os(n_os)
    if not numero_os:
        return None
    foto = ExpedicaoDesenhoFoto.query.filter_by(n_os=numero_os).first()
    if foto is None or agora_br() - foto.foto_em >= VALIDADE_FOTO:
        try:
            documentos = _localizar_erp(numero_os)
        except Exception:
            if foto is None:
                raise
            logger.warning("ERP fora: desenhos da OS %s saem da foto de %s", numero_os, foto.foto_em, exc_info=True)
        else:
            foto = _gravar_foto(numero_os, foto, documentos)
    if not foto.arquivos:
        return None
    return {
        "numero_os": numero_os,
        "documentos": [
            {"filename": a.nome_arquivo, "subdivisao": a.subdivisao, "arquivo_id": a.id}
            for a in foto.arquivos
        ],
    }


def os_filhas(n_os) -> list[dict]:
    """OS que a OS do item precisa (aba Dependencias do GRV), da filha direta
    ate' a ultima neta, na ordem da arvore. Reaproveita o rastreio da Producao:
    ele parte do orcamento, entao OS sem orcamento vinculado volta sem filhas.
    Levanta excecao se o ERP estiver fora."""
    numero_os = _numero_os(n_os)
    if not numero_os:
        return []
    try:
        grafo = producao_service.obter_dependencias(numero_os)
    except LookupError:
        return []
    titulos = {no["number"]: no["title"] for no in grafo["nodes"]}
    filhas_de: dict[str, list[str]] = {}
    for ligacao in grafo["edges"]:
        filhas_de.setdefault(ligacao["dependent_order_number"], []).append(ligacao["prerequisite_order_number"])
    raiz = grafo["selected_order_number"] or numero_os
    resultado: list[dict] = []
    # A mesma OS pode atender dois pais; aparece uma vez so', no primeiro.
    vistas = {raiz}

    def descer(pai: str, nivel: int) -> None:
        for filha in sorted(filhas_de.get(pai, []), key=lambda n: (len(n), n)):
            if filha in vistas:
                continue
            vistas.add(filha)
            resultado.append({"numero": filha, "titulo": titulos.get(filha, ""), "nivel": nivel})
            descer(filha, nivel + 1)

    descer(raiz, 1)
    return resultado


def obter_arquivo(n_os, indice: int = 0) -> tuple[bytes, str]:
    """`indice` e' a posicao em localizar()["documentos"]; 0 e' o principal."""
    achado = localizar(n_os)
    if not achado:
        raise LookupError("Esta OS não tem desenho anexado no ERP.")
    if not 0 <= indice < len(achado["documentos"]):
        raise LookupError("Desenho não encontrado nesta OS.")
    return _conteudo(achado["documentos"][indice]["arquivo_id"])


def obter_arquivos(n_os) -> list[tuple[bytes, str]]:
    """Todos os desenhos da OS, na ordem de localizar()."""
    achado = localizar(n_os)
    if not achado:
        raise LookupError("Esta OS não tem desenho anexado no ERP.")
    return [_conteudo(documento["arquivo_id"]) for documento in achado["documentos"]]


def _conteudo(arquivo_id: int) -> tuple[bytes, str]:
    """PDF da foto; na primeira vez busca no ERP direto pela referencia
    guardada (sem refazer a busca da OS) e guarda."""
    arquivo = db.session.get(ExpedicaoDesenhoFotoArquivo, arquivo_id)
    if arquivo is None:
        raise LookupError("Desenho não encontrado nesta OS.")
    nome = arquivo.nome_arquivo or "documento"
    if arquivo.conteudo is not None:
        return bytes(arquivo.conteudo), nome
    conteudo, _nome_erp = producao_service.obter_arquivo(
        arquivo.foto.n_os,
        arquivo.aux_code,
        arquivo.tipo,
        arquivo.documento_id,
        # Com a origem informada a Producao nao precisa buscar a OS de novo.
        ordem={"codigo": arquivo.origem_cod_os},
        source_cod_os=arquivo.origem_cod_os,
        source_aux_code=arquivo.origem_aux_code,
    )
    try:
        arquivo.conteudo = conteudo
        db.session.commit()
    except Exception:  # noqa: BLE001 - sem guardar, abre do mesmo jeito
        db.session.rollback()
        logger.warning("PDF do desenho %s nao coube na foto", arquivo_id, exc_info=True)
    return conteudo, nome
