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
CFOPS_QUALIDADE_PERMITIDOS = {"1124", "2124"}
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


def disparar_qualidade_se_necessario(numero_nota: str) -> QualidadeCertificado | None:
    """Cria (se ainda não existir) a pendência de análise de qualidade para a NF
    informada quando o remetente for um dos fornecedores monitorados.

    Não faz commit — o chamador é responsável por confirmar a transação.
    Retorna o registro criado, ou None quando não se aplica.
    """
    numero_nota = str(numero_nota or "").strip()
    if not numero_nota:
        return None

    nota_item = ItemNota.query.filter_by(numero_nota=numero_nota).first()
    if not nota_item:
        return None

    if not fornecedor_exige_qualidade(nota_item.fornecedor):
        return None

    if not nota_elegivel_para_qualidade(numero_nota):
        return None

    ja_existe = QualidadeCertificado.query.filter_by(numero_nota=numero_nota).first()
    if ja_existe:
        return ja_existe

    registro = QualidadeCertificado(
        numero_nota=numero_nota,
        chave_acesso=nota_item.chave_acesso,
        fornecedor=nota_item.fornecedor,
        status="Pendente de análise",
    )
    db.session.add(registro)
    return registro
