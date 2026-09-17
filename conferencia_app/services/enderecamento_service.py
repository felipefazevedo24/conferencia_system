"""Saldos físicos, transferências atômicas e fila de atualização de locais no GRV."""
from datetime import datetime
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace

from flask import current_app

from ..extensions import db
from ..models import (EnderecoSaldo as Saldo, EnderecoMovimento as Movimento,
                      LocalizacaoArmazem, RecebimentoEnderecamento as Tarefa,
                      RecebimentoEnderecamentoTrava as Trava)
from . import recebimento_enderecamento_service as receb


def texto(valor, nome, limite):
    valor = str(valor or '').strip()
    if not valor or len(valor) > limite or ';' in valor:
        raise ValueError(f'Informe {nome} válido (até {limite} caracteres).')
    return valor


def quantidade(valor):
    try:
        q = Decimal(str(valor).replace(',', '.'))
        if not q.is_finite() or q <= 0 or q >= Decimal('1000000000000'):
            raise ValueError()
        if q != q.quantize(Decimal('.000001')):
            raise ValueError()
        return q
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError('Informe uma quantidade válida, com até seis casas decimais.')


def normalizar(codigo):
    """A etiqueta é a fonte da verdade, mas 'R1 PD1', 'r1-pd1' e 'R1-PD1' não
    podem virar três endereços diferentes no catálogo."""
    return ' '.join(str(codigo or '').split()).upper()


def local(codigo, recebe=True):
    codigo = normalizar(texto(codigo, 'um endereço', 80))
    registro = LocalizacaoArmazem.query.filter_by(codigo=codigo).first()
    # Endereço desativado não recebe material, mas o que já está lá precisa
    # poder sair — senão desativar um endereço prende o conteúdo dele.
    if registro and not registro.ativo and recebe:
        raise ValueError('Endereço desativado. Solicite a regularização ao responsável.')
    if not registro:
        db.session.add(LocalizacaoArmazem(codigo=codigo, corredor='', prateleira='', posicao=''))
    return codigo


def saldo(sku, endereco, unidade, conferido=False):
    outros = Saldo.query.filter_by(sku=sku).first()
    if outros and outros.unidade != unidade:
        raise ValueError(f'Este SKU é controlado em {outros.unidade}. Use a mesma unidade.')
    registro = Saldo.query.filter_by(sku=sku, endereco=endereco).first()
    if not registro:
        registro = Saldo(sku=sku, endereco=endereco, unidade=unidade,
                         quantidade=0, conferido=conferido)
        db.session.add(registro)
        db.session.flush()
    return registro


def chave_recebimento(tarefa):
    # SQLite pode reutilizar IDs depois de uma exclusão; a criação identifica a tarefa original.
    return f'recebimento:{tarefa.id}:{tarefa.criado_em.isoformat()}'


def creditar_recebimento(tarefa, enderecos_anteriores):
    """Chamado dentro da transação e trava do recebimento; uma entrada por tarefa."""
    chave = chave_recebimento(tarefa)
    if Movimento.query.filter_by(chave=chave).first():
        return
    unidade = str(tarefa.unidade or tarefa.item.unidade_comercial or 'UN').strip().upper()
    parcelas = []
    total = Decimal(0)
    for parcela in tarefa.alocacoes:
        # A conferência usa floats (ex.: 0.1 * 3); normalize o resíduo binário no livro físico.
        q = quantidade(str(Decimal(str(parcela['quantidade'])).quantize(Decimal('.000001'))))
        reg = saldo(tarefa.sku, parcela['endereco'], unidade,
                    conferido=parcela['endereco'] not in enderecos_anteriores)
        reg.quantidade += q
        reg.atualizado_em = datetime.now()
        total += q
        parcelas.append({**parcela, 'quantidade': str(q)})
    db.session.add(Movimento(chave=chave, sku=tarefa.sku, unidade=unidade,
        tipo='Recebimento', quantidade=total,
        usuario=tarefa.confirmado_por or tarefa.criado_por,
        motivo=f'NF {tarefa.item.numero_nota}',
        detalhes={'tarefa': tarefa.id, 'nota': tarefa.item.numero_nota, 'alocacoes': parcelas},
        sincronizado_em=datetime.now()))


def enderecos_atuais(sku):
    """Endereços vigentes do material.

    Enquanto uma operação não foi sincronizada, é ela que descreve onde o
    material está: consultar o GRV nesse intervalo devolveria a lista velha e
    a operação seguinte seria recusada sem motivo."""
    pendente = (Movimento.query.filter_by(sku=sku, sincronizado_em=None)
                .order_by(Movimento.id.desc()).first())
    if isinstance(pendente.detalhes if pendente else None, dict) and pendente.detalhes.get('depois'):
        return list(pendente.detalhes['depois'])
    return receb.enderecos(receb.buscar_localizacao_produto_grv(sku))


