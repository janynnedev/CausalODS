# Final campaign wiring v3

## 1. Objetivo

Esta camada prepara H1-A, H1-B, H1-C, H2, H3 e H4 sobre os datasets científicos congelados. Ela valida inputs, calcula planos capability-aware, fixa proveniência e injeta executores do `experimental-orchestrator-v3`. Este documento descreve readiness; nenhuma campanha final ou conclusão científica é produzida pelo Prompt 13C.

## 2. Frozen inputs

São somente leitura: Gates 1–12, trace-only v2, `rca-features-v3`, `benchmark-v3`, `degradation-v3`, Explanation Layer v2, Orchestrator v3, RCAEval materializado e `causalods-local-v3/final-500`. O registry referencia os roots e o hash dos respectivos `SHA256SUMS`; não copia datasets ou caches.

## 3. Hypothesis matrix

| Campanha | Fator controlado | Contrato reutilizado |
|---|---|---|
| H1-A | quatro métodos de RCA | benchmark-v3 |
| H1-B | `NO_GRAPH`, `GRAPH`, `GRAPH_BUSINESS` | FeatureView v3 |
| H1-C | cinco combinações de modalidade | FeatureView v3 |
| H2 | sete métodos | benchmark-v3 |
| H3 | três modos, mesmos RCAResult/evidence/reliability | Explanation Layer v2 |
| H4 | 14 operadores × quatro severidades | degradation-v3 |

## 4. Datasets

O arquivo `experiments/configs/final-v3/datasets.json` registra RCAEval (735/735) e causalods-local-v3 (500/500), com paths, contagens esperadas e hash do checksum manifest. RCAEval mantém suas modalidades reais; o local mantém as quatro modalidades observáveis.

## 5. Capability rules

Capabilities vêm exclusivamente de `capabilities.json`. Modalidade ausente gera `SKIP_NOT_APPLICABLE` no contrato final e o skip detalhado do orchestrator congelado. Ausência não é falha, não vira zero de acurácia e nunca é preenchida sinteticamente.

## 6. Methods

H1-A usa exatamente `temporal-baseline`, `max-zscore`, `first-anomalous` e `xgboost-rca-v3`. H2 acrescenta `logistic-regression`, `random-forest` e `mlp-light-v3`. Predições operacionais coletadas durante a materialização não são reaproveitadas; o executor delega ao benchmark científico e exige catálogo explícito de inputs derivados pelo pipeline frozen.

## 7. Feature views

H1-B usa exatamente `NO_GRAPH`, `GRAPH` e `GRAPH_BUSINESS`. H1-C usa `METRIC_ONLY`, `TRACE_ONLY`, `METRIC_TRACE`, `METRIC_TRACE_LOG` e `METRIC_TRACE_LOG_BUSINESS`. Os hashes são os da `FeatureRegistryV3`; `GRAPH_BUSINESS` requer `business_event` real.

## 8. Protocols

O wiring oferece `within-dataset`, `cross-system`, `cross-source` e `leave-one-system-out`, todos delegados ao `ProtocolPlanner` congelado. Train, validation e test são disjuntos, persistidos antes da execução e derivados por seed determinística. H3 usa `within-dataset` para preservar uma amostra total única de 40 incidentes.

## 9. H3 explanation modes

H3 usa exatamente `generic`, `raw_telemetry` e `evidence_grounded`. O grupo de três runs compartilha o mesmo RCAResult científico, ranking e Reliability Gate. O mapa privado liga blind ID, incidente e modo; o índice público contém apenas blind IDs. O cliente live lê `CAUSALODS_LLM_MODEL`, `CAUSALODS_LLM_ENDPOINT` e `CAUSALODS_LLM_API_KEY`; ausência produz `LLM_BINDING_NOT_CONFIGURED`. Testes não fazem chamadas live.

## 10. Scientific preprocessing and benchmark catalog

`experiments.final_campaigns_v3 preprocess` is the only promotion path from frozen evidence to scientific inputs. It validates both frozen registries, reads normalized `UnifiedEvent`, delegates aggregation/graph/features to `IncidentEvidenceAssembler`, `EventGraphBuilder`, `IncidentDatasetGeneratorV3` and `FeatureRegistryV3`, binds train/validation/test to the frozen planner, and writes `scientific-inputs/{datasets,manifests}`, `benchmark-catalog.json`, anti-leakage/report/checksums. Runtime prediction, ranking, SHAP, Reliability Gate and LLM output never enter features.

RCAEval preprocessing is cache-only. The archived normalized cache must be mounted explicitly with `--rcaeval-cache`; the command never reconstructs or writes it. Resume accepts only checksum-valid BenchmarkDatasets and reports zero new artifacts when all keys are valid. Cache identity includes frozen dataset/manifests, view, protocol/holdout/seed, split, registry and preprocessing version.

Healthy local controls retain `root_cause_service=null`. They remain frozen and usable for abstention analysis, but are explicitly excluded from top-1 benchmark splits; no positive service is fabricated.

## 11. Model specification versus trained artifacts

