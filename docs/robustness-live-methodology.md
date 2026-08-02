# CausalODS — Metodologia da robustez live v1

## Objetivo

Validar recuperação, unicidade e auditabilidade do runtime v2 diante de três falhas de infraestrutura controladas:

1. backlog no Kafka;
2. indisponibilidade temporária do OpenSearch;
3. encerramento abrupto do correlation-engine com mensagem pendente.

## Unidade experimental

Cada execução gera um pedido com produto exclusivo, resolve o `trace_id` pelos logs do `order-service` e consulta os índices por esse `trace_id`. Contagens globais não são usadas como vínculo causal.

## Falhas

- **Kafka backlog:** `docker pause correlation-engine`, emissão do pedido, comprovação de `lag >= 1`, `docker unpause` e observação da drenagem.
- **OpenSearch indisponível:** `docker pause causalods-opensearch`, emissão do pedido, espera superior ao timeout normal de trace, captura de erro explícito, restauração e busca do resultado.
- **Restart:** pausa do correlation-engine para formar backlog, emissão do pedido e `SIGKILL`; a política de restart deve recuperar o container e consumir a mensagem pendente.

## Invariantes

- no máximo um RCAResult por trace;
- no máximo um incidente por trace;
- backlog termina em zero;
- proveniência v2 preservada;
- nenhuma perda silenciosa;
- restauração obrigatória em `finally`;
- controles saudáveis antes e depois de cada bloco.

## Interpretação da indisponibilidade do OpenSearch

O catálogo permite zero ou um RCAResult. Zero RCAResult só não é classificado como perda silenciosa quando existe erro de persistência explícito nos logs. Isso mede segurança operacional separadamente de garantia de retry durável.

## Matriz final

- runtime: v2;
- três cenários;
- dez repetições por cenário;
- execução sequencial;
- seis controles saudáveis, um antes e um depois de cada bloco.
