"""Read-only host preflight for the frozen H3 provider; never executes H3 or an LLM."""

from __future__ import annotations

import gc
import json
import resource
from pathlib import Path

from .core import FrozenDatasetRegistry, build_plan, load_campaign_config
from .providers import FrozenRawTelemetryMapping, load_h3_inputs


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
    supplied = load_h3_inputs(config=config, repository_root=REPOSITORY_ROOT)
    raw = supplied["raw_telemetry"]
    if not isinstance(raw, FrozenRawTelemetryMapping):
        raise RuntimeError("H3_RAW_TELEMETRY_NOT_LAZY")
    print("H3_PROVIDER_LOAD = PASS")
    print("H3_RAW_TELEMETRY_LAZY = PASS")
    print(f"H3_PLANNED_RUNS = {len(runs)}")
    print(f"H3_PROVIDER_RCA_RESULTS = {len(supplied['rca_results'])}")
    print(f"H3_PROVIDER_RAW_DESCRIPTORS = {len(raw)}")
    print(f"RSS_AFTER_PROVIDER_KB = {_rss_kb()}")

    index_path = REPOSITORY_ROOT / payload["criteria"]["scientific_rca_results"]
    index = json.loads(index_path.read_text(encoding="utf-8"))
    smallest = min(index["incidents"], key=lambda item: (
        (REPOSITORY_ROOT / item["raw_telemetry_path"]).stat().st_size,
        item["incident_id"],
    ))
    decoded = raw[smallest["incident_id"]]
    print(f"REPRESENTATIVE_INCIDENT = {smallest['incident_id']}")
    print(f"REPRESENTATIVE_EVENTS = {len(decoded)}")
    print(f"RSS_AFTER_REPRESENTATIVE_KB = {_rss_kb()}")
    del decoded
    gc.collect()
    print(f"RSS_AFTER_RELEASE_KB = {_rss_kb()}")
    print(f"PEAK_RSS_KB = {resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}")
    ready = len(runs) == 120 and len(supplied["rca_results"]) == len(raw) == 40
    print(f"READY_FOR_REAL_H3 = {'YES' if ready else 'NO'}")


if __name__ == "__main__":
    main()
