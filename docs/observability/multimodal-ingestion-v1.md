# Ingestão multimodal v1 — extensão v3 opt-in

## Isolamento da v2

O `TraceConsumer` e o tópico `traces` atuais permanecem inalterados. Nenhum
módulo de `consumer`, `normalizer`, `models`, `pipeline`, `ranking` ou
`persistence` v2 importa a ingestão multimodal.

A implementação nova está em `correlation-engine/multimodal_v1/ingestion/`.
Ela termina em `UnifiedEvent v1`; não chama XGBoost, ranking, RCA ou índices
OpenSearch v2.

## Infraestrutura encontrada e estratégia de tópicos

O Collector padrão possui apenas a pipeline de traces e publica OTLP Protobuf
no tópico `traces`. Para preservar esse fluxo, a ingestão v3 usa outro consumer
group e tópicos separados:

| Tópico padrão v3 | Formato de entrada | `signal_type` de saída |
|---|---|---|
| `traces-v3` | `ExportTraceServiceRequest` OTLP Protobuf | `trace` |
| `logs-v1` | `ExportLogsServiceRequest` OTLP Protobuf | `log` |
| `metrics-v1` | `ExportMetricsServiceRequest` OTLP Protobuf | `metric` |
| `business-events-v1` | JSON `business-event-v1` | `business_event` |
| `multimodal-dead-letter-v1` | JSON de dead letter | — |

Os nomes são configuráveis por `MULTIMODAL_TRACE_TOPIC`,
`MULTIMODAL_LOG_TOPIC`, `MULTIMODAL_METRIC_TOPIC`, `BUSINESS_EVENT_TOPIC` e
`MULTIMODAL_DEAD_LETTER_TOPIC`. Para shadow ingest dos traces existentes, a v3
pode usar `MULTIMODAL_TRACE_TOPIC=traces`; o group padrão independente é
`causalods-multimodal-v3`.

O arquivo `collector/otel-collector-config.multimodal-v1.yaml` é uma
configuração opt-in para os três sinais OTLP. Ele não substitui o arquivo
padrão nem é montado pelo Compose atual.

## Fluxo

```text
Kafka record
  → seleção por tópico/signal_type
  → decode do formato de origem
  → normalizador específico
  → UnifiedEvent v1
  → validação Draft 2020-12
  → preparação imutável (PreparedBatch)
  → entrega atômica do lote ao sink idempotente
  → commit Kafka explícito de partition/offset+1
  → confirmação de dedupe e watermarks
```

O sink recebe uma tupla imutável, deve ser idempotente pela identidade
determinística dos eventos e levantar exceção em falha. Dedupe e watermarks
não mudam durante preparação ou entrega. Se o sink ou commit falhar, o lote
inteiro é reprocessado. Se o sink tiver sucesso e o commit falhar, o retry pode
reenviar o lote; a idempotência do sink torna essa repetição segura.

Somente erros esperados de decode, schema ou semântica de entrada geram
`DeadLetterRecord`. Erros internos incrementam `internal_error`, propagam e
impedem commit. A DLQ registra hash SHA-256, tamanho e metadados Kafka, mas não
inclui o payload por padrão. Inclusão é opt-in, limitada e sinaliza truncamento.

## Normalizadores

- `TraceEventNormalizer` chama o `SpanNormalizer` v2 e adapta o resultado sem
  modificar o contrato `Span`.
- `LogEventNormalizer` aceita logs com ou sem `trace_id`/`span_id`.
- `MetricEventNormalizer` suporta gauge, sum, histogram,
  exponential-histogram e summary, preservando temporality, monotonicidade,
  buckets, limites, min/max, count, sum e quantis conforme a modalidade.
  Exemplares completos ficam em `attributes.exemplars` e recebem
  `metric_exemplar_descriptor`; eles não são descritos como OpenTelemetry Link.
- `BusinessEventNormalizer` valida JSON com
  `business-event-v1.schema.json`.

