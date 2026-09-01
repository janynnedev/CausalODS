"""Read-only host preflight for the frozen H3 provider; never executes H3 or an LLM."""

from __future__ import annotations

import os
import resource
import tempfile
from pathlib import Path

from experiments.causalods_campaign_bindings import build_executors

from .core import FrozenDatasetRegistry, build_plan, load_campaign_config
from .providers import FrozenRawTelemetryMapping


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CONFIG_ROOT = REPOSITORY_ROOT / "experiments/configs/final-v3"


def _rss_kb() -> int:
    status = Path("/proc/self/status")
    if status.is_file():
        for line in status.read_text(encoding="utf-8").splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1])
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss


def main() -> None:
    payload = load_campaign_config(CONFIG_ROOT / "h3.json")
    registry = FrozenDatasetRegistry.load(CONFIG_ROOT / "datasets.json", REPOSITORY_ROOT)
    config, runs = build_plan(payload, registry.capabilities())
    os.environ.setdefault("CAUSALODS_LLM_MODEL", "h3-wiring-preflight-only")
    os.environ.setdefault("CAUSALODS_LLM_ENDPOINT", "http://127.0.0.1:1")
    os.environ.setdefault("CAUSALODS_LLM_API_KEY", "h3-wiring-preflight-only")
    with tempfile.TemporaryDirectory(prefix="causalods-h3-wiring-") as temporary:
        executors = build_executors(
            config=config, output=Path(temporary), repository_root=REPOSITORY_ROOT)
    delegate = executors["H3"].delegate
    rca_results = delegate.rca_results
    raw = delegate.raw_telemetry
    if not isinstance(raw, FrozenRawTelemetryMapping):
        raise RuntimeError("H3_RAW_TELEMETRY_NOT_LAZY")
    print("H3_FINAL_WIRING_PREFLIGHT = PASS")
    print("H3_PROVIDER_LOAD = PASS")
    print("H3_RAW_TELEMETRY_LAZY = PASS")
    print(f"H3_PLANNED_RUNS = {len(runs)}")
    print(f"H3_ORIGINAL_PLAN_INCIDENTS = {len({run.incident_id for run in runs})}")
    print(f"H3_PROVIDER_RCA_RESULTS = {len(rca_results)}")
    print(f"H3_PROVIDER_RAW_DESCRIPTORS = {len(raw)}")
    print(f"RSS_AFTER_PROVIDER_KB = {_rss_kb()}")

    print(f"PEAK_RSS_KB = {resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}")
    ready = len(runs) == 120 and len(rca_results) == len(raw) == 40
    print(f"READY_FOR_REAL_H3 = {'YES' if ready else 'NO'}")


if __name__ == "__main__":
    main()
