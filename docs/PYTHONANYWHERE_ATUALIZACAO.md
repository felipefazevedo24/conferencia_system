# Atualização do Sync no PythonAnywhere

Runbook de copiar-e-colar. Dois cenários: **subida normal** (Parte 1) e
**subida com alteração de banco** (Parte 1 + Parte 2).

> A VM Windows que roda a bridge do ERP é outro servidor, sem relação com
> este processo. Não precisa mexer nela pra nada que esteja neste documento.

**Como saber se preciso da Parte 2:** olhe o que mudou em
`conferencia_app/models.py` no commit. Ganhou uma linha `db.Column(...)`
nova **dentro de uma classe que já existia antes**? → precisa da Parte 2.
Só classe nova (tabela inteira nova)? Só mudou template/rota/e-mail? → só
Parte 1.

---

## Parte 1 — Subida normal (sempre faça esta parte)

**Onde:** PythonAnywhere → aba **Consoles** → abra o console Bash do projeto
(ou crie um novo).

**Passo 1 — entrar na pasta do projeto:**
```bash
cd ~/conferencia_system
```

**Passo 2 — conferir se está limpo:**
```bash
git status
```
Leia o resultado antes de continuar:
- Termina em `nothing to commit, working tree clean` → **pode seguir pro Passo 3.**
- Aparece qualquer coisa em "Changes not staged" ou "Untracked files" →
  **pare aqui** e me manda o texto completo que apareceu, antes de continuar.

**Passo 3 — puxar o código novo:**
```bash
git pull
```
Deve terminar em `Fast-forward`. Se aparecer erro de conflito, **pare aqui**
e me manda o erro.

**Passo 4 — só se o commit alterou banco, vá pra Parte 2 agora.** Se não
alterou, pule direto pro Passo 5.

**Passo 5 — reiniciar o app:**

**Onde:** PythonAnywhere → aba **Web** → botão verde **Reload** (perto do
topo da página do seu app).

**Passo 6 — testar:** abra `sync.columbiamachine.com` e confira a tela que
mudou.

---

## Parte 2 — Alteração de banco (só se o Passo 4 mandou você pra cá)

**Onde:** mesmo console Bash da Parte 1 (continue nele, já está na pasta
certa).

**Passo A — rodar a migration do Comex:**
```bash
python scripts/aplicar_migracao_comex.py
```
Deve terminar em:
```
Migration Comex aplicada com sucesso (idempotente - nada quebra se rodar de novo).
```

**Se der erro `ArgumentError: Could not parse SQLAlchemy URL` ou `Access
denied`:** vá pra seção "Se der erro na Parte 2" abaixo antes de continuar.

**Passo B — voltar pra Parte 1, Passo 5** (Reload + testar).

> Se o commit alterou banco de **outro módulo** que não o Comex (não existe
> `scripts/aplicar_migracao_comex.py` pra ele), me avisa antes de dar
> Reload — vai precisar de um script equivalente pra aquele módulo.

---

## Se der erro na Parte 2

O comando abaixo resolve os dois erros mais comuns do Passo A
(`ArgumentError` e `Access denied`). Ainda no mesmo console Bash:

**Passo 1 — pegue a URL do banco:**

**Onde:** PythonAnywhere → aba **Web** → clique no link do arquivo WSGI
(nome parecido com `/var/www/seuusuario_pythonanywhere_com_wsgi.py`) →
copie o valor que está dentro das aspas na linha `DATABASE_URL`.

**Passo 2 — rode o comando abaixo, trocando só o texto entre `'...'` pelo
valor copiado (mantenha as aspas simples, exatamente como estão):**
```bash
DATABASE_URL='COLE_AQUI_A_URL_COPIADA' python scripts/aplicar_migracao_comex.py
```

⚠️ **Tem que ser aspas simples `'...'`, não aspas duplas `"..."`.** O nome
do banco tem um `$` no meio (ex.: `usuario$nome_do_banco`) — com aspas
duplas o Bash apaga tudo depois do `$`, e o banco vira o nome errado.

Deu certo? Volte pro Passo B da Parte 2.

---

## Checklist rápido

| O que mudou | Parte 1 | Parte 2 |
|---|---|---|
| Só código (rotas, templates, JS, textos de e-mail) | ✅ | ❌ |
| Tabela nova no `models.py` (classe nova) | ✅ | ❌ |
| Coluna nova numa tabela que já existia | ✅ | ✅ |

## Outros problemas

- **Tela específica dá "Erro interno do servidor" depois do Reload** →
  esqueceu a Parte 2. Volte e rode o Passo A.
- **Não sei se o `git status` está limpo ou não** → me manda o texto
  completo que apareceu, eu confirmo.
