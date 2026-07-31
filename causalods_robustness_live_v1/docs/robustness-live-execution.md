# Execução da robustez live

## Pré-flight

```bash
docker compose up -d
RCA_MODEL_VERSION=v2 docker compose up -d --force-recreate --no-deps correlation-engine

docker compose ps
docker compose exec -T correlation-engine printenv RCA_MODEL_VERSION
curl -fs http://localhost:9200 >/dev/null
```

## Testes locais

```bash
PYTHONPATH=correlation-engine:experiments \
python -m pytest \
  correlation-engine/tests/test_robustness_*.py \
  experiments/tests/test_robustness_live_*.py \
  -q
```

## Smoke

```bash
python experiments/run_robustness_live.py \
  --campaign-id robustness-live-smoke-v1 \
  --repeat 1
```

```bash
python experiments/analyze_robustness_live.py \
  --campaign-dir results/robustness/live/robustness-live-smoke-v1
```

## Campanha final

```bash
./experiments/run_robustness_live.sh start \
  --campaign-id robustness-live-v2-repeat10 \
  --repeat 10
```

```bash
./experiments/run_robustness_live.sh status
./experiments/run_robustness_live.sh logs
```

## Validação final

```bash
python experiments/analyze_robustness_live.py \
  --campaign-dir results/robustness/live/robustness-live-v2-repeat10

(cd results/robustness/live/robustness-live-v2-repeat10 && sha256sum -c SHA256SUMS)
```
