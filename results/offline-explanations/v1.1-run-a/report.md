# Explicações auditáveis do CausalODS

- Explicações geradas: 500
- Decisões confiáveis: 300
- Decisões não confiáveis: 200
- Falhas de adaptação: 0
- Evidências por explicação: 10–13

## Cobertura por tipo de evidência

| Fonte | Itens | Explicações | Cobertura |
|---|---:|---:|---:|
| anomaly | 500 | 500 | 1.000 |
| graph | 500 | 500 | 1.000 |
| model | 500 | 500 | 1.000 |
| score_breakdown | 2500 | 500 | 1.000 |
| shap | 1250 | 500 | 1.000 |
| temporal | 500 | 500 | 1.000 |

## Amostra estratificada

### 0897024b-d437-4a07-ae45-625ec721529a

- Modelo: xgboost-rca-v1
- Confiável: False
- Serviço: order-service
- Confiança: 0.1954885656333551

O CausalODS classificou order-service como principal candidato com confiança 0.1955, mas a decisão não ultrapassou os critérios de confiabilidade.

**Decisão:** A decisão foi mantida como hipótese e não como incidente. Critérios não atendidos: minimum_confidence.

**Evidências:**

- [temporal] O serviço order-service obteve score temporal 0.972715; primeira falha/anomalia: False.
- [shap] O suporte SHAP agregado para order-service foi 0.000000.
- [model] O modelo atribuiu probabilidade 0.002364 ao serviço order-service.
- [anomaly] O serviço order-service obteve score de anomalia 0.000000.
- [graph] O serviço order-service obteve score estrutural 0.000000.
- [score_breakdown] Componente temporal_score contribuiu com 0.9727 para o serviço classificado.
- [score_breakdown] Componente model_probability contribuiu com 0.0024 para o serviço classificado.
- [score_breakdown] Componente anomaly_score contribuiu com 0.0000 para o serviço classificado.
- [score_breakdown] Componente graph_score contribuiu com 0.0000 para o serviço classificado.
- [score_breakdown] Componente shap_support_score contribuiu com 0.0000 para o serviço classificado.

**Limitações:**

- O resultado não ultrapassou o gate de confiabilidade.

### 08ad6722-b808-4fdd-898b-20d3b3ff3b82

- Modelo: xgboost-rca-v1
- Confiável: False
- Serviço: order-service
- Confiança: 0.19593318371457066

O CausalODS classificou order-service como principal candidato com confiança 0.1959, mas a decisão não ultrapassou os critérios de confiabilidade.

**Decisão:** A decisão foi mantida como hipótese e não como incidente. Critérios não atendidos: minimum_confidence.

**Evidências:**

- [temporal] O serviço order-service obteve score temporal 0.974938; primeira falha/anomalia: False.
- [shap] O suporte SHAP agregado para order-service foi 0.000000.
- [model] O modelo atribuiu probabilidade 0.002364 ao serviço order-service.
- [anomaly] O serviço order-service obteve score de anomalia 0.000000.
- [graph] O serviço order-service obteve score estrutural 0.000000.
- [score_breakdown] Componente temporal_score contribuiu com 0.9749 para o serviço classificado.
- [score_breakdown] Componente model_probability contribuiu com 0.0024 para o serviço classificado.
- [score_breakdown] Componente anomaly_score contribuiu com 0.0000 para o serviço classificado.
- [score_breakdown] Componente graph_score contribuiu com 0.0000 para o serviço classificado.
- [score_breakdown] Componente shap_support_score contribuiu com 0.0000 para o serviço classificado.

**Limitações:**

- O resultado não ultrapassou o gate de confiabilidade.

### 01f494e2-2efc-450d-bc6c-3ebfa8104610

- Modelo: xgboost-rca-v1
- Confiável: False
- Serviço: order-service
- Confiança: 0.0009456053376197815

O CausalODS classificou order-service como principal candidato com confiança 0.0009, mas a decisão não ultrapassou os critérios de confiabilidade.

**Decisão:** A decisão foi mantida como hipótese e não como incidente. Critérios não atendidos: minimum_confidence; minimum_margin.

**Evidências:**

