# CausalODS — Sensibilidade à Intensidade da Latência

## Configuração

- Versões: v1, v2
- Serviços: payment-service, order-service
- Delays: 500, 1500 ms
- Repetições por condição: 1
- Controle saudável: True
- Estado: completed

## Validação

- Integridade estrutural: OK

## Taxa de detecção por condição

| Versão | Serviço | Delay (ms) | n | Detecção | Incidente | Top-1 | Confiança média |
|---|---|---:|---:|---:|---:|---:|---:|
| v1 | order-service | 1500 | 1 | 0.000 | 0.000 | 1.000 | 0.1944 |
| v1 | order-service | 500 | 1 | 0.000 | 0.000 | 1.000 | 0.1982 |
| v1 | payment-service | 1500 | 1 | 0.000 | 0.000 | 1.000 | 0.1996 |
| v1 | payment-service | 500 | 1 | 0.000 | 0.000 | 1.000 | 0.1429 |
| v2 | order-service | 1500 | 1 | 1.000 | 1.000 | 1.000 | 0.8165 |
| v2 | order-service | 500 | 1 | 1.000 | 1.000 | 1.000 | 0.8185 |
| v2 | payment-service | 1500 | 1 | 1.000 | 1.000 | 1.000 | 0.8227 |
| v2 | payment-service | 500 | 1 | 1.000 | 1.000 | 1.000 | 0.7647 |

## Limiar empírico

| Versão | Serviço | ≥50% | ≥80% | ≥95% | Violações monotônicas |
|---|---|---:|---:|---:|---:|
| v1 | payment-service | None | None | None | 0 |
| v1 | order-service | None | None | None | 0 |
| v2 | payment-service | 500 | 500 | 500 | 0 |
| v2 | order-service | 500 | 500 | 500 | 0 |

## Controle saudável

| Versão | n | Falso positivo | Confiança média | Confiança máxima |
|---|---:|---:|---:|---:|
| v1 | 1 | 0.000 | 0.0009 | 0.0009 |
| v2 | 1 | 0.000 | 0.0202 | 0.0202 |

## Comparação v2 × v1

| Serviço | Delay (ms) | Detecção v1 | Detecção v2 | Diferença | p Fisher | p Holm | Cliff δ confiança |
|---|---:|---:|---:|---:|---:|---:|---:|
| payment-service | 500 | 0.000 | 1.000 | 1.000 | 1 | 1 | 1.000 |
| payment-service | 1500 | 0.000 | 1.000 | 1.000 | 1 | 1 | 1.000 |
| order-service | 500 | 0.000 | 1.000 | 1.000 | 1 | 1 | 1.000 |
| order-service | 1500 | 0.000 | 1.000 | 1.000 | 1 | 1 | 1.000 |

## Critérios de interpretação

- A taxa de detecção é a proporção de resultados com `is_reliable=true`.
- A taxa end-to-end também exige causa Top-1 e incidente corretos.
- O limiar empírico é o menor delay que atinge a taxa definida.
- O teste de Fisher compara proporções independentes de v1 e v2 em cada condição.
- Holm-Bonferroni corrige as comparações múltiplas.
- A campanha mede sensibilidade à magnitude; não mede ainda validade externa.
