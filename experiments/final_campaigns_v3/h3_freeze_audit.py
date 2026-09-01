"""Offline static gate for the final H3 OpenAI binding."""

from __future__ import annotations

import json
from pathlib import Path

from .h3_openai_freeze import (
    CONDITIONS, MANIFEST_PATH, MODEL_ID, contract_fingerprint, load_manifest,
    request_payload, output_instruction_binding_valid, prompt_provenance_fields,
)


ROOT = Path(__file__).resolve().parents[2]


def audit() -> dict:
    errors = []
    try:
        manifest = load_manifest()
    except Exception as exc:
        return {"status": "FAIL", "errors": [str(exc)]}
    payload = request_payload("frozen-static-probe")
    required = {"model", "messages", "reasoning_effort", "temperature",
                "max_completion_tokens", "stream"}
    if set(payload) != required or "top_p" in payload or "seed" in payload:
        errors.append("request_payload_contract")
    if payload["model"] != MODEL_ID or payload["temperature"] != 0.0:
        errors.append("exact_model_or_temperature")
    if not output_instruction_binding_valid():
        errors.append("output_instruction_binding")
    if payload["messages"][0]["content"] != __import__(
            "experiments.final_campaigns_v3.h3_openai_freeze", fromlist=[
                "DEVELOPER_MESSAGE"]).DEVELOPER_MESSAGE:
        errors.append("developer_message_binding")
    for mode in CONDITIONS:
        fields = prompt_provenance_fields(
            manifest, mode=mode, prompt="frozen-static-probe", prompt_hash="0" * 64)
        if (fields["common_prompt_hash"] != manifest["prompt_hashes"]["common"]
                or fields["prompt_template_hash"] != manifest["prompt_hashes"][mode]
                or fields["prompt_hash"] != "0" * 64
                or len(fields["rendered_input_hash"]) != 64):
            errors.append(f"prompt_provenance_binding:{mode}")
    input_audit_path = ROOT / "artifacts/final-experiments-v3/h3-input-token-audit.json"
    try:
        token_audit = json.loads(input_audit_path.read_text(encoding="utf-8"))
        if (token_audit.get("requests_checked") != 120
                or token_audit.get("selected_budget") != manifest["input_token_budget"]
                or token_audit.get("budget_freeze") != "PASS"
                or token_audit.get("over_budget_count") != 0):
            errors.append("input_token_audit")
        if token_audit.get("rca_identity_audit") != "PASS":
            errors.append("rca_identity")
    except (OSError, json.JSONDecodeError):
        errors.append("input_token_audit_missing")
    status = "PASS" if not errors else "FAIL"
    return {"status": status, "errors": errors,
            "output_contract_binding": ("PASS" if output_instruction_binding_valid() else "FAIL"),
            "prompt_provenance_binding": ("PASS" if not any(
                item.startswith("prompt_provenance_binding") for item in errors) else "FAIL"),
            "manifest_path": str(MANIFEST_PATH.relative_to(ROOT)),
            "manifest_sha256": __import__("hashlib").sha256(MANIFEST_PATH.read_bytes()).hexdigest(),
            "contract_fingerprint": contract_fingerprint(manifest),
            "request_payload": payload, "conditions": list(CONDITIONS)}


def main() -> int:
    report = audit()
    print(json.dumps(report, sort_keys=True))
    print(f"H3_OUTPUT_CONTRACT_BINDING_AUDIT = {report['output_contract_binding']}")
    print(f"H3_PROMPT_PROVENANCE_AUDIT = {report['prompt_provenance_binding']}")
    print(f"H3_LLM_FREEZE_STATIC_AUDIT = {report['status']}")
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