- [temporal] O serviço order-service obteve score temporal 0.000000; primeira falha/anomalia: False.
- [shap] O suporte SHAP agregado para order-service foi 0.000000.
- [model] O modelo atribuiu probabilidade 0.002364 ao serviço order-service.
- [anomaly] O serviço order-service obteve score de anomalia 0.000000.
- [graph] O serviço order-service obteve score estrutural 0.000000.
- [score_breakdown] Componente model_probability contribuiu com 0.0024 para o serviço classificado.
- [score_breakdown] Componente anomaly_score contribuiu com 0.0000 para o serviço classificado.
- [score_breakdown] Componente graph_score contribuiu com 0.0000 para o serviço classificado.
- [score_breakdown] Componente shap_support_score contribuiu com 0.0000 para o serviço classificado.
- [score_breakdown] Componente temporal_score contribuiu com 0.0000 para o serviço classificado.

**Limitações:**

- O resultado não ultrapassou o gate de confiabilidade.

### 02dd586f-3d1e-43ae-9dde-29f6203f9408

- Modelo: xgboost-rca-v1
- Confiável: False
- Serviço: order-service
- Confiança: 0.0009456053376197815

O CausalODS classificou order-service como principal candidato com confiança 0.0009, mas a decisão não ultrapassou os critérios de confiabilidade.

**Decisão:** A decisão foi mantida como hipótese e não como incidente. Critérios não atendidos: minimum_confidence; minimum_margin.

**Evidências:**

- [temporal] O serviço order-service obteve score temporal 0.000000; primeira falha/anomalia: False.
- [shap] O suporte SHAP agregado para order-service foi 0.000000.
- [model] O modelo atribuiu probabilidade 0.002364 ao serviço order-service.
- [anomaly] O serviço order-service obteve score de anomalia 0.000000.
- [graph] O serviço order-service obteve score estrutural 0.000000.
- [score_breakdown] Componente model_probability contribuiu com 0.0024 para o serviço classificado.
- [score_breakdown] Componente anomaly_score contribuiu com 0.0000 para o serviço classificado.
- [score_breakdown] Componente graph_score contribuiu com 0.0000 para o serviço classificado.
- [score_breakdown] Componente shap_support_score contribuiu com 0.0000 para o serviço classificado.
- [score_breakdown] Componente temporal_score contribuiu com 0.0000 para o serviço classificado.

**Limitações:**

- O resultado não ultrapassou o gate de confiabilidade.

### 01ebfb50-3c07-4a20-8858-69e62b5de851

- Modelo: xgboost-rca-v1
- Confiável: False
- Serviço: payment-service
- Confiança: 0.19946046941991444

O CausalODS classificou payment-service como principal candidato com confiança 0.1995, mas a decisão não ultrapassou os critérios de confiabilidade.

**Decisão:** A decisão foi mantida como hipótese e não como incidente. Critérios não atendidos: minimum_confidence.

**Evidências:**

- [temporal] O serviço payment-service obteve score temporal 0.992574; primeira falha/anomalia: False.
- [shap] O suporte SHAP agregado para payment-service foi 0.000000.
- [model] O modelo atribuiu probabilidade 0.002364 ao serviço payment-service.
- [anomaly] O serviço payment-service obteve score de anomalia 0.000000.
- [graph] O serviço payment-service obteve score estrutural 0.000000.
- [score_breakdown] Componente temporal_score contribuiu com 0.9926 para o serviço classificado.
- [score_breakdown] Componente model_probability contribuiu com 0.0024 para o serviço classificado.
- [score_breakdown] Componente anomaly_score contribuiu com 0.0000 para o serviço classificado.
- [score_breakdown] Componente graph_score contribuiu com 0.0000 para o serviço classificado.
- [score_breakdown] Componente shap_support_score contribuiu com 0.0000 para o serviço classificado.

**Limitações:**

- O resultado não ultrapassou o gate de confiabilidade.

### 04568875-8ac1-4c1d-81de-a8f90130df01

- Modelo: xgboost-rca-v1
- Confiável: False
- Serviço: payment-service
- Confiança: 0.1994873737725531

O CausalODS classificou payment-service como principal candidato com confiança 0.1995, mas a decisão não ultrapassou os critérios de confiabilidade.

**Decisão:** A decisão foi mantida como hipótese e não como incidente. Critérios não atendidos: minimum_confidence.

