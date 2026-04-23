"""
Script para criar as tabelas do módulo Facilities no MySQL do PythonAnywhere.

Como usar:
1. Acesse o console Bash do PythonAnywhere
2. Execute: cd /home/felipefazevedo/conferencia_system
3. Execute: export DATABASE_URL="mysql+pymysql://felipefazevedo:columbiasync2026@felipefazevedo.mysql.pythonanywhere-services.com/felipefazevedo\$sync"
4. Execute: python scripts/migrate_facilities_tables.py
"""

import os
import sys

# Adiciona o diretório pai ao path para importar os módulos da aplicação
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, text, inspect

DATABASE_URL = os.environ.get('DATABASE_URL')

if not DATABASE_URL:
    print("ERRO: Variável DATABASE_URL não definida.")
    print("Execute: export DATABASE_URL=\"mysql+pymysql://felipefazevedo:columbiasync2026@felipefazevedo.mysql.pythonanywhere-services.com/felipefazevedo\\$sync\"")
    sys.exit(1)

engine = create_engine(DATABASE_URL, echo=True)

# SQL para criar as tabelas do Facilities
CREATE_TABLES_SQL = [
    # 1. Colaboradores (sem dependências)
    """
    CREATE TABLE IF NOT EXISTS facilities_colaborador (
        id INT AUTO_INCREMENT PRIMARY KEY,
        nome VARCHAR(100) NOT NULL,
        cargo VARCHAR(80),
        setor VARCHAR(80),
        telefone VARCHAR(20),
        nivel_acesso VARCHAR(20) NOT NULL DEFAULT 'solicitante',
        ativo BOOLEAN NOT NULL DEFAULT TRUE,
        criado_em DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_colab_nome (nome),
        INDEX idx_colab_setor (setor),
        INDEX idx_colab_nivel (nivel_acesso)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
    """,
    
    # 2. Projetos (sem dependências)
    """
    CREATE TABLE IF NOT EXISTS facilities_projeto (
        id INT AUTO_INCREMENT PRIMARY KEY,
        nome VARCHAR(150) NOT NULL,
        cliente_nome VARCHAR(150),
        cliente_telefone VARCHAR(20),
        cliente_endereco VARCHAR(300),
        observacoes TEXT,
        status VARCHAR(30) NOT NULL DEFAULT 'Em andamento',
        criado_em DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_projeto_nome (nome),
        INDEX idx_projeto_status (status)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
    """,
    
    # 3. Tarefas (depende de facilities_projeto)
    """
    CREATE TABLE IF NOT EXISTS facilities_tarefa (
        id INT AUTO_INCREMENT PRIMARY KEY,
        projeto_id INT NOT NULL,
        titulo VARCHAR(150) NOT NULL,
        local VARCHAR(100),
        descricao TEXT,
        status VARCHAR(30) NOT NULL DEFAULT 'nao_planejado',
        observacao TEXT,
        impedimento TEXT,
        foto_path VARCHAR(500),
        data_inicio_prevista DATE,
        data_fim_prevista DATE,
        atualizado_em DATETIME,
        criado_em DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_tarefa_projeto (projeto_id),
        INDEX idx_tarefa_status (status),
        FOREIGN KEY (projeto_id) REFERENCES facilities_projeto(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
    """,
    
    # 4. Limpeza (depende de facilities_colaborador)
    """
    CREATE TABLE IF NOT EXISTS facilities_limpeza (
        id INT AUTO_INCREMENT PRIMARY KEY,
        colaborador_id INT,
        titulo VARCHAR(150) NOT NULL,
        local VARCHAR(150),
        data_agendada DATE NOT NULL,
        hora_inicio VARCHAR(5),
        hora_fim VARCHAR(5),
        observacoes TEXT,
        concluido BOOLEAN NOT NULL DEFAULT FALSE,
        criado_em DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_limpeza_colab (colaborador_id),
        INDEX idx_limpeza_data (data_agendada),
        FOREIGN KEY (colaborador_id) REFERENCES facilities_colaborador(id) ON DELETE SET NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
    """,
    
    # 5. EPI Material (sem dependências)
    """
    CREATE TABLE IF NOT EXISTS facilities_epi_material (
        id INT AUTO_INCREMENT PRIMARY KEY,
        codigo_interno VARCHAR(30) NOT NULL,
        nome VARCHAR(150) NOT NULL,
        tipo VARCHAR(20) NOT NULL DEFAULT 'epi',
        ativo BOOLEAN NOT NULL DEFAULT TRUE,
        criado_em DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_material_codigo (codigo_interno),
        INDEX idx_material_tipo (tipo)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
    """,
    
    # 6. EPI Solicitação (depende de facilities_colaborador)
    """
    CREATE TABLE IF NOT EXISTS facilities_epi_solicitacao (
        id INT AUTO_INCREMENT PRIMARY KEY,
        colaborador_id INT NOT NULL,
        solicitante_id INT,
        liberador_id INT,
        tipo VARCHAR(20) NOT NULL DEFAULT 'epi',
        codigo_item VARCHAR(30) NOT NULL,
        nome_item VARCHAR(150) NOT NULL,
        tamanho VARCHAR(20),
        quantidade INT NOT NULL DEFAULT 1,
        motivo TEXT,
        status VARCHAR(20) NOT NULL DEFAULT 'solicitado',
        solicitado_em DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        liberado_em DATETIME,
        criado_em DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_solic_colab (colaborador_id),
        INDEX idx_solic_tipo (tipo),
        INDEX idx_solic_codigo (codigo_item),
        INDEX idx_solic_status (status),
        INDEX idx_solic_data (solicitado_em),
        FOREIGN KEY (colaborador_id) REFERENCES facilities_colaborador(id) ON DELETE CASCADE,
        FOREIGN KEY (solicitante_id) REFERENCES facilities_colaborador(id) ON DELETE SET NULL,
        FOREIGN KEY (liberador_id) REFERENCES facilities_colaborador(id) ON DELETE SET NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
    """
]


def check_table_exists(table_name):
    """Verifica se a tabela já existe no banco."""
    inspector = inspect(engine)
    return table_name in inspector.get_table_names()


def main():
    print("=" * 60)
    print("Migração das tabelas do módulo Facilities")
    print("=" * 60)
    
    tables = [
        "facilities_colaborador",
        "facilities_projeto", 
        "facilities_tarefa",
        "facilities_limpeza",
        "facilities_epi_material",
        "facilities_epi_solicitacao"
    ]
    
    # Verificar tabelas existentes
    existing = []
    missing = []
    for t in tables:
        if check_table_exists(t):
            existing.append(t)
        else:
            missing.append(t)
    
    if existing:
        print(f"\n✓ Tabelas já existentes: {', '.join(existing)}")
    
    if not missing:
        print("\n✅ Todas as tabelas já existem! Nenhuma ação necessária.")
        return
    
    print(f"\n⚠ Tabelas a criar: {', '.join(missing)}")
    
    with engine.connect() as conn:
        for i, sql in enumerate(CREATE_TABLES_SQL):
            table_name = tables[i]
            if table_name in existing:
                print(f"⏭ Pulando {table_name} (já existe)")
                continue
            
            try:
                print(f"\n📦 Criando tabela: {table_name}...")
                conn.execute(text(sql))
                conn.commit()
                print(f"   ✓ {table_name} criada com sucesso!")
            except Exception as e:
                print(f"   ✗ Erro ao criar {table_name}: {e}")
    
    print("\n" + "=" * 60)
    print("Migração concluída!")
    print("=" * 60)


if __name__ == "__main__":
    main()
