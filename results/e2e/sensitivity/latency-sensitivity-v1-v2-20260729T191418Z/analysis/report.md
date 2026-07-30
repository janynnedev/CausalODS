# CausalODS — Sensibilidade à Intensidade da Latência

## Configuração

- Versões: v1, v2
- Serviços: payment-service, order-service
- Delays: 250, 500, 750, 1000, 1500, 2000, 2500 ms
- Repetições por condição: 10
- Controle saudável: True
- Estado: completed

## Validação

- Integridade estrutural: OK

## Taxa de detecção por condição

| Versão | Serviço | Delay (ms) | n | Detecção | Incidente | Top-1 | Confiança média |
|---|---|---:|---:|---:|---:|---:|---:|
| v1 | order-service | 1000 | 10 | 0.000 | 0.000 | 1.000 | 0.1954 |
| v1 | order-service | 1500 | 10 | 0.000 | 0.000 | 1.000 | 0.1974 |
| v1 | order-service | 2000 | 10 | 0.000 | 0.000 | 1.000 | 0.1970 |
| v1 | order-service | 250 | 10 | 0.000 | 0.000 | 1.000 | 0.0206 |
| v1 | order-service | 2500 | 10 | 0.000 | 0.000 | 1.000 | 0.1985 |
| v1 | order-service | 500 | 10 | 0.000 | 0.000 | 1.000 | 0.1929 |
| v1 | order-service | 750 | 10 | 0.000 | 0.000 | 1.000 | 0.1960 |
| v1 | payment-service | 1000 | 10 | 0.000 | 0.000 | 1.000 | 0.1890 |
| v1 | payment-service | 1500 | 10 | 0.000 | 0.000 | 1.000 | 0.1864 |
| v1 | payment-service | 2000 | 10 | 0.000 | 0.000 | 1.000 | 0.1870 |
| v1 | payment-service | 250 | 10 | 0.000 | 0.000 | 0.000 | 0.0009 |
| v1 | payment-service | 2500 | 10 | 0.000 | 0.000 | 1.000 | 0.1893 |
| v1 | payment-service | 500 | 10 | 0.000 | 0.000 | 0.900 | 0.1527 |
| v1 | payment-service | 750 | 10 | 0.000 | 0.000 | 1.000 | 0.1828 |
| v2 | order-service | 1000 | 10 | 1.000 | 1.000 | 1.000 | 0.8162 |
| v2 | order-service | 1500 | 10 | 1.000 | 1.000 | 1.000 | 0.8188 |
| v2 | order-service | 2000 | 10 | 1.000 | 1.000 | 1.000 | 0.8181 |
| v2 | order-service | 250 | 10 | 1.000 | 1.000 | 1.000 | 0.6208 |
| v2 | order-service | 2500 | 10 | 1.000 | 1.000 | 1.000 | 0.8196 |
| v2 | order-service | 500 | 10 | 1.000 | 1.000 | 1.000 | 0.8139 |
| v2 | order-service | 750 | 10 | 1.000 | 1.000 | 1.000 | 0.8177 |
| v2 | payment-service | 1000 | 10 | 1.000 | 1.000 | 1.000 | 0.8131 |
| v2 | payment-service | 1500 | 10 | 1.000 | 1.000 | 1.000 | 0.8088 |
| v2 | payment-service | 2000 | 10 | 1.000 | 1.000 | 1.000 | 0.8098 |
| v2 | payment-service | 250 | 10 | 0.800 | 0.800 | 1.000 | 0.4896 |
| v2 | payment-service | 2500 | 10 | 1.000 | 1.000 | 1.000 | 0.8132 |
| v2 | payment-service | 500 | 10 | 1.000 | 1.000 | 1.000 | 0.7717 |
| v2 | payment-service | 750 | 10 | 1.000 | 1.000 | 1.000 | 0.8017 |

## Limiar empírico

| Versão | Serviço | ≥50% | ≥80% | ≥95% | Violações monotônicas |
|---|---|---:|---:|---:|---:|
| v1 | payment-service | None | None | None | 0 |
| v1 | order-service | None | None | None | 0 |
| v2 | payment-service | 250 | 250 | 500 | 0 |
| v2 | order-service | 250 | 250 | 250 | 0 |

## Controle saudável

| Versão | n | Falso positivo | Confiança média | Confiança máxima |
|---|---:|---:|---:|---:|
| v1 | 10 | 0.000 | 0.0009 | 0.0009 |
| v2 | 10 | 0.000 | 0.0138 | 0.0177 |

## Comparação v2 × v1

| Serviço | Delay (ms) | Detecção v1 | Detecção v2 | Diferença | p Fisher | p Holm | Cliff δ confiança |
|---|---:|---:|---:|---:|---:|---:|---:|
| payment-service | 250 | 0.000 | 0.800 | 0.800 | 0.000714456 | 0.000714456 | 1.000 |
| payment-service | 500 | 0.000 | 1.000 | 1.000 | 1.08251e-05 | 0.000151551 | 1.000 |
| payment-service | 750 | 0.000 | 1.000 | 1.000 | 1.08251e-05 | 0.000151551 | 1.000 |
| payment-service | 1000 | 0.000 | 1.000 | 1.000 | 1.08251e-05 | 0.000151551 | 1.000 |
| payment-service | 1500 | 0.000 | 1.000 | 1.000 | 1.08251e-05 | 0.000151551 | 1.000 |
| payment-service | 2000 | 0.000 | 1.000 | 1.000 | 1.08251e-05 | 0.000151551 | 1.000 |
| payment-service | 2500 | 0.000 | 1.000 | 1.000 | 1.08251e-05 | 0.000151551 | 1.000 |
| order-service | 250 | 0.000 | 1.000 | 1.000 | 1.08251e-05 | 0.000151551 | 1.000 |
| order-service | 500 | 0.000 | 1.000 | 1.000 | 1.08251e-05 | 0.000151551 | 1.000 |
| order-service | 750 | 0.000 | 1.000 | 1.000 | 1.08251e-05 | 0.000151551 | 1.000 |
| order-service | 1000 | 0.000 | 1.000 | 1.000 | 1.08251e-05 | 0.000151551 | 1.000 |
| order-service | 1500 | 0.000 | 1.000 | 1.000 | 1.08251e-05 | 0.000151551 | 1.000 |
| order-service | 2000 | 0.000 | 1.000 | 1.000 | 1.08251e-05 | 0.000151551 | 1.000 |
| order-service | 2500 | 0.000 | 1.000 | 1.000 | 1.08251e-05 | 0.000151551 | 1.000 |

## Critérios de interpretação

- A taxa de detecção é a proporção de resultados com `is_reliable=true`.
- A taxa end-to-end também exige causa Top-1 e incidente corretos.
- O limiar empírico é o menor delay que atinge a taxa definida.
- O teste de Fisher compara proporções independentes de v1 e v2 em cada condição.
- Holm-Bonferroni corrige as comparações múltiplas.
- A campanha mede sensibilidade à magnitude; não mede ainda validade externa.
