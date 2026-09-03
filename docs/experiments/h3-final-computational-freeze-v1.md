# H3 final computational freeze v1

Status: `PASS`

The H3 computational campaign is complete. Its authoritative directory is
`artifacts/final-experiments-v3-h3-final/H3/final-h3-v3/3ff75671bd14ae773dc577bf161a3212da0c6eaf0b6359d4b3843394e7f2f676`.
That directory is immutable: no H3 execution, resume, provider call, explanation
regeneration, prompt refreeze, or model-contract change is permitted by this
freeze.

The external machine-readable record is
`artifacts/final-experiments-v3-h3-freeze/h3-computational-freeze-v1.json`.
It binds the canonical `SHA256SUMS` and the campaign's scientific artifacts.
The campaign contains 120 successful runs over 40 incidents and three
conditions, 40 runs per condition. It contains 120 unique provider response
identities and zero fallback outputs. The same-RCA, campaign-validity, human
blinding, and V2 12/12 gates passed.

Canonical selection followed
`prefer_first_attempt_success_else_first_retry_success`, based only on technical
status and attempt order. It selected 119 first attempts and one retry: run
`029be23de01d0dc8af43e5f9811326ad0bf635aabc59f50c764eb3c7cfef8e0c`,
incident `rcaeval:re1ob_adservice_delay_5`, under the evidence-bearing condition.
No content-quality signal participated in that selection.

The frozen provider contract is OpenAI, GPT-5.4 Mini,
`gpt-5.4-mini-2026-03-17`, chat completions, no reasoning effort,
temperature 0.0, 1,024 maximum completion tokens, 32,768 input-token budget,
non-streaming, with `top_p` and API seed omitted. The common prompt hash is
`9c9f5fe166ac0b0a586bd45b93b72caebaadc4eea0ba92370b84a81bff1676a3`.

The human-evaluation instrument is a derived external artifact. Ratings and
later analyses cannot change this computational freeze.
