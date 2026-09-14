# Endereçamento após recebimento

Na própria **Conferência de Recebimento**, o cartão **Endereçamento** aparece logo após **Conferido / Ag. Lançamento**. Ao clicar, a fila aparece na mesma tela; não existe página ou item adicional no menu lateral. A fila é criada apenas na confirmação final de `/validar`, para itens conformes e com quantidade positiva. A validação preliminar não cria tarefas. Divergências seguem a tratativa existente; não são liberadas automaticamente para armazenagem. A tarefa usa o ID do item da nota, preservando a identidade mesmo quando números de NF se repetem.

## Operação

- A confirmação do recebimento oferece endereçar agora ou deixar na fila. Ao aceitar, abre a etapa dentro de `/conferencia?etapa=enderecamento`, filtrada pelos itens recém-conferidos.
- Filtros por NF, SKU, descrição e fornecedor; filas Pendente, Aguardando sincronização e Concluído.
- Câmera do celular lê SKU e local. Não há entrada manual, colagem, upload de fotografia ou alternativa de teclado para esses campos. Quantidade e lote podem ser digitados.
- SKU deve ser o `codigo_grv` vinculado ao item; um vínculo ausente precisa ser corrigido no recebimento. O próximo scan atualiza o vínculo da tarefa pendente.
- Local precisa existir e estar ativo em `LocalizacaoArmazem`. O responsável pode cadastrar/ativar/desativar locais na própria tela; deve usar exatamente o código da etiqueta.
- Endereço existente é apresentado após bipar o SKU. A operação normal só aceita esses locais; se não houver endereço, aceita qualquer local cadastrado e ativo.
- “Endereço lotado” acrescenta o destino bipado, mantendo todos os endereços anteriores, sem duplicatas: `A;B;C`.
- Destino alternativo exige permissão de gestão e justificativa. Também preserva os endereços anteriores: esta operação não transfere saldos antigos nem apaga endereços de produtos que ainda possam conter estoque.
- A soma das quantidades por endereço deve coincidir com a quantidade efetivamente conferida, considerando a conversão aplicada. Quantidades/lotes ficam no Sync; a API de endereço do GRV recebe somente a lista de locais.
- Histórico registra origem, usuário, data, quantidades, motivo, endereços anteriores/enviados e cada tentativa. Falhas ficam na fila com botão para tentar sincronizar novamente. O reenvio não duplica as parcelas.
- O responsável com permissão de gestão pode usar **Revisar leituras** em envios pendentes, informando uma justificativa. A tarefa volta à fila Pendente para bipar novamente; o histórico anterior é mantido e nenhum endereço é removido do GRV.

## Acessos

As duas permissões aparecem na Gestão de Acessos:

- `PAGE_RECEBIMENTO_ENDERECAMENTO`: Recebimento > Endereçamento. Padrão para Admin, Conferente, Fiscal e Logística; respeita personalizações de acesso existentes.
- `MANAGE_RECEBIMENTO_ENDERECAMENTO`: Recebimento > Gerenciar locais e autorizar destino alternativo. Padrão apenas Admin; conceder junto da permissão de página quando necessário.

## Publicação

1. Publicar o aplicativo e executar a migração `20260914_receb_enderecamento` pelo processo normal de deploy (`flask db upgrade`). O bootstrap também cria as novas tabelas em instalações que usam `db.create_all`; a migração tolera tabelas já criadas.
2. Atualizar e reiniciar **também o bridge** `scripts/erp_lancamento_api_bridge.py`. O endpoint autenticado `POST /api/erp/produto-localizacao` consulta `tproduto` diretamente no Postgres, sem cache e inclusive para produtos sem saldo. SKU inexistente ou ambíguo bloqueia o fluxo.
3. Manter `ERP_LANCAMENTO_API_URL`/`ERP_LANCAMENTO_API_TOKEN` configurados para a consulta. A gravação reaproveita `atualizar_localizacao_estoque` e `INVENTARIO_LOCALIZACAO_API_URL`, como no inventário.
4. Conferir os endereços ativos e os vínculos de SKU antes de iniciar a operação. Não há criação retroativa de pendências para recebimentos antigos.
5. Acessar por HTTPS e liberar câmera no celular. Validar no aparelho real a leitura das etiquetas usadas no armazém.

O decoder [html5-qrcode 2.3.8](https://github.com/mebjas/html5-qrcode) está incluído em `static/vendor/html5-qrcode`, com licença Apache 2.0, sem dependência de CDN em tempo de uso. Usa leitura de câmera, sem expor importação de arquivos. Os recibos assinados vinculam leituras a usuário/item e expiram em 30 minutos; isso protege o fluxo contra reaproveitamento acidental, mas não constitui atestado de hardware contra um cliente que falsifique chamadas à API.

A trava por SKU serializa envios deste módulo entre workers, com expiração de 10 minutos para recuperação após interrupção. Uma consulta nova precede cada envio. O endpoint PATCH existente não oferece compare-and-swap; alterações concorrentes feitas diretamente no ERP ou pelo inventário não compartilham essa trava. Não há promessa de transação distribuída ou alteração de saldo no GRV.
