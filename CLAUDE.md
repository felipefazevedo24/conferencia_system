# CLAUDE.md — Sync (conferencia_system)

Instruções para agentes trabalhando neste repositório. O que está aqui vem
de incidente real: quase toda regra abaixo existe porque a ausência dela já
quebrou produção ou custou retrabalho.

## O projeto

Sistema interno da Columbia Machine Brasil: recebimento, expedição,
logística, intralog, compras, comex, produção e qualidade. Flask + SQLAlchemy,
app factory em `conferencia_app/__init__.py`.

- **Produção:** PythonAnywhere (MySQL), `sync.columbiamachine.com`. Deploy é
  `git pull` + Reload — **não há staging**. O que entra no `main` vai pro ar.
- **Local:** SQLite (`database.db`).
- **Bridge do ERP:** VM Windows separada (`scripts/erp_lancamento_api_bridge.py`),
  alcançada por Tailscale. Fala com o Postgres do ERP CPS.
- **Idioma:** código, comentários, commits e conversa em português. Comentários
  explicam *por quê*, não *o quê*.

---

## 1. Pensar antes de codar

**Não assuma. Não esconda dúvida. Exponha o trade-off.**

- Diga suas premissas. Se estiver em dúvida, pergunte antes de construir.
- Se houver mais de uma interpretação, apresente — não escolha em silêncio.
- Se existe caminho mais simples, diga. Discorde quando for o caso.

