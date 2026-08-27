# SCIENTIFIC_PREPROCESSING_P3_2_CRASH_RESUME_GATE

## Arquitetura preparada

O caminho de produção é `materialize -> baseline contribution workers -> baseline finalizer -> incident workers -> checkpoints -> dataset`. O baseline e cada incidente são isolados em subprocessos; o coordenador conserva somente metadados, linhas compactas e snapshots.

`prepare-baseline` foi adicionado como comando oficial. Ele resolve a assignment pelo `ExperimentPlannerV3`, deriva os mesmos `base_key` e `split_key` e produz somente `work/<base_key>/baseline/snapshot.json`. `preprocess-incident` exige esse snapshot oficial, sem criar baseline próprio.

Falhas de worker de baseline agora persistem `work/<base_key>/state.json` com `status: FAILED_WORKER`; não há artefato científico final nesse caminho de erro.

## Verificações concluídas nesta etapa

- 52 testes de performance/hardening/wiring: PASS.
- Equivalência P2/P3 multi-incidente: PASS.
- Golden artifacts: 7/7 PASS.
- V2 freeze: 12/12 PASS.
- V2 -> V3 imports: 0.
- `compileall experiments/final_campaigns_v3`: PASS.
- `git diff --check`: PASS.

## Gate ainda pendente

O teste end-to-end obrigatório com fixture congelada, interrupção do coordenador, `preprocess --resume`, corrupção de checkpoint e falha de worker ainda não foi implementado/executado. Portanto não existem hashes de clean run versus resumed run, nem contagens de HIT/MISS/RECOMPUTED que possam ser declaradas honestamente.

P3_IMPLEMENTATION = FAIL
CRASH_RESUME_GATE = FAIL
READY_FOR_HEAVY_INCIDENT_GATE = NO

READY_FOR_FULL_PREPROCESS_RESUME não é declarado.
