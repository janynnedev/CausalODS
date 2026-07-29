# Comparação experimental CausalODS v1 × v2

Campanha analisada: `/workspaces/CausalODS/results/e2e/final/final-v1-v2-50`

## Integridade e proveniência

- **v1**: válido=True; linhas=250; repetições=50; erros=0; avisos=0.
- **v2**: válido=True; linhas=250; repetições=50; erros=0; avisos=0.

## Métricas gerais

| Versão | Execuções | Pass rate | Top-1 fault-only | Reliability | Incidente | FP healthy | FN falhas | Confiança healthy | Confiança falhas |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| v1 | 250 | 60.00% | 100.00% | 60.00% | 60.00% | 0.00% | 50.00% | 0.0009 | 0.3907 |
| v2 | 250 | 100.00% | 100.00% | 100.00% | 100.00% | 0.00% | 0.00% | 0.0198 | 0.8026 |

## Resultados por cenário

| Versão | Cenário | N | Pass rate | Top-1 | Reliability | Incidente | Confiança média | Request p95 (ms) |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| v1 | healthy | 50 | 100.00% | — | 100.00% | 100.00% | 0.0009 | 249.55 |
| v1 | payment_failure | 50 | 100.00% | 100.00% | 100.00% | 100.00% | 0.5691 | 121.87 |
| v1 | payment_latency | 50 | 0.00% | 100.00% | 0.00% | 0.00% | 0.1994 | 1582.13 |
| v1 | order_failure | 50 | 100.00% | 100.00% | 100.00% | 100.00% | 0.5991 | 81.62 |
| v1 | order_latency | 50 | 0.00% | 100.00% | 0.00% | 0.00% | 0.1952 | 1795.18 |
| v2 | healthy | 50 | 100.00% | — | 100.00% | 100.00% | 0.0198 | 228.03 |
| v2 | payment_failure | 50 | 100.00% | 100.00% | 100.00% | 100.00% | 0.7716 | 116.80 |
| v2 | payment_latency | 50 | 100.00% | 100.00% | 100.00% | 100.00% | 0.8225 | 1577.78 |
| v2 | order_failure | 50 | 100.00% | 100.00% | 100.00% | 100.00% | 0.8000 | 76.31 |
| v2 | order_latency | 50 | 100.00% | 100.00% | 100.00% | 100.00% | 0.8162 | 1795.36 |

## Testes estatísticos pareados

As execuções foram pareadas por `(scenario, iteration)`. Métricas booleanas usam McNemar exato bicaudal; métricas contínuas usam bootstrap pareado da diferença média `v2 − v1`.

| Métrica | Pares | v1 somente correta | v2 somente correta | p-valor |
|---|---:|---:|---:|---:|
| runner_passed | 250 | 0 | 100 | 0.000000 |
| top1_correct | 200 | 0 | 0 | 1.000000 |
| reliability_correct | 250 | 0 | 100 | 0.000000 |
| incident_decision_correct | 250 | 0 | 100 | 0.000000 |
| incident_root_cause_correct | 200 | 0 | 100 | 0.000000 |

| Métrica contínua | Pares | Diferença média v2−v1 | IC95% inferior | IC95% superior |
|---|---:|---:|---:|---:|
| confidence | 250 | 0.3333 | 0.3034 | 0.3633 |
| request_duration_ms | 250 | -3.0442 | -6.6224 | -0.0886 |
| duration_ms | 250 | -1034.4348 | -1317.1280 | -759.7553 |

## Interpretação metodológica

- O cenário `healthy` não possui ground truth de causa raiz; nele são avaliados ausência de confiabilidade e ausência de incidente.
- Top-1 accuracy é calculada apenas nos quatro cenários de falha.
- Resultados de confiança devem ser interpretados junto com reliability e margem, e não como acurácia isolada.
- O relatório é derivado dos JSONs preservados pela campanha e não consulta o OpenSearch.
