"""Assistência Técnica: operação, NF e retorno por item da Solicitação de NF.

O tipo de operação, a NF e o controle de retorno saem do cabeçalho e passam
para o item — é isso que permite uma solicitação virar mais de uma nota.

As solicitações que já existem têm itens sem nenhuma dessas colunas; sem o
backfill abaixo, todo o histórico apareceria vazio nas telas novas. O
preenchimento copia do cabeçalho o que historicamente valia para todos os
itens da solicitação, e usa `tipo_operacao IS NULL` como marca de "ainda não
preenchido", gravando o tipo por último: se a migração for interrompida no
meio, rodar de novo refaz o que faltou.
"""
from alembic import op
import sqlalchemy as sa

revision = '20260918_assistencia_tecnica'
down_revision = '20260917_endereco_movimentos'
branch_labels = None
depends_on = None

# Tipos que não têm controle de retorno (espelha TIPOS_SEM_RETORNO do
# solicitacao_nf_service; mudou lá, mude aqui).
TIPOS_SEM_RETORNO = ('Garantia', 'Bonificação', 'Remessa de retorno de demonstração')

# Os seis tipos que já existem em produção, com o texto de ajuda ao
# solicitante. O texto é sugestão inicial: confirme com a Assistência Técnica
# e com o Fiscal antes de considerar fechado.
TIPOS_INICIAIS = (
    ('Garantia', 1,
     'Envio de material para substituição, reparo ou avaliação técnica de item '
     'com defeito dentro do prazo de garantia.'),
    ('Bonificação', 2,
     'Envio de material sem custo ao cliente, como cortesia, ação comercial ou '
     'compensação. Não gera cobrança.'),
    ('Remessa para Teste', 3,
     'Envio temporário para teste, avaliação técnica ou homologação. O material '
     'deve retornar ao fim do prazo combinado.'),
    ('Materiais para atendimento técnico no cliente', 4,
     'Peças, ferramentas ou equipamentos levados pela equipe técnica para um '
     'atendimento. Pode voltar ou ser consumido no atendimento — a equipe ajusta '
     'item a item.'),
    ('Remessa para Conserto', 5,
     'Envio de material para conserto em terceiro. A nota sai antes do material '
     'e o item deve retornar.'),
    ('Remessa de retorno de demonstração', 6,
     'Devolução de material que estava em demonstração. A nota sai antes do '
     'material e não há retorno a controlar.'),
)

# Unidade do material junto do item: sem ela a quantidade fica ambígua.
COLUNAS_ITEM = (
    ('material_unidade', sa.String(20), {}),
    ('tipo_operacao', sa.String(60), {}),
    ('sera_vendido', sa.Boolean(), {'nullable': False, 'server_default': '0'}),
    ('necessita_retorno', sa.Boolean(), {'nullable': False, 'server_default': '0'}),
    ('status', sa.String(40), {'nullable': False, 'server_default': 'Solicitado'}),
    ('numero_nf', sa.String(80), {}),
    ('data_emissao_nf', sa.DateTime(), {}),
    ('data_prevista_retorno', sa.Date(), {}),
    ('data_efetiva_retorno', sa.Date(), {}),
    ('quantidade_retornada', sa.Float(), {'nullable': False, 'server_default': '0'}),
    ('numero_nf_retorno', sa.String(80), {}),
)


def _colunas(inspector, tabela):
    return {c['name'] for c in inspector.get_columns(tabela)}


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # Tabela nova: normalmente o db.create_all() já criou. Criar aqui também
    # deixa a migração de pé sozinha, para quem rodar só o script.
    if not inspector.has_table('tipo_operacao_nf'):
        op.create_table('tipo_operacao_nf',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('nome', sa.String(60), nullable=False, unique=True),
            sa.Column('descricao_ajuda', sa.Text()),
            sa.Column('requer_retorno_padrao', sa.Boolean(), nullable=False, server_default='0'),
            sa.Column('ativo', sa.Boolean(), nullable=False, server_default='1'),
            sa.Column('ordem_exibicao', sa.Integer(), nullable=False, server_default='0'))
        op.create_index('ix_tipo_operacao_nf_ativo', 'tipo_operacao_nf', ['ativo'])
    _semear_tipos(bind)

    if not inspector.has_table('solicitacao_nf_item'):
        return  # banco sem o módulo de Solicitação de NF; nada a migrar

    existentes = _colunas(inspector, 'solicitacao_nf_item')
    for nome, tipo, extra in COLUNAS_ITEM:
        if nome not in existentes:
            op.add_column('solicitacao_nf_item', sa.Column(nome, tipo, **extra))

    # Recado de quem abre o pedido: as outras observações são do time interno.
    if 'observacoes' not in _colunas(inspector, 'solicitacao_nf'):
        op.add_column('solicitacao_nf', sa.Column('observacoes', sa.String(500)))

    if 'item_id' not in _colunas(inspector, 'solicitacao_nf_log'):
        op.add_column('solicitacao_nf_log', sa.Column('item_id', sa.Integer()))
        op.create_index('ix_solicitacao_nf_log_item_id', 'solicitacao_nf_log', ['item_id'])

    _backfill(bind)


