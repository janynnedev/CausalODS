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

O serviço `platform-v3-runtime`, exclusivo do overlay, consome os tópicos v3,
normaliza eventos, mantém lifecycle por identidade, processa bundles distribuídos
com falha, executa o modelo operacional verificado e persiste as quatro projeções
operacionais. Seu health só declara `ready` quando consumer, modelo e OpenSearch
estão efetivamente disponíveis. O artefato operacional não substitui modelos das
campanhas científicas.
