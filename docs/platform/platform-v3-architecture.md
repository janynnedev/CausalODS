# Arquitetura da plataforma CausalODS v3

## Fluxo alvo e fronteiras

```text
Order/Payment -> OTel -> Collector -> Kafka v3
  -> MultimodalIngestionService -> UnifiedEvent
  -> IncidentEvidenceAssembler -> EventEvidenceGraph
  -> FeatureRegistry/Extractor v3 -> xgboost-rca-v3
  -> Reliability Gate -> RCAResult -> EvidenceBuilder
  -> StructuredExplanation -> projeções OpenSearch -> Grafana
```

A v2 permanece em `traces`, `causalods-incidents` e
`causalods-rca-results`. A extensão v3 é opt-in, usa os tópicos `traces-v3`,
`logs-v1`, `metrics-v1`, `business-events-v1` e cinco índices com prefixo
`causalods-v3-`. O overlay `docker-compose.platform-v3.yml` não substitui o
Compose original.

O código em `platform_v3` é uma camada de adaptação. UnifiedEvent,
IncidentEvidenceBundle, EventEvidenceGraph, RCAResult, EvidenceObject e
StructuredExplanation continuam sendo as fontes contratuais. Grafana nunca é
fonte de verdade.

## Persistência e links

| Índice | Documento canônico projetado | Links principais |
|---|---|---|
| `causalods-v3-unified-events` | UnifiedEvent | event, trace, span, correlation, entidade, serviço |
| `causalods-v3-incidents` | IncidentEvidenceBundle + visão auditável do grafo | incident, traces, correlation IDs |
| `causalods-v3-rca-results` | RCAResult | incident, trace, ranking, evidências |
| `causalods-v3-explanations` | StructuredExplanation | incident, evidências citadas, hashes |
| `causalods-v3-experiment-runs` | projeção de ExperimentRun v3 | campaign, run, hipótese |

`trace_id` e `correlation_id` são campos separados. Um correlation ID pode
apontar para vários traces; nenhum dos dois é convertido no outro.

## Estado do runtime

Os contratos e o benchmark `xgboost-rca-v3` existem, mas não há artefato de
modelo v3 publicado para o runtime online nem sink que encadeie toda a ingestão
v3 até a persistência. O release gate marca essa lacuna como bloqueante em vez
de inferir que o benchmark já seja o runtime.
