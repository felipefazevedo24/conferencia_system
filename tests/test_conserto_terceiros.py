from datetime import datetime

from conferencia_app import create_app
from conferencia_app.extensions import db
from conferencia_app.models import ConsertoBaixa, ConsertoEstoque, ItemNota
from conferencia_app.services.conserto_service import ConsertoService


def build_test_app(tmp_path):
    return create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        }
    )


def test_criar_saldo_conserto_grava_classificacao(tmp_path):
    app = build_test_app(tmp_path)

    with app.app_context():
        saldo = ConsertoService.criar_saldo_remessa(
            numero_nf_remessa="1001",
            chave_nf_remessa="1" * 44,
            data_emissao=datetime(2026, 3, 31),
            fornecedor_cnpj="12345678000199",
            fornecedor_nome="Fornecedor Conserto",
            produto_codigo="MAT-001",
            produto_descricao="Material enviado",
            quantidade=10,
            tipo_operacao="Conserto",
            tipo_controle="Meu em poder de terceiros",
            cfop_remessa="5915",
            usuario="admin",
        )

        assert saldo.tipo_operacao == "Conserto"
        assert saldo.tipo_controle == "Meu em poder de terceiros"
        assert saldo.cfop_remessa == "5915"
        assert saldo.status == "Pendente de retorno"


def test_retorno_conserto_gera_baixa_pendente_para_autorizacao(tmp_path):
    app = build_test_app(tmp_path)

    with app.app_context():
        ConsertoService.criar_saldo_remessa(
            numero_nf_remessa="2001",
            chave_nf_remessa="2" * 44,
            data_emissao=datetime(2026, 3, 30),
            fornecedor_cnpj="98765432000188",
            fornecedor_nome="Terceiro Conserto",
            produto_codigo="MAT-002",
            produto_descricao="Item conserto",
            quantidade=5,
            tipo_operacao="Conserto",
            tipo_controle="Meu em poder de terceiros",
            cfop_remessa="5915",
            usuario="admin",
        )

        db.session.add(
            ItemNota(
                numero_nota="3001",
                chave_acesso="3" * 44,
                cfop="1915",
                fornecedor="Terceiro Conserto",
                codigo="MAT-002",
                descricao="Item conserto",
                qtd_real=5,
                status="Concluido",
                cnpj_emitente="98765432000188",
                cnpj_destinatario=ConsertoService.CNPJ_EMPRESA_PADRAO,
            )
        )
        db.session.commit()

        resumo = ConsertoService.processar_retorno_no_lancamento("3001", "admin")
        baixa = ConsertoBaixa.query.one()
        estoque = ConsertoEstoque.query.one()

        assert resumo["baixas_sugeridas"] == 1
        assert baixa.cfop_retorno == "1915"
        assert baixa.status_baixa == "Pendente de confirmacao"
        assert estoque.quantidade_saldo == 5


def test_relatorio_estoque_consolida_operacoes(tmp_path):
    app = build_test_app(tmp_path)

    with app.app_context():
        ConsertoService.criar_saldo_remessa(
            numero_nf_remessa="4001",
            chave_nf_remessa="4" * 44,
            data_emissao=datetime(2026, 3, 29),
            fornecedor_cnpj="11111111000111",
            fornecedor_nome="Fornecedor Conserto",
            produto_codigo="MAT-003",
            produto_descricao="Item conserto",
            quantidade=2,
            tipo_operacao="Conserto",
            cfop_remessa="5915",
            usuario="admin",
        )
        relatorio = ConsertoService.montar_relatorio_estoque()
        operacoes = {item["tipo_operacao"]: item for item in relatorio["grupos_operacao"]}

        assert relatorio["cards"]["registros_abertos"] == 1
        assert relatorio["cards"]["quantidade_em_terceiros"] == 2
        assert operacoes["Conserto"]["quantidade_saldo"] == 2


