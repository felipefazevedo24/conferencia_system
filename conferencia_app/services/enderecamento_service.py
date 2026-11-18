"""Endereço dos materiais no GRV: movimentação, catálogo de locais e fila de envio.

O saldo é do GRV. O que o Sync grava aqui é em qual endereço cada material
está, mais o histórico de quem mudou o quê."""
import re
from datetime import date, datetime, timedelta
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
# 33 (bens de pequeno valor) entrou em 06/10/2026: controla_estoque = 0 no GRV.
# 07 (insumos administrativos), 37 (material de terceiro) e 42 (matéria-prima
# material específico): decisão da Logística em 06/10/2026. Só saem das listas;
# endereçar pelo "Movimentar" continua permitido.
FAMILIAS_SEM_ENDERECO = ('07', '09', '33', '37', '41', '42')

# Exclusões que valem só no cruzamento família+grupo: produto em processo
# (família 03) não é endereçado quando é produção por terceiros, mas o que é
# feito aqui dentro continua sendo. O GRV manda o grupo como CÓDIGO numérico
# (p.cod_grupo), não como nome, então os pares esperam o código do grupo.
# Vazio = regra inativa: um cruzamento sem grupo conhecido não casa com nada.
# 03 + grupo 2 = "PRODUÇÃO POR TERCEIROS" (o grupo 1, produção própria, segue).
FAMILIA_GRUPO_SEM_ENDERECO = (('03', '2'),)


def codigo_familia(familia):
    """'N - 09 - SERVIÇOS' → '09'."""
    achado = re.search(r'\d{1,3}', str(familia or ''))
    return achado.group(0).lstrip('0').zfill(2) if achado else None


def sem_enderecamento(familia, grupo=None, controla_estoque=None):
    """Material que não controla estoque nem tem endereço.

    O que manda é o "controla estoque" do produto no GRV. Bridge antiga não
    manda o campo (None): aí vale a família sozinha (serviço, uso e consumo
    sem estoque) ou o cruzamento família+grupo."""
    if controla_estoque is not None and str(controla_estoque).strip() in ('0', 'False', 'false'):
        return True
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


# Grafias da mesma unidade (06/10/2026: conferente digitou "M" num SKU
# controlado em "MT" e o endereçamento travou). Só sinônimos de verdade:
# PC e UN continuam diferentes, não dá para afirmar que são a mesma coisa.
SINONIMOS_UNIDADE = {
    'M': ('M', 'MT', 'MTS', 'MTR', 'METRO', 'METROS'),
    'UN': ('UN', 'UND', 'UNID', 'UNIDADE', 'UNIDADES'),
    'PC': ('PC', 'PÇ', 'PCS', 'PÇS', 'PCA', 'PECA', 'PEÇA', 'PECAS', 'PEÇAS'),
    'KG': ('KG', 'KGS', 'QUILO', 'QUILOS'),
    'L': ('L', 'LT', 'LTS', 'LITRO', 'LITROS'),
    'M2': ('M2', 'M²'),
    'M3': ('M3', 'M³'),
}
_UNIDADE_CANONICA = {grafia: base for base, grafias in SINONIMOS_UNIDADE.items() for grafia in grafias}


def unidade_canonica(unidade):
    texto = ' '.join(str(unidade or '').split()).upper().rstrip('.')
    return _UNIDADE_CANONICA.get(texto, texto)


# Quantidade por endereço (decisão da Logística em 17/11/2026): material com
# UM endereço não tem nada gravado - a quantidade do endereço é o saldo do GRV,
# calculada na hora. Só material com 2+ endereços tem quantidade por endereço
# no Sync (EnderecoSaldo), na unidade de estoque do GRV, mexida por
# recebimento, movimentação, contagem e pelas saídas do GRV (TSAIDAAX).
# `conferido` diz se a quantidade é confiável (partiu de zero conhecido ou de
# contagem); só endereço conferido que zera sai sozinho do GRV.

def _q(valor):
    return Decimal(str(valor or 0)).quantize(Decimal('.000001'))


