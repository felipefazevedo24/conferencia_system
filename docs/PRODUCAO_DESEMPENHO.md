# Carregamento da Produ??o

A corre??o est? em `conferencia_app/services/producao_service.py`, no servidor web do Sync. N?o altera o cat?logo SQL nem exige atualiza??o do ERP Bridge na VM.

- Pedidos simult?neos da mesma estrutura compartilham a consulta em andamento.
- Busca e metadados da OS/documentos s?o reaproveitados por at? 10 segundos por processo e aplicativo, com limite de 128 entradas. A estrutura mant?m o limite de 16 entradas. Resultados s?o copiados para evitar altera??es entre pedidos.
- Ap?s identificar a OS, itens, opera??es e ocorr?ncias s?o consultados em paralelo, com no m?ximo tr?s trabalhadores para estruturas por processo.
- Consultas que falham n?o s?o armazenadas pelo novo cache. Opera??es e materiais por pe?a continuam usando suas consultas existentes.
- Trabalhadores de miniaturas preservam o contexto Flask para acessar a configura??o do aplicativo.

## Aplica??o e verifica??o

Publicar o arquivo do servi?o junto da vers?o atual do aplicativo e reiniciar o processo web pelo procedimento habitual da hospedagem. Reiniciar somente o ERP Bridge n?o aplica esta mudan?a.

Na aplica??o publicada, registrar na aba Network do navegador os tempos da busca 7807, abertura da estrutura e sele??o de uma pe?a, primeiro com cache frio e depois repetindo dentro de 10 segundos. Repetir ap?s 10 segundos para verificar atualiza??o. Conferir documentos, opera??es e troca r?pida entre OS diferentes. O cache ? local ao processo; diferentes trabalhadores web podem precisar aquecer seus pr?prios dados.

Valida??o local: testes de concorr?ncia, expira??o, isolamento entre aplicativos, resultados independentes e regress?es de imagens. Os testes usam consultas simuladas; n?o comprovam a lat?ncia do GRV ou da rede da VM. Consultas lentas no banco ainda podem dominar a primeira busca.
