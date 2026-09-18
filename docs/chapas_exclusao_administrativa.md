# Exclusão manual do controle de chapas

Na página **Logística → Estoque → Controle de Chapas**, um Admin pode expandir o material e clicar em **Excluir** no item/lote desejado. O modal identifica código, NF, AR e lote antes de confirmar. A exclusão vale apenas para o item escolhido, inclusive quando outra linha tem a mesma NF ou código.

O item deixa de aparecer na listagem e no histórico de zerados, mesmo após atualizar a página, recalcular ou relançar a nota. A operação preserva o recebimento, a quantidade marcada como chapa, a NF, os cálculos e o saldo do GRV. Usuário e data ficam registrados em `chapa_controle_exclusao`.

O botão é renderizado somente para Admin e o endpoint `DELETE /api/logistica/chapas/<item_id>` também exige esse papel. A permissão de inventário de outros usuários não permite excluir. O JSON deve confirmar `confirmacao_item_id`; repetir a exclusão é idempotente.

## Auditoria

O botão **Histórico de alterações** permite consultar exclusões, mudanças de quantidade em UND e alterações de cálculo (material, formato, medidas e peso por peça). Cada evento mostra usuário, data/hora, identificação do item/NF/AR e valores antes/depois. A busca aceita NF, código, descrição, AR, usuário ou ação e o resultado é paginado.

Os eventos ficam em `chapa_auditoria`, gravados na mesma transação da alteração. Não são apagados quando a NF, a marcação de exclusão ou o cálculo são removidos. Operações repetidas com os mesmos valores não geram novo evento nessa auditoria; rollback da alteração também descarta o evento. A exclusão continua exclusiva de Admin; usuários com acesso ao inventário podem consultar o histórico.

A auditoria unificada registra alterações realizadas após esta atualização. Não reconstrói quantidades anteriores que nunca foram registradas. Os históricos anteriores de cálculo continuam acessíveis pela calculadora.

## Publicação

Publicar os arquivos e fazer Reload do aplicativo. O bootstrap existente cria as tabelas novas. Para instalações gerenciadas por Alembic, foram incluídas as migrations idempotentes `20260918_chapa_exclusao` (posterior a `20260918_inv_descricao`) e `20260918_chapa_auditoria`. Não há alteração de colunas existentes nem atualização da bridge.

## Validação

`python -m pytest tests/test_chapas_exclusao.py -q`

Testes cobrem Admin e outros papéis, confirmação, persistência da exclusão, preservação de NF/cálculo, migrations, compatibilidade com a exclusão de NF pendente, auditoria de UND pelo recebimento, cálculos, rollback, ausência de eventos repetidos e histórico após remoção da NF. O teste Chromium cobre cancelamento, confirmação, consulta da auditoria e recarregamento. O GRV é simulado nos testes.