def info_grv(sku, fresco=True):
    """Cadastro e saldo do material no GRV, ou None se a consulta falhar.

    `fresco`: consulta só deste código, na hora (quem chama vai escrever).
    Bridge antiga sem o endpoint por código cai no snapshot com cache."""
    from . import erp_estoque_service as erp
    codigo = str(sku or '').strip().upper()
    try:
        try:
            estoque = erp.buscar_estoque_produtos_grv([codigo]) if fresco else erp.buscar_estoque_grv()
        except LookupError:
            estoque = erp.buscar_estoque_grv()
    except Exception:
        current_app.logger.warning('Saldo do GRV indisponível para %s', codigo, exc_info=True)
        return None
    return (estoque.get('por_codigo') or {}).get(codigo)


def _linha(sku, endereco, unidade):
    """Linha de quantidade do material no endereço, na unidade do GRV. Linha
    antiga em outra unidade (chapa em KG x UND, unidade do fornecedor) não é
    comparável: recomeça zerada e a conferir."""
    reg = Saldo.query.filter_by(sku=sku, endereco=endereco).first()
    if not reg:
        reg = Saldo(sku=sku, endereco=endereco, unidade=unidade, quantidade=0, conferido=False)
        db.session.add(reg)
    elif unidade_canonica(reg.unidade) != unidade_canonica(unidade):
        reg.quantidade, reg.conferido = 0, False
    reg.unidade = unidade
    reg.atualizado_em = agora_br()
    return reg


def _descontrolar(sku):
    """Voltou a ter um endereço só (ou nenhum): a quantidade é a do GRV."""
    Saldo.query.filter_by(sku=sku).delete(synchronize_session=False)


def _sem_zerados(sku, lista):
    """Tira da lista o endereço conferido que zerou. Nunca esvazia a lista:
    o último endereço só sai quando o saldo inteiro do GRV zerar."""
    zerados = {r.endereco for r in Saldo.query.filter_by(sku=sku, conferido=True)
               if r.quantidade <= 0}
    restantes = [e for e in lista if normalizar(e) not in zerados]
    return restantes or list(lista)[-1:]


def _fechar_controle(sku, depois, podar=True):
    """`podar=False` quando a lista já foi enviada ao GRV e não pode mudar aqui."""
    if podar:
        depois = _sem_zerados(sku, depois)
    if len(depois) <= 1:
        _descontrolar(sku)
    else:
        vigentes = {normalizar(e) for e in depois}
        for reg in Saldo.query.filter_by(sku=sku):
            if reg.endereco not in vigentes:
                db.session.delete(reg)
    return depois


def chave_recebimento(tarefa):
    # SQLite pode reutilizar IDs depois de uma exclusão; a criação identifica a tarefa original.
    return f'recebimento:{tarefa.id}:{tarefa.criado_em.isoformat()}'


