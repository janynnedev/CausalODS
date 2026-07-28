# Relatório Comparativo da Campanha E2E RCA v1 × v2

## Resumo executivo

Foram analisadas **300 execuções**, 150 por versão, distribuídas igualmente entre `healthy`, `payment_failure` e `payment_latency`.

| Indicador | v1 | v2 | Variação |
|---|---:|---:|---:|
| Taxa global de aprovação | 66,67% (100/150) | 100,00% (150/150) | +33,33 p.p. |
| Recall de incidentes | 50,00% | 100,00% | +50,00 p.p. |
| Falsos positivos healthy | 0/50 | 0/50 | Sem aumento |
| Payment latency confiável | 0/50 | 50/50 | +100 p.p. |
| Confiança média payment latency | 0,1996 | 0,8230 | +0,6234 |

A v2 preservou o comportamento correto no cenário saudável e em falhas explícitas, enquanto corrigiu a incapacidade da v1 de produzir decisões confiáveis e incidentes acionáveis para degradação de latência.

## Resultado por cenário

| Cenário | v1 aprovadas | v2 aprovadas | v1 confiáveis | v2 confiáveis | v1 incidentes | v2 incidentes |
|---|---:|---:|---:|---:|---:|---:|
| healthy | 50/50 | 50/50 | 0/50 | 0/50 | 0/50 | 0/50 |
| payment_failure | 50/50 | 50/50 | 50/50 | 50/50 | 50/50 | 50/50 |
| payment_latency | 0/50 | 50/50 | 0/50 | 50/50 | 0/50 | 50/50 |

## Detecção operacional

| Versão | TP | FN | TN | FP | Acurácia | Precisão | Recall | Especificidade | F1 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| v1 | 50 | 50 | 50 | 0 | 66,67% | 100% | 50% | 100% | 66,67% |
| v2 | 100 | 0 | 50 | 0 | 100% | 100% | 100% | 100% | 100% |

## Interpretação

- As duas versões localizaram `payment-service` no Top-1 em todas as 100 execuções com falha.
- O ganho da v2 não é de Top-1, mas de **confiança, confiabilidade e criação correta de incidentes**.
- Em `payment_latency`, a confiança média passou de 0,1996 para 0,8230.
- Em `healthy`, a v2 permaneceu abaixo de 0,0366, sem falsos incidentes.

## Limitações

1. Os dois cenários com falha têm a mesma causa raiz (`payment-service`).
2. O atraso e a taxa de injeção são fixos (1.500 ms e 1,0).
3. As campanhas usam seed fixa 42 e um único ambiente local.
4. V1 e v2 foram executadas sequencialmente, não aleatorizadas.
5. Ainda não há comparação com os três baselines nem estudo de ablação.

## Próximas etapas

1. Congelar os artefatos atuais.
2. Implementar `order_failure` e `order_latency`.
3. Reexecutar a comparação com cinco cenários.
4. Implementar os três baselines.
5. Executar a ablação mínima.
6. Variar delay, fault rate, seed e carga.
7. Auditar a persistência das evidências e regras de confiabilidade.
8. Realizar validação externa.

## Fontes

- v1: `e2e-20260728T041551Z.json` — SHA-256 `c5c30040b08f90e6c5b8e618da68a1750a26f90a8040742688977c8c9ba9f729`
- v2: `e2e-20260728T075528Z.json` — SHA-256 `1997b4ef023c8fa71a7f430188c82235ef3e8d2b5304b601c61f309a25f14eb1`
