"""Termo de retirada de veículo: geração, listagem e guarda do termo assinado."""
from __future__ import annotations

import os
from datetime import datetime

from sqlalchemy.orm import defer

from ..extensions import db
from ..models import AgendamentoVeiculo, ViagemRetiradaVeiculo
from ..tempo import agora_br

POSTO_NOME = "AUTO POSTO 101 LTDA"
POSTO_CNPJ = "18.658.693/0001-70"

EXTENSOES_ASSINADO = {"pdf", "jpg", "jpeg", "png", "webp", "heic"}
# O app não tem MAX_CONTENT_LENGTH: foto de celular passa fácil de 5 MB, mas
# acima disso é quase sempre arquivo errado.
TAMANHO_MAX_ASSINADO = 20 * 1024 * 1024


def clausulas() -> list[str]:
    return [
        "O veículo deve ser usado somente a serviço da Columbia Machine Brasil, "
        "no trajeto e local informados neste termo.",
        f"Abastecimento, quando necessário, somente no posto {POSTO_NOME} "
        f"(CNPJ {POSTO_CNPJ}).",
        "O solicitante declara possuir CNH válida e compatível com a categoria do veículo.",
        "O solicitante deve respeitar o Código de Trânsito Brasileiro: limites de "
        "velocidade, sinalização, uso do cinto de segurança, proibição do celular ao "
        "volante e de dirigir após consumo de bebida alcoólica.",
        "O solicitante é responsável pelas infrações de trânsito cometidas entre a "
        "retirada e a devolução registradas neste termo e será indicado como condutor "
        "infrator junto ao órgão de trânsito. Nos termos do art. 462, § 1º, da CLT, "
        "autoriza desde já o desconto em folha de pagamento do valor das multas "
        "decorrentes dessas infrações.",
        "O veículo deve ser devolvido nas mesmas condições em que foi retirado. "
        "Qualquer avaria, ocorrência ou problema mecânico deve ser comunicado ao "
        "responsável pela liberação.",
    ]


def _texto(dados: dict, chave: str, rotulo: str, limite: int) -> str:
    valor = str(dados.get(chave) or "").strip()
    if not valor:
        raise ValueError(f"Informe {rotulo}.")
    return valor[:limite]


def criar(dados: dict, usuario: str) -> ViagemRetiradaVeiculo:
    try:
        veiculo_id = int(dados.get("veiculo_id") or 0)
    except (TypeError, ValueError):
        veiculo_id = 0
    veiculo = db.session.get(AgendamentoVeiculo, veiculo_id) if veiculo_id else None
    if not veiculo:
        raise ValueError("Selecione o veículo.")

    retirado_por = _texto(dados, "retirado_por", "o nome de quem está retirando", 160)
    local = _texto(dados, "local", "o local", 200)
    liberado_por = _texto(dados, "liberado_por", "o responsável pela liberação", 160)

    data = str(dados.get("data") or "").strip()
    hora = str(dados.get("hora") or "").strip()
    try:
        retirada_em = datetime.strptime(f"{data} {hora}", "%Y-%m-%d %H:%M")
    except ValueError:
        raise ValueError("Informe data e horário da retirada.")

    try:
        km = int(str(dados.get("km") or "").replace(".", "").strip())
    except ValueError:
        raise ValueError("Informe o km do veículo na retirada.")
    if km < 0:
        raise ValueError("Km inválido.")

    r = ViagemRetiradaVeiculo(
        veiculo_id=veiculo.id,
        retirado_por=retirado_por,
        retirada_em=retirada_em,
        km_retirada=km,
        local=local,
        liberado_por=liberado_por,
        criado_por=usuario,
    )
    db.session.add(r)
    db.session.commit()
    return r


def obter(rid: int) -> ViagemRetiradaVeiculo | None:
    return db.session.get(ViagemRetiradaVeiculo, rid)


def veiculo_label(v: AgendamentoVeiculo | None) -> str:
    if not v:
        return "-"
    return v.nome_exibicao + (f" ({v.placa})" if v.placa else "")


def listar() -> list[dict]:
    # O arquivo assinado fica fora da listagem: são fotos de vários MB.
    rows = (
        ViagemRetiradaVeiculo.query
        .options(defer(ViagemRetiradaVeiculo.assinado_arquivo))
        .order_by(ViagemRetiradaVeiculo.retirada_em.desc(), ViagemRetiradaVeiculo.id.desc())
        .limit(500)
        .all()
    )
    veiculos = {v.id: v for v in AgendamentoVeiculo.query.filter(
        AgendamentoVeiculo.id.in_({r.veiculo_id for r in rows} or {0})
    )}
    return [
        {
            "id": r.id,
            "veiculo_label": veiculo_label(veiculos.get(r.veiculo_id)),
            "retirado_por": r.retirado_por,
            "retirada_em": r.retirada_em.isoformat() if r.retirada_em else None,
            "km_retirada": r.km_retirada,
            "local": r.local,
            "liberado_por": r.liberado_por,
            "assinado": r.assinado_em is not None,
            "assinado_em": r.assinado_em.isoformat() if r.assinado_em else None,
            "assinado_por": r.assinado_por,
        }
        for r in rows
    ]


def salvar_assinado(rid: int, conteudo: bytes, nome: str, mime: str | None, usuario: str) -> ViagemRetiradaVeiculo | None:
    r = obter(rid)
    if not r:
        return None
    ext = os.path.splitext(nome or "")[1].lower().lstrip(".")
    if ext not in EXTENSOES_ASSINADO:
        raise ValueError("Envie o termo assinado em PDF ou foto (JPG, PNG, WEBP, HEIC).")
    if not conteudo:
        raise ValueError("Arquivo vazio.")
    if len(conteudo) > TAMANHO_MAX_ASSINADO:
        raise ValueError("Arquivo acima de 20 MB.")
    r.assinado_arquivo = conteudo
    r.assinado_nome = (nome or f"retirada_{rid}.{ext}")[:200]
    r.assinado_mime = (mime or "application/octet-stream")[:100]
    r.assinado_em = agora_br()
    r.assinado_por = usuario
    db.session.commit()
    return r