def creditar_recebimento(tarefa, enderecos_anteriores):
    """Chamado dentro da transação e trava do recebimento, DEPOIS que o GRV já
    aceitou os endereços: aqui nada de regra pode levantar erro, senão a
    tarefa fica presa em "Aguardando sincronização" reenviando para sempre.
    Unidade que não bate só deixa de creditar e o endereço fica a conferir."""
    chave = chave_recebimento(tarefa)
    if Movimento.query.filter_by(chave=chave).first():
        return
    sku = tarefa.sku
    unidade = str(tarefa.unidade or tarefa.item.unidade_comercial or 'UN').strip().upper()
    antes = list(dict.fromkeys(normalizar(e) for e in enderecos_anteriores or [] if normalizar(e)))
    parcelas = []
    for parcela in tarefa.alocacoes:
        # A conferência usa floats (ex.: 0.1 * 3); normalize o resíduo binário.
        parcelas.append({**parcela, 'endereco': normalizar(parcela['endereco']),
                         'quantidade': str(_q(parcela['quantidade']))})
    depois = list(dict.fromkeys(antes + [p['endereco'] for p in parcelas]))
    total = sum((Decimal(p['quantidade']) for p in parcelas), Decimal(0))
    detalhes = {'tarefa': tarefa.id, 'nota': tarefa.item.numero_nota, 'alocacoes': parcelas}
    if len(depois) <= 1:
        _descontrolar(sku)
    else:
        info = info_grv(sku, fresco=False) or {}
        unidade_grv = str(info.get('unidade') or '').strip().upper()
        if not unidade_grv or unidade_canonica(unidade) != unidade_canonica(unidade_grv):
            for endereco in depois:
                _linha(sku, endereco, unidade_grv or unidade)
            detalhes['sem_credito'] = (f'Quantidade em {unidade} e o estoque é controlado em {unidade_grv}: '
                                       'conte os endereços.' if unidade_grv else
                                       'Saldo do GRV indisponível: conte os endereços.')
        else:
            if len(antes) == 1 and not Saldo.query.filter_by(sku=sku, endereco=antes[0]).first():
                # Era o único endereço, com o saldo todo do GRV. Se a NF já entrou
                # no GRV, o saldo já inclui esta entrada. Estimativa: a conferir.
                base = _q(info.get('qtde_total')) - (total if tarefa.item.status == 'Lançado' else 0)
                reg = _linha(sku, antes[0], unidade_grv)
                reg.quantidade, reg.conferido = max(base, Decimal(0)), False
            for p in parcelas:
                reg = _linha(sku, p['endereco'], unidade_grv)
                if p['endereco'] not in antes:
                    reg.quantidade, reg.conferido = Decimal(0), True  # endereço novo: parte do zero
                reg.quantidade += Decimal(p['quantidade'])
        db.session.flush()
        _fechar_controle(sku, depois, podar=False)
    db.session.add(Movimento(chave=chave, sku=sku, unidade=unidade,
        tipo='Recebimento', quantidade=total,
        usuario=tarefa.confirmado_por or tarefa.criado_por,
        motivo=f'NF {tarefa.item.numero_nota}',
        detalhes=detalhes, sincronizado_em=agora_br()))


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


def _movimentar_quantidade(sku, atuais, origem, destino, q, unidade):
    """Aplica a quantidade movimentada quando o material tem (ou passa a ter)
    2+ endereços e devolve a lista de endereços que passa a valer.

    Transferência com sobra conhecida na origem mantém a origem na lista
    (parcial); sem sobra conhecida, a origem sai, como sempre foi."""
    vigentes = list(dict.fromkeys(normalizar(e) for e in atuais if normalizar(e)))
    if len(set(vigentes) | {destino}) < 2:
        return resultado(atuais, origem, destino)
    info = info_grv(sku)
    if info is None:
        raise ValueError('Saldo do GRV indisponível para conferir a quantidade. Tente novamente em instantes.')
    unidade_grv = str(info.get('unidade') or '').strip().upper()
    if unidade_grv and unidade_canonica(unidade) != unidade_canonica(unidade_grv):
        raise ValueError(f'Informe a quantidade em {unidade_grv}, a unidade de estoque deste material.')
    unidade_grv = unidade_grv or unidade
    if len(vigentes) == 1 and not Saldo.query.filter_by(sku=sku, endereco=vigentes[0]).first():
        # Era o único endereço: tinha o saldo inteiro do GRV, número exato.
        reg = _linha(sku, vigentes[0], unidade_grv)
        reg.quantidade, reg.conferido = max(_q(info.get('qtde_total')), Decimal(0)), True
    # Sem origem e com um endereço só, a quantidade sai dele (divide o material).
    sai_de = origem or (vigentes[0] if len(vigentes) == 1 else None)
    if sai_de:
        reg = _linha(sku, sai_de, unidade_grv)
        reg.quantidade = max(reg.quantidade - q, Decimal(0))
    reg_destino = _linha(sku, destino, unidade_grv)
    if destino not in vigentes:
        reg_destino.quantidade, reg_destino.conferido = Decimal(0), True
    reg_destino.quantidade += q
    db.session.flush()
    origem_com_sobra = origem and Saldo.query.filter(Saldo.sku == sku, Saldo.endereco == origem,
                                                     Saldo.conferido.is_(True), Saldo.quantidade > 0).first()
    depois = resultado(atuais, None if origem_com_sobra else origem, destino)
    return _fechar_controle(sku, depois)


