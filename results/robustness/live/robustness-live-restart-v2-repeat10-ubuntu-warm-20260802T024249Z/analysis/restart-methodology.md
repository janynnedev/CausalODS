# Metodologia — reinicialização do Correlation Engine

A campanha avaliou a recuperação do CausalODS após uma reinicialização
forçada do `correlation-engine`.

## Procedimento

Para cada uma das dez iterações experimentais:

1. o ambiente saudável foi aquecido com duas requisições excluídas das
   métricas;
2. um controle saudável medido foi executado;
3. uma falha de latência de 1500 ms foi configurada no
   `payment-service`;
4. o `correlation-engine` foi pausado;
5. a requisição experimental foi enviada;
6. o container foi encerrado com `SIGKILL`;
7. o container foi iniciado explicitamente;
8. a reinicialização foi confirmada pela mudança de `StartedAt`;
9. o consumidor Kafka foi aguardado até recuperar lag zero;
10. o RCAResult foi consultado e validado;
11. o ambiente foi restaurado.

Após as dez iterações, foi executado um controle saudável adicional.

## Composição

- 10 execuções experimentais;
- 11 controles saudáveis medidos;
- 22 requisições de aquecimento;
- 2 aquecimentos por controle;
- aquecimentos excluídos das métricas de robustez.

## Critérios de validade

Uma execução foi considerada tecnicamente válida somente quando:

- a requisição retornou HTTP 2xx;
- o trace foi localizado;
- a reinicialização foi comprovada pela mudança de `StartedAt`;
- o consumidor retornou a lag zero;
- exatamente um RCAResult foi produzido;
- não houve perda ou duplicação;
- o serviço raiz foi identificado como `payment-service`;
- o resultado foi classificado como confiável;
- a proveniência correspondeu ao runtime e pipeline v2;
- o ambiente foi restaurado corretamente.

## Observação sobre o ambiente

Essa campanha foi executada em Ubuntu nativo com Docker instalado via
APT. Os cenários anteriores de backlog Kafka e indisponibilidade do
OpenSearch foram executados em outro ambiente. Por isso, tempos
absolutos entre ambientes não devem ser comparados diretamente.
