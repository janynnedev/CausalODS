# Estratégia de identidade e propagação de contexto (v3 opt-in)

Esta extensão não integra o runtime trace-only v2. O `docker-compose.yml`
padrão continua sem habilitar a instrumentação v3. Para habilitá-la
explicitamente no smoke HTTP:

```bash
docker compose -f docker-compose.yml -f docker-compose.identity-v3.yml \
  up -d --build order-service payment-service
```

## Identidades

O CausalODS mantém três identidades distintas:

| Identidade | Semântica | Transporte |
|---|---|---|
| `trace_id` | Identidade técnica principal de uma execução distribuída | W3C `traceparent`, gerenciado pelo OpenTelemetry |
| `business.correlation.id` | Workflow, pedido, pagamento ou saga; pode reunir vários traces | baggage allowlisted e `X-Correlation-Id`/`business-correlation-id` para interoperabilidade |
| `business.entity.id` | Entidade de domínio, acompanhada de `business.entity.type` | atributos de span e contratos normalizados |

`correlationId` nunca substitui `trace_id`. Um workflow pode possuir vários traces; cada trace continua tecnicamente independente.

## Validação e segurança

IDs distribuídos aceitam de 1 a 128 caracteres no conjunto `[A-Za-z0-9._:-]`, começando por alfanumérico. Valores ausentes ou inválidos geram um UUID no início da requisição HTTP. No engine, valor inválido não interrompe ingestão: ele vira `None` e registra `invalid_business_correlation_id`.

Não são propagados headers de autorização, cookies, tokens, e-mail, telefone, CPF/SSN, senhas ou secrets. Marcadores sensíveis também invalidam um correlation ID. O ID deve ser opaco; dados de cliente, produto e pagamento não pertencem ao contexto distribuído.

## HTTP order → payment

1. O filtro do order-service aceita `X-Correlation-Id` válido ou gera UUID.
2. O filtro coloca apenas `business.correlation.id` no baggage, MDC e span atual.
3. O interceptor v3 propaga o `X-Correlation-Id` canônico ao payment-service.
5. A auto-instrumentação OpenTelemetry preserva/injeta `traceparent`, `tracestate` e baggage. O código da aplicação não sobrescreve esses headers.
6. O payment-service valida o ID e reconstrói um baggage novo contendo somente `business.correlation.id`.
7. O encoder Logback inclui MDC, portanto os logs estruturados v3 contêm `correlationId` e `business.correlation.id` durante a requisição.

Baggage não é convertido automaticamente em atributos. A cópia para `Span.current().setAttribute()` é explícita e limitada aos IDs não sensíveis definidos acima.

## Kafka

O inventário atual não encontrou produtor/consumidor Kafka nos microsserviços. O único produtor é o OpenTelemetry Collector, que publica um `ExportTraceServiceRequest` OTLP/Protobuf no tópico `traces`; o `TraceConsumer` consome esse envelope. Não é correto adicionar headers por span nesse envelope opaco.

Como helper futuro e desconectado, `multimodal_v1.kafka_propagation.KafkaContextPropagator` fornece:

- `inject()`/`extract()` para `traceparent`, `tracestate`, baggage controlado e `business-correlation-id` explícito;
- allowlist de baggage contendo apenas `business.correlation.id`;
- descarte de headers desconhecidos ou sensíveis;
- `build_span_links()` para fan-out, fan-in e batching, quando parentesco direto seria semanticamente incorreto.

Na integração real, `traceparent` e `tracestate` deverão ser processados pelos
propagators oficiais OpenTelemetry. Os dicionários retornados por
`build_span_links()` são descritores testáveis, ainda não objetos
OpenTelemetry `Link`. Para múltiplas mensagens causais, converta-os em links;
não escolha arbitrariamente um único parent.

## Correlation Engine

O runtime v2 não importa nem executa estes componentes. O
`SpanToUnifiedEventAdapter` v3 procura, quando disponíveis, somente:

- `business.correlation.id` → `UnifiedEvent.correlation_id`;
- `business.entity.type`;
- `business.entity.id`.

O adaptador aceita atributos armazenados por uma origem enriquecida ou
fornecidos explicitamente. Em colisões, atributos adicionais vencem os
armazenados, mas `duration_ns` e `end_time_unix_nano` calculados são reservados.

`identity_context.py` agrega IDs em coleção ordenada e imutável. Com zero IDs,
o canônico é `None`; com um, é o valor único; com mais de um, é `None` e a flag
`conflicting_business_correlation_ids` é registrada. Nenhum primeiro valor é
selecionado por ordem de chegada.

## Persistência reservada para v3

Sem criar integração, mappings, aliases ou índices, ficam reservados:

- `causalods-events-v1`
- `causalods-event-graphs-v1`
- `causalods-rca-results-v3`
- `causalods-incidents-v3`

Os índices v2 `causalods-traces`, `causalods-incidents` e
`causalods-rca-results` permanecem inalterados.

## Comandos curl

Sem correlation ID — o order-service gera e devolve um:

```bash
curl -i -X POST http://localhost:8081/orders \
  -H 'Content-Type: application/json' \
  -d '{"product":"Notebook","quantity":2}'
```

Correlation ID recebido e preservado:

```bash
curl -i -X POST http://localhost:8081/orders \
  -H 'Content-Type: application/json' \
  -H 'X-Correlation-Id: saga-order-2026-0001' \
  -d '{"product":"Notebook","quantity":2}'
```

Valor excessivo — deve ser substituído por um UUID, sem retornar o valor original:

```bash
OVERSIZED_ID=$(printf 'x%.0s' $(seq 1 129))
curl -i -X POST http://localhost:8081/orders \
  -H 'Content-Type: application/json' \
  -H "X-Correlation-Id: ${OVERSIZED_ID}" \
  -d '{"product":"Notebook","quantity":2}'
```

## Smoke HTTP de propagação

```bash
docker compose -f docker-compose.yml -f docker-compose.identity-v3.yml \
  up -d --build order-service payment-service

WORKFLOW_ID=saga-e2e-001
curl -fsS -D /tmp/causalods-e2e-headers.txt \
  -X POST http://localhost:8081/orders \
  -H 'Content-Type: application/json' \
  -H "X-Correlation-Id: ${WORKFLOW_ID}" \
  -d '{"product":"Notebook","quantity":2}'

grep -i "x-correlation-id: ${WORKFLOW_ID}" /tmp/causalods-e2e-headers.txt
docker compose logs order-service payment-service \
  | grep "${WORKFLOW_ID}"
```

Este teste confirma apenas a propagação HTTP; não é um E2E completo do pipeline
RCA. No Jaeger (`http://localhost:16686`), selecione o trace do order-service e confirme:

- mesmo `trace_id` nos spans order/payment do fluxo síncrono;
- `business.correlation.id=saga-e2e-001`;
- `traceparent` não aparece como dado de negócio e não foi substituído pelo correlation ID.

Para demonstrar vários traces no mesmo workflow, execute duas requisições de entrada independentes com o mesmo `X-Correlation-Id`; elas devem produzir trace IDs diferentes e o mesmo `business.correlation.id`.

## Testes automatizados

```bash
cd correlation-engine
.venv/bin/python -m pytest tests/test_distributed_identity.py -q

cd ../services/order-service
mvn -Dtest=DistributedIdentityTest test

cd ../payment-service
mvn -Dtest=DistributedIdentityTest test
```

Os testes cobrem geração/aceitação, limite de tamanho, helper Kafka,
sanitização real de baggage, ausência de PII, descritores de links, adaptador e
conflitos independentes da ordem. Contratos RCA/Incident v2 não são alterados.
