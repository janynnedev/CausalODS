"""Host-only runner for the real coordinator SIGTERM/resume gate.

Do not run this in the Codex sandbox: its PID namespace forwards SIGTERM to
the test executor.  On a normal host it invokes the same public CLI exercised
by the integration test and emits exactly one final JSON object.
"""
from __future__ import annotations

import importlib.util
import json
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TEST = ROOT / "experiments/tests/test_scientific_preprocessing_resume_gate_v3.py"


def main() -> int:
    spec = importlib.util.spec_from_file_location("p3_resume_gate", TEST)
    if spec is None or spec.loader is None:
        raise RuntimeError("P3_RESUME_GATE_TEST_NOT_LOADABLE")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with tempfile.TemporaryDirectory(prefix="causalods-p3-resume-") as temporary:
        temporary_path = Path(temporary)
        module.test_public_cli_clean_sigterm_resume_and_corrupt_checkpoint(temporary_path)
        core = module.LAST_RESULT
        module.test_feature_worker_failure_then_resume_matches_clean_oracle(temporary_path)
        module.test_prepare_incident_resume_interoperability(temporary_path)
    if not core:
        raise RuntimeError("P3_RESUME_GATE_NO_RESULT")
    result = {**core, "status": "PASS", "clean": "PASS",
              "sigterm_resume": "PASS", "corruption_resume": "PASS",
              "worker_failure_resume": "PASS", "preprocess_incident_resume": "PASS"}
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
