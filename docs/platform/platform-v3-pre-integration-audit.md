# Auditoria pré-integração da plataforma v3

Data da auditoria: 2026-08-11
Branch: `fix/codex`
Commit inicial: `5a69ab1bce71e1d090d8d842e4ed7ded119245af`
Worktree inicial: limpo e sincronizado com `origin/fix/codex`

## Linha de base

| Verificação | Resultado |
|---|---|
| Engine Python, CWD `correlation-engine/` | 709 passed, 4 skipped, 0 failed, 4 warnings |
| Experimentos Python, raiz do repositório | 49 passed, 0 skipped, 0 failed |
| Freeze v2 | 12/12 hashes válidos após restaurar o artefato que a própria suíte regrava |
| Runtime v2 importando `multimodal_v1` | 0 imports fora de testes e do próprio pacote v3 |
| Docker Compose | configuração resolvida; o acesso exigiu autorização fora do sandbox |

A suíte do engine deve ser iniciada dentro de `correlation-engine/`: seis testes v2
usam caminhos relativos. Ela também regrava `training/models/xgboost_rca.pkl` em um
teste de treinamento. O gate deve executar engine e experimentos sequencialmente e
restaurar/verificar o artefato congelado antes de validar os hashes.

## Prompts 1–11 e componentes encontrados

Os marcos estão presentes no histórico por commits/tags, de
`milestone/multimodal-v3-identity` até
`milestone/test_experimental_orchestrator_v3`. Os contratos e APIs reais são:

| Componente | Caminho/API principal |
|---|---|
| UnifiedEvent v1 / DerivedEvidence | `correlation-engine/multimodal_v1/contracts.py`; `UnifiedEvent`, `DerivedEvidence` |
| Distributed identity | `multimodal_v1/identity.py`, `identity_context.py`, `kafka_propagation.py` |
| Ingestão multimodal | `multimodal_v1/ingestion/`; `MultimodalIngestionService` |
| IncidentEvidenceAssembler | `multimodal_v1/evidence/assembler.py`; `IncidentEvidenceAssembler` |
| EventEvidenceGraph | `multimodal_v1/event_graph/`; `EventGraphBuilder`, `EventEvidenceGraph` |
| CandidatePruner | `multimodal_v1/event_graph/pruning.py`; `CandidatePruner` |
| Feature registry/dataset v3 | `multimodal_v1/features_v3/`; `FeatureRegistryV3`, `RCAFeatureDatasetV3` |
| Benchmark/model v3 | `multimodal_v1/benchmark_v3/`; benchmark `rca-model-benchmark-v3`, modelo lógico `xgboost-rca-v3` |
| Degradação | `multimodal_v1/degradation_v3/`; `ObservationDegradationHarness` |
| Datasets públicos | `multimodal_v1/public_datasets_v3/`; `RCAEvalAdapter`, `AIOps2020Adapter` |
| RCA/Evidence | `models/rca_result.py`, `ranking/evidence_builder.py`; `RCAResult`, `EvidenceBuilder` |
| Explicação | `explanations_v2/`; `PromptBuilder`, `ExplanationValidator`, `ExplanationService` |
| Orquestrador experimental | `experiments/multimodal_v3/`; `CampaignManifestV3`, `ExperimentOrchestratorV3` |
| Resultados v1/v2 | `results/` e `docs/realignment/v2-freeze-manifest.md` |
| Persistência OpenSearch | `correlation-engine/persistence/opensearch/` |

## Wiring operacional do Prompt 12

A auditoria de integração confirmou e reutilizou as APIs, sem redefinir contratos:

| Etapa | API usada pelo runtime |
|---|---|
| Kafka/normalização | `MultimodalKafkaConsumer.process_message` e `MultimodalIngestionService.prepare/deliver/confirm` |
| Lifecycle | `IncidentEvidenceAssembler.ingest/active_bundles` com identidade correlation/trace e idle determinístico |
| Grafo/pruning | `EventGraphBuilder.build` e `CandidatePruner.prune`, sem ground truth |
| Features | `RCAFeatureExtractorV3.extract` e full view de `FeatureRegistryV3` (40 colunas) |
| Treino/load | `benchmark_v3.train_and_evaluate`, `load_verified_artifact`, pipeline `joblib` e `positive_scores` |
| Ranking/RCA | `EvidenceBuilder.build`, `RCARankingService.rank_context`, `ReliabilityGate.evaluate`, `RCAResult.create` |
| Explicação | `explanations_v2.ExplanationService`; fallback determinístico preserva root cause/confiança/citações |
| Persistência | `VisualizationDocumentAdapter` e `OpenSearchProjectionRepository` |

O artefato em `artifacts/platform-v3/models/` declara
`purpose=runtime_e2e_validation`. Ele usa o manifesto e a cadeia de integridade do
benchmark real, mas não constitui resultado científico H1/H2 nem usa o conjunto de
teste final. O runtime novo existe somente em `platform_v3` e no overlay; a auditoria
do runtime v2 continua com zero imports para `multimodal_v1`.

## OpenSearch existente

Versão declarada no Compose: `opensearchproject/opensearch:2.19.0`.

| Índice | Mapping | Consumidor |
|---|---|---|
| `causalods-incidents` | parcialmente dinâmico; `evidence` como `object` | repositório legado de incidentes |
| `causalods-rca-results` | `dynamic: false`; payload não indexado | `OpenSearchRCAResultRepository` |

Não há aliases, política de retenção nem index templates versionados no código atual.
O índice legado de incidentes está sujeito a explosão de campos se evidências
arbitrárias forem inseridas. A v3 deve usar índices novos, mapping explícito e JSON
canônico não indexado para atributos livres.

## Infraestrutura e visualização existentes

| Componente | Estado inicial |
|---|---|
| Kafka 7.8.0 | presente no Compose |
| OpenSearch 2.19.0 | presente no Compose |
| OpenTelemetry Collector 0.130.0 | presente; pipeline somente de traces |
| Jaeger 1.66.0 | presente e recebe traces |
| Prometheus 3.5.0 | presente; scrape de order/payment |
| Micrometer | endpoints Prometheus configurados nos serviços |
| Grafana | ausente do Compose e sem provisioning versionado |
| Tempo | ausente |
| Loki | ausente da implementação atual |
| logs/métricas OTLP | explicitamente desabilitados nos serviços Java |

## Riscos de integração

1. Não confundir o benchmark científico v3 com um runtime online v3 já integrado.
2. Não afirmar multimodalidade real no E2E Docker enquanto logs, métricas e business
   events não forem emitidos pela aplicação; fixtures devem ser marcadas por estágio.
3. Não alterar o collector/Compose congelado: qualquer Tempo/Grafana deve ser overlay.
4. Não indexar `attributes` ou payloads de datasets com dynamic mapping.
5. Não misturar ground truth aos documentos operacionais.
6. Não executar validações experimentais em paralelo com testes que regravem modelos.
7. Não declarar `READY_FOR_FINAL_EXPERIMENTS` sem fault E2E real, modelo operacional v3,
   persistência e queryabilidade efetivamente verificadas.
