# H4 selective resume: forensic audit and blocked repair

No campaign run/resume command was executed. No provider, retraining or retuning was used. The canonical store, recovery payloads and active duplicates were not modified. `h4-resume-repair-audit.json` contains 1811 individually audited pre-OOM payloads. Reproduce with `python3 experiments/scripts/audit_h4_resume_readonly.py` from the repository root.

## Proven findings

- Pre-OOM: 967 SUCCESS + 844 SKIPPED_MODALITY_UNAVAILABLE = 1811 unique IDs.
- Active bad attempt: exactly 145 IDs (65 SUCCESS, 80 skips), all overlapping recovery. All 145 active files match the current index checksums. These checksums do not authenticate the different historical files.
- Frozen plan: 2240 positions, 1300 applicable and 940 skips. Missing positions: 333 applicable + 96 skips = 429. These are structural counts, not an authorization to execute only those positions yet.
- All 1811 payloads match the persisted plan's available scientific identity fields, including split lists for SUCCESS.
- First failing payload predicate for all 967 SUCCESS: `provenance.model_specification_hash == run.model_hash`. Actual value is absent. `trained_model_artifact_hash` is absent too.
- `FrozenH4ModelEvaluator` returns the trained artifact hash, but `_H4ProvenanceExecutor` does not carry it into persisted provenance. The benchmark-specific fields required by the shared selective validator are therefore missing in genuine H4 outputs. This is a producer/validator contract mismatch, not an environment timestamp mismatch.
- First failing payload predicate for all 844 skips: `status.state == SUCCESS`.
- The old selective resume reads only the active index, moves rejected payloads, and replaces that index without preserving it. There is no original recovery index or pre-recovery SHA256SUMS in this store. The outer SHA256SUMS covers nine preparation artifacts, not the historical run files.

The report deliberately distinguishes the first **payload** rejection from the earlier historical checksum predicate. Without the original index, the latter cannot be reconstructed. A freshly computed digest is an inventory, not proof of historical integrity. No historical SUCCESS is certified reusable at this point. No missing provenance has been inserted into scientific results.

## Implemented safeguards

- Creation `environment.json` is preserved byte-for-byte. Resume compares semantic Python version, scientific package versions, scientific commit, platform/architecture and contracts. Existing final/development worktree policy still applies; immutable configuration, registry, plan, model, split and tranche checks remain in the CLI.
- Timestamp, Python build text and observational git metadata differences are recorded under a sibling `resume-environments/<store-name>/` directory. This is outside the exact-cover campaign SHA256SUMS namespace.
- H4 with pending recovery now fails closed before selective mutation: `H4_RECOVERY_REQUIRES_VERIFIED_CANONICAL_REPAIR`. This intentionally prevents further duplicate execution; it is not the completed recovery implementation.
- Subsequent quarantine operations preserve the prior checksum index in `pre-recovery-index.json` before replacing the active index.

## Validation

- Focused environment/legacy recovery tests: 7 PASS. Cover timestamp/build metadata, immutable bytes and SHA256SUMS, commit/runtime/package/contract drift, dirty final worktree, and no mutation for legacy recovery.
- Expanded wiring/orchestrator/tranche/environment regression: 81 PASS. After the final environment report location adjustment, wiring/environment rerun: 41 PASS.
- Hardening/H4 readiness/environment suite: 53 PASS, one pre-existing state-dependent failure. `test_h4_technical_canary_is_three_system_noncanonical_and_nonleaking` assumes the canonical campaign directory is empty; that assertion is incompatible with preserving the campaign now present. Its data and assertion were not changed.
- Existing corruption regression additionally proves that quarantine preserves the prior checksum index.
- py_compile and git diff --check: PASS.
- Recovery inventory rechecked: 1811/1811 bytes match the audit digests.
- Offline freeze check: H1/H2 17 checks, H3 canonical 411, H3 freeze 1, scientific inputs 200, v2 12/12: PASS. Model binding: PASS, artifact SHA256 `a10ede7c8bfd922e14765caaf8dc6bfbf7e18627a3a49b722895bb2240e9f889`.
- `freeze-readiness/` contains the existing preparation preflight outputs. Its static `selective_resume` and `execution_readiness` PASS fields are NOT a certification of the interrupted store; the forensic blocker in this report takes precedence. That preflight made zero inference/training/provider calls.

## Memory assessment

The existing lazy mapping and single-incident cache tests pass; observation loading is lazy and the current cache holds one incident. The H4 wrapper retains the last degraded observation, and the executor retains baseline evaluation records. The orchestrator also keeps completed payloads and reloads all persisted payloads at the end, so absence of result accumulation cannot be claimed. These observations do not establish the cause of the 14.8 GB OOM. No representative sustained RSS readiness measurement was completed, and no scientific executor or memory behavior was changed. Memory readiness remains FAIL/unproven.

## Blocking evidence and remaining work

Needed: an independent pre-bad-resume copy of `runs/index.json` (1811 entries), or an equivalent checksum manifest/backup authenticating the historical payloads; and a verifiable provenance binding of the historical executions to the frozen trained model. Frozen model availability today alone does not fill in the discarded per-execution provenance.

Until those are available, no recovery promotion, canonical selection, historical checksum certification, or SUCCESS reuse claim is safe. The seven requested end-to-end selective-repair scenarios and crash-safe promotion are consequently unfinished. The historical skips remain preserved, and the existing orchestrator executes ineligible runs without model calls, but selective reuse of those skips is not implemented. The campaign must remain stopped.

`docs/CODEX_CONTEXT.md`, referenced by AGENTS.md, is absent; implementation decisions were based on inspected code and tests.
