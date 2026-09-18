"""Migração do módulo de Assistência Técnica contra um banco SUJO.

O risco real desta migração não é criar coluna: é o preenchimento do
histórico. As solicitações que já existem têm o tipo de operação, a NF e o
retorno só no cabeçalho, e sem copiar isso para os itens todo o histórico
apareceria vazio nas telas novas. O banco montado aqui tem o schema ANTIGO,
com solicitações em três situações diferentes.
"""
import importlib.util
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect
from alembic.migration import MigrationContext
from alembic.operations import Operations

RAIZ = Path(__file__).resolve().parents[1]
MIGRACAO = RAIZ / 'migrations' / 'versions' / '20260918_assistencia_tecnica.py'

SCHEMA_ANTIGO = """
create table solicitacao_nf (
  id integer primary key, protocolo varchar(20), solicitante_codigo varchar(40),
  solicitante_nome varchar(160) not null, solicitante_setor varchar(120),
  tipo_operacao varchar(40) not null, venda_posterior boolean not null default 0,
  cliente_codigo varchar(40), cliente_nome varchar(160) not null, cliente_documento varchar(30),
  status varchar(60) not null default 'Solicitado',
  separado_por varchar(100), separado_at datetime, observacoes_separacao varchar(500),
  faturado_por varchar(100), faturado_at datetime, numero_nf varchar(80),
  observacoes_faturamento varchar(500), numero_nf_retorno varchar(80),
  retorno_por varchar(100), retorno_at datetime, observacoes_retorno varchar(500),
  nf_parceiro_nome varchar(200), nf_parceiro_endereco varchar(400),
  ordem_faturamento integer, ip_solicitante varchar(64),
  created_at datetime not null, updated_at datetime not null);
create table solicitacao_nf_item (
  id integer primary key, solicitacao_id integer not null, linha integer not null default 0,
  material_codigo varchar(80), material_nome varchar(200), material_local varchar(160),
  quantidade float not null default 0, separado boolean not null default 0);
create table solicitacao_nf_log (
  id integer primary key, solicitacao_id integer not null, acao varchar(30) not null,
  usuario varchar(100), status_anterior varchar(20), status_novo varchar(20),
  detalhes text, created_at datetime not null);
"""

CABECALHO = ("id,protocolo,solicitante_codigo,solicitante_nome,solicitante_setor,tipo_operacao,"
             "venda_posterior,cliente_codigo,cliente_nome,cliente_documento,status,separado_por,"
             "separado_at,faturado_por,faturado_at,numero_nf,numero_nf_retorno,retorno_por,"
             "retorno_at,created_at,updated_at")

# Três situações que existem de verdade hoje.
SOLICITACOES = [
    # Garantia já faturada: tipo sem controle de retorno.
    (1, 'SNF-000001', 'F01', 'ANA', 'AT', 'Garantia', 0, 'C1', 'CLIENTE UM', '1',
     'Notas fiscais emitidas', 'joao', '2026-01-02', 'fiscal', '2026-01-03', 'NF-555',
     None, None, None),
    # Remessa para Teste que saiu com venda posterior e já voltou.
    (2, 'SNF-000002', 'F02', 'BRUNO', 'AT', 'Remessa para Teste', 1, 'C2', 'CLIENTE DOIS', '2',
     'Estoque em poder de terceiros', 'joao', '2026-02-02', 'fiscal', '2026-02-03', 'NF-777',
     'NF-R-1', 'fiscal', '2026-03-01'),
    # Atendimento técnico ainda em separação, com um item separado e outro não.
    (3, 'SNF-000003', 'F03', 'CARLA', 'AT', 'Materiais para atendimento técnico no cliente', 0,
     'C3', 'CLIENTE TRES', '3', 'Solicitado', None, None, None, None, None, None, None, None),
]

ITENS = [(1, 1, 0, 'M1', 'MATERIAL UM', 'R1', 2.0, 1),
         (2, 1, 1, 'M2', 'MATERIAL DOIS', 'R2', 5.0, 1),
         (3, 2, 0, 'M3', 'MATERIAL TRES', 'R3', 1.0, 1),
         (4, 3, 0, 'M4', 'MATERIAL QUATRO', 'R4', 7.0, 1),
         (5, 3, 1, 'M5', 'MATERIAL CINCO', 'R5', 9.0, 0)]


@pytest.fixture
def banco_sujo(tmp_path):
    caminho = tmp_path / 'sujo.db'
    conn = sqlite3.connect(caminho)
    conn.executescript(SCHEMA_ANTIGO)
    for linha in SOLICITACOES:
        conn.execute(
            f"insert into solicitacao_nf ({CABECALHO}) "
            f"values ({','.join('?' * 19)},'2026-01-01','2026-01-01')", linha)
    conn.executemany('insert into solicitacao_nf_item values (?,?,?,?,?,?,?,?)', ITENS)
    conn.commit()
    conn.close()
    return caminho


