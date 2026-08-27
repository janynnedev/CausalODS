# RCAEval heavy gate plan v3

Do not run the two incidents concurrently. First resolve both official bindings:

```bash
PYTHONPATH="$PWD:$PWD/correlation-engine" correlation-engine/.venv/bin/python -m experiments.final_campaigns_v3 resolve-preprocessing-binding --registry experiments/configs/final-v3/datasets.json --config-dir experiments/configs/final-v3 --model-specification experiments/configs/final-v3/scientific-model-specification.json --incident-id rcaeval:re3tt_ts-route-service_f3_6 --protocol within-dataset --seed 42
PYTHONPATH="$PWD:$PWD/correlation-engine" correlation-engine/.venv/bin/python -m experiments.final_campaigns_v3 resolve-preprocessing-binding --registry experiments/configs/final-v3/datasets.json --config-dir experiments/configs/final-v3 --model-specification experiments/configs/final-v3/scientific-model-specification.json --incident-id rcaeval:re2tt_ts-route-service_socket_1 --protocol within-dataset --seed 42
```

Record `base_key`, `split_key`, train/validation/test sizes and incident split. The official preprocessing gate output is `artifacts/validation/rcaeval-heavy-preprocessing-gate-v3`.

Use this rule literally:

```text
if base_key A == base_key B AND split_key A == split_key B:
  prepare baseline once
else:
  prepare each binding.
```

Binding A (`rcaeval:re3tt_ts-route-service_f3_6`):

```bash
PYTHONPATH="$PWD:$PWD/correlation-engine" /usr/bin/time -v correlation-engine/.venv/bin/python -m experiments.final_campaigns_v3 prepare-baseline --resume --registry experiments/configs/final-v3/datasets.json --config-dir experiments/configs/final-v3 --model-specification experiments/configs/final-v3/scientific-model-specification.json --rcaeval-cache /home/janynne/mnt/rcaeval-official-v3 --protocol within-dataset --seed 42 --incident-id rcaeval:re3tt_ts-route-service_f3_6 --output artifacts/validation/rcaeval-heavy-preprocessing-gate-v3
PYTHONPATH="$PWD:$PWD/correlation-engine" /usr/bin/time -v correlation-engine/.venv/bin/python -m experiments.final_campaigns_v3 preprocess-incident --resume --registry experiments/configs/final-v3/datasets.json --config-dir experiments/configs/final-v3 --model-specification experiments/configs/final-v3/scientific-model-specification.json --rcaeval-cache /home/janynne/mnt/rcaeval-official-v3 --protocol within-dataset --seed 42 --incident-id rcaeval:re3tt_ts-route-service_f3_6 --output artifacts/validation/rcaeval-heavy-preprocessing-gate-v3
```

Binding B (`rcaeval:re2tt_ts-route-service_socket_1`):

```bash
PYTHONPATH="$PWD:$PWD/correlation-engine" /usr/bin/time -v correlation-engine/.venv/bin/python -m experiments.final_campaigns_v3 prepare-baseline --resume --registry experiments/configs/final-v3/datasets.json --config-dir experiments/configs/final-v3 --model-specification experiments/configs/final-v3/scientific-model-specification.json --rcaeval-cache /home/janynne/mnt/rcaeval-official-v3 --protocol within-dataset --seed 42 --incident-id rcaeval:re2tt_ts-route-service_socket_1 --output artifacts/validation/rcaeval-heavy-preprocessing-gate-v3
PYTHONPATH="$PWD:$PWD/correlation-engine" /usr/bin/time -v correlation-engine/.venv/bin/python -m experiments.final_campaigns_v3 preprocess-incident --resume --registry experiments/configs/final-v3/datasets.json --config-dir experiments/configs/final-v3 --model-specification experiments/configs/final-v3/scientific-model-specification.json --rcaeval-cache /home/janynne/mnt/rcaeval-official-v3 --protocol within-dataset --seed 42 --incident-id rcaeval:re2tt_ts-route-service_socket_1 --output artifacts/validation/rcaeval-heavy-preprocessing-gate-v3
```

Run incident commands serially. Record `/usr/bin/time -v` coordinator RSS separately from checkpoint worker `peak_rss_kb`, plus elapsed time, return code/signal and checkpoint integrity.