def registrar(dados, usuario):
    """Muda o material de endereço no GRV.

    O saldo total é do GRV. Com 2+ endereços, a quantidade movimentada mexe
    na quantidade por endereço do Sync (ver _movimentar_quantidade); com um
    endereço só, é anotação do histórico."""
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
        depois = _movimentar_quantidade(sku, atuais, origem, destino, q, unidade)
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


def skus_em_inventario(skus=None):
    """SKUs com ajuste de inventário ainda em análise (nem concluído, nem
    descartado). Calculado na hora, não gravado: assim a marcação some sozinha
    quando o ajuste termina, por qualquer caminho."""
    from ..models import LogisticaInventarioAjuste as Ajuste
    query = (db.session.query(Ajuste.codigo_produto)
             .filter(Ajuste.status_modulo.notin_(('Concluido', 'Descartado'))))
    if skus is not None:
        alvo = {str(s or '').strip().upper() for s in skus} - {''}
        if not alvo:
            return set()
        query = query.filter(Ajuste.codigo_produto.in_(alvo))
    return {str(codigo).strip().upper() for (codigo,) in query.distinct()}


def _registrar_contagem(codigo, endereco, contado, usuario, observacao, estoque, agregado):
    """Grava a contagem no inventário normal, com o retrato do GRV na hora.
    Divergiu, o próprio inventário abre o ajuste em Validação."""
    from ..models import LogisticaInventarioInicial as Contagem
    from . import erp_estoque_service, logistica_inventario_ajuste_service as ajuste_svc
    saldo_grv = float(agregado.get('qtde_total') or 0)
    custo = erp_estoque_service.custo_medio_para(codigo, endereco, estoque)
    agora = agora_br()
    contagem = Contagem(local_codigo=endereco, codigo_produto=codigo,
                        unidade_medida=str(agregado.get('unidade') or 'UN').strip().upper()[:20],
                        quantidade=contado, observacao=observacao,
                        criado_por=usuario, atualizado_em=agora, qtde_grv_no_momento=saldo_grv,
                        custo_medio_no_momento=custo, grv_consultado_em=agora)
    db.session.add(contagem)
    db.session.commit()
    ajuste = ajuste_svc.detectar_divergencia(contagem, saldo_grv, custo,
                                             erp_estoque_service.descricao_para(codigo, estoque))
    # Divergente e sem ajuste novo = já havia um aberto para este material neste endereço.
    return {'contagem': contagem.id, 'ajuste': ajuste.id if ajuste else None,
            'contado': contado, 'saldo_grv': saldo_grv,
            'divergente': abs(contado - saldo_grv) > ajuste_svc.TOLERANCIA_DIVERGENCIA}


def inventariar_material(sku, endereco, contado, usuario):
    """Inventário pedido no Endereçamento para um material já endereçado: quem
    pede informa na hora a quantidade contada e a contagem entra no fluxo
    normal. Diferente da movimentação, a contagem é gravada mesmo quando
    confere com o GRV - foi um inventário feito, e isso fica registrado."""
    from . import erp_estoque_service
    # Mesma grafia do inventário: código em maiúsculas.
    codigo = texto(sku, 'o SKU', 80).upper()
    endereco = normalizar(texto(endereco, 'um endereço', 80))
    contado = float(quantidade(contado))
    estoque = erp_estoque_service.buscar_estoque_grv()
    agregado = (estoque.get('por_codigo') or {}).get(codigo)
    if not agregado:
        raise ValueError('Material não encontrado no estoque do GRV. Atualize a lista e tente novamente.')
    return _registrar_contagem(codigo, endereco, contado, usuario,
                               'Inventário pedido no endereçamento.', estoque, agregado)