def test_sincronizacao_conserto_usa_erp_postgres_e_confirma_baixa(tmp_path, monkeypatch):
    app = build_test_app(tmp_path)

    with app.app_context():
        monkeypatch.setattr(
            ConsertoService,
            "_buscar_conserto_erp_bridge",
            staticmethod(
                lambda data_inicial: [
                    {
                        "numero_nf_remessa": "9001",
                        "chave_nf_remessa": "9" * 44,
                        "data_emissao": "2026-05-10",
                        "fornecedor_cnpj": "12345678000199",
                        "fornecedor_nome": "Fornecedor GRV",
                        "produto_codigo": "MAT-ERP",
                        "produto_descricao": "Material ERP",
                        "quantidade_enviada": 10,
                        "quantidade_retornada": 0,
                        "cfop_remessa": "5915",
                        "tipo_operacao": "Conserto",
                        "retornos": [
                            {
                                "numero_nf_retorno": "8001",
                                "chave_nf_retorno": "8" * 44,
                                "data_nf_retorno": "2026-05-15",
                                "quantidade": 4,
                                "cfop_retorno": "1915",
                                "origem_vinculo": "nf_entrada_referenciada",
                            }
                        ],
                    }
                ]
            ),
        )

        resumo = ConsertoService.sincronizar_notas_fiscais("admin", data_inicial=datetime(2026, 5, 1))
        estoque = ConsertoEstoque.query.one()
        baixa = ConsertoBaixa.query.one()

        assert resumo["remessas_erp"] == 1
        assert resumo["saldos_criados"] == 1
        assert resumo["baixas_confirmadas_erp"] == 1
        assert estoque.numero_nf_remessa == "9001"
        assert estoque.quantidade_enviada == 10
        assert estoque.quantidade_saldo == 6
        assert baixa.numero_nf_retorno == "8001"
        assert baixa.status_baixa == "Confirmado"


def test_processos_conserto_consolida_por_nf_remessa(tmp_path):
    app = build_test_app(tmp_path)

    with app.app_context():
        saldo = ConsertoService.criar_saldo_remessa(
            numero_nf_remessa="7001",
            chave_nf_remessa="a" * 44,
            data_emissao=datetime(2026, 5, 1),
            fornecedor_cnpj="12345678000199",
            fornecedor_nome="Fornecedor Processo",
            produto_codigo="MAT-PROC",
            produto_descricao="Material processo",
            quantidade=5,
            tipo_operacao="Conserto",
            cfop_remessa="5915",
            usuario="admin",
        )
        saldo.quantidade_saldo = 2
        db.session.commit()

        processos = ConsertoService.listar_processos_conserto()

        assert len(processos) == 1
        assert processos[0]["numero_nf_remessa"] == "7001"
        assert processos[0]["quantidade_enviada"] == 5
        assert processos[0]["quantidade_baixada"] == 3
        assert processos[0]["quantidade_saldo"] == 2


def test_consulta_manual_por_numero_preenche_dados_do_retorno(tmp_path):
    app = build_test_app(tmp_path)

    with app.app_context():
        saldo = ConsertoService.criar_saldo_remessa(
            numero_nf_remessa="5001",
            chave_nf_remessa="6" * 44,
            data_emissao=datetime(2026, 3, 31),
            fornecedor_cnpj="33333333000133",
            fornecedor_nome="Fornecedor Teste",
            produto_codigo="MAT-005",
            produto_descricao="Produto teste",
            quantidade=7,
            tipo_operacao="Conserto",
            cfop_remessa="5915",
            usuario="admin",
        )

        db.session.add(
            ItemNota(
                numero_nota="6001",
                chave_acesso="7" * 44,
                cfop="1915",
                fornecedor="Fornecedor Teste",
                codigo="MAT-005",
                descricao="Produto teste",
                qtd_real=4,
                status="Concluido",
                cnpj_emitente="33333333000133",
                cnpj_destinatario=ConsertoService.CNPJ_EMPRESA_PADRAO,
            )
        )
        db.session.commit()

        payload = ConsertoService.consultar_retorno_manual_por_numero("6001", saldo.id)

        assert payload["numero_nf_retorno"] == "6001"
        assert payload["chave_nf_retorno"] == "7" * 44
        assert payload["cfop_retorno"] == "1915"
        assert payload["quantidade"] == 4
