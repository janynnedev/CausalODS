from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess

from platform_v3.constants import ARTIFACT_ROOT, ROOT
from platform_v3.live import seed_demo
from platform_v3.smoke import run_contract_smoke
from platform_v3.validation import live_health, write_validation_artifacts


COMPOSE = (
    "docker", "compose", "-f", str(ROOT / "docker-compose.yml"),
    "-f", str(ROOT / "docker-compose.platform-v3.yml"),
)


def _compose(*args: str) -> int:
    return subprocess.run((*COMPOSE, *args), cwd=ROOT, check=False).returncode


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="platform-v3")
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("up")
    commands.add_parser("down")
    commands.add_parser("health")
    commands.add_parser("smoke")
    validate = commands.add_parser("validate")
    validate.add_argument("--live", action="store_true", default=False)
    validate.add_argument("--output", type=Path, default=ARTIFACT_ROOT)
    report = commands.add_parser("report")
    report.add_argument("--live", action="store_true", default=False)
    report.add_argument("--output", type=Path, default=ARTIFACT_ROOT)
    seed = commands.add_parser("seed-demo")
    seed.add_argument("--campaign-root", type=Path)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.command == "up":
        return _compose("up", "-d")
    if args.command == "down":
        return _compose("down")
    if args.command == "health":
        status = live_health()
        print(json.dumps(status, sort_keys=True))
        return 0 if all(item.get("reachable") for item in status.values()) else 2
    if args.command == "smoke":
        report = run_contract_smoke()
        print(json.dumps(report, sort_keys=True))
        return 0 if report["status"] == "PASS" else 2
    if args.command in {"validate", "report"}:
        paths = write_validation_artifacts(args.output, live=args.live)
        report = json.loads(paths["report_json"].read_text(encoding="utf-8"))
        print(json.dumps({"decision": report["decision"], "output": str(args.output)}, sort_keys=True))
        return 0 if report["decision"] == "READY_FOR_FINAL_EXPERIMENTS" else 2
    if args.command == "seed-demo":
        report = seed_demo(campaign_root=args.campaign_root)
        print(json.dumps(report, sort_keys=True))
        return 0 if report["status"] == "PASS" else 2
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
