# UnifiedEvent v1

`UnifiedEvent` é o envelope normalizado, imutável e multimodal do CausalODS. Ele foi adicionado em `correlation-engine/multimodal_v1/` e não altera o `Span`, schemas, modelo ou pipeline trace-only v2.

## Versão e modalidades

- Schema lógico: `unified-event-v1`.
- JSON Schema: `correlation-engine/multimodal_v1/schemas/unified-event-v1.schema.json` (Draft 2020-12).
- Schema de relações: `correlation-engine/multimodal_v1/schemas/derived-evidence-v1.schema.json` (Draft 2020-12).
- `signal_type`: `trace`, `log`, `metric` ou `business_event`.
- Relações inferidas não pertencem ao evento observado. Elas usam o contrato separado `DerivedEvidence` (`derived-evidence-v1`). Por isso `causal_parent_event_ids` não existe no `UnifiedEvent`.

## Grupos de campos

| Grupo | Campos |
|---|---|
| Identidade | `event_id`, `event_name`, `event_type`, `signal_type` |
| Tempo | `event_timestamp`, `observed_timestamp`, `ingested_timestamp` |
| Tracing | `trace_id`, `span_id`, `parent_span_id`, `trace_flags`, `trace_state`, `span_links` |
| Negócio | `correlation_id`, `business_entity_type`, `business_entity_id`, `business_operation`, `business_outcome` |
| Recurso | `service_name`, `service_instance_id`, `service_version`, `deployment_environment` |
| Estado | `severity`, `error_type`, `status_code` |
| Métrica | `metric_name`, `metric_unit`, `metric_value`, `metric_aggregation` |
| Metadados | `attributes`, `resource_attributes`, `instrumentation_scope`, `schema_version`, `source_reference`, `source_dataset` |
| Qualidade | `missing_fields`, `late_arrival_ms`, `data_quality_flags` |

Identidade, modalidade, timestamp do evento e versão são obrigatórios. Os demais campos são opcionais. Para `trace`, ausências de `trace_id`/`span_id` são registradas em `missing_fields`; para `metric`, o mesmo ocorre com `metric_name`/`metric_value`; para `business_event`, a ausência de operação ou do par tipo/ID de entidade também é registrada. Eventos incompletos permanecem ingeríveis e recebem `missing_modality_fields`, permitindo datasets públicos legados. Os JSON Schemas não usam `required` condicionais para esses campos modais.

## Schema de entrada e representação canônica

O JSON Schema descreve a **entrada aceita**. Timestamps ISO 8601 timezone-aware podem usar `Z` ou qualquer offset válido, como `2026-08-03T09:00:00-03:00`. O contrato Python converte essa entrada para UTC. `to_dict()` e `to_json()` produzem a **representação canônica**, sempre com sufixo `Z`.

Assim, aceitar offsets na borda não muda a invariável interna: todo timestamp armazenado no objeto é UTC. Timestamp ISO 8601 sem timezone e `datetime` ingênuo são rejeitados pelo Python.

## Validação

- Timestamps precisam ter timezone; qualquer offset ISO 8601 válido é aceito na entrada, normalizado para UTC e serializado com sufixo `Z`. Timestamps ingênuos são rejeitados.
- IDs não podem ser vazios, conter whitespace ou caracteres de controle. Trace/span IDs fora do tamanho hexadecimal OTLP são aceitos e marcados com `legacy_<campo>_format`.
- Valores métricos e atrasos precisam ser finitos; atraso não pode ser negativo.
- `trace_flags` aceita inteiros de 0 a 255.
- Metadados devem ser JSON-safe e são congelados recursivamente. Todas as chaves, inclusive as aninhadas em `attributes`, `resource_attributes`, `instrumentation_scope`, `span_links` e `DerivedEvidence.attributes`, precisam ser strings. Chaves não textuais são rejeitadas, nunca convertidas, evitando colisões como `1` e `"1"`.
- `status_code` aceita somente string, inteiro não booleano ou `null`.
- Campos desconhecidos e versões diferentes de `unified-event-v1` são rejeitados por `from_dict`.

## Serialização

```python
from datetime import datetime, timezone
from multimodal_v1.contracts import UnifiedEvent

event = UnifiedEvent(
    event_id="metric:payment:cpu:1",
    event_name="cpu.utilization",
    event_type="gauge",
    signal_type="metric",
    event_timestamp=datetime.now(timezone.utc),
    metric_name="cpu.utilization",
    metric_unit="1",
    metric_value=0.72,
    service_name="payment-service",
)

payload = event.to_dict()
canonical_json = event.to_json()
restored = UnifiedEvent.from_dict(payload)
```

`to_json()` usa chaves ordenadas, separadores canônicos, UTF-8 lógico e proíbe NaN/infinito, produzindo JSON determinístico.

## Compatibilidade com spans v2

`SpanToUnifiedEventAdapter` converte `models.span.Span` sem modificar esse modelo. O adaptador preserva trace/span/parent IDs, serviço, operação, status, tempos e duração; acrescenta `source_reference=causalods-span-v2` e calcula `late_arrival_ms`. Ele é opt-in e não foi conectado ao `TraceConsumer` ou `RCAPipeline` v2.

## DerivedEvidence

`DerivedEvidence` representa uma relação inferida com `evidence_id`, `relation_type`, `source_event_ids`, `target_event_id`, `inferred_at`, `source_component`, confiança e atributos. `source_event_ids` é não vazio e não admite duplicatas; confiança aceita `null` ou valores entre 0 e 1. Seu JSON Schema usa `additionalProperties: false` e versão constante `derived-evidence-v1`.

Separar observações de inferências preserva proveniência e impede que uma aresta causal calculada seja apresentada como telemetria originalmente observada. `UnifiedEvent` não possui `causal_parent_event_ids` nem qualquer outro campo de causalidade inferida.

## Validação de JSON Schema

Os testes de contrato utilizam `jsonschema.Draft202012Validator`
com `FormatChecker`. A dependência é de desenvolvimento/teste e não faz
parte do runtime de produção da pipeline v2.

Os testes validam efetivamente os schemas `unified-event-v1` e
`derived-evidence-v1`, incluindo formatos RFC 3339, campos obrigatórios,
restrição de propriedades adicionais e limites numéricos.