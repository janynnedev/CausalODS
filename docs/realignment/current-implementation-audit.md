# Auditoria da implementação atual antes do pipeline multimodal v3

Data da auditoria: 2026-08-03  
Commit auditado: `fa7835f25bdeda403aedc55019181c8317cf4048` (`fix/codex`)  
Escopo: implementação ativa e artefatos versionados; `.causalods-backup/`, ambientes virtuais, `target/`, `build/` e pacotes extraídos em `artifacts/imported-packages/` foram distinguidos do código ativo.

## Conclusão executiva

A implementação de produção continua **trace-only**. O fluxo ativo é: OTLP traces em Protobuf → Kafka `traces` → `TraceConsumer` → `OTLPDeserializer` → `SpanNormalizer` → `TraceBuilder`/`TraceLifecycleManager` → `RCAPipeline` → grafo, features, predição v1/v2, SHAP, ranking, `ReliabilityGate` → OpenSearch. A v2 está selecionada no Compose por `RCA_MODEL_VERSION=v2`; o default da classe `Settings`, isoladamente, ainda é `v1`.

Há preparação periférica para logs, métricas, eventos e `correlationId`, mas nenhum desses sinais integra o contrato de entrada do RCA atual. Não foi implementado pipeline v3 nesta auditoria e nenhum arquivo v1/v2 foi removido, renomeado ou alterado.

## Inventário real

### Ingestão Kafka e OTLP

- Consumidor ativo único: `correlation-engine/consumer/kafka_consumer.py`, classe `TraceConsumer`, `kafka-python.KafkaConsumer`, grupo `correlation-engine`, tópico configurável e default `traces`.
- O consumer usa `poll(timeout_ms=1000)`, `auto_offset_reset="latest"`, filtra apenas `GET /actuator/prometheus` e `GET /actuator/health`, e fecha traces por timeout de 5 s.
- Desserializador: `correlation-engine/consumer/otlp_deserializer.py`; aceita bytes `ExportTraceServiceRequest` e chama `ParseFromString`.
- Inspetor auxiliar: `correlation-engine/consumer/otlp_inspector.py`.
- Collector: `collector/otel-collector-config.yaml`; recebe OTLP gRPC 4317 e HTTP 4318, mas possui somente pipeline `traces`; exporta Kafka com `encoding: otlp_proto` e também Jaeger.
- Formato efetivamente suportado pelo engine: **OTLP TraceService/Protobuf binário**. Não há desserializador OTLP JSON, logs (`ExportLogsServiceRequest`) ou métricas (`ExportMetricsServiceRequest`).

### Normalização e ciclo do trace

- Normalizador ativo: `correlation-engine/normalizer/span_normalizer.py` (`SpanNormalizer`). Extrai IDs, `service.name`, operação, tempos, duração e status para `models/span.py::Span`.
- `correlation-engine/normalizer/models.py::NormalizedSpan` e `normalizer/attribute_mapper..py` existem, mas não são usados pelo `TraceConsumer`.
- O normalizador ativo não preserva atributos, kind, eventos do span, links, trace state nem `correlationId`.
- Reconstrução: `correlation-engine/models/trace_builder.py`, `models/trace_state.py` (`SpanNode`, `TraceState`) e `correlation/trace_lifecycle.py`.
- Há implementações históricas em `core-old/`; não fazem parte do caminho ativo.

### Contratos de dados

- Telemetria: `models/span.py`, `models/trace.py`, `models/trace_state.py`.
- Grafo: `models/service_graph.py` (`ServiceNode`, `DependencyEdge`, `ServiceGraph`).
- Features: `models/feature_vector.py`, `models/rca_feature.py`, `models/rca_feature_schema.py`, `models/rca_feature_schema_v2.py`.
- RCA auditável: `models/evidence.py` (`EvidenceItem`, enums e serialização), `models/rca_context.py::RCAContext`, `models/rca_result.py` (`ScoreBreakdown`, `RankedService`, `ReliabilityDecision`, `RCAResult`). `RCAResult.schema_version` é `1.0`.
- Incidentes: `models/incident.py`, `incident/incident.py`, `incident/incident_builder.py` e `storage/document_mapper.py`.
- Explicações: `explanations/contracts.py`, `evidence_adapter.py` e `deterministic_generator.py`; versão do gerador `causalods-explanation-v1.1`.

### Grafo e correlação

- `graph/graph_builder.py` agrega por serviço e cria arestas pai-filho entre serviços, com contagem, latência e erro.
- `correlation/temporal_correlator.py` e `correlation/trace_lifecycle.py` executam correlação temporal/ciclo de vida.
- `features/graph_feature_extractor.py`, `temporal_feature_extractor.py`, `trace_feature_extractor.py`, `anomaly_feature_extractor.py` e `feature_aggregator.py` alimentam `pipeline/feature_pipeline.py`.
- O grafo é um **grafo de dependências derivado de spans**, não um grafo multimodal de eventos e nem prova causal por si só.

### Features, pipelines e modelos

- Schema v1 (`rca-features-v1`): 14 features — volume/erro/latência, graus/dependências, ordem/lead time de falha e anomalia.
- Schema v2 (`rca-features-v2`): preserva as 14 features v1 e acrescenta `exclusive_latency_ms` e `exclusive_latency_ratio`.
- Dataset v1: `dataset/{dataset_builder,feature_generator,label_generator,scenario_generator}.py` e `dataset/schema.py`.
- Dataset v2: variantes `_v2.py` dos quatro geradores; dataset congelado em `training/data/rca_dataset_v2.csv`.
- Predição: `pipeline/prediction_pipeline.py`/`training/predictor.py` (v1) e `pipeline/prediction_pipeline_v2.py`/`training/predictor_v2.py` (v2).
- SHAP: módulos comuns em `shap_analysis/` e `shap_service_v2.py` para v2.
- Orquestração compartilhada: `pipeline/rca_pipeline.py`, `rca_context_factory.py`, `rca_result_factory.py`, `result_adapters.py`; identifica `causalods-rca-v1` ou `causalods-rca-v2`.
- Modelos ativos/versionados: `training/models/xgboost_rca.pkl` (v1) e `xgboost_rca_v2.pkl` + manifesto (v2). `xgboost_rca_legacy_v0.pkl` é legado explícito.

### Persistência OpenSearch

- Cliente: `persistence/opensearch/client.py`.
- `causalods-traces`: `trace_repository.py` + `trace_mapper.py`; mapping estático de trace e spans.
- `causalods-incidents`: `incident_index_manager.py`, `repository.py`, `mapper.py`; existe também `index_manager.py`, uma definição anterior do mesmo índice com mapping diferente.
- `causalods-rca-results`: `rca_result_index_manager.py` + `rca_result_repository.py`; indexa metadados e armazena o payload completo como objeto desabilitado para indexação.
- Memória/abstrações: `storage/repository.py`, `memory_repository.py`, `incident_service.py`.
- Risco: os índices não possuem sufixo de versão. A v3 deve usar novos índices/aliases e nunca alterar mappings v2 in-place.

### Testes e experimentos

- 36 arquivos de testes do engine em `correlation-engine/tests/` cobrem contratos, datasets, features, grafo, temporal, treino/predição v1/v2, manifesto/validador de artefato, SHAP, ranking, persistência, explicações e robustez.
- 6 arquivos em `experiments/tests/` cobrem baselines, comparação E2E, sensibilidade e robustez live.
- Três smoke tests Java existem: um em cada microsserviço e um projeto Gradle legado em `services/src/test/`.
- Scripts ativos em `experiments/`: campanha/runner E2E, comparação, baselines, relatório/evidências, explicações, sensibilidade de latência e robustez offline/live. `build_integrated_evidence_bundle.py` consolida o pacote de evidências.
- Campanhas preservadas: `results/e2e/final/final-v1-v2-50`, sensibilidade em `results/e2e/sensitivity/`, explicações em `results/offline-explanations/`, robustez em `results/robustness/` e pacote integrado `results/integrated/final-paper-v1`.

## Lacunas obrigatórias para a multimodalidade

| Área | Evidência atual | Lacuna explícita |
|---|---|---|
| Logs | JSON logging/Loki e `BusinessLogger` nos serviços | Sem receiver/exporter Kafka de logs, consumer, normalizador, schema ou features de logs no RCA |
| Métricas | Prometheus e endpoints Actuator | Sem OTLP metrics/Kafka, normalizador ou alinhamento de janelas no RCA |
| Eventos de negócio | Logs com campo `event`; enum `BUSINESS_EVENT` no modelo de evidência | Não há contrato/ingestão/persistência de eventos de negócio como modalidade |
| `correlationId` | Filtros/interceptor HTTP nos serviços | Não é preservado pelo `SpanNormalizer`, não integra `RCAContext` e não correlaciona modalidades |
| Grafo de eventos | Grafo serviço-serviço derivado de spans | Faltam nós tipados, arestas temporais/correlacionais, proveniência e política de deduplicação multimodal |
| Datasets públicos | Apenas datasets sintéticos/próprios versionados | Não há importador, licença, mapeamento de schema ou benchmark público reproduzível |
| LLM | Explicador determinístico v1.1 e evidências estruturadas | Não há cliente LLM, prompt versionado, validação de grounding/alucinação ou avaliação específica de LLM |
| Visualização | Infraestrutura Grafana/Jaeger/Prometheus/Loki; OpenSearch | Não foram encontrados dashboards/provisioning ativos nem UI de incidente/evidência multimodal |

## Riscos técnicos encontrados

1. Testes v2 dependem do CWD `correlation-engine/`; executá-los desde a raiz gera 6 falsos negativos por caminhos relativos.
2. `Settings.MODEL_PATH_V2` e manifesto são relativos ao CWD; o Compose mascara esse risco no contêiner.
3. `SpanNormalizer` descarta atributos e eventos, bloqueando `correlationId` e evidências multimodais.
4. O consumer não explicita `enable_auto_commit`, idempotência, DLQ ou tratamento de Protobuf inválido; robustez existe sobretudo no plano experimental.
5. Há dois managers para `causalods-incidents` com mappings divergentes.
6. `core-old/`, contratos não usados e projetos Java duplicados aumentam ambiguidade; devem ser preservados no freeze e classificados, não apagados durante a v3.
7. O teste Spring depende de auto-attach Mockito/Byte Buddy, incompatível com este sandbox/JDK; o wrapper tenta escrever em `$HOME`.
8. Pastas `frontend/`, `grafana/` e `loki/` não contêm artefatos de visualização versionados relevantes.

## Árvore proposta para v3 (não implementada)

```text
correlation-engine/
├── consumer_v3/
│   ├── trace_consumer.py
│   ├── log_consumer.py
│   ├── metric_consumer.py
│   └── business_event_consumer.py
├── multimodal_v1/
│   ├── contracts.py
│   ├── envelope.py
│   ├── correlation_keys.py
│   ├── normalizers/{trace,log,metric,business_event}.py
│   └── validators.py
├── event_graph_v1/
│   ├── contracts.py
│   ├── builder.py
│   └── temporal_edges.py
├── features_v3/
│   ├── schema.py
│   ├── trace_features.py
│   ├── log_features.py
│   ├── metric_features.py
│   ├── business_event_features.py
│   └── fusion.py
├── pipeline_v3/
│   ├── context.py
│   ├── feature_pipeline.py
│   ├── prediction_pipeline.py
│   └── rca_pipeline.py
├── training_v3/
│   ├── dataset_builder.py
│   ├── preprocessing.py
│   ├── train.py
│   └── models/xgboost_rca_v3.{pkl,manifest.json}
├── persistence/opensearch_v3/
│   ├── event_repository.py
│   ├── event_graph_repository.py
│   └── rca_result_repository.py
└── tests_v3/
    ├── contract/
    ├── unit/
    ├── integration/
    └── regression_v2/
collector/otel-collector-config-v3.yaml
experiments/v3/
docs/realignment/
```

Nomes finais podem ser refinados, mas os namespaces, tópicos, schemas, modelos, índices e resultados da v3 devem ser novos. A v2 deve continuar selecionável e testável sem importar módulos v3.

## Plano incremental de migração

1. Congelar e verificar v2 pelos hashes do manifesto adjacente; criar teste de regressão que valide os artefatos sem regravá-los.
2. Definir `multimodal-schema-v1`: envelope imutável, modality, timestamp/event time, service, trace/span IDs, `correlationId`, source e provenance.
3. Criar configuração Collector v3 paralela e tópicos versionados; manter `traces` e o consumer atual intactos.
4. Adicionar normalizadores e contract tests por modalidade, inicialmente em shadow mode, sem persistir incidentes.
5. Construir event graph v1 em índice novo e comparar sua visão trace-only com o grafo v2.
6. Criar feature schema v3 como contrato novo; nunca acrescentar colunas ao schema v2.
7. Gerar dataset v3 com separação por campanha/cenário, incluir adaptadores documentados para datasets públicos e registrar licença/proveniência.
8. Treinar `xgboost-rca-v3` com manifesto/hashes e executar ablações trace-only versus modalidades adicionais.
9. Executar pipeline v3 em shadow/canary; comparar saídas com v2 antes de habilitar persistência em índices v3.
10. Adicionar LLM somente após estabilizar evidências v3, com resposta estruturada, referências de evidência e avaliação de fidelidade; depois provisionar visualização.

## Resultado dos testes nesta auditoria

- Engine, no CWD correto: `115 passed, 1 skipped, 3 warnings`.
- Experimentos: `22 passed`.
- Execução agregada desde a raiz: `131 passed, 1 skipped, 6 failed`; os 6 erros foram exclusivamente caminhos relativos v2, confirmados verdes ao repetir no CWD correto.
- Maven order/payment: ambos carregaram o contexto Spring, mas cada um terminou com 1 erro ambiental no auto-attach do Mockito/Byte Buddy. Não houve falha de asserção.
- Gradle legado: não executado; `gradle` não está instalado e o wrapper não pôde criar o cache em `$HOME` no sandbox.

Assim, as suítes Python anteriores permanecem verdes. Não é correto declarar a matriz inteira verde neste ambiente porque três smoke tests Java não puderam ser concluídos por restrições de execução documentadas.