def resultado(atuais, origem, destino):
    """Lista de endereços que passa a valer depois da operação.

    Com origem, o material sai dela — mover é substituir. Sem origem, ele passa
    a constar também no destino, que é o caso de guardar em dois lugares. A
    grafia que já está no GRV é preservada para não duplicar endereço por
    diferença de maiúscula ou espaço."""
    depois = [e for e in atuais if not (origem and normalizar(e) == origem)]
    if destino not in {normalizar(e) for e in depois}:
        depois.append(destino)
    return depois


def registrar(dados, usuario):
    """Muda o material de endereço no GRV.

    O Sync não controla saldo — isso é do GRV. O que sai daqui é só a lista de
    endereços do SKU; a quantidade é anotação do histórico e não entra em
    cálculo nenhum."""
    sku = texto(dados.get('sku'), 'o SKU', 80)
    chave = 'operacao:' + texto(dados.get('chave'), 'a identificação da operação', 64)
    unidade = texto(dados.get('unidade'), 'a unidade', 20).upper()
    q = quantidade(dados.get('quantidade'))
    destino = normalizar(texto(dados.get('destino'), 'o destino', 80))
    informada = str(dados.get('origem') or '').strip()
    # Sem origem o material passa a constar também no destino, sem sair de onde está.
    origem = normalizar(texto(informada, 'a origem', 80)) if informada else None
    if origem == destino:
        raise ValueError('Origem e destino devem ser diferentes.')
    # Motivo é opcional: exigir texto a cada operação trava o operador no chão.
    motivo = str(dados.get('motivo') or '').strip()[:500]
    assinatura = dict(sku=sku, unidade=unidade, quantidade=str(q),
                      origem=origem, destino=destino, motivo=motivo)
    token = receb.adquirir_trava(SimpleNamespace(sku=sku))
    try:
        anterior = Movimento.query.filter_by(chave=chave).first()
        if anterior:
            if anterior.usuario != usuario or anterior.detalhes.get('pedido') != assinatura:
                raise ValueError('Esta identificação já pertence a outra operação. Atualize a tela.')
            return anterior.id
        # Uma entrada já confirmada deve terminar antes de mover o mesmo material.
        if Tarefa.query.filter_by(sku=sku, status='Aguardando sincronização').first():
            raise ValueError('Há um recebimento deste SKU aguardando sincronização. Sincronize-o primeiro.')
        atuais = enderecos_atuais(sku)
        if origem and origem not in {normalizar(e) for e in atuais}:
            raise ValueError('A origem não consta nos endereços atuais do material. Atualize a consulta.')
        destino = local(destino)
        if origem:
            local(origem, recebe=False)
        depois = resultado(atuais, origem, destino)
        mov = Movimento(chave=chave, sku=sku, unidade=unidade, tipo='Movimentação',
            origem=origem, destino=destino, quantidade=q, usuario=usuario, motivo=motivo,
            detalhes={'pedido': assinatura, 'antes': atuais, 'depois': depois})
        db.session.add(mov)
        db.session.flush()
        mov_id = mov.id
        db.session.commit()
        return mov_id
    except Exception:
        db.session.rollback()
        raise
    finally:
        Trava.query.filter_by(sku=sku, token=token).update({'token': None, 'expira_em': None})
        db.session.commit()


def sincronizar(sku):
    token = receb.adquirir_trava(SimpleNamespace(sku=sku))
    try:
        pendentes = (Movimento.query.filter_by(sku=sku, sincronizado_em=None)
                     .order_by(Movimento.id).all())
        if not pendentes:
            return
        # Cada operação guardou a lista completa que deve valer depois dela, e
        # encadeia a anterior — um envio só resolve a fila inteira.
        locais = []
        for mov in pendentes:
            if isinstance(mov.detalhes, dict) and mov.detalhes.get('depois'):
                locais = list(mov.detalhes['depois'])
        # A integração existente não aceita localização vazia; não registrar sucesso fictício.
        if not locais:
            raise ValueError('O GRV não permite limpar a última localização por esta integração.')
        resposta = receb.atualizar_localizacao_estoque(sku, ';'.join(locais))
        if isinstance(resposta, dict) and (resposta.get('sucesso') is False or resposta.get('success') is False):
            raise ValueError('GRV recusou a atualização. Tente sincronizar novamente.')
        for mov in pendentes:
            mov.sincronizado_em = datetime.now()
            mov.erro = None
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        current_app.logger.exception('Falha ao sincronizar movimentação do SKU %s', sku)
        erro = str(exc) if isinstance(exc, ValueError) else 'Operação salva no Sync. A comunicação com o GRV falhou; tente sincronizar novamente.'
        Movimento.query.filter_by(sku=sku, sincronizado_em=None).update({'erro': erro[:500]})
        db.session.commit()
    finally:
        Trava.query.filter_by(sku=sku, token=token).update({'token': None, 'expira_em': None})
        db.session.commit()