Normalizadores conhecem somente formatos de entrada e `UnifiedEvent`; não
dependem de modelo, features ou ranking.

## Business event v1

Eventos aceitos:

- `order.created`
- `payment.requested`
- `payment.approved`
- `payment.failed`
- `order.approved`
- `order.rejected`

Exemplo:

```json
{
  "event_id": "evt-order-42-created",
  "event_name": "order.created",
  "event_timestamp": "2026-08-04T15:00:00Z",
  "correlation_id": "saga-order-42",
  "business_entity_type": "order",
  "business_entity_id": "order-42",
  "service_name": "order-service",
  "business_operation": "create_order",
  "business_outcome": "created",
  "attributes": {"channel": "web"},
  "schema_version": "business-event-v1"
}
```

Schema desconhecido, timestamp sem timezone, campo obrigatório ausente ou ID
fora dos limites do contrato segue para dead letter sem afetar o consumer v2.

## Qualidade, atraso e deduplicação

A chave de deduplicação é `(event_id, source_reference)`. Assim, uma repetição
da mesma origem é descartada, enquanto IDs iguais de datasets/origens distintos
continuam ingestíveis. O cache é LRU e tem capacidade configurável no construtor.

O `source_reference` de saída vem exclusivamente do contexto Kafka confiável.
Valores declarados pelo produtor são preservados apenas como
`attributes.reported_source_reference`/`reported_source_dataset`.

Watermarks usam streams canônicas independentes:

- trace: origem + `trace_id`;
- log: origem + serviço + instrumentation scope;
- métrica: origem + serviço + nome + atributos da série;
- business event: origem + correlation ID ou entidade.

Eventos anteriores ao watermark confirmado recebem `out_of_order`. Atraso
acima de `late_threshold_ms` recebe `late_arrival`; timestamps além de
`future_tolerance_ms` recebem `clock_skew_future_event`. Apenas eventos
entregues e com offset confirmado avançam watermarks.

Os contadores internos, separados por sinal, são:

- `received`: mensagens Kafka recebidas;
- `normalized`: eventos aceitos após deduplicação;
- `rejected`: mensagens enviadas à DLQ;
- `late`: eventos aceitos marcados como atrasados;
- `duplicates`: eventos descartados por duplicação.
- `internal_error`: falhas inesperadas que impedem commit;
- `delivered`: eventos entregues com sucesso;
- `committed`: mensagens com offset confirmado.

## Dependência da extensão

`jsonschema==4.26.0` é dependência de runtime declarada isoladamente em:

```bash
pip install -r correlation-engine/requirements-multimodal-v3.txt
```

## Execução opt-in

O consumer exige um sink v3 explícito:

```python
from multimodal_v1.ingestion.kafka_consumer import MultimodalKafkaConsumer

class JsonSink:
    def deliver(self, events):
        for event in events:
            print(event.to_json())

consumer = MultimodalKafkaConsumer(sink=JsonSink())
consumer.consume()
```

Esse exemplo apenas imprime eventos. Não há conexão automática com RCA,
OpenSearch ou pipeline v2.

O consumer possui `close()` e context manager; o encerramento fecha consumer,
faz `producer.flush()` e fecha o producer.

## Validação real do Collector

A configuração opt-in foi validada com a mesma imagem do projeto:

```bash
docker run --rm \
  -v "$PWD/collector/otel-collector-config.multimodal-v1.yaml:/etc/otelcol/config.yaml:ro" \
  otel/opentelemetry-collector-contrib:0.130.0 \
  validate --config=/etc/otelcol/config.yaml
```

Resultado: exit code 0. A imagem aceitou `encoding: otlp_proto` nos exporters
Kafka das pipelines de traces, logs e métricas. A configuração permanece
opt-in e não substitui o Collector v2.

## Testes

```bash
cd correlation-engine
.venv/bin/python -m pytest tests/test_multimodal_ingestion_v1.py -q
.venv/bin/python -m pytest tests -q

cd ..
correlation-engine/.venv/bin/python -m pytest experiments/tests -q
```
