# Runbook da plataforma v3

Execute na raiz do repositório.

## Subir, verificar e parar

```bash
PYTHONPATH=correlation-engine .venv-host/bin/python -m platform_v3 up
PYTHONPATH=correlation-engine .venv-host/bin/python -m platform_v3 health
PYTHONPATH=correlation-engine .venv-host/bin/python -m platform_v3 down
```

Equivalente Compose:

```bash
docker compose -f docker-compose.yml -f docker-compose.platform-v3.yml up -d
docker compose -f docker-compose.yml -f docker-compose.platform-v3.yml ps
docker compose -f docker-compose.yml -f docker-compose.platform-v3.yml down
```

Não use `down -v`: os volumes não devem ser removidos sem autorização.

Reset não destrutivo do ambiente v3:

```bash
PYTHONPATH=correlation-engine .venv-host/bin/python -m platform_v3 down
PYTHONPATH=correlation-engine .venv-host/bin/python -m platform_v3 up
```

## Cenários operacionais

Healthy:

```bash
curl -sS -i -X POST http://localhost:8081/orders \
  -H 'Content-Type: application/json' \
  -H 'X-Correlation-Id: healthy-manual-v3' \
  -d '{"product":"Healthy validation","quantity":1}'
```

Fault controlado no payment-service (restaure `none` ao terminar):

```bash
CAUSALODS_PAYMENT_FAULT_MODE=error docker compose -f docker-compose.yml -f docker-compose.platform-v3.yml up -d --force-recreate payment-service
curl -sS -i -X POST http://localhost:8081/orders -H 'Content-Type: application/json' -H 'X-Correlation-Id: payment-failure-manual-v3' -d '{"product":"Fault validation","quantity":1}'
CAUSALODS_PAYMENT_FAULT_MODE=none docker compose -f docker-compose.yml -f docker-compose.platform-v3.yml up -d --force-recreate payment-service
```

Latency usa o mesmo fluxo com
`CAUSALODS_PAYMENT_FAULT_MODE=latency` e
`CAUSALODS_PAYMENT_FAULT_DELAY_MS=750`. Esses cenários validam o caminho até
Tempo; enquanto o runtime multimodal/modelo v3 estiver desconectado, não são o
fault E2E requerido pelo release gate.

## Smoke, validação e experimento reduzido

```bash
PYTHONPATH=correlation-engine .venv-host/bin/python -m platform_v3 smoke
PYTHONPATH=correlation-engine .venv-host/bin/python -m platform_v3 seed-demo
PYTHONPATH=correlation-engine .venv-host/bin/python -m platform_v3 validate --live --output /tmp/causalods-platform-v3-live
PYTHONPATH=.:correlation-engine .venv-host/bin/python -m experiments.multimodal_v3.cli smoke --campaign-id prompt12-smoke --output /tmp/causalods-prompt12-experiment
PYTHONPATH=correlation-engine .venv-host/bin/python -m platform_v3 seed-demo --campaign-root /tmp/causalods-prompt12-experiment/prompt12-smoke
```

Nenhum desses comandos executa a campanha científica completa ou live LLM.

## Acesso e consultas

- Grafana: `http://localhost:3000`;
- OpenSearch health: `curl -fsS http://localhost:9200/_cluster/health`;
- Tempo ready: `curl -fsS http://localhost:3200/ready`;
- Prometheus: `http://localhost:9090`.

```bash
curl -fsS 'http://localhost:9200/causalods-v3-incidents/_search?q=incident_id:INCIDENT_ID'
curl -fsS 'http://localhost:9200/causalods-v3-unified-events/_search?q=trace_id:TRACE_ID'
curl -fsS 'http://localhost:9200/causalods-v3-unified-events/_search?q=correlation_id:CORRELATION_ID'
curl -fsS 'http://localhost:9200/causalods-v3-rca-results/_search?q=incident_id:INCIDENT_ID'
curl -fsS 'http://localhost:9200/causalods-v3-explanations/_search?q=incident_id:INCIDENT_ID'
curl -fsS --get 'http://localhost:3200/api/search' --data-urlencode 'q={ resource.service.name = "order-service" }'
curl -fsS 'http://localhost:3200/api/traces/TRACE_ID'
```

Os cenários healthy/fault/latency live continuam bloqueados até o runtime v3
possuir um runner que injete a falha e encadeie o sink multimodal até os índices.
