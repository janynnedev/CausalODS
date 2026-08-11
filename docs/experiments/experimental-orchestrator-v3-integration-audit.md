# Auditoria de integração do orquestrador experimental multimodal v3

Data: 2026-08-11. Baseline anterior às alterações: engine `705 passed, 4 skipped,
4 warnings`; experiments `22 passed`; `V2_FREEZE_HASHES_OK=12/12`.

## APIs reutilizadas

| Componente | Caminho | API pública e versão | Função na campanha |
|---|---|---|---|
| UnifiedEvent/DerivedEvidence | `correlation-engine/multimodal_v1/contracts.py` | `UnifiedEvent` (`unified-event-v1`), `DerivedEvidence` | Envelope canônico; nunca recebe label |
| Incident evidence | `multimodal_v1/evidence/assembler.py` | `IncidentEvidenceAssembler`; `IncidentEvidenceBundle` | Unidade multimodal por incidente |
| Event graph | `multimodal_v1/event_graph/` | `EventGraphBuilder.build`, `EventEvidenceGraph.graph_fingerprint` | Grafo e proveniência de regras |
| Candidate pruning | `multimodal_v1/event_graph/pruning.py` | `CandidatePruner.prune`; `candidate-pruning-v1` | H1 e métricas de pruning |
| Feature registry/views | `multimodal_v1/features_v3/registry.py` | `FeatureRegistryV3.select`, `FeatureViewV3`; `rca-features-v3` | Full view e ablações |
| Feature dataset/target | `multimodal_v1/features_v3/dataset.py` | `RCAFeatureDatasetV3`, `RCAServiceTargetV3` | Splits e labels somente para avaliação |
| Benchmark | `multimodal_v1/benchmark_v3/` | `train_and_evaluate`, `compare_predictions`; `rca-model-benchmark-v3` | H1/H2, sete métodos e métricas |
| Degradação | `multimodal_v1/degradation_v3/` | `ObservationDegradationHarness.apply`, `degradation_metrics`; `observation-degradation-harness-v3` | H4 pareado 0/10/30/50 |
| RCAEval | `multimodal_v1/public_datasets_v3/rcaeval.py` | `RCAEvalAdapter`; `rcaeval-adapter-v3` | Capabilities RE1/RE2/RE3 |
| AIOps2020 | `multimodal_v1/public_datasets_v3/aiops2020.py` | `AIOps2020Adapter`; `aiops2020-adapter-v2` | Métricas e call-chain |
| Protocolos | `multimodal_v1/public_datasets_v3/protocols.py` | `ProtocolPlanner`; `public-dataset-protocol-v2` | within/cross-system/cross-source/LOSO |
| RCA evidence | `correlation-engine/ranking/evidence_builder.py` | `EvidenceBuilder.build`; `evidence-builder-v1` | Evidência; não decide causa |
| Prompt/explanation | `correlation-engine/explanations_v2/` | `PromptBuilder`, `ExplanationService`, `ExplanationValidator` | H3 com decisão RCA imutável |
| Estatística | `experiments/analysis_utils.py` | `mcnemar_exact`, `paired_bootstrap_difference` | Comparações pareadas |

`CandidatePruner.audit_pruning_with_ground_truth` permanece restrito a
`evaluation_mode=True`; o label nunca realimenta produção. O benchmark ajusta
preprocessing/modelos somente no train e seleciona hiperparâmetros no validation.

## Incompatibilidade e adaptação mínima

Os três modos já existiam, mas o `PromptBuilder` produzia o mesmo contexto em
todos. A menor adaptação foi feita no builder existente: contexto mínimo no
generic, seleção raw limitada/determinística/sanitizada no raw, e EvidenceObject
no grounded. O objeto completo permanece apenas na provenance interna e não é
renderizado no prompt generic/raw. O parser limita citações aos IDs permitidos.

A primeira versão da integração também apenas registrava o nome do protocolo.
Isso foi corrigido sem criar lógica concorrente: `ExperimentPlannerV3` chama
diretamente `ProtocolPlanner.manifest`, persiste seus folds e deriva validation
somente do train. Um fold pequeno demais termina como
`SKIPPED_PROTOCOL_NOT_APPLICABLE`. `DatasetCapabilityResolverV3` chama
`list_incidents`/`load_incident` dos adapters e deriva as modalidades do
`IncidentManifest`; a configuração serializada da CLI é um snapshot dessas
capabilities, não uma inferência por nome do dataset.

Não foi necessário duplicar UnifiedEvent, grafo, features, modelos, harness,
adapters ou validators. Campanhas reais injetam adapters, datasets e RCAResults no
`ExperimentOrchestratorV3`; o smoke usa exclusivamente fixtures e Stub LLM.

## Freeze e isolamento

Os 12 hashes v2 estavam válidos antes da implementação. O novo namespace é
`experiments/multimodal_v3`; não há import v3 nos módulos congelados do runtime
v2. Modelos v1/v2 não são destino de escrita e artefatos do benchmark permanecem
independentes como v3.

O baseline executado antes da edição foi: engine `705 passed, 4 skipped`,
experiments `22 passed`, freeze `12/12`. Nenhum dataset público foi copiado e
nenhum provider externo foi chamado.
