# CausalODS — Metodologia da campanha de robustez

## Objetivo

Avaliar se o CausalODS preserva correção, unicidade, auditabilidade e segurança
operacional quando recebe telemetria imperfeita ou quando dependências da
pipeline sofrem interrupções temporárias.

A campanha é dividida em duas fases para separar erros semânticos de trace de
falhas de infraestrutura.

## Fase A — robustez semântica offline

Executável enquanto outras campanhas E2E estão ativas, porque não acessa
Docker, Kafka, OpenSearch nem os índices experimentais.

Cenários:

1. `control`;
2. `duplicate_span`;
3. `out_of_order_spans`;
4. `incomplete_trace`;
5. `delayed_span_before_timeout`;
6. `delayed_span_after_timeout`.

Invariantes principais:

- cada trace é finalizado no máximo uma vez;
- `span_id` duplicado não aumenta a quantidade de spans únicos;
- ordem de chegada não altera a semântica;
- trace incompleto não libera decisão confiável;
- span tardio não reabre trace finalizado;
- mutações equivalentes preservam o fingerprint semântico.

## Fase B — robustez live

Só deve ser executada após a campanha de sensibilidade terminar e ser
arquivada.

Cenários:

1. `kafka_backlog`;
2. `opensearch_unavailable`;
3. `correlation_engine_restart`.

Métricas:

- `rca_result_count`;
- `incident_count`;
- `top_service`;
- `is_reliable`;
- `data_loss_count`;
- `recovery_time_ms`;
- `processing_latency_ms`;
- reinícios antes/depois;
- validade de proveniência.

Invariantes globais:

- no máximo um RCAResult por trace;
- no máximo um incidente por trace;
- nenhuma perda silenciosa;
- restauração da falha após cada iteração;
- modelo e pipeline registrados corretamente.

## Oráculo de segurança

Para trace incompleto ou finalizado por timeout:

```text
is_reliable não pode ser true
incident_count deve ser 0
```

Para duplicação, desordem e atraso antes do timeout:

```text
fingerprint semântico deve ser igual ao controle
trace_finalization_count deve ser 1
```

Para backlog, indisponibilidade e reinício:

```text
rca_result_count <= 1
incident_count <= 1
data_loss_count = 0
proveniência válida
```

## Matriz live planejada

- 3 cenários de infraestrutura;
- 10 repetições por cenário;
- 30 execuções;
- um controle saudável antes e depois de cada bloco;
- runtime v2 fixo;
- execução sequencial, nunca paralela.

## Critérios de interrupção

Interromper o bloco quando:

- a restauração da dependência falhar;
- houver mais de um RCAResult para o mesmo trace;
- houver mais de um incidente para o mesmo trace;
- a proveniência divergir;
- o serviço não recuperar health dentro da janela definida.

## Limitações

A fundação offline usa um simulador determinístico de ciclo de vida. Ela valida
contratos e invariantes, mas não substitui a execução contra o TraceBuilder,
KafkaConsumer, OpenSearchTraceRepository e IncidentService reais. Essa
integração pertence à Fase B.
