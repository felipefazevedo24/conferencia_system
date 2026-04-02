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


def test_criar_saldo_industrializacao_grava_classificacao(tmp_path):
    app = build_test_app(tmp_path)

    with app.app_context():
        saldo = ConsertoService.criar_saldo_remessa(
            numero_nf_remessa="1001",
            chave_nf_remessa="1" * 44,
            data_emissao=datetime(2026, 3, 31),
            fornecedor_cnpj="12345678000199",
            fornecedor_nome="Fornecedor Industrializacao",
            produto_codigo="MAT-001",
            produto_descricao="Material enviado",
            quantidade=10,
            tipo_operacao="Industrializacao",
            tipo_controle="Meu em poder de terceiros",
            cfop_remessa="5901",
            usuario="admin",
        )

        assert saldo.tipo_operacao == "Industrializacao"
        assert saldo.tipo_controle == "Meu em poder de terceiros"
        assert saldo.cfop_remessa == "5901"
        assert saldo.status == "Pendente de retorno"


def test_retorno_industrializacao_gera_baixa_pendente_para_autorizacao(tmp_path):
    app = build_test_app(tmp_path)

    with app.app_context():
        ConsertoService.criar_saldo_remessa(
            numero_nf_remessa="2001",
            chave_nf_remessa="2" * 44,
            data_emissao=datetime(2026, 3, 30),
            fornecedor_cnpj="98765432000188",
            fornecedor_nome="Terceiro Industrial",
            produto_codigo="MAT-002",
            produto_descricao="Item industrializado",
            quantidade=5,
            tipo_operacao="Industrializacao",
            tipo_controle="Meu em poder de terceiros",
            cfop_remessa="5901",
            usuario="admin",
        )

        db.session.add(
            ItemNota(
                numero_nota="3001",
                chave_acesso="3" * 44,
                cfop="5902",
                fornecedor="Terceiro Industrial",
                codigo="MAT-002",
                descricao="Item industrializado",
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
        assert baixa.cfop_retorno == "5902"
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
        ConsertoService.criar_saldo_remessa(
            numero_nf_remessa="4002",
            chave_nf_remessa="5" * 44,
            data_emissao=datetime(2026, 3, 29),
            fornecedor_cnpj="22222222000122",
            fornecedor_nome="Fornecedor Industrial",
            produto_codigo="MAT-004",
            produto_descricao="Item industrializacao",
            quantidade=3,
            tipo_operacao="Industrializacao",
            cfop_remessa="5901",
            usuario="admin",
        )

        relatorio = ConsertoService.montar_relatorio_estoque()
        operacoes = {item["tipo_operacao"]: item for item in relatorio["grupos_operacao"]}

        assert relatorio["cards"]["registros_abertos"] == 2
        assert relatorio["cards"]["quantidade_em_terceiros"] == 5
        assert operacoes["Conserto"]["quantidade_saldo"] == 2
        assert operacoes["Industrializacao"]["quantidade_saldo"] == 3


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
                cfop="5916",
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
        assert payload["cfop_retorno"] == "5916"
        assert payload["quantidade"] == 4
