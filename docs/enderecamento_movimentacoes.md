# Endereçamento e movimentações

Acesso: **Logística → Recebimento → Endereçamento e movimentações**, em `/wms/enderecamento`. O atalho `/recebimento/enderecamento` abre a mesma página. O endereçamento continua integrado à Conferência de Recebimento.

## Operação

- **Materiais e endereços:** busca por SKU/local, saldo físico e unidade. Transferência parcial conserva o restante na origem; a total deixa saldo zero na origem e envia os locais atualizados ao GRV.
- **A endereçar:** mesma fila do recebimento, mais antigos primeiro. Materiais com recebimento reaberto ou sem vínculo de SKU mostram o motivo antes das leituras. NF com status fiscal Lançado continua elegível.
- **Histórico e sincronização:** usuário, data, motivo, quantidades, origem/destino e diferenças de contagem. Filtro de envios pendentes e tentativa manual de sincronização.
- Leitor por teclado: Enter avança os campos. Câmera: botão ao lado de SKU, origem e destino. Na contagem, informar o total físico do SKU naquele endereço; na transferência, informar somente a quantidade movimentada.
- A conferência de saldo exige `MANAGE_RECEBIMENTO_ENDERECAMENTO`; consultas e transferências usam `PAGE_RECEBIMENTO_ENDERECAMENTO`. As permissões existentes são preservadas.

## Fonte do saldo e entrada em operação

O saldo por endereço é mantido pelo **Sync**. O GRV recebe sua lista de localizações; a operação não movimenta o saldo contábil/agregado do ERP.

Novos recebimentos endereçados alimentam o saldo uma única vez, após confirmação da sincronização. Se o endereço já existia no GRV antes do recebimento e não havia saldo conferido no Sync, fica marcado **Conferir saldo inicial**: as quantidades antigas não são deduzidas de uma lista de endereços. Um responsável deve contar o total físico antes da primeira transferência.

Recebimentos concluídos antes da implantação não são importados automaticamente. Para esse estoque, registrar a contagem por SKU/endereço, incluindo a unidade. O valor contado substitui o saldo do endereço e a diferença fica no histórico. Use a unidade da conferência/estoque; o módulo bloqueia unidades diferentes para o mesmo SKU.

Quantidades de localidades sem conferência inicial não devem ser tratadas como saldo total. Não há baixa automática por consumo, picking ou expedição neste módulo: conciliar saídas externas com conferências de saldo antes de movimentar o saldo remanescente.

## Integridade e falhas

- Débito, crédito e histórico de transferência são uma única transação. Não admite saldo negativo, valores não finitos, origem igual ao destino ou local desativado.
- Chave de operação impede duplicidade por reenvio/clique repetido. Após falha de rede, a tela preserva a chave; consultar o histórico antes de iniciar outra operação com dados diferentes.
- A consulta ao GRV precisa estar disponível para validar o SKU e os endereços antes de registrar uma operação. Se a atualização posterior falhar, o saldo local e histórico permanecem salvos e o envio fica pendente.
- O scheduler existente repete também os envios de movimentações. Usa `ENDERECAMENTO_RETRY_AUTO_ENABLED` e `ENDERECAMENTO_RETRY_POLL_INTERVAL_SECONDS`; padrão: primeira execução após 90 segundos e intervalo de 10 minutos.
- Trava persistente por SKU é compartilhada entre recebimento, transferência e reenvio. Expira em 10 minutos para recuperação após interrupção.
- Endereços externos não controlados são preservados. Um endereço controlado só é removido do GRV se zerou numa operação pendente que o afeta. A integração atual não aceita limpar a última localização: saldo zero fica salvo no Sync e o envio permanece pendente, com explicação visível.
- A API do GRV não oferece atualização condicional. Inventário e edições diretas no ERP não usam a trava deste módulo; evitar alteração simultânea do mesmo SKU nesses sistemas e reconciliar pelo histórico.
- Recebimento já integrado ao saldo deve ser corrigido por transferência ou conferência de saldo. Não é reaberto para criar nova entrada física duplicada.
- Exclusão de NF pendente com tarefa vinculada arquiva a tarefa e seus eventos no histórico fiscal antes de remover os vínculos. Movimentações e saldos físicos são preservados. Tarefa com envio pendente/em execução exige revisão antes da exclusão.

## Publicação no PythonAnywhere

1. Publicar os arquivos desta alteração no mesmo checkout usado pelo Web App e ativar seu virtualenv. Conferir com `git status` antes de atualizar para preservar alterações do servidor.
2. Usar o **mesmo banco configurado no WSGI**, definindo `DATABASE_URL` no console. Não enviar a senha em mensagens nem gravá-la no repositório.
3. Executar `python scripts/aplicar_migracao_enderecamento.py`. O script cria somente as tabelas deste módulo quando ausentes; não importa saldos e não depende do stamp de outras migrations. Alternativamente, usar a migration `20260917_endereco_movimentos` pelo fluxo Alembic já gerenciado da instalação. O bootstrap também cria as tabelas novas.
4. Executar `python scripts/aplicar_migracao_enderecamento.py --check`. Deve informar que as cinco tabelas estão disponíveis.
5. Fazer **Web → Reload**. Atualizar a página do navegador e abrir `/wms/enderecamento`.
6. A consulta e a escrita reutilizam `ERP_LANCAMENTO_API_URL`/`ERP_LANCAMENTO_API_TOKEN` e `INVENTARIO_LOCALIZACAO_API_URL`. Não foi alterado o código da bridge nesta entrega; ela precisa já oferecer a consulta de localização usada pelo recebimento.
7. Validar com o time um recebimento, uma transferência parcial e uma total controladas. Confirmar as etiquetas/câmera no aparelho físico e a resposta do GRV real. Os testes automatizados usam GRV simulado.

## Validação automatizada

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_enderecamento_modulo.py tests/test_recebimento_enderecamento.py tests/test_enderecamento_modulo_browser.py tests/test_recebimento_enderecamento_browser.py -q
```

Os testes cobrem avanço fiscal, permissões, impedimentos antecipados, criação idempotente de saldo, quantidades fracionadas/inválidas, transferências parciais/totais, conservação de saldo, rollback, trava por SKU, falhas/reenvios, migração e exclusão com vínculos. Os testes Playwright exercitam backend real em SQLite, GRV simulado, interface desktop/mobile, tema escuro e ausência de erros JavaScript. Não substituem validação da câmera física nem carga/concorrência no MySQL de produção.
