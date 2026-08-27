# SCIENTIFIC_PREPROCESSING_P3_1_CORRECTION

## Correções aplicadas

- O baseline não materializa `UnifiedEvent` nem um dicionário de contribuições no processo pai. Cada contribuição é calculada por `baseline-contribution-worker`, gravada atomicamente e descartada no encerramento do subprocesso.
- `baseline-finalizer` lê os checkpoints em `incident_id` ordenado e reconstrói o baseline com o algoritmo original (`fmean`, `pstdev`, seleção de métrica e templates). O heap transitório do finalizador é liberado ao terminar.
- Checkpoints de contribuição e snapshots exigem `status: COMPLETED`; checkpoints parciais não são reutilizáveis.
- A identidade de cache agora é um hash canônico de `cache_key`, versão/configuração do adaptador, hashes das fontes, hash do manifesto e, para fontes locais, a lista congelada de fontes normalizadas.
- A identidade de cache é parte dos bindings dos checkpoints de incidente e, portanto, é validada em qualquer resume.
- `preprocess-incident` não constrói baseline. Exige `--protocol` e `--seed`, resolve a assignment oficial pelo `ExperimentPlannerV3`, deriva `base_key`/`split_key` oficiais e somente reutiliza o baseline oficial concluído.
- Workers registram PID, RSS de pico e retorno; esses dados permanecem fora dos hashes científicos.

## Arquivos modificados

- `experiments/final_campaigns_v3/preprocessing.py`
- `experiments/final_campaigns_v3/cli.py`
- `experiments/tests/test_scientific_preprocessing_performance_v3.py`

## Evidência executada

- Equivalência P2/P3 multi-incidente: PASS. O teste inclui logs, métricas e traces em ordem de entrada distinta da ordem temporal e exige `baseline_json` e `baseline_sha256` exatos.
- Golden artifacts: 7/7 PASS.
- P3 hardening, wiring e performance: 52 testes PASS.
- `compileall experiments/final_campaigns_v3`: PASS.
- `git diff --check`: PASS.
- V2 freeze: 12/12 PASS.
- Imports de V2 para V3: 0 (`v2_to_v3_import_count`).

## Limite deliberado

Não foi executado RCAEval completo nem os dois incidentes reais pesados. O teste integrado de interrupção real seguida de `preprocess --resume`, exigido pelo bloqueador 6, ainda precisa ser acrescentado/executado com fixture de dataset congelado pequena antes de aprovação do heavy incident gate.

P3_IMPLEMENTATION = FAIL
READY_FOR_HEAVY_INCIDENT_GATE = NO

READY_FOR_FULL_PREPROCESS não é declarado.