**Evidências:**

- [temporal] O serviço payment-service obteve score temporal 0.992709; primeira falha/anomalia: False.
- [shap] O suporte SHAP agregado para payment-service foi 0.000000.
- [model] O modelo atribuiu probabilidade 0.002364 ao serviço payment-service.
- [anomaly] O serviço payment-service obteve score de anomalia 0.000000.
- [graph] O serviço payment-service obteve score estrutural 0.000000.
- [score_breakdown] Componente temporal_score contribuiu com 0.9927 para o serviço classificado.
- [score_breakdown] Componente model_probability contribuiu com 0.0024 para o serviço classificado.
- [score_breakdown] Componente anomaly_score contribuiu com 0.0000 para o serviço classificado.
- [score_breakdown] Componente graph_score contribuiu com 0.0000 para o serviço classificado.
- [score_breakdown] Componente shap_support_score contribuiu com 0.0000 para o serviço classificado.

**Limitações:**

- O resultado não ultrapassou o gate de confiabilidade.

### 16316e43-02c3-468f-b556-185e91371a61

- Modelo: xgboost-rca-v1
- Confiável: True
- Serviço: order-service
- Confiança: 0.5990543842315674

O CausalODS classificou order-service como causa raiz com confiança 0.5991 e considerou a decisão confiável.

**Decisão:** A decisão pode acionar o fluxo operacional de incidente, preservando a proveniência do RCAResult.

**Evidências:**

- [temporal] O serviço order-service obteve score temporal 1.000000; primeira falha/anomalia: True.
- [shap] O suporte SHAP agregado para order-service foi 0.000000.
- [model] O modelo atribuiu probabilidade 0.997636 ao serviço order-service.
- [anomaly] O serviço order-service obteve score de anomalia 0.000000.
- [graph] O serviço order-service obteve score estrutural 0.000000.
- [score_breakdown] Componente temporal_score contribuiu com 1.0000 para o serviço classificado.
- [score_breakdown] Componente model_probability contribuiu com 0.9976 para o serviço classificado.
- [score_breakdown] Componente anomaly_score contribuiu com 0.0000 para o serviço classificado.
- [score_breakdown] Componente graph_score contribuiu com 0.0000 para o serviço classificado.
- [score_breakdown] Componente shap_support_score contribuiu com 0.0000 para o serviço classificado.

### 1c8f8746-e752-475f-a1c6-e99ac9376aad

- Modelo: xgboost-rca-v1
- Confiável: True
- Serviço: order-service
- Confiança: 0.5990543842315674

O CausalODS classificou order-service como causa raiz com confiança 0.5991 e considerou a decisão confiável.

**Decisão:** A decisão pode acionar o fluxo operacional de incidente, preservando a proveniência do RCAResult.

**Evidências:**

- [temporal] O serviço order-service obteve score temporal 1.000000; primeira falha/anomalia: True.
- [shap] O suporte SHAP agregado para order-service foi 0.000000.
- [model] O modelo atribuiu probabilidade 0.997636 ao serviço order-service.
- [anomaly] O serviço order-service obteve score de anomalia 0.000000.
- [graph] O serviço order-service obteve score estrutural 0.000000.
- [score_breakdown] Componente temporal_score contribuiu com 1.0000 para o serviço classificado.
- [score_breakdown] Componente model_probability contribuiu com 0.9976 para o serviço classificado.
- [score_breakdown] Componente anomaly_score contribuiu com 0.0000 para o serviço classificado.
- [score_breakdown] Componente graph_score contribuiu com 0.0000 para o serviço classificado.
- [score_breakdown] Componente shap_support_score contribuiu com 0.0000 para o serviço classificado.

### 03313082-81ac-4a69-aaaa-25b5bc477c49

- Modelo: xgboost-rca-v1
- Confiável: True
- Serviço: payment-service
- Confiança: 0.5690543842315674

O CausalODS classificou payment-service como causa raiz com confiança 0.5691 e considerou a decisão confiável.

**Decisão:** A decisão pode acionar o fluxo operacional de incidente, preservando a proveniência do RCAResult.

**Evidências:**

