# CausalODS — Robustez live

- Execuções: 30
- Válidas tecnicamente: 20
- Invariantes aprovados: 20
- Restauração completa: True

| Cenário | Runs | Pass rate | RCA duplicado | Incidente duplicado | Perda | Proveniência inválida | Recuperação mediana (ms) |
|---|---:|---:|---:|---:|---:|---:|---:|
| correlation_engine_restart | 10 | 0.000 | 0 | 0 | 0 | 0 | n/a |
| kafka_backlog | 10 | 1.000 | 0 | 0 | 0 | 0 | 9117.315176001284 |
| opensearch_unavailable | 10 | 1.000 | 0 | 0 | 0 | 0 | 2083.5568594920915 |