def _semear_tipos(bind):
    """Cadastra os tipos que faltarem, sem tocar no que já foi editado na tela."""
    existentes = {row[0] for row in bind.exec_driver_sql('select nome from tipo_operacao_nf')}
    for nome, ordem, ajuda in TIPOS_INICIAIS:
        if nome in existentes:
            continue
        bind.exec_driver_sql(
            'insert into tipo_operacao_nf (nome, descricao_ajuda, requer_retorno_padrao,'
            ' ativo, ordem_exibicao) values (?, ?, ?, 1, ?)'
            if bind.dialect.name == 'sqlite' else
            'insert into tipo_operacao_nf (nome, descricao_ajuda, requer_retorno_padrao,'
            ' ativo, ordem_exibicao) values (%s, %s, %s, 1, %s)',
            (nome, ajuda, 0 if nome in TIPOS_SEM_RETORNO else 1, ordem))


def _backfill(bind):
    """Copia para o item o que antes valia por solicitação inteira.

    Subconsulta correlacionada em vez de UPDATE ... FROM: a segunda forma tem
    sintaxe diferente em MySQL e SQLite, e este script roda nos dois."""
    pendente = "where tipo_operacao is null"
    do_cabecalho = ("(select s.{campo} from solicitacao_nf s "
                    "where s.id = solicitacao_nf_item.solicitacao_id)")

    # Ordem importa: o tipo de operação é gravado por último, porque é ele a
    # marca de que o item já foi preenchido.
    op.execute(f"""
        update solicitacao_nf_item
           set sera_vendido = coalesce({do_cabecalho.format(campo='venda_posterior')}, 0)
         {pendente}
    """)
    lista = ', '.join(f"'{t}'" for t in TIPOS_SEM_RETORNO)
    op.execute(f"""
        update solicitacao_nf_item
           set necessita_retorno = case
                 when {do_cabecalho.format(campo='tipo_operacao')} in ({lista}) then 0
                 else 1 end
         {pendente}
    """)
    op.execute(f"""
        update solicitacao_nf_item
           set numero_nf = {do_cabecalho.format(campo='numero_nf')},
               data_emissao_nf = {do_cabecalho.format(campo='faturado_at')},
               numero_nf_retorno = {do_cabecalho.format(campo='numero_nf_retorno')}
         {pendente}
    """)
    # Histórico não tem retorno parcial: o que voltou, voltou inteiro.
    op.execute(f"""
        update solicitacao_nf_item
           set quantidade_retornada = case
                 when {do_cabecalho.format(campo='retorno_at')} is not null
                      then coalesce(quantidade, 0) else 0 end
         {pendente}
    """)
    # O status do cabeçalho valia para todos os itens; só "Solicitado" se
    # desdobra, porque ali o item já podia estar separado.
    op.execute(f"""
        update solicitacao_nf_item
           set status = case
                 when {do_cabecalho.format(campo='status')} = 'Solicitado'
                      then (case when separado = 1 then 'Separado' else 'Solicitado' end)
                 else coalesce({do_cabecalho.format(campo='status')}, 'Solicitado') end
         {pendente}
    """)
    op.execute(f"""
        update solicitacao_nf_item
           set tipo_operacao = {do_cabecalho.format(campo='tipo_operacao')}
         {pendente}
    """)


def downgrade():
    for nome, _tipo, _extra in COLUNAS_ITEM:
        op.drop_column('solicitacao_nf_item', nome)
    op.drop_column('solicitacao_nf', 'observacoes')
    op.drop_index('ix_solicitacao_nf_log_item_id', table_name='solicitacao_nf_log')
    op.drop_column('solicitacao_nf_log', 'item_id')