- [temporal] O serviço payment-service obteve score temporal 0.850000; primeira falha/anomalia: True.
- [shap] O suporte SHAP agregado para payment-service foi 0.000000.
- [model] O modelo atribuiu probabilidade 0.997636 ao serviço payment-service.
- [anomaly] O serviço payment-service obteve score de anomalia 0.000000.
- [graph] O serviço payment-service obteve score estrutural 0.000000.
- [score_breakdown] Componente model_probability contribuiu com 0.9976 para o serviço classificado.
- [score_breakdown] Componente temporal_score contribuiu com 0.8500 para o serviço classificado.
- [score_breakdown] Componente anomaly_score contribuiu com 0.0000 para o serviço classificado.
- [score_breakdown] Componente graph_score contribuiu com 0.0000 para o serviço classificado.
- [score_breakdown] Componente shap_support_score contribuiu com 0.0000 para o serviço classificado.

### 05c48498-9e65-4cb8-a7e6-51392ade66dd

- Modelo: xgboost-rca-v1
- Confiável: True
- Serviço: payment-service
- Confiança: 0.5690543842315674

O CausalODS classificou payment-service como causa raiz com confiança 0.5691 e considerou a decisão confiável.

**Decisão:** A decisão pode acionar o fluxo operacional de incidente, preservando a proveniência do RCAResult.

**Evidências:**

- [temporal] O serviço payment-service obteve score temporal 0.850000; primeira falha/anomalia: True.
- [shap] O suporte SHAP agregado para payment-service foi 0.000000.
- [model] O modelo atribuiu probabilidade 0.997636 ao serviço payment-service.
- [anomaly] O serviço payment-service obteve score de anomalia 0.000000.
- [graph] O serviço payment-service obteve score estrutural 0.000000.
- [score_breakdown] Componente model_probability contribuiu com 0.9976 para o serviço classificado.
- [score_breakdown] Componente temporal_score contribuiu com 0.8500 para o serviço classificado.
- [score_breakdown] Componente anomaly_score contribuiu com 0.0000 para o serviço classificado.
- [score_breakdown] Componente graph_score contribuiu com 0.0000 para o serviço classificado.
- [score_breakdown] Componente shap_support_score contribuiu com 0.0000 para o serviço classificado.

### 0a05f402-edb8-48f7-a34e-8a36af5d57c1

- Modelo: xgboost-rca-v2
- Confiável: False
- Serviço: order-service
- Confiança: 0.01919883378328181

O CausalODS classificou order-service como principal candidato com confiança 0.0192, mas a decisão não ultrapassou os critérios de confiabilidade.

**Decisão:** A decisão foi mantida como hipótese e não como incidente. Critérios não atendidos: minimum_confidence; minimum_margin.

**Evidências:**

- [temporal] O serviço order-service obteve score temporal 0.000000; primeira falha/anomalia: False.
- [shap] A feature avg_latency_ms apresentou contribuição SHAP -1.993213 para o serviço order-service.
- [model] O modelo atribuiu probabilidade 0.000350 ao serviço order-service.
- [anomaly] O serviço order-service obteve score de anomalia 0.000000.
- [graph] O serviço order-service obteve score estrutural 0.000000.
- [shap] A feature error_count apresentou contribuição SHAP -0.682862 para o serviço order-service.
- [shap] A feature exclusive_latency_ms apresentou contribuição SHAP -3.659267 para o serviço order-service.
- [shap] A feature exclusive_latency_ratio apresentou contribuição SHAP 0.708824 para o serviço order-service.
- [score_breakdown] Componente shap_support_score contribuiu com 0.0762 para o serviço classificado.
- [score_breakdown] Componente model_probability contribuiu com 0.0004 para o serviço classificado.
- [score_breakdown] Componente anomaly_score contribuiu com 0.0000 para o serviço classificado.
- [score_breakdown] Componente graph_score contribuiu com 0.0000 para o serviço classificado.
- [score_breakdown] Componente temporal_score contribuiu com 0.0000 para o serviço classificado.

**Limitações:**

- O resultado não ultrapassou o gate de confiabilidade.

### 0d7700a1-201e-440d-9894-69e1ea351708

- Modelo: xgboost-rca-v2
- Confiável: False
- Serviço: order-service
- Confiança: 0.01919883378328181