def aplicar(caminho, vezes=1):
    spec = importlib.util.spec_from_file_location('migracao_at', MIGRACAO)
    migracao = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migracao)
    engine = create_engine(f'sqlite:///{caminho}')
    try:
        with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
            for _ in range(vezes):
                migracao.upgrade()
    finally:
        engine.dispose()
    return migracao


def linhas(caminho, sql):
    conn = sqlite3.connect(caminho)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql)]
    finally:
        conn.close()


def test_historico_e_preenchido_a_partir_do_cabecalho(banco_sujo):
    aplicar(banco_sujo)
    itens = {r['id']: r for r in linhas(banco_sujo, 'select * from solicitacao_nf_item')}
    assert len(itens) == 5, 'nenhum item pode ser perdido'

    # Garantia faturada: NF e status copiados, sem controle de retorno.
    assert itens[1]['tipo_operacao'] == 'Garantia'
    assert itens[1]['status'] == 'Notas fiscais emitidas'
    assert itens[1]['numero_nf'] == 'NF-555'
    assert itens[1]['necessita_retorno'] == 0
    assert itens[1]['quantidade_retornada'] == 0

    # Remessa para teste já retornada: venda posterior e retorno cheio.
    assert itens[3]['sera_vendido'] == 1
    assert itens[3]['necessita_retorno'] == 1
    assert itens[3]['numero_nf_retorno'] == 'NF-R-1'
    assert itens[3]['quantidade_retornada'] == itens[3]['quantidade']

    # Em separação: o status do item se desdobra pelo que já estava separado.
    assert itens[4]['status'] == 'Separado'
    assert itens[5]['status'] == 'Solicitado'


def test_rodar_duas_vezes_nao_altera_o_resultado(banco_sujo):
    aplicar(banco_sujo)
    depois_da_primeira = linhas(banco_sujo, 'select * from solicitacao_nf_item order by id')
    aplicar(banco_sujo, vezes=2)
    assert linhas(banco_sujo, 'select * from solicitacao_nf_item order by id') == depois_da_primeira
    assert len(linhas(banco_sujo, 'select * from tipo_operacao_nf')) == 6


def test_edicao_feita_na_tela_sobrevive_a_nova_execucao(banco_sujo):
    """Rodar de novo não pode desfazer o que a equipe ajustou depois."""
    aplicar(banco_sujo)
    conn = sqlite3.connect(banco_sujo)
    conn.execute("update solicitacao_nf_item set necessita_retorno = 0, status = 'Retornado' where id = 3")
    conn.execute("update tipo_operacao_nf set descricao_ajuda = 'texto revisado' where nome = 'Garantia'")
    conn.commit()
    conn.close()
    aplicar(banco_sujo)
    item = linhas(banco_sujo, 'select * from solicitacao_nf_item where id = 3')[0]
    assert item['necessita_retorno'] == 0 and item['status'] == 'Retornado'
    tipo = linhas(banco_sujo, "select * from tipo_operacao_nf where nome = 'Garantia'")[0]
    assert tipo['descricao_ajuda'] == 'texto revisado'


def test_ciclo_de_downgrade_refaz_o_preenchimento(banco_sujo):
    migracao = aplicar(banco_sujo)
    engine = create_engine(f'sqlite:///{banco_sujo}')
    try:
        with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
            migracao.downgrade()
            migracao.upgrade()
        colunas = {c['name'] for c in inspect(engine).get_columns('solicitacao_nf_item')}
    finally:
        engine.dispose()
    assert {nome for nome, _, _ in migracao.COLUNAS_ITEM} <= colunas
    assert all(r['tipo_operacao'] for r in linhas(banco_sujo, 'select * from solicitacao_nf_item'))


def test_banco_sem_o_modulo_nao_quebra(tmp_path):
    """Instalação que nunca teve Solicitação de NF: cria só a tabela de tipos."""
    caminho = tmp_path / 'vazio.db'
    sqlite3.connect(caminho).close()
    aplicar(caminho)
    assert len(linhas(caminho, 'select * from tipo_operacao_nf')) == 6


def test_script_aplica_e_verifica_sem_iniciar_aplicacao(banco_sujo):
    import os
    env = dict(os.environ, DB_PATH=str(banco_sujo))
    env.pop('DATABASE_URL', None)
    comando = [sys.executable, 'scripts/aplicar_migracao_assistencia_tecnica.py']
    for argumentos, codigo in [(['--check'], 1), ([], 0), ([], 0), (['--check'], 0)]:
        resultado = subprocess.run(comando + argumentos, env=env, capture_output=True,
                                   text=True, cwd=RAIZ)
        assert resultado.returncode == codigo, resultado.stdout + resultado.stderr
