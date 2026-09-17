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


def quantidade(valor, zero=False):
    try:
        q = Decimal(str(valor).replace(',', '.'))
        if not q.is_finite() or q < 0 or (not zero and q == 0) or q >= Decimal('1000000000000'):
            raise ValueError()
        if q != q.quantize(Decimal('.000001')):
            raise ValueError()
        return q
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError('Informe uma quantidade válida, com até seis casas decimais.')


def local(codigo):
    codigo = texto(codigo, 'um endereço', 80)
    registro = LocalizacaoArmazem.query.filter_by(codigo=codigo).first()
    if registro and not registro.ativo:
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


def registrar(dados, usuario, pode_ajustar=False):
    sku = texto(dados.get('sku'), 'o SKU', 80)
    chave = 'operacao:' + texto(dados.get('chave'), 'a identificação da operação', 64)
    tipo = dados.get('tipo')
    if tipo not in ('Transferência', 'Conferência de saldo'):
        raise ValueError('Operação inválida.')
    if tipo == 'Conferência de saldo' and not pode_ajustar:
        raise PermissionError('Somente responsáveis autorizados podem conferir saldos.')
    unidade = texto(dados.get('unidade'), 'a unidade', 20).upper()
    q = quantidade(dados.get('quantidade'), zero=tipo == 'Conferência de saldo')
    destino = texto(dados.get('destino'), 'o destino', 80)
    origem = texto(dados.get('origem'), 'a origem', 80) if tipo == 'Transferência' else None
    motivo = str(dados.get('motivo') or '').strip()
    if len(motivo) < 5 or len(motivo) > 500:
        raise ValueError('Informe um motivo com 5 a 500 caracteres.')
    assinatura = dict(sku=sku, tipo=tipo, unidade=unidade, quantidade=str(q),
                      origem=origem, destino=destino, motivo=motivo)
    token = receb.adquirir_trava(SimpleNamespace(sku=sku))
    try:
        anterior = Movimento.query.filter_by(chave=chave).first()
        if anterior:
            if anterior.usuario != usuario or anterior.detalhes.get('pedido') != assinatura:
                raise ValueError('Esta identificação já pertence a outra operação. Atualize a tela.')
            return anterior.id
        # Uma entrada já confirmada deve terminar antes de conferir/mover seu saldo.
        if Tarefa.query.filter_by(sku=sku, status='Aguardando sincronização').first():
            raise ValueError('Há um recebimento deste SKU aguardando sincronização. Sincronize-o primeiro.')
        atuais = receb.enderecos(receb.buscar_localizacao_produto_grv(sku))
        destino = local(destino)
        alvo = saldo(sku, destino, unidade, conferido=destino not in atuais)
        detalhes = {'pedido': assinatura, 'destino_antes': str(alvo.quantidade)}
        if tipo == 'Transferência':
            if origem == destino:
                raise ValueError('Origem e destino devem ser diferentes.')
            local(origem)
            fonte = Saldo.query.filter_by(sku=sku, endereco=origem).first()
            if not fonte or not fonte.conferido or not alvo.conferido:
                raise ValueError('Confira o saldo inicial dos endereços antes de movimentar este material.')
            if fonte.quantidade < q:
                raise ValueError('Quantidade maior que o saldo disponível na origem. Atualize a consulta.')
            detalhes['origem_antes'] = str(fonte.quantidade)
            fonte.quantidade -= q
            fonte.atualizado_em = datetime.now()
            alvo.quantidade += q
        else:
            # Contagem física substitui o saldo, incluindo zero; diferença permanece no histórico.
            detalhes['diferenca'] = str(q - alvo.quantidade)
            alvo.quantidade = q
            alvo.conferido = True
        alvo.atualizado_em = datetime.now()
        mov = Movimento(chave=chave, sku=sku, unidade=unidade, tipo=tipo,
            origem=origem, destino=destino, quantidade=q, usuario=usuario,
            motivo=motivo, detalhes=detalhes)
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
        pendentes = Movimento.query.filter_by(sku=sku, sincronizado_em=None).all()
        if not pendentes:
            return
        atuais = receb.enderecos(receb.buscar_localizacao_produto_grv(sku))
        saldos = Saldo.query.filter_by(sku=sku).all()
        tocados = {e for m in pendentes for e in (m.origem, m.destino) if e}
        zerados = {s.endereco for s in saldos if s.conferido and s.quantidade == 0 and s.endereco in tocados}
        locais = list(dict.fromkeys([e for e in atuais if e not in zerados] +
                                   [s.endereco for s in saldos if s.quantidade > 0]))
        # A integração existente não aceita localização vazia; não registrar sucesso fictício.
        if not locais:
            raise ValueError('Saldo zerado no Sync. O GRV não permite limpar a última localização por esta integração.')
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
