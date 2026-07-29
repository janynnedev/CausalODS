# Avaliação dos baselines do CausalODS

Entrada: `/workspaces/CausalODS/results/e2e/final/final-v1-v2-50/baseline-input/rca-documents.json`

## Definições

- `max_temporal_score`: escolhe o serviço com maior score temporal pré-modelo.
- `max_anomaly_zscore`: escolhe o maior z-score estritamente acima de zero; caso contrário, abstém.
- `first_anomaly_in_time`: escolhe a evidência temporal com timestamp de anomalia mais antigo.
- Acurácia principal é calculada nos cenários de falha. No cenário healthy é avaliada a abstenção.

## Métricas gerais

| Baseline | Fonte | N falhas | Acurácia fault-only | N healthy | Abstenção healthy | Tempo médio (ms) |
|---|---|---:|---:|---:|---:|---:|
| first_anomaly_in_time | v1 | 200 | 25.00% | 50 | 100.00% | 0.009546 |
| first_anomaly_in_time | v2 | 200 | 25.00% | 50 | 100.00% | 0.009018 |
| max_anomaly_zscore | v1 | 200 | 0.00% | 50 | 100.00% | 0.008031 |
| max_anomaly_zscore | v2 | 200 | 0.00% | 50 | 100.00% | 0.006815 |
| max_temporal_score | v1 | 200 | 100.00% | 50 | 100.00% | 0.011463 |
| max_temporal_score | v2 | 200 | 100.00% | 50 | 100.00% | 0.010341 |

## Métricas por cenário

| Baseline | Fonte | Cenário | N | Acurácia | Abstenção | Score médio |
|---|---|---|---:|---:|---:|---:|
| first_anomaly_in_time | v1 | healthy | 50 | 100.00% | 100.00% | — |
| first_anomaly_in_time | v1 | order_failure | 50 | 100.00% | 0.00% | 1.000000 |
| first_anomaly_in_time | v1 | order_latency | 50 | 0.00% | 100.00% | — |
| first_anomaly_in_time | v1 | payment_failure | 50 | 0.00% | 0.00% | 0.400000 |
| first_anomaly_in_time | v1 | payment_latency | 50 | 0.00% | 100.00% | — |
| first_anomaly_in_time | v2 | healthy | 50 | 100.00% | 100.00% | — |
| first_anomaly_in_time | v2 | order_failure | 50 | 100.00% | 0.00% | 1.000000 |
| first_anomaly_in_time | v2 | order_latency | 50 | 0.00% | 100.00% | — |
| first_anomaly_in_time | v2 | payment_failure | 50 | 0.00% | 0.00% | 0.400000 |
| first_anomaly_in_time | v2 | payment_latency | 50 | 0.00% | 100.00% | — |
| max_anomaly_zscore | v1 | healthy | 50 | 100.00% | 100.00% | 0.000000 |
| max_anomaly_zscore | v1 | order_failure | 50 | 0.00% | 100.00% | 0.000000 |
| max_anomaly_zscore | v1 | order_latency | 50 | 0.00% | 100.00% | 0.000000 |
| max_anomaly_zscore | v1 | payment_failure | 50 | 0.00% | 100.00% | 0.000000 |
| max_anomaly_zscore | v1 | payment_latency | 50 | 0.00% | 100.00% | 0.000000 |
| max_anomaly_zscore | v2 | healthy | 50 | 100.00% | 100.00% | 0.000000 |
| max_anomaly_zscore | v2 | order_failure | 50 | 0.00% | 100.00% | 0.000000 |
| max_anomaly_zscore | v2 | order_latency | 50 | 0.00% | 100.00% | 0.000000 |
| max_anomaly_zscore | v2 | payment_failure | 50 | 0.00% | 100.00% | 0.000000 |
| max_anomaly_zscore | v2 | payment_latency | 50 | 0.00% | 100.00% | 0.000000 |
| max_temporal_score | v1 | healthy | 50 | 100.00% | 100.00% | 0.000000 |
| max_temporal_score | v1 | order_failure | 50 | 100.00% | 0.00% | 1.000000 |
| max_temporal_score | v1 | order_latency | 50 | 100.00% | 0.00% | 0.971297 |
| max_temporal_score | v1 | payment_failure | 50 | 100.00% | 0.00% | 0.850000 |
| max_temporal_score | v1 | payment_latency | 50 | 100.00% | 0.00% | 0.992191 |
| max_temporal_score | v2 | healthy | 50 | 100.00% | 100.00% | 0.000000 |
| max_temporal_score | v2 | order_failure | 50 | 100.00% | 0.00% | 1.000000 |
| max_temporal_score | v2 | order_latency | 50 | 100.00% | 0.00% | 0.971239 |
| max_temporal_score | v2 | payment_failure | 50 | 100.00% | 0.00% | 0.850000 |
| max_temporal_score | v2 | payment_latency | 50 | 100.00% | 0.00% | 0.991939 |

## Comparação pareada com o CausalODS

McNemar exato é calculado somente nos quatro cenários de falha e usa os mesmos `(scenario, iteration)`.

| Comparação | Pares | Baseline somente correto | CausalODS somente correto | p-valor |
|---|---:|---:|---:|---:|
| first_anomaly_in_time:v1 | 200 | 0 | 150 | 0.000000 |
| first_anomaly_in_time:v2 | 200 | 0 | 150 | 0.000000 |
| max_anomaly_zscore:v1 | 200 | 0 | 200 | 0.000000 |
| max_anomaly_zscore:v2 | 200 | 0 | 200 | 0.000000 |
| max_temporal_score:v1 | 200 | 0 | 0 | 1.000000 |
| max_temporal_score:v2 | 200 | 0 | 0 | 1.000000 |

## Observações de validade

- Os baselines usam somente evidências pré-modelo registradas no RCAResult; não usam o ranking final como entrada.
- O mesmo trace e a mesma iteração são usados na comparação pareada entre baseline e CausalODS.
- Um baseline pode abster quando não existe evidência compatível; a abstenção em falha é contabilizada como erro.
