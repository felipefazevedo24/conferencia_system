"""Endereço dos materiais no GRV: movimentação, catálogo de locais e fila de envio.

O saldo é do GRV. O que o Sync grava aqui é em qual endereço cada material
está, mais o histórico de quem mudou o quê."""
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace

from flask import current_app

from ..extensions import db
from ..models import (EnderecoSaldo as Saldo, EnderecoMovimento as Movimento,
                      LocalizacaoArmazem, RecebimentoEnderecamento as Tarefa,
                      RecebimentoEnderecamentoTrava as Trava)
from . import recebimento_enderecamento_service as receb
from ..tempo import agora_br


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


# Serviço e uso-e-consumo sem estoque entram no recebimento mas não têm
# endereço. O padrão fica aqui, não só no config, para a regra não virar
# inócua em silêncio onde a configuração não estiver carregada.
FAMILIAS_SEM_ENDERECO = ('09', '41')

# Exclusões que valem só no cruzamento família+grupo: produto em processo
# (família 03) não é endereçado quando é produção por terceiros, mas o que é
# feito aqui dentro continua sendo. O GRV manda o grupo como CÓDIGO numérico
# (p.cod_grupo), não como nome, então os pares esperam o código do grupo.
# Vazio = regra inativa: um cruzamento sem grupo conhecido não casa com nada.
FAMILIA_GRUPO_SEM_ENDERECO = ()  # ex.: (('03', '12'),)


def codigo_familia(familia):
    """'N - 09 - SERVIÇOS' → '09'."""
    achado = re.search(r'\d{1,3}', str(familia or ''))
    return achado.group(0).lstrip('0').zfill(2) if achado else None


def sem_enderecamento(familia, grupo=None):
    """Material que não controla estoque nem tem endereço.

    Vale pela família sozinha (serviço, uso e consumo sem estoque) ou pelo
    cruzamento família+grupo, quando a família só é excluída em parte dos
    casos."""
    codigo = codigo_familia(familia)
    if not codigo:
        return False
    familias = current_app.config.get('ENDERECAMENTO_FAMILIAS_SEM_ENDERECO', FAMILIAS_SEM_ENDERECO)
    if codigo in {str(c).lstrip('0').zfill(2) for c in familias}:
        return True
    alvo = str(grupo or '').strip()
    if not alvo:
        return False
    pares = current_app.config.get('ENDERECAMENTO_FAMILIA_GRUPO_SEM_ENDERECO', FAMILIA_GRUPO_SEM_ENDERECO)
    return any(codigo == str(fam).lstrip('0').zfill(2) and alvo == str(grp).strip()
               for fam, grp in pares)


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
        reg.atualizado_em = agora_br()
        total += q
        parcelas.append({**parcela, 'quantidade': str(q)})
    db.session.add(Movimento(chave=chave, sku=tarefa.sku, unidade=unidade,
        tipo='Recebimento', quantidade=total,
        usuario=tarefa.confirmado_por or tarefa.criado_por,
        motivo=f'NF {tarefa.item.numero_nota}',
        detalhes={'tarefa': tarefa.id, 'nota': tarefa.item.numero_nota, 'alocacoes': parcelas},
        sincronizado_em=agora_br()))


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


def materiais_no_endereco(endereco, atualizar=True):
    """SKUs que estão no endereço, segundo o GRV. Sem cache por padrão: quem
    chama está prestes a escrever, e agir sobre lista velha mexeria no material
    errado."""
    from .erp_estoque_service import buscar_estoque_grv
    alvo = normalizar(endereco)
    achados = []
    for sku, agregado in (buscar_estoque_grv(forcar_atualizacao=atualizar).get('por_codigo') or {}).items():
        locais = [normalizar(e) for bruto in (agregado.get('localizacoes') or [])
                  for e in str(bruto).split(';')]
        if alvo in locais:
            achados.append(sku)
    return sorted(achados)


def esvaziar(endereco, usuario):
    """Tira o endereço de todos os materiais que estão nele.

    Usado ao desativar um endereço: o lugar sai de circulação e nada mais deve
    apontar para ele. Material que ficaria sem endereço nenhum não é tocado —
    a integração do GRV recusa localização vazia (HTTP 422) — e volta no
    relatório para o responsável decidir o destino."""
    endereco = normalizar(texto(endereco, 'um endereço', 80))
    relatorio = {'endereco': endereco, 'limpos': [], 'sem_outro_endereco': [], 'falhas': []}
    for sku in materiais_no_endereco(endereco):
        token = receb.adquirir_trava(SimpleNamespace(sku=sku))
        try:
            atuais = enderecos_atuais(sku)
            depois = [e for e in atuais if normalizar(e) != endereco]
            if not any(normalizar(e) == endereco for e in atuais):
                continue  # saiu de lá entre a consulta e agora
            if not depois:
                relatorio['sem_outro_endereco'].append(sku)
                continue
            db.session.add(Movimento(
                chave=f'desativacao:{endereco}:{sku}:{agora_br().isoformat()}',
                sku=sku, unidade='', tipo='Endereço desativado', origem=endereco,
                destino=None, quantidade=0, usuario=usuario,
                motivo=f'Endereço {endereco} desativado',
                detalhes={'antes': atuais, 'depois': depois}))
            db.session.commit()
            relatorio['limpos'].append(sku)
        except Exception as exc:
            db.session.rollback()
            current_app.logger.exception('Falha ao tirar o endereço %s do SKU %s', endereco, sku)
            relatorio['falhas'].append({'sku': sku, 'erro': str(exc)[:200]})
        finally:
            Trava.query.filter_by(sku=sku, token=token).update({'token': None, 'expira_em': None})
            db.session.commit()
    # O envio ao GRV vai por fora da trava, como no resto do módulo.
    for sku in relatorio['limpos']:
        try:
            sincronizar(sku)
        except Exception:
            current_app.logger.exception('Envio ao GRV pendente após desativar %s (SKU %s)', endereco, sku)
    return relatorio


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
            mov.sincronizado_em = agora_br()
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
