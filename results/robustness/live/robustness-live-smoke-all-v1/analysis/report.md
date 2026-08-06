# CausalODS — Robustez live

- Execuções: 3
- Válidas tecnicamente: 2
- Invariantes aprovados: 2
- Restauração completa: True

| Cenário | Runs | Pass rate | RCA duplicado | Incidente duplicado | Perda | Proveniência inválida | Recuperação mediana (ms) |
|---|---:|---:|---:|---:|---:|---:|---:|
| correlation_engine_restart | 1 | 0.000 | 0 | 0 | 0 | 0 | n/a |
| kafka_backlog | 1 | 1.000 | 0 | 0 | 0 | 0 | 8120.855504996143 |
| opensearch_unavailable | 1 | 1.000 | 0 | 0 | 0 | 0 | 3089.918190962635 |