All six configs bind `rca-model-benchmark-v3-spec` and the SHA-256 of `scientific-model-specification.json`. This is a pre-execution benchmark specification identity, not a serialized model. Learned methods persist a distinct per-fold artifact path/hash, splits, seed, hyperparameters, feature schema and training dataset. Heuristics persist an implementation/version hash and no fictitious ML artifact. H2 promotes verified folds to `trained-artifacts/INDEX.json`; different methods/folds cannot overwrite one another.

## 12. H4 degradation

H4 usa os 14 operadores congelados e severidades `0`, `0.1`, `0.3`, `0.5`. A política definitiva é `READY_AFTER_H1_H2`: o provider resolve um artefato científico treinado uma vez no clean fold e usa o mesmo SHA nas quatro severidades. O harness altera somente observações, preservando ground truth, identidade, split e sistema. Operadores não aplicáveis a uma capability são skips estruturados.

O hash da feature original é calculável diretamente da observação clean. Em selective resume, a nova instância restaura as métricas do run 0% `SUCCESS` checksum-bound no store; não repete o run 0%, e somente a severidade inválida volta à fila.

## 13. Provenance

Antes dos runs, cada campanha grava `campaign-manifest.json`, `execution-plan.json`, `configuration.json`, snapshot do registry e `environment.json`. Também grava split/model manifest e, quando aplicável, prompt/degradation manifest. São persistidos commit, dirty state, Python, dependências, OS/arquitetura, hashes de dataset/config/model/features e versões dos contratos.

## 14. Anti-leakage

`ScientificInput` contém somente evidence, capabilities e proveniência sem labels. `ScientificEvaluation` contém ground truth e predição. Chaves de ground truth, cenário e fault metadata são rejeitadas recursivamente no input. Labels só entram depois da inferência, na avaliação.

## 15. Run IDs

O `run_id` continua sendo o SHA-256 do JSON canônico definido pelo Orchestrator v3: campanha, hipótese, dataset/sistema/incidente, protocolo/holdout, view, graph/pruning, método e hashes, seed, degradação, modo de explicação, repetição, execution mode e configuration hash. Ordenação de input não altera a identidade; mudança material altera o ID.

## 16. Resume

Resume requer run ID, configuration hash, source manifest/dataset hash, model specification hash, trained artifact/implementation hash, status `SUCCESS` e checksum íntegro. Artefato ausente/corrompido ou hash divergente vai para quarentena e somente aquele run volta à fila.

## 17. Failure policy

Os estados finais são `SUCCESS`, `SKIP_NOT_APPLICABLE`, `FAILED_INPUT`, `FAILED_EXECUTION` e `FAILED_VALIDATION`. `failures.jsonl` registra run, timestamp, stage, tipo/mensagem sanitizada, retryable, dataset, incidente, fator experimental e configuration hash. Skips permanecem fora do denominador aplicável; failures não viram acurácia zero nem desaparecem.

## 18. Statistical pairing

O preflight persiste, para cada par de métodos/views, denominadores individuais, interseção pareada, diferenças exclusivas e hashes dos conjuntos. A chave contém dataset, system, protocolo, holdout, incidente, seed, repetição e source manifest. H4 acrescenta operador e model specification e exige exatamente `{0,.1,.3,.5}`. Top-1 está preparado para McNemar pareado, MRR para bootstrap pareado com IC 95%, comparações múltiplas para Holm e Macro-F1 para descrição.

## 19. Dependency order

Ordem obrigatória: H1-A → H1-B → H1-C → H2 → promoção do scientific RCA bundle → H3. H4 ocorre após H2 porque reutiliza o índice de modelos científicos verificados. O preflight distingue `READY_NOW`, `READY_AFTER_DEPENDENCY` e `NOT_READY`; ausência de RCAResults antes de H1/H2 é dependência sequencial, não LLM fake.

## 20. Exact preprocessing command

```bash
PYTHONPATH="$PWD:$PWD/correlation-engine" correlation-engine/.venv/bin/python -m experiments.final_campaigns_v3 preprocess --registry experiments/configs/final-v3/datasets.json --config-dir experiments/configs/final-v3 --model-specification experiments/configs/final-v3/scientific-model-specification.json --rcaeval-cache /MOUNT/READ_ONLY/rcaeval-official-v3 --output artifacts/final-experiments-v3/scientific-inputs --resume
```

## 21. Exact launch commands

Executar somente após auditoria externa, árvore Git limpa, catálogo científico aprovado e providers H3/H4 configurados:

```bash
PYTHONPATH="$PWD:$PWD/correlation-engine" correlation-engine/.venv/bin/python -m experiments.final_campaigns_v3 run --mode final --config experiments/configs/final-v3/h1a.json --registry experiments/configs/final-v3/datasets.json --output artifacts/final-experiments-v3 --executor-factory experiments.causalods_campaign_bindings:build_executors
PYTHONPATH="$PWD:$PWD/correlation-engine" correlation-engine/.venv/bin/python -m experiments.final_campaigns_v3 run --mode final --config experiments/configs/final-v3/h1b.json --registry experiments/configs/final-v3/datasets.json --output artifacts/final-experiments-v3 --executor-factory experiments.causalods_campaign_bindings:build_executors
PYTHONPATH="$PWD:$PWD/correlation-engine" correlation-engine/.venv/bin/python -m experiments.final_campaigns_v3 run --mode final --config experiments/configs/final-v3/h1c.json --registry experiments/configs/final-v3/datasets.json --output artifacts/final-experiments-v3 --executor-factory experiments.causalods_campaign_bindings:build_executors
PYTHONPATH="$PWD:$PWD/correlation-engine" correlation-engine/.venv/bin/python -m experiments.final_campaigns_v3 run --mode final --config experiments/configs/final-v3/h2.json --registry experiments/configs/final-v3/datasets.json --output artifacts/final-experiments-v3 --executor-factory experiments.causalods_campaign_bindings:build_executors
PYTHONPATH="$PWD:$PWD/correlation-engine" correlation-engine/.venv/bin/python -m experiments.final_campaigns_v3 run --mode final --config experiments/configs/final-v3/h3.json --registry experiments/configs/final-v3/datasets.json --output artifacts/final-experiments-v3 --executor-factory experiments.causalods_campaign_bindings:build_executors
PYTHONPATH="$PWD:$PWD/correlation-engine" correlation-engine/.venv/bin/python -m experiments.final_campaigns_v3 run --mode final --config experiments/configs/final-v3/h4.json --registry experiments/configs/final-v3/datasets.json --output artifacts/final-experiments-v3 --executor-factory experiments.causalods_campaign_bindings:build_executors
```

## 22. Exact dry-run commands

O preflight conjunto e os seis dry-runs não executam ML, inference live ou LLM:

```bash
PYTHONPATH="$PWD:$PWD/correlation-engine" correlation-engine/.venv/bin/python -m experiments.final_campaigns_v3 preflight --registry experiments/configs/final-v3/datasets.json --config-dir experiments/configs/final-v3 --output artifacts/final-experiments-v3/preflight
PYTHONPATH="$PWD:$PWD/correlation-engine" correlation-engine/.venv/bin/python -m experiments.final_campaigns_v3 dry-run --mode development --allow-dirty-development --config experiments/configs/final-v3/h1a.json --registry experiments/configs/final-v3/datasets.json --output artifacts/final-experiments-v3
PYTHONPATH="$PWD:$PWD/correlation-engine" correlation-engine/.venv/bin/python -m experiments.final_campaigns_v3 dry-run --mode development --allow-dirty-development --config experiments/configs/final-v3/h1b.json --registry experiments/configs/final-v3/datasets.json --output artifacts/final-experiments-v3
PYTHONPATH="$PWD:$PWD/correlation-engine" correlation-engine/.venv/bin/python -m experiments.final_campaigns_v3 dry-run --mode development --allow-dirty-development --config experiments/configs/final-v3/h1c.json --registry experiments/configs/final-v3/datasets.json --output artifacts/final-experiments-v3
PYTHONPATH="$PWD:$PWD/correlation-engine" correlation-engine/.venv/bin/python -m experiments.final_campaigns_v3 dry-run --mode development --allow-dirty-development --config experiments/configs/final-v3/h2.json --registry experiments/configs/final-v3/datasets.json --output artifacts/final-experiments-v3
PYTHONPATH="$PWD:$PWD/correlation-engine" correlation-engine/.venv/bin/python -m experiments.final_campaigns_v3 dry-run --mode development --allow-dirty-development --config experiments/configs/final-v3/h3.json --registry experiments/configs/final-v3/datasets.json --output artifacts/final-experiments-v3
PYTHONPATH="$PWD:$PWD/correlation-engine" correlation-engine/.venv/bin/python -m experiments.final_campaigns_v3 dry-run --mode development --allow-dirty-development --config experiments/configs/final-v3/h4.json --registry experiments/configs/final-v3/datasets.json --output artifacts/final-experiments-v3
```

## 23. Output layout

Preflight fica em `artifacts/final-experiments-v3/preflight`. Campanhas ficam em `H1-A/`, `H1-B/`, `H1-C/`, `H2/`, `H3/` e `H4/`, seguidas por `campaign_id/configuration_hash`. Outputs incompatíveis não são sobrescritos.

## 24. Freeze boundaries

A implementação é aditiva sob `experiments/final_campaigns_v3`, configs finais, bindings e esta documentação. Ela não muda contratos congelados. Snapshots antes/depois e validação integral de `SHA256SUMS` protegem RCAEval e final-500. Nenhum comando de campanha final foi executado no Prompt 13C.

## 25. Known limitations

O benchmark exige o catálogo completo produzido pelo preprocessing. Neste workspace, o cache normalizado RCAEval foi arquivado externamente e não está montado; portanto a promoção completa deve permanecer fechada até esse input read-only estar disponível. H3 exige o bundle de RCAResults científico e credenciais live externas; H4 exige o índice de artefatos treinados produzido após H2. Determinismo de bibliotecas ML é registrado como seeded best effort. RCAEval não oferece business events e terá denominadores menores nas views correspondentes.
