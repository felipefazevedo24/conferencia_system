# Validação e limpeza obrigatórias

Toda alteração deve ser validada com rigor antes de ser considerada concluída.
Escolha verificações proporcionais ao impacto: fluxo real, casos de erro,
permissões e regressões relevantes. Para mudanças de interface, verifique
também o comportamento no navegador quando possível. Não declare um teste
ou uma validação em produção como realizado sem tê-lo executado; informe
falhas, limitações e o que ainda depende de publicação.

## Artefatos temporários

- Use a pasta temporária do sistema para novos testes pontuais, scripts de
  diagnóstico, bancos de teste, capturas, relatórios e caches. Não acumule
  pastas `.pytest_tmp*` ou semelhantes na raiz do projeto.
- Ao finalizar as verificações, remova todos os arquivos e diretórios
  temporários criados para a tarefa, inclusive scripts de diagnóstico e
  novos arquivos de teste pontuais. Não os inclua em commits.
- Só mantenha novos testes ou ferramentas de diagnóstico no repositório
  quando o usuário solicitar explicitamente que sejam permanentes.
- Preserve os testes permanentes já existentes, código da aplicação,
  configurações, dados reais e alterações de outras tarefas. A limpeza não
  autoriza apagar arquivos preexistentes indiscriminadamente.
- Antes de excluir, confirme a origem e os caminhos dos artefatos. Em
  exclusões recursivas, valide que o destino está dentro da pasta temporária
  específica da tarefa ou do workspace autorizado; não siga links para fora
  desse escopo e nunca apague a pasta temporária inteira do sistema.
- Confira o estado final do repositório e registre na resposta os testes
  executados e eventuais limitações, sem deixar arquivos descartáveis apenas
  para comprovar a validação.