def abrir_inventario(mov_id, usuario):
    """Abre um inventário normal a partir de uma movimentação cuja quantidade
    não bate com o saldo do GRV (o conferente respondeu "sim" na tela).

    A comparação é com o saldo total do SKU, o mesmo número que o diálogo
    mostra. A contagem cai no fluxo de sempre: divergiu, vira ajuste em
    Validação. Quem decide se há divergência é o servidor, não a tela.
    Devolve None quando não há o que abrir."""
    from . import erp_estoque_service, logistica_inventario_ajuste_service as ajuste_svc
    mov = db.session.get(Movimento, mov_id)
    detalhes = dict(mov.detalhes or {})
    if detalhes.get('inventario'):
        # Mesma operação reenviada: não conta nem avisa o gestor duas vezes.
        return {**detalhes['inventario'], 'repetido': True}
    codigo = mov.sku.strip().upper()
    estoque = erp_estoque_service.buscar_estoque_grv()
    agregado = (estoque.get('por_codigo') or {}).get(codigo)
    if not agregado:
        return None
    # Unidade diferente da do GRV: os números não são comparáveis.
    if agregado.get('unidade') and unidade_canonica(agregado['unidade']) != unidade_canonica(mov.unidade):
        return None
    saldo_grv = float(agregado.get('qtde_total') or 0)
    if abs(float(mov.quantidade) - saldo_grv) <= ajuste_svc.TOLERANCIA_DIVERGENCIA:
        return None
    resumo = _registrar_contagem(codigo, mov.destino, float(mov.quantidade), usuario,
                                 f'Aberto pelo endereçamento (movimentação {mov.id}).', estoque, agregado)
    mov.detalhes = {**detalhes, 'inventario': resumo}
    db.session.commit()
    return resumo


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

    Usado ao desativar um endereço (só admin): o lugar sai de circulação e
    nada mais deve apontar para ele. Material que só estava nele fica SEM
    endereço no GRV - a API aceita localização vazia (testado em 06/10/2026)
    - e volta no relatório para alguém endereçar de novo."""
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
            Saldo.query.filter_by(sku=sku, endereco=endereco).delete(synchronize_session=False)
            _fechar_controle(sku, depois, podar=False)
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
        locais, ultima = [], None
        for mov in pendentes:
            if isinstance(mov.detalhes, dict) and 'depois' in mov.detalhes:
                locais, ultima = list(mov.detalhes['depois'] or []), mov
        # Ficar sem endereço só vale quando a última operação é a desativação
        # do endereço (feita por admin) ou a limpeza do material sem saldo.
        # Fora disso, lista vazia é engano: não apagar o endereço do GRV nem
        # registrar sucesso fictício.
        if not locais and not (ultima is not None and ultima.tipo in TIPOS_QUE_ESVAZIAM):
            raise ValueError('Esta operação deixaria o material sem endereço no GRV. Só a desativação de endereço ou o saldo zerado podem fazer isso.')
        extra = {} if locais else {'permitir_vazio': True}
        resposta = receb.atualizar_localizacao_estoque(sku, ';'.join(locais), **extra)
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


TIPO_SALDO_ZERADO = 'Saldo zerado no GRV'
TIPO_ENDERECO_ZEROU = 'Endereço zerado'
TIPO_SAIDA = 'Saída GRV'
TIPO_SAIDA_SEM_ENDERECO = 'Saída sem endereço'
TIPO_SAIDA_NAO_BAIXADA = 'Saída não baixada'
TIPO_CONTAGEM = 'Contagem de endereço'
TIPOS_QUE_ESVAZIAM = ('Endereço desativado', TIPO_SALDO_ZERADO)
# Saída anterior à implantação não baixa nada: o ponto de partida dos
# materiais com 2+ endereços é a contagem.
SAIDAS_DESDE = date(2026, 11, 17)
LIMITE_LIMPEZA_POR_CICLO = 100


def enderecos_do_agregado(agregado):
    return list(dict.fromkeys(normalizar(e) for bruto in (agregado.get('localizacoes') or [])
                              for e in str(bruto).split(';') if normalizar(e)))


def _ocupado(sku):
    """Envio ou recebimento em andamento: fica para o próximo ciclo."""
    return bool(Movimento.query.filter_by(sku=sku, sincronizado_em=None).first()
                or Tarefa.query.filter_by(sku=sku, status='Aguardando sincronização').first())


def _soltar(sku, token):
    Trava.query.filter_by(sku=sku, token=token).update({'token': None, 'expira_em': None})
    db.session.commit()


def _registrar_poda(sku, atuais, depois, motivo):
    """Registra o envio ao GRV quando endereços zerados saíram da lista."""
    if [normalizar(e) for e in depois] == [normalizar(e) for e in atuais]:
        return False
    db.session.add(Movimento(chave=f'zerou:{sku}:{agora_br().isoformat()}'[:100], sku=sku, unidade='',
                             tipo=TIPO_ENDERECO_ZEROU, quantidade=0, usuario='sistema', motivo=motivo[:500],
                             detalhes={'antes': list(atuais), 'depois': list(depois)}))
    return True


def _sincronizar_sem_erro(skus):
    for sku in skus:
        try:
            sincronizar(sku)
        except Exception:
            db.session.rollback()
            current_app.logger.exception('Envio ao GRV pendente (SKU %s)', sku)


def limpar_sem_saldo(usuario='sistema'):
    """Material sem saldo no depósito 1 do GRV fica sem endereço (decisão da
    Logística em 17/11/2026): endereço de material que acabou só manda gente
    procurar em prateleira vazia. O endereço antigo fica no histórico.

    Fica de fora o material em análise de inventário (o saldo do GRV pode
    estar errado e a contagem acabou de gravar o endereço) e o SKU com envio
    em andamento. No máximo LIMITE_LIMPEZA_POR_CICLO por vez."""
    from .erp_estoque_service import buscar_estoque_grv
    por_codigo = buscar_estoque_grv(forcar_atualizacao=True).get('por_codigo') or {}
    candidatos = [sku for sku, ag in por_codigo.items()
                  if float(ag.get('qtde_total') or 0) <= 0 and enderecos_do_agregado(ag)]
    relatorio = {'limpos': [], 'falhas': []}
    if not candidatos:
        return relatorio
    em_inventario = skus_em_inventario(candidatos)
    candidatos = [s for s in candidatos if s not in em_inventario and not _ocupado(s)]
    for sku in candidatos[:LIMITE_LIMPEZA_POR_CICLO]:
        try:
            token = receb.adquirir_trava(SimpleNamespace(sku=sku))
        except ValueError:
            continue  # trava ocupada: próximo ciclo
        try:
            atuais = enderecos_atuais(sku)
            if atuais:
                _descontrolar(sku)
                db.session.add(Movimento(
                    chave=f'sem-saldo:{sku}:{agora_br().isoformat()}'[:100], sku=sku,
                    unidade=str(por_codigo[sku].get('unidade') or '').strip().upper()[:20],
                    tipo=TIPO_SALDO_ZERADO, quantidade=0, usuario=usuario,
                    motivo='Material sem saldo no GRV', detalhes={'antes': atuais, 'depois': []}))
                db.session.commit()
                relatorio['limpos'].append(sku)
        except Exception as exc:
            db.session.rollback()
            current_app.logger.exception('Falha ao limpar o endereço do SKU %s sem saldo', sku)
            relatorio['falhas'].append({'sku': sku, 'erro': str(exc)[:200]})
        finally:
            _soltar(sku, token)
    _sincronizar_sem_erro(relatorio['limpos'])
    return relatorio


def importar_saidas(skus=None):
    """Baixa na quantidade por endereço as saídas concluídas do GRV dos
    materiais com 2+ endereços. Cada linha de saída (guid_linha) entra uma
    vez só. Saída com mais de um endereço (o GRV copia a lista do cadastro),
    com endereço que o material não tem ou em outra unidade não baixa: vai
    para as saídas a corrigir. Devolve quantas linhas entraram."""
    from ..compras import queries
    from ..compras.db import fetch_all
    from .erp_estoque_service import buscar_estoque_grv
    por_codigo = buscar_estoque_grv().get('por_codigo') or {}
    alvo = {str(s or '').strip().upper() for s in skus} if skus is not None else None
    multi = {sku: ag for sku, ag in por_codigo.items()
             if len(enderecos_do_agregado(ag)) >= 2 and (alvo is None or sku in alvo)}
    if not multi:
        return 0
    desde = max(SAIDAS_DESDE, agora_br().date() - timedelta(days=30))
    linhas = fetch_all(queries.SQL_ENDERECAMENTO_SAIDAS,
                       {'cod_empresa': 1, 'desde': desde.isoformat(), 'codigos': sorted(multi)})
    chaves = [f"saida:{l['guid_linha']}" for l in linhas]
    feitas = {c for (c,) in db.session.query(Movimento.chave).filter(Movimento.chave.in_(chaves))} if chaves else set()
    por_sku = {}
    for l in linhas:
        if f"saida:{l['guid_linha']}" not in feitas:
            por_sku.setdefault(str(l['codigo']).strip().upper(), []).append(l)
    novas, enviar = 0, []
    for sku, linhas_sku in por_sku.items():
        try:
            token = receb.adquirir_trava(SimpleNamespace(sku=sku))
        except ValueError:
            continue
        try:
            atuais = enderecos_atuais(sku)
            vigentes = [normalizar(e) for e in atuais]
            unidade_grv = str(multi[sku].get('unidade') or '').strip().upper()
            for l in linhas_sku:
                informados = list(dict.fromkeys(normalizar(e) for e in str(l['localizacao'] or '').split(';') if normalizar(e)))
                q = _q(l['qtde'])
                unidade = str(l['unidade'] or unidade_grv).strip().upper()[:20]
                detalhes = {'saida': l['saida'], 'data': str(l['data']), 'informado': l['localizacao'],
                            'guid': l['guid_linha']}
                comparavel = unidade_canonica(unidade) == unidade_canonica(unidade_grv or unidade)
                if len(informados) == 1 and informados[0] in vigentes and comparavel:
                    reg = _linha(sku, informados[0], unidade_grv or unidade)
                    if q > reg.quantidade:
                        detalhes['faltou'] = str(q - reg.quantidade)
                    reg.quantidade = max(reg.quantidade - q, Decimal(0))
                    tipo, origem = TIPO_SAIDA, informados[0]
                else:
                    detalhes['opcoes'] = atuais
                    detalhes['pendencia'] = (f'Saída em {unidade} e o estoque é em {unidade_grv}.' if not comparavel else
                                             'Saída sem endereço.' if not informados else
                                             'Saída com mais de um endereço.' if len(informados) > 1 else
                                             'Endereço da saída não está na lista do material.')
                    tipo, origem = TIPO_SAIDA_SEM_ENDERECO, None
                db.session.add(Movimento(chave=f"saida:{l['guid_linha']}", sku=sku, unidade=unidade, tipo=tipo,
                                         origem=origem, quantidade=q, usuario='sistema',
                                         motivo=f"Saída {l['saida']} do GRV", detalhes=detalhes,
                                         sincronizado_em=agora_br()))
                novas += 1
            db.session.flush()
            if _registrar_poda(sku, atuais, _fechar_controle(sku, atuais), 'Endereço zerado pelas saídas do GRV'):
                enviar.append(sku)
            db.session.commit()
        except Exception:
            db.session.rollback()
            current_app.logger.exception('Falha ao baixar as saídas do SKU %s', sku)
        finally:
            _soltar(sku, token)
    _sincronizar_sem_erro(enviar)
    return novas


def resolver_saida(mov_id, endereco, usuario):
    """Diz de qual endereço saiu uma saída que veio sem endereço definido.
    Sem endereço: a saída não baixa nada (ex.: anterior à contagem)."""
    mov = db.session.get(Movimento, mov_id)
    if not mov or mov.tipo not in (TIPO_SAIDA_SEM_ENDERECO, TIPO_SAIDA, TIPO_SAIDA_NAO_BAIXADA):
        raise ValueError('Saída não encontrada.')
    if mov.tipo != TIPO_SAIDA_SEM_ENDERECO:
        return mov.id  # já resolvida (clique repetido)
    sku = mov.sku
    token = receb.adquirir_trava(SimpleNamespace(sku=sku))
    enviar = False
    try:
        db.session.refresh(mov)
        if mov.tipo != TIPO_SAIDA_SEM_ENDERECO:
            return mov.id
        detalhes = dict(mov.detalhes or {})
        alvo = normalizar(endereco) if str(endereco or '').strip() else None
        if alvo:
            atuais = enderecos_atuais(sku)
            if alvo not in [normalizar(e) for e in atuais]:
                raise ValueError('Este endereço não está na lista do material. Atualize a tela.')
            unidade_grv = str((info_grv(sku, fresco=False) or {}).get('unidade') or mov.unidade).strip().upper()
            if unidade_canonica(mov.unidade) != unidade_canonica(unidade_grv):
                raise ValueError(f'A saída está em {mov.unidade} e o estoque em {unidade_grv}: conte o endereço em vez de baixar.')
            reg = _linha(sku, alvo, unidade_grv)
            if mov.quantidade > reg.quantidade:
                detalhes['faltou'] = str(mov.quantidade - reg.quantidade)
            reg.quantidade = max(reg.quantidade - mov.quantidade, Decimal(0))
            mov.tipo, mov.origem = TIPO_SAIDA, alvo
            db.session.flush()
            enviar = _registrar_poda(sku, atuais, _fechar_controle(sku, atuais), 'Endereço zerado pelas saídas do GRV')
        else:
            mov.tipo = TIPO_SAIDA_NAO_BAIXADA
        detalhes['resolvido'] = {'endereco': alvo, 'usuario': usuario, 'em': agora_br().isoformat()}
        mov.detalhes = detalhes
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    finally:
        _soltar(sku, token)
    if enviar:
        _sincronizar_sem_erro([sku])
    return mov.id


def saidas_a_corrigir(pagina=1, busca=''):
    query = Movimento.query.filter_by(tipo=TIPO_SAIDA_SEM_ENDERECO)
    if busca:
        query = query.filter(Movimento.sku.contains(busca, autoescape=True))
    return query.count(), query.order_by(Movimento.id).offset((pagina - 1) * 40).limit(40).all()


def contar_endereco(sku, endereco, contado, usuario):
    """Quantidade contada no endereço de material com 2+ endereços: vira a
    quantidade do endereço, conferida. Zero tira o endereço do GRV.
    As saídas que já estão no GRV entram antes, para não baixarem de novo a
    quantidade que acabou de ser contada."""
    sku = texto(sku, 'o SKU', 80)
    endereco = normalizar(texto(endereco, 'um endereço', 80))
    try:
        contado = Decimal(str(contado).strip().replace(',', '.'))
        if not contado.is_finite() or contado < 0 or contado >= Decimal('1000000000000'):
            raise ValueError()
        contado = _q(contado)
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError('Informe a quantidade contada (zero se o endereço estiver vazio).')
    try:
        importar_saidas([sku])
    except Exception:
        db.session.rollback()
        current_app.logger.warning('Contagem de %s sem importar as saídas antes.', sku, exc_info=True)
    token = receb.adquirir_trava(SimpleNamespace(sku=sku))
    enviar = False
    try:
        atuais = enderecos_atuais(sku)
        vigentes = [normalizar(e) for e in atuais]
        if endereco not in vigentes:
            raise ValueError('Este endereço não está na lista do material. Atualize a tela.')
        if len(vigentes) < 2:
            raise ValueError('Material com um endereço só: a quantidade do endereço é o saldo do GRV. Use "Pedir inventário".')
        info = info_grv(sku)
        if info is None:
            raise ValueError('Saldo do GRV indisponível. Tente novamente em instantes.')
        unidade = str(info.get('unidade') or 'UN').strip().upper()
        reg = _linha(sku, endereco, unidade)
        anterior = str(reg.quantidade) if reg.conferido else None
        reg.quantidade, reg.conferido = contado, True
        db.session.add(Movimento(chave=f'contagem:{sku}:{endereco}:{agora_br().isoformat()}'[:100], sku=sku,
                                 unidade=unidade, tipo=TIPO_CONTAGEM, destino=endereco, quantidade=contado,
                                 usuario=usuario, motivo='Contagem do endereço',
                                 detalhes={'anterior': anterior}, sincronizado_em=agora_br()))
        db.session.flush()
        enviar = _registrar_poda(sku, atuais, _fechar_controle(sku, atuais), f'Endereço {endereco} contado vazio')
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    finally:
        _soltar(sku, token)
    if enviar:
        _sincronizar_sem_erro([sku])


def quantidades_por_endereco(skus):
    """{(SKU, ENDEREÇO): linha} dos materiais com quantidade por endereço."""
    alvo = {str(s or '').strip() for s in skus} - {''}
    if not alvo:
        return {}
    return {(r.sku.strip().upper(), r.endereco): r for r in Saldo.query.filter(Saldo.sku.in_(alvo))}
