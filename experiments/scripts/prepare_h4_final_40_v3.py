#!/usr/bin/env python3
"""Generate the H4 final-40 readiness freeze without loading or scoring real incidents."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from experiments.final_campaigns_v3.h4_final_40 import prepare_h4_final_40


ROOT = Path(__file__).resolve().parents[2]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path,
                        default=ROOT / "artifacts/final-experiments-v3-h4-final-40")
    args = parser.parse_args(argv)
    result = prepare_h4_final_40(ROOT, args.output)
    print(json.dumps({"execution_readiness": result["execution_readiness"],
                      "inference_calls": result["inference_calls"],
                      "output": str(args.output.resolve())}, sort_keys=True))
    return 0 if result["execution_readiness"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
