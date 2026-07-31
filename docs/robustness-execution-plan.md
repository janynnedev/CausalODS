# Plano de execução da robustez

## Agora, enquanto a sensibilidade roda

Executar apenas:

```bash
PYTHONPATH=correlation-engine \
python -m pytest \
  correlation-engine/tests/test_robustness_*.py \
  -q
```

```bash
python \
  experiments/run_robustness_offline.py \
  --timeout-ms 5000 \
  --output-dir \
  results/robustness/offline-foundation-v1
```

```bash
python \
  experiments/build_robustness_live_plan.py \
  --repeat 10 \
  --output \
  results/robustness/live-plan-v1.json
```

Não executar falhas Docker.

## Depois da campanha de sensibilidade

1. validar `status.json`;
2. executar a análise de sensibilidade;
3. preservar checksums;
4. fazer merge das branches concluídas;
5. implementar os adaptadores live;
6. executar smoke com uma repetição;
7. executar 10 repetições por cenário.

## Adaptadores live que serão ligados ao plano

- `KafkaBacklogDriver`;
- `OpenSearchOutageDriver`;
- `CorrelationEngineRestartDriver`;
- `RCAResultProbe`;
- `IncidentProbe`;
- `ProvenanceProbe`;
- `RecoveryProbe`.

A interface de saída desses adaptadores deve produzir `LiveObservation`, já
implementado nesta fundação.
