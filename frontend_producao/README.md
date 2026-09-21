# Fonte do Painel Visual de Montagem

Este diretório contém a fonte React do bundle servido em
`/producao-original/`. Foi copiado do projeto `Estrutura/apps/web` na
alteração do cronograma para que a lógica do mapa, da árvore e da seleção de
OS possa ser mantida junto com a aplicação Sync.

O backend existente continua em `conferencia_app/routes/producao_routes.py`
e `conferencia_app/services/producao_service.py`. O mapa e a árvore usam o
mesmo `GET /api/v1/orders/<os>/structure`.

Para atualizar o bundle, execute na raiz `conferencia_system`:

```powershell
cd frontend_producao
npm.cmd install
cd ..
.\.venv312\Scripts\python.exe scripts\build_producao_frontend.py
```

O script compila a fonte, copia os arquivos gerados e atualiza as referências
em `static/producao_original/index.html`. Ele mantém os arquivos antigos
para permitir rollback pelo controle de versão.
