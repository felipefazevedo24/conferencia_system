"""Regras de negócio da análise de Qualidade no recebimento.

Quando a conferência de recebimento de uma NF é finalizada e o remetente
(fornecedor/emitente) é um dos fornecedores de tratamento térmico monitorados
(Brasimet, Metal Paulista ou Friese), gera-se automaticamente uma pendência de
análise de qualidade para o analista preencher os dados do certificado.
"""
import unicodedata

from ..extensions import db
from ..models import ItemNota, QualidadeCertificado


# Palavras-chave (normalizadas, sem acento e minúsculas) que identificam os
# fornecedores que exigem análise de qualidade no recebimento.
FORNECEDORES_QUALIDADE = ("brasimet", "metal paulista", "friese")
CNPJS_QUALIDADE = {
    "60.856.820/0026-60",  # Brasimet
    "10.205.087/0001-97",  # Metal Paulista
    "43.201.912/0001-34",  # Friese
}
CFOPS_QUALIDADE_PERMITIDOS = {"1124", "2124", "5124", "6124"}
CFOPS_QUALIDADE_OCULTAR = {"5902", "5903"}


def _normalizar(texto: str | None) -> str:
    if not texto:
        return ""
    normalizado = unicodedata.normalize("NFKD", str(texto))
    sem_acento = "".join(c for c in normalizado if not unicodedata.combining(c))
    return sem_acento.strip().lower()


def fornecedor_exige_qualidade(fornecedor: str | None) -> bool:
    """Retorna True se o nome do fornecedor casar com um dos monitorados."""
    alvo = _normalizar(fornecedor)
    if not alvo:
        return False
    return any(chave in alvo for chave in FORNECEDORES_QUALIDADE)


def _normalizar_cfop(cfop: str | None) -> str:
    return "".join(ch for ch in str(cfop or "") if ch.isdigit())[:4]


def _normalizar_cnpj(cnpj: str | None) -> str:
    return "".join(ch for ch in str(cnpj or "") if ch.isdigit())[:14]


CNPJS_QUALIDADE_NORMALIZADOS = {_normalizar_cnpj(cnpj) for cnpj in CNPJS_QUALIDADE}


def fornecedor_exige_qualidade_por_cnpj(cnpj_emitente: str | None) -> bool:
    cnpj = _normalizar_cnpj(cnpj_emitente)
    if not cnpj:
        return False
    return cnpj in CNPJS_QUALIDADE_NORMALIZADOS


def _agrupar_cfops_por_nota(numeros_notas: list[str]) -> dict[str, set[str]]:
    notas_limpa = [str(n or "").strip() for n in numeros_notas if str(n or "").strip()]
    if not notas_limpa:
        return {}

    rows = (
        db.session.query(ItemNota.numero_nota, ItemNota.cfop)
        .filter(ItemNota.numero_nota.in_(notas_limpa))
        .all()
    )
    mapa: dict[str, set[str]] = {n: set() for n in notas_limpa}
    for numero_nota, cfop in rows:
        mapa.setdefault(numero_nota, set()).add(_normalizar_cfop(cfop))
    return mapa


def _cfops_elegiveis_para_qualidade(cfops: set[str]) -> bool:
    if not cfops:
        return False
    if cfops & CFOPS_QUALIDADE_OCULTAR:
        return False
    return bool(cfops & CFOPS_QUALIDADE_PERMITIDOS)


def nota_elegivel_para_qualidade(numero_nota: str) -> bool:
    numero_nota = str(numero_nota or "").strip()
    if not numero_nota:
        return False
    cfops_por_nota = _agrupar_cfops_por_nota([numero_nota])
    return _cfops_elegiveis_para_qualidade(cfops_por_nota.get(numero_nota, set()))


def notas_qualidade_visiveis_map(numeros_notas: list[str]) -> dict[str, bool]:
    cfops_por_nota = _agrupar_cfops_por_nota(numeros_notas)
    return {
        nota: _cfops_elegiveis_para_qualidade(cfops)
        for nota, cfops in cfops_por_nota.items()
    }


def inferir_os_por_pedido_compra(pedido_compra: str | None) -> str:
    pedido = str(pedido_compra or "").strip()
    if not pedido:
        return ""

    try:
        from .pedidos_service import buscar_linhas_pedido

        linhas = buscar_linhas_pedido(pedido)
    except Exception:
        return ""

    valores: list[str] = []
    vistos: set[str] = set()
    for linha in linhas or []:
        for chave in ("cod_os_completo", "cod_os", "n_os", "numero_os", "os_numero"):
            valor = str((linha or {}).get(chave) or "").strip()
            if not valor or valor in vistos:
                continue
            vistos.add(valor)
            valores.append(valor)
            break

    return ", ".join(valores)[:120]


def sincronizar_qualidade_por_pedido(numero_nota: str, pedido_compra: str | None) -> bool:
    numero_nota = str(numero_nota or "").strip()
    if not numero_nota:
        return False

    os_inferida = inferir_os_por_pedido_compra(pedido_compra)
    if not os_inferida:
        return False

    registro = QualidadeCertificado.query.filter_by(numero_nota=numero_nota).first()
    if not registro:
        return False

    alterou = False
    if not str(registro.os or "").strip():
        registro.os = os_inferida
        alterou = True
    if not str(registro.grid_os or "").strip():
        registro.grid_os = os_inferida
        alterou = True
    if not str(registro.sapatas_os or "").strip():
        registro.sapatas_os = os_inferida
        alterou = True
    return alterou


def disparar_qualidade_se_necessario(numero_nota: str) -> QualidadeCertificado | None:
    """Cria (se ainda não existir) a pendência de análise de qualidade para a NF
    informada quando o remetente for um dos fornecedores monitorados.

    Não faz commit — o chamador é responsável por confirmar a transação.
    Retorna o registro criado, ou None quando não se aplica.
    """
    numero_nota = str(numero_nota or "").strip()
    if not numero_nota:
        return None

    itens_nota = ItemNota.query.filter_by(numero_nota=numero_nota).order_by(ItemNota.id.asc()).all()
    if not itens_nota:
        return None
    nota_item = itens_nota[0]

    # Prioriza o CNPJ do emitente (com normalizacao para aceitar com/sem
    # pontuacao). Mantemos fallback por nome para nao quebrar historicos
    # antigos que possam ter CNPJ ausente.
    if not (
        fornecedor_exige_qualidade_por_cnpj(nota_item.cnpj_emitente)
        or fornecedor_exige_qualidade(nota_item.fornecedor)
    ):
        return None

    if not nota_elegivel_para_qualidade(numero_nota):
        return None

    ja_existe = QualidadeCertificado.query.filter_by(numero_nota=numero_nota).first()
    if ja_existe:
        return ja_existe

    pedido_compra = next((str(item.pedido_compra or "").strip() for item in itens_nota if str(item.pedido_compra or "").strip()), "")
    os_inferida = inferir_os_por_pedido_compra(pedido_compra)

    registro = QualidadeCertificado(
        numero_nota=numero_nota,
        chave_acesso=nota_item.chave_acesso,
        fornecedor=nota_item.fornecedor,
        os=os_inferida or None,
        grid_os=os_inferida or None,
        sapatas_os=os_inferida or None,
        status="Pendente de análise",
    )
    db.session.add(registro)
    return registro
