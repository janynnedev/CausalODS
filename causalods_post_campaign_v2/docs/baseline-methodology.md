# Metodologia dos baselines

## Baseline 1 — maior correlação temporal

Seleciona o serviço com maior `temporal_score` registrado pelo `TemporalCorrelator` antes da composição do ranking final.

Desempate:

1. menor rank temporal;
2. nome do serviço em ordem lexicográfica.

O baseline abstém quando não existe evidência temporal ou quando o maior score não é estritamente positivo.

## Baseline 2 — maior z-score de anomalia

Seleciona o serviço com maior `zscore` nas evidências de anomalia.

O baseline abstém quando:

- não existe evidência de anomalia;
- todos os z-scores são menores ou iguais a zero.

Essa regra evita transformar um empate saudável em uma causa raiz arbitrária.

## Baseline 3 — primeiro serviço anômalo no tempo

Seleciona a evidência temporal com o menor `anomaly_started_at` ou `observed_at`.

Quando a evidência é explicitamente marcada como `is_first_anomaly=True`, ela continua elegível mesmo sem timestamp; nesse caso, rank e nome do serviço são usados para desempate determinístico.

## Ground truth

```text
healthy          → None
payment_failure  → payment-service
payment_latency  → payment-service
order_failure    → order-service
order_latency    → order-service
```

## Métricas

Nos quatro cenários de falha:

```text
accuracy = previsões corretas / execuções de falha
```

No saudável:

```text
abstention rate = execuções sem previsão / execuções saudáveis
```

Uma abstenção durante falha é incorreta. Uma abstenção saudável é correta.

## Prevenção de vazamento

Os baselines não leem:

- `root_cause_service` do ranking final;
- `final_score` do CausalODS;
- incidente criado;
- decisão de confiabilidade.

Eles usam somente as evidências temporais e de anomalia preservadas no `RCAResult`.