O CausalODS classificou order-service como principal candidato com confiança 0.0192, mas a decisão não ultrapassou os critérios de confiabilidade.

**Decisão:** A decisão foi mantida como hipótese e não como incidente. Critérios não atendidos: minimum_confidence; minimum_margin.

**Evidências:**

- [temporal] O serviço order-service obteve score temporal 0.000000; primeira falha/anomalia: False.
- [shap] A feature avg_latency_ms apresentou contribuição SHAP -1.993213 para o serviço order-service.
- [model] O modelo atribuiu probabilidade 0.000350 ao serviço order-service.
- [anomaly] O serviço order-service obteve score de anomalia 0.000000.
- [graph] O serviço order-service obteve score estrutural 0.000000.
- [shap] A feature error_count apresentou contribuição SHAP -0.682862 para o serviço order-service.
- [shap] A feature exclusive_latency_ms apresentou contribuição SHAP -3.659267 para o serviço order-service.
- [shap] A feature exclusive_latency_ratio apresentou contribuição SHAP 0.708824 para o serviço order-service.
- [score_breakdown] Componente shap_support_score contribuiu com 0.0762 para o serviço classificado.
- [score_breakdown] Componente model_probability contribuiu com 0.0004 para o serviço classificado.
- [score_breakdown] Componente anomaly_score contribuiu com 0.0000 para o serviço classificado.
- [score_breakdown] Componente graph_score contribuiu com 0.0000 para o serviço classificado.
- [score_breakdown] Componente temporal_score contribuiu com 0.0000 para o serviço classificado.

**Limitações:**

- O resultado não ultrapassou o gate de confiabilidade.

### 028d39d4-effa-428b-b68e-5604dd2aaf6c

- Modelo: xgboost-rca-v2
- Confiável: True
- Serviço: order-service
- Confiança: 0.8162961138917156

O CausalODS classificou order-service como causa raiz com confiança 0.8163 e considerou a decisão confiável.

**Decisão:** A decisão pode acionar o fluxo operacional de incidente, preservando a proveniência do RCAResult.

**Evidências:**

- [temporal] O serviço order-service obteve score temporal 0.971915; primeira falha/anomalia: False.
- [shap] A feature avg_latency_ms apresentou contribuição SHAP 1.496979 para o serviço order-service.
- [model] O modelo atribuiu probabilidade 0.999823 ao serviço order-service.
- [anomaly] O serviço order-service obteve score de anomalia 0.000000.
- [graph] O serviço order-service obteve score estrutural 0.000000.
- [shap] A feature error_count apresentou contribuição SHAP -0.203156 para o serviço order-service.
- [shap] A feature exclusive_latency_ms apresentou contribuição SHAP 7.356163 para o serviço order-service.
- [shap] A feature exclusive_latency_ratio apresentou contribuição SHAP 0.886676 para o serviço order-service.
- [score_breakdown] Componente model_probability contribuiu com 0.9998 para o serviço classificado.
- [score_breakdown] Componente temporal_score contribuiu com 0.9719 para o serviço classificado.
- [score_breakdown] Componente shap_support_score contribuiu com 0.8879 para o serviço classificado.
- [score_breakdown] Componente anomaly_score contribuiu com 0.0000 para o serviço classificado.
- [score_breakdown] Componente graph_score contribuiu com 0.0000 para o serviço classificado.

### 04068d01-51ac-45ac-be14-257c42ff550f

- Modelo: xgboost-rca-v2
- Confiável: True
- Serviço: order-service
- Confiança: 0.800033828558584

O CausalODS classificou order-service como causa raiz com confiança 0.8000 e considerou a decisão confiável.

**Decisão:** A decisão pode acionar o fluxo operacional de incidente, preservando a proveniência do RCAResult.

**Evidências:**

