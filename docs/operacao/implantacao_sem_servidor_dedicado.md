# Implantacao Sem Servidor Dedicado (Tablets)

Este guia usa 1 PC da empresa como host local para tablets na rede Wi-Fi.

## Resumo da Arquitetura
- 1 PC fixo ligado durante o turno roda o app Flask.
- Banco unico no proprio PC host (arquivo SQLite local).
- Tablets acessam por navegador: http://IP_DO_PC:5000

## Importante
- Nao use SQLite em pasta compartilhada de rede para operacao multiusuario.
- Use o banco local do PC host para evitar corrupcao/lock em rede.

## Passo a Passo
1. No PC host, abra o projeto.
2. Garanta que a virtualenv existe em .venv.
3. Execute run_tablet_server.bat.
4. Descubra o IP do PC host (ex: 192.168.0.25).
5. Nos tablets, acesse http://192.168.0.25:5000.
6. No navegador do tablet, usar "Adicionar a tela inicial" para abrir como app.

## Variaveis Opcionais
- APP_HOST (padrao 0.0.0.0)
- APP_PORT (padrao 5000)
- APP_THREADS (padrao 8)
- DATABASE_URL (se quiser trocar para PostgreSQL no futuro)
- DB_PATH (se quiser mover o arquivo sqlite local)

## Recomendacao de Operacao
- PC host com energia estabilizada.
- Backup diario do arquivo database.db para outra pasta.
- Evitar desligar PC no meio do turno.

## Migracao Futura (quando quiser)
- Trocar para PostgreSQL sem alterar fluxo de telas.
- Basta configurar DATABASE_URL e rodar migracoes.
