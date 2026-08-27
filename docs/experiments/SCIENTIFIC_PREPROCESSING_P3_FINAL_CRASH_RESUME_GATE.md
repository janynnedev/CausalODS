# SCIENTIFIC_PREPROCESSING_P3_FINAL_CRASH_RESUME_GATE

| Requirement | Result | Evidence |
| --- | --- | --- |
| Resume/checkpoint contracts | PASS | Snapshot, contribution and scientific incident payload validators are centralized. |
| Deterministic host fixture | PASS | Six temporary incidents, `within-dataset`, seed 42, traces/metrics/logs and positive ground truth. |
| Host SIGTERM harness | READY FOR HOST | `experiments/scripts/run_scientific_preprocessing_resume_gate_v3.py` sends SIGTERM to coordinator PID only. |
| Process-group quiescence | PASS | Linux `/proc` helper observes the coordinator PGID without signalling the group; sandbox-safe natural-exit unit test passed. |
| Feature worker failure/resume | PASS | Forced non-initial incident failure persisted `FAILED_WORKER/FEATURES/FEATURE`, preserved earlier checkpoints, resumed with hits and matched the clean inventory. |
| Incident/full interoperability | PASS | `preprocess-incident --resume` checkpoint was reused by full `preprocess --resume` under the sole compatible `base_key`. |
| Durable baseline progress | PASS | Captured state writes were monotonic through every training incident and finalized with the official baseline hash/bindings. |
| Corruption recovery | PASS | Invalid scientific checkpoint was rejected, recomputed and the final inventory matched the clean oracle. |
| RCAEval stratified builder/runner | READY FOR HOST | Selected 16-manifest and metadata snapshots are immutable; runner validates preflight, binding, checkpoints, worker RSS and inventory. |
| Sandbox-safe validation | PASS | 58 consolidated tests plus 1 standalone corruption/rebuild test passed (59 executed checks). |
| Golden artifacts | PASS | 7/7 in the regression suite. |
| Compile/diff | PASS | `compileall` and `git diff --check` passed. |
| V2 freeze | PASS | 12/12. |
| V2 -> V3 imports | PASS | 0. |

The host runner is intentionally not run in the Codex sandbox, whose PID namespace forwards coordinator SIGTERM to the test executor. Run it on the host with:

```bash
PYTHONPATH="$PWD:$PWD/correlation-engine" \
correlation-engine/.venv/bin/python \
experiments/scripts/run_scientific_preprocessing_resume_gate_v3.py
```

The real host SIGTERM harness and real RCAEval preprocessing were deliberately
not executed in the sandbox.

| Release item | Result |
| --- | --- |
| A | PASS |
| B | PASS |
| C | PASS |
| D | PASS |
| E | PASS |
| F | PASS |
| G | PASS |
| H | PASS |
| I | PASS |
| J | PASS |
| K | PASS |
| L | PASS |
| M | PASS |
| N | PASS |
| P | PASS |

P3_IMPLEMENTATION = PASS
HOST_RELEASE_HARNESS = READY_TO_RUN_ON_HOST
RCAEVAL_STRATIFIED_GATE = READY_TO_RUN_ON_HOST
READY_FOR_HEAVY_INCIDENT_GATE = NO
READY_FOR_FULL_PREPROCESS_RESUME = NO
