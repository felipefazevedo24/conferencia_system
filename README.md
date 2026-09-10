# Conferencia System

Sistema Flask para recebimento, conferencia, expedicao, compras, logistica e integracoes com o ERP, acompanhado pelo aplicativo Flutter do motorista.

## Estrutura

- `conferencia_app/`: aplicacao Flask, modelos, rotas e servicos.
- `templates/` e `static/`: interface web e arquivos publicos.
- `migrations/`: revisoes Alembic do banco da aplicacao.
- `scripts/`: manutencoes, migracoes pontuais e bridge do ERP.
- `tests/`: testes automatizados do backend. Nao e artefato de producao e nao entra no bundle de deploy.
- `motorista_app/`: aplicativo Flutter Android do motorista.
- `deploy/`: configuracoes e utilitarios de implantacao.
- `docs/`: documentacao operacional, especificacoes e arquivos de referencia.
- `instance/`: configuracoes, credenciais e dados locais. O diretorio nao e versionado.

Os arquivos Python mantidos na raiz (`app.py`, `wsgi.py` e `serve_tablet.py`) sao entrypoints. Arquivos temporarios, logs, bancos locais, backups, caches e builds devem permanecer fora do Git.

## Desenvolvimento

```powershell
.\.venv\Scripts\Activate.ps1
python app.py
```

Testes do backend:

```powershell
python -m pytest -q
```

Build do aplicativo do motorista:

```powershell
Set-Location motorista_app
flutter pub get
flutter analyze
flutter test
flutter build apk
```

## Banco e deploy

Use Alembic para alteracoes permanentes de schema. Os scripts em `scripts/` existem para manutencoes operacionais identificadas e nao devem ser copiados para a raiz.

- Bridge ERP: [docs/BRIDGE_ERP_ATUALIZACAO.md](docs/BRIDGE_ERP_ATUALIZACAO.md)
- API de dados de expedicao: [docs/API_DADOS_ENVIO.md](docs/API_DADOS_ENVIO.md)
- Especificacao Comex: [docs/COMEX_ESPECIFICACAO.md](docs/COMEX_ESPECIFICACAO.md)
- Atualizacao do PythonAnywhere (subida normal e com alteracao de banco): [docs/PYTHONANYWHERE_ATUALIZACAO.md](docs/PYTHONANYWHERE_ATUALIZACAO.md)

Para gerar o pacote do PythonAnywhere:

```powershell
.\scripts\build_pythonanywhere_bundle.ps1
```

## Regras de organizacao

- Codigo de aplicacao fica em `conferencia_app/`; scripts executaveis pontuais ficam em `scripts/`.
- Documentacao e planilhas de referencia ficam em `docs/`.
- Nao versionar bancos (`*.db`), logs (`*.log`), backups, tokens ou credenciais.
- Nao remover testes para reduzir o deploy: o empacotador deve exclui-los. Testes protegem os fluxos operacionais e devem acompanhar o codigo.
- Artefatos gerados (`dist/`, `build/`, caches Python/pytest e APKs) podem ser apagados e recriados.
