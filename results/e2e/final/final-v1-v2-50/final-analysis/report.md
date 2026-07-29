# Resultados experimentais consolidados — CausalODS

Campanha: `/workspaces/CausalODS/results/e2e/final/final-v1-v2-50`

## 1. Configuração avaliada

Foram avaliadas as versões RCA v1 e v2 nos cenários `healthy`, `payment_failure`, `payment_latency`, `order_failure` e `order_latency`. O cenário saudável é usado para medir falsos positivos; os quatro cenários de falha possuem ground truth de serviço causa raiz.

## 2. Resultado geral v1 × v2

| Versão | Execuções | Pass rate | Top-1 fault-only | Reliability | Incidente | FP healthy | FN falhas |
|---|---:|---:|---:|---:|---:|---:|---:|
| v1 | 250 | 60.00% | 100.00% | 60.00% | 60.00% | 0.00% | 50.00% |
| v2 | 250 | 100.00% | 100.00% | 100.00% | 100.00% | 0.00% | 0.00% |

## 3. Resultado por cenário

| Versão | Cenário | Pass rate | Top-1 | Reliability | Incidente | Confiança média |
|---|---|---:|---:|---:|---:|---:|
| v1 | healthy | 100.00% | — | 100.00% | 100.00% | 0.0009 |
| v1 | payment_failure | 100.00% | 100.00% | 100.00% | 100.00% | 0.5691 |
| v1 | payment_latency | 0.00% | 100.00% | 0.00% | 0.00% | 0.1994 |
| v1 | order_failure | 100.00% | 100.00% | 100.00% | 100.00% | 0.5991 |
| v1 | order_latency | 0.00% | 100.00% | 0.00% | 0.00% | 0.1952 |
| v2 | healthy | 100.00% | — | 100.00% | 100.00% | 0.0198 |
| v2 | payment_failure | 100.00% | 100.00% | 100.00% | 100.00% | 0.7716 |
| v2 | payment_latency | 100.00% | 100.00% | 100.00% | 100.00% | 0.8225 |
| v2 | order_failure | 100.00% | 100.00% | 100.00% | 100.00% | 0.8000 |
| v2 | order_latency | 100.00% | 100.00% | 100.00% | 100.00% | 0.8162 |

## 4. Comparação estatística v1 × v2

| Métrica | Pares | v1 somente correta | v2 somente correta | p-valor McNemar |
|---|---:|---:|---:|---:|
| runner_passed | 250 | 0 | 100 | 0.000000 |
| top1_correct | 200 | 0 | 0 | 1.000000 |
| reliability_correct | 250 | 0 | 100 | 0.000000 |
| incident_decision_correct | 250 | 0 | 100 | 0.000000 |
| incident_root_cause_correct | 200 | 0 | 100 | 0.000000 |

## 5. Baselines

| Método | Fonte | Acurácia fault-only | Abstenção healthy | Tempo médio (ms) |
|---|---|---:|---:|---:|
| first_anomaly_in_time | v1 | 25.00% | 100.00% | 0.009546 |
| first_anomaly_in_time | v2 | 25.00% | 100.00% | 0.009018 |
| max_anomaly_zscore | v1 | 0.00% | 100.00% | 0.008031 |
| max_anomaly_zscore | v2 | 0.00% | 100.00% | 0.006815 |
| max_temporal_score | v1 | 100.00% | 100.00% | 0.011463 |
| max_temporal_score | v2 | 100.00% | 100.00% | 0.010341 |

### Comparação pareada baseline × CausalODS

| Comparação | Pares | Baseline somente correto | CausalODS somente correto | p-valor |
|---|---:|---:|---:|---:|
| first_anomaly_in_time:v1 | 200 | 0 | 150 | 0.000000 |
| first_anomaly_in_time:v2 | 200 | 0 | 150 | 0.000000 |
| max_anomaly_zscore:v1 | 200 | 0 | 200 | 0.000000 |
| max_anomaly_zscore:v2 | 200 | 0 | 200 | 0.000000 |
| max_temporal_score:v1 | 200 | 0 | 0 | 1.000000 |
| max_temporal_score:v2 | 200 | 0 | 0 | 1.000000 |

## 6. Interpretação

1. Top-1 deve ser analisado somente nos cenários de falha; no saudável, qualquer Top-1 é apenas uma ordenação fraca.
2. A decisão operacional depende do reliability gate e da criação correta de incidente.
3. Diferenças entre versões devem ser discutidas por cenário e não apenas pela média geral.
4. Os baselines precisam usar exatamente os mesmos traces de cada execução para preservar a comparação pareada.

## 7. Ameaças à validade que devem acompanhar as tabelas

- ambiente controlado e número reduzido de serviços;
- falhas sintéticas com intensidade fixa;
- dependência da representatividade dos dados de treinamento;
- possíveis efeitos de ordem e aquecimento dos containers;
- validade externa ainda dependente de dataset ou aplicação pública adicional.