Neste projeto isso tem retorno medido: as rodadas de pergunta antes de
construir o Nesting, o Picking e a Homologação evitaram retrabalho. Onde
eu assumi sem perguntar (o nome "Chapa Picking", a ordem do status "Nesting
Liberado"), deu retrabalho.

**Investigue com dado real antes de modelar.** O parser do Nesting foi
validado contra 3 relatórios de verdade antes de virar código; a integração
do Picking só foi desenhada depois de descobrir, olhando os 535 registros
reais, que a API não tem identidade de linha.

---

## 2. Escopo: relatar ≠ implementar

**Implemente o que foi pedido. Relate o que você viu.**

Não implemente sem pedir:
- funcionalidade além do pedido;
- abstração para um único uso;
- configurabilidade que ninguém pediu;
- tratamento de erro para cenário impossível.

Mas **sempre relate** (sem implementar por conta própria):
- bug latente que você cruzou no caminho;
- dado que contradiz a premissa do pedido;
- risco de perda de dado.

Os achados mais valiosos das últimas sessões estavam fora do pedido: o
`importar_relatorio` que apagaria observação/baixa num reimport, a API de
picking sem chave estável, o furo de permissão no menu Compras. Calar sobre
isso seria pior do que "sair do escopo".

---

## 3. Mudanças cirúrgicas

**Toque só no que precisa.**

- Não "melhore" código adjacente, comentário ou formatação.
- Siga o estilo existente, mesmo que você faria diferente.
- Código morto que não é seu: mencione, não apague.
- Órfão que **a sua** mudança criou (import, variável): aí sim, remova.

Aqui isso importa mais que o normal: **várias sessões empurram para o mesmo
`main` ao mesmo tempo**. Cada linha extra que você toca é risco de conflito
com o trabalho de outra pessoa.

---

## 4. Verificação

Transforme a tarefa em critério verificável e feche o ciclo. A escada usada
neste projeto, do mais barato ao mais caro:

1. `python -c "import ast; ast.parse(open(ARQUIVO, encoding='utf-8').read())"`
2. **Boot-check:** `python -c "from conferencia_app import create_app; create_app()"`
3. Renderizar a tela de verdade com `test_client()` e conferir o HTML —
   pega erro de Jinja que o boot-check não pega.
4. Teste focado: `python -m pytest tests/ -k "<modulo>" -q`
5. Suíte completa antes do push.

Nunca diga que algo funciona sem ter rodado. Se um teste falhou, mostre a
saída.

---

## 5. Banco de dados — a regra mais cara do projeto

**Antes de qualquer coisa:** o que mudou em `conferencia_app/models.py`?

| O que mudou | O que precisa |
|---|---|
| Classe nova (tabela inteira nova) | Nada. `db.create_all()` cria sozinho. |
| `db.Column(...)` nova **em classe que já existia** | Migration + script de aplicação |
| Só rota/template/serviço | Nada |

Para coluna nova em tabela existente, o padrão do projeto **não é**
`flask db upgrade`. É:

1. `migrations/versions/<data>_<modulo>.py` — **idempotente**: inspeciona o
   banco e só adiciona a coluna se ela ainda não existir. Nunca apaga dado.
2. `scripts/aplicar_migracao_<modulo>.py` — liga o `Operations` do Alembic no
   engine ativo e roda o `upgrade()`. Copie um script existente; são todos iguais.

Antes de commitar a migration, **teste num banco sujo**: crie um SQLite sem
as colunas novas, insira uma linha, rode o script **duas vezes** e confirme
que as colunas entraram e o dado sobreviveu.

⚠️ **Em produção, o comando vai prefixado com `DATABASE_URL`:**

```bash
DATABASE_URL='<copiado do arquivo WSGI, entre aspas simples>' python scripts/aplicar_migracao_X.py
```

Sem o prefixo o script roda contra um SQLite local vazio, "dá sucesso" e a
produção continua sem a coluna — e aí **todo o módulo passa a dar erro 500**.
Isso já aconteceu duas vezes (Comex e Consumo de Chapa). Ao entregar uma
mudança de banco, escreva o comando completo pro usuário, com o prefixo.

---

## 6. Deploy

O runbook de copiar-e-colar é o `docs/PYTHONANYWHERE_ATUALIZACAO.md`. Ao
terminar uma tarefa, diga explicitamente se é **Parte 1** (só `git pull` +
Reload) ou **Parte 1 + Parte 2** (com a migration).

Duas armadilhas já vividas:
- `pip install` fora do virtualenv → `ModuleNotFoundError` no Reload. Tem que
  estar no venv da aba **Web**.
- `cd` na pasta errada → `fatal: not a git repository`. É `~/conferencia_system`.

> A tabela da "Parte 2" no runbook está desatualizada (lista 2 módulos; já
> existem 10 scripts). Fonte da verdade: `ls scripts/aplicar_migracao_*.py`.

---

## 7. Git — o `main` se mexe embaixo de você

```bash
git fetch origin && git log HEAD..origin/main --oneline   # antes de commitar
git commit ...
git fetch origin && git log HEAD..origin/main --oneline   # DE NOVO, antes do push
git pull origin main --no-edit                            # se mexeu
git push origin main
```

Checar só uma vez não basta: já levamos dois `non-fast-forward` porque outra
sessão empurrou entre o fetch e o push.

- **Merge, nunca rebase. Nunca `--force`.**
- Antes de mesclar, veja se o remoto mexeu nos *seus* arquivos:
  `git diff --stat <seu-ultimo-commit>..origin/main -- <arquivos>`
- Um diff gigante entre local e remoto costuma ser artefato do sentido do
  diff, não conflito real. Confirme antes de reagir.
- **Se parecer que o remoto destruiu algo, pare e investigue.** Já pareceu
  que o `main` tinha perdido 1048 linhas de teste — era remoção deliberada do
  dono (commit `e0c76eb`, Controladoria). Nunca "conserte" sem confirmar.
- Commite só os arquivos da sua tarefa. A raiz do repo costuma ter planilhas
  e HTMLs soltos do usuário — não são seus, não commite.

---

## 8. Como adicionar um módulo

Siga um módulo recente como molde (`intralog_picking`, `compras_homologacao`).
Checklist completo:

1. `conferencia_app/models.py` — model, com docstring explicando o workflow.
2. `conferencia_app/services/<modulo>_service.py` — regra de negócio. O
   service levanta `ValueError` com mensagem em português pro usuário final.
3. `conferencia_app/routes/<modulo>_routes.py` — rotas finas: traduzem
   `ValueError` → HTTP 400, `None` → 404. Toda rota com `@permission_required`.
4. `conferencia_app/auth.py` — chave nova em `PERMISSION_CATALOG`. `Admin`
   ganha tudo automaticamente; avalie incluir no cargo certo em
   `BASE_ROLE_PERMISSIONS`.
5. `conferencia_app/__init__.py` — importar e registrar o blueprint.
6. `templates/<modulo>.html` — estende `base.html`, usa as classes `eui-*`.
7. `templates/base.html` — link no menu **e** a permissão nova no `{% if %}`
   que abre a seção (esquecer o segundo esconde a seção inteira de quem só
   tem a permissão nova — já aconteceu).
8. Teste em `tests/`, com fixture de dado **real** quando houver.

Convenções que valem manter:
- Estado derivado de sistema externo **se calcula, não se grava** — assim
  nunca sai de sincronia (ver `concluido` no Picking).
- Arquivo/foto vai pro banco como `LargeBinary().with_variant(LONGBLOB, "mysql")`,
  não pra drive externo.
- Integração com o ERP fica isolada num service e **nunca derruba a tela**:
  falhou, devolve vazio + um sinalizador, e a página avisa.

---

## 9. Testes

```bash
python -m pytest tests/ -q                    # tudo (~40 min)
python -m pytest tests/ -k "<modulo>" -q      # durante o desenvolvimento
```

Helpers: `build_test_app(tmp_path)` e `login_admin(client)` (usuário `ADMIN`).
Chamada externa (ERP/bridge) **sempre** com `patch` — teste não pode depender
de rede.

**Falhas pré-existentes conhecidas** em `tests/test_app.py` (vêm de outras
sessões, não do seu trabalho — verificado em 14/09/2026):

```
test_alembic_env_works_with_app_context
test_check_active_session_retries_after_operational_error
test_stats_requires_authentication
test_confirmar_lancamento_reverte_quando_manifestacao_falha
test_portaria_pode_consultar_nfes_liberadas_mas_nao_baixar_documento
test_pagina_e_api_de_notas_liberadas_estao_disponiveis_para_todos_os_perfis
test_api_processo_recebimento_painel_bloqueia_portaria
test_excluir_registro_expedicao_remove_vinculos_mesmo_com_foto_perdida
test_auditor_preserva_codigo_material_do_erp_sem_formatar
```

Não gaste tempo rediagnosticando essas. Mas **confirme o baseline** antes de
assumir: se aparecer uma décima falha, ela provavelmente é sua.

---

## 10. O que ainda não fazemos e deveria

Lista honesta de dívidas. Se você encontrar oportunidade de atacar alguma
(e tiver aval), vale mais que funcionalidade nova:

- **Rodar `tests/` inteiro, não só `test_app.py`.** Existem 4 arquivos de
  teste; por muito tempo só um foi rodado antes dos pushes.
- **As 9 falhas acima deveriam ser corrigidas ou marcadas `xfail`** com o
  motivo. Baseline quebrado normaliza falha e esconde regressão de verdade.
- **Não há CI.** Tudo depende de alguém lembrar de rodar a suíte. Um GitHub
  Action rodando `pytest` no push resolveria.
- **Não há staging.** `main` vai direto pra produção.
- **A suíte leva ~40 min** e quase tudo está num `test_app.py` de milhares de
  linhas. Deveria ser quebrado por módulo.
- **Migration não tem teste automatizado** — a verificação de idempotência é
  feita à mão, por script solto, e se perde depois.
- **A tabela da Parte 2 no runbook de deploy está desatualizada.**

---

## Sinais de que estas instruções estão funcionando

Diffs menores e rastreáveis ao pedido; pergunta antes da implementação em vez
de correção depois do erro; nenhuma subida de banco sem o `DATABASE_URL`;
nenhum push sem `fetch` imediatamente antes.
