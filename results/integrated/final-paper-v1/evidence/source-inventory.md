# Inventário integrado de evidências

Gerado em UTC: `2026-08-02T05:48:54.648552+00:00`

Commit: `9ea6e92ad626be22b03853223143d342fbc3d2e0`

Branch: `feat/integrated-experimental-report-v1`

## Fontes

| ID | Categoria | Existe | Arquivos | Tamanho | Manifestos válidos |
|---|---|---:|---:|---:|---:|
| `e2e_final` | e2e | True | 57 | 15375516 bytes | 5/5 |
| `e2e_comparison_report` | e2e | True | 1 | 2807 bytes | não encontrado |
| `latency_sensitivity` | sensitivity | True | 50 | 7502945 bytes | 4/4 |
| `explanations_final` | explanations | True | 4 | 3013876 bytes | 1/1 |
| `explanations_evaluation` | explanations | True | 4 | 1997 bytes | 1/1 |
| `explanations_run_a` | explanations | True | 6 | 5005200 bytes | 1/1 |
| `explanations_run_b` | explanations | True | 6 | 5005200 bytes | 1/1 |
| `robustness_original` | robustness | True | 8 | 72057 bytes | 2/2 |
| `robustness_restart` | robustness | True | 44 | 204813 bytes | 3/3 |
| `robustness_consolidated` | robustness | True | 2 | 91164 bytes | não encontrado |

## Detalhamento

### e2e_final

Campanha final v1 versus v2, com cinquenta repetições por cenário.

- Caminho: `results/e2e/final/final-v1-v2-50`
- Existe: `True`
- Arquivos: `57`
- Tamanho total: `15375516` bytes

Manifestos:

- `results/e2e/final/final-v1-v2-50/SHA256SUMS`: `valid=True`
- `results/e2e/final/final-v1-v2-50/SHA256SUMS.post-export`: `valid=True`
- `results/e2e/final/final-v1-v2-50/baselines/SHA256SUMS`: `valid=True`
- `results/e2e/final/final-v1-v2-50/comparison/SHA256SUMS`: `valid=True`
- `results/e2e/final/final-v1-v2-50/final-analysis/SHA256SUMS`: `valid=True`

Relatórios localizados:

- `baselines/report.md`
- `comparison/report.md`
- `final-analysis/report.md`

### e2e_comparison_report

Relatório comparativo v1 versus v2.

- Caminho: `results/e2e/final/comparison/relatorio_comparacao_e2e_rca_v1_v2.md`
- Existe: `True`
- Arquivos: `1`
- Tamanho total: `2807` bytes

Relatórios localizados:

- `results/e2e/final/comparison/relatorio_comparacao_e2e_rca_v1_v2.md`

### latency_sensitivity

Sensibilidade do RCA a diferentes intensidades de latência.

- Caminho: `results/e2e/sensitivity/latency-sensitivity-v1-v2-20260729T191418Z`
- Existe: `True`
- Arquivos: `50`
- Tamanho total: `7502945` bytes

Manifestos:

- `results/e2e/sensitivity/latency-sensitivity-v1-v2-20260729T191418Z/FINAL_SHA256SUMS`: `valid=True`
- `results/e2e/sensitivity/latency-sensitivity-v1-v2-20260729T191418Z/SHA256SUMS`: `valid=True`
- `results/e2e/sensitivity/latency-sensitivity-v1-v2-20260729T191418Z/analysis/SHA256SUMS`: `valid=True`
- `results/e2e/sensitivity/latency-sensitivity-v1-v2-20260729T191418Z/analysis-before-top1-field-fix/SHA256SUMS`: `valid=True`

Relatórios localizados:

- `analysis/report.md`
- `analysis-before-top1-field-fix/report.md`

### explanations_final

Explicações geradas para a campanha final.

- Caminho: `results/offline-explanations/final-v1-v2-50`
- Existe: `True`
- Arquivos: `4`
- Tamanho total: `3013876` bytes

Manifestos:

- `results/offline-explanations/final-v1-v2-50/SHA256SUMS`: `valid=True`

Relatórios localizados:

- `report.md`

### explanations_evaluation

Avaliação de cobertura e consistência das explicações v1.1.

- Caminho: `results/offline-explanations/v1.1-evaluation`
- Existe: `True`
- Arquivos: `4`
- Tamanho total: `1997` bytes

Manifestos:

- `results/offline-explanations/v1.1-evaluation/SHA256SUMS`: `valid=True`

Relatórios localizados:

- `report.md`

### explanations_run_a

Primeira execução usada na avaliação de determinismo.

- Caminho: `results/offline-explanations/v1.1-run-a`
- Existe: `True`
- Arquivos: `6`
- Tamanho total: `5005200` bytes

Manifestos:

- `results/offline-explanations/v1.1-run-a/SHA256SUMS`: `valid=True`

Relatórios localizados:

- `report.md`

### explanations_run_b

Segunda execução usada na avaliação de determinismo.

- Caminho: `results/offline-explanations/v1.1-run-b`
- Existe: `True`
- Arquivos: `6`
- Tamanho total: `5005200` bytes

Manifestos:

- `results/offline-explanations/v1.1-run-b/SHA256SUMS`: `valid=True`

Relatórios localizados:

- `report.md`

### robustness_original

Campanha original de backlog Kafka, indisponibilidade do OpenSearch e tentativa inicial de restart.

- Caminho: `results/robustness/live/robustness-live-v2-repeat10`
- Existe: `True`
- Arquivos: `8`
- Tamanho total: `72057` bytes

Manifestos:

- `results/robustness/live/robustness-live-v2-repeat10/IMPORTED_SHA256SUMS`: `valid=True`
- `results/robustness/live/robustness-live-v2-repeat10/analysis/SHA256SUMS`: `valid=True`

Relatórios localizados:

- `analysis/report.md`

### robustness_restart

Campanha corrigida de reinicialização do Correlation Engine.

- Caminho: `results/robustness/live/robustness-live-restart-v2-repeat10-ubuntu-warm-20260802T024249Z`
- Existe: `True`
- Arquivos: `44`
- Tamanho total: `204813` bytes

Manifestos:

- `results/robustness/live/robustness-live-restart-v2-repeat10-ubuntu-warm-20260802T024249Z/FINAL_SHA256SUMS`: `valid=True`
- `results/robustness/live/robustness-live-restart-v2-repeat10-ubuntu-warm-20260802T024249Z/SHA256SUMS`: `valid=True`
- `results/robustness/live/robustness-live-restart-v2-repeat10-ubuntu-warm-20260802T024249Z/analysis/SHA256SUMS`: `valid=True`

Relatórios localizados:

- `analysis/control-cadence.md`
- `analysis/report.md`
- `analysis/restart-methodology.md`

### robustness_consolidated

Conjunto consolidado com trinta execuções válidas de robustez.

- Caminho: `results/robustness/consolidated/final-robustness-v2`
- Existe: `True`
- Arquivos: `2`
- Tamanho total: `91164` bytes