- [temporal] O serviço order-service obteve score temporal 1.000000; primeira falha/anomalia: True.
- [shap] A feature avg_latency_ms apresentou contribuição SHAP -1.066426 para o serviço order-service.
- [model] O modelo atribuiu probabilidade 0.999593 ao serviço order-service.
- [anomaly] O serviço order-service obteve score de anomalia 0.000000.
- [graph] O serviço order-service obteve score estrutural 0.000000.
- [shap] A feature error_count apresentou contribuição SHAP 1.424204 para o serviço order-service.
- [shap] A feature exclusive_latency_ms apresentou contribuição SHAP -1.429205 para o serviço order-service.
- [shap] A feature exclusive_latency_ratio apresentou contribuição SHAP 0.292523 para o serviço order-service.
- [score_breakdown] Componente temporal_score contribuiu com 1.0000 para o serviço classificado.
- [score_breakdown] Componente model_probability contribuiu com 0.9996 para o serviço classificado.
- [score_breakdown] Componente shap_support_score contribuiu com 0.8008 para o serviço classificado.
- [score_breakdown] Componente anomaly_score contribuiu com 0.0000 para o serviço classificado.
- [score_breakdown] Componente graph_score contribuiu com 0.0000 para o serviço classificado.

### 005714a6-777b-4e31-b22a-db8a1af35b38

- Modelo: xgboost-rca-v2
- Confiável: True
- Serviço: payment-service
- Confiança: 0.8217754064439825

O CausalODS classificou payment-service como causa raiz com confiança 0.8218 e considerou a decisão confiável.

**Decisão:** A decisão pode acionar o fluxo operacional de incidente, preservando a proveniência do RCAResult.

**Evidências:**

- [temporal] O serviço payment-service obteve score temporal 0.988410; primeira falha/anomalia: False.
- [shap] A feature avg_latency_ms apresentou contribuição SHAP 1.519012 para o serviço payment-service.
- [model] O modelo atribuiu probabilidade 0.999848 ao serviço payment-service.
- [anomaly] O serviço payment-service obteve score de anomalia 0.000000.
- [graph] O serviço payment-service obteve score estrutural 0.000000.
- [shap] A feature error_count apresentou contribuição SHAP -0.204623 para o serviço payment-service.
- [shap] A feature exclusive_latency_ms apresentou contribuição SHAP 7.223567 para o serviço payment-service.
- [shap] A feature exclusive_latency_ratio apresentou contribuição SHAP 0.708857 para o serviço payment-service.
- [score_breakdown] Componente model_probability contribuiu com 0.9998 para o serviço classificado.
- [score_breakdown] Componente temporal_score contribuiu com 0.9884 para o serviço classificado.
- [score_breakdown] Componente shap_support_score contribuiu com 0.8966 para o serviço classificado.
- [score_breakdown] Componente anomaly_score contribuiu com 0.0000 para o serviço classificado.
- [score_breakdown] Componente graph_score contribuiu com 0.0000 para o serviço classificado.

### 03d041a6-ca09-4707-916a-a2cd6293b9ea

- Modelo: xgboost-rca-v2
- Confiável: True
- Serviço: payment-service
- Confiança: 0.8220274870712576

O CausalODS classificou payment-service como causa raiz com confiança 0.8220 e considerou a decisão confiável.

**Decisão:** A decisão pode acionar o fluxo operacional de incidente, preservando a proveniência do RCAResult.

**Evidências:**

- [temporal] O serviço payment-service obteve score temporal 0.989670; primeira falha/anomalia: False.
- [shap] A feature avg_latency_ms apresentou contribuição SHAP 1.519012 para o serviço payment-service.
- [model] O modelo atribuiu probabilidade 0.999848 ao serviço payment-service.
- [anomaly] O serviço payment-service obteve score de anomalia 0.000000.
- [graph] O serviço payment-service obteve score estrutural 0.000000.
- [shap] A feature error_count apresentou contribuição SHAP -0.204623 para o serviço payment-service.
- [shap] A feature exclusive_latency_ms apresentou contribuição SHAP 7.223567 para o serviço payment-service.
- [shap] A feature exclusive_latency_ratio apresentou contribuição SHAP 0.708857 para o serviço payment-service.
- [score_breakdown] Componente model_probability contribuiu com 0.9998 para o serviço classificado.
- [score_breakdown] Componente temporal_score contribuiu com 0.9897 para o serviço classificado.
- [score_breakdown] Componente shap_support_score contribuiu com 0.8966 para o serviço classificado.
- [score_breakdown] Componente anomaly_score contribuiu com 0.0000 para o serviço classificado.
- [score_breakdown] Componente graph_score contribuiu com 0.0000 para o serviço classificado.
