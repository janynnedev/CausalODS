# H3 OpenAI GPT-5.4 Mini freeze v1

H3 keeps the existing `generic`, `raw_telemetry`, and `evidence_grounded`
conditions and changes only the explanatory information supplied to the model.
All three conditions consume the same immutable, precomputed `RCAResult`; the
LLM neither diagnoses nor changes ranking, confidence, reliability, or evidence.

The frozen transport is OpenAI Chat Completions at
`https://api.openai.com/v1/chat/completions`, using the dated snapshot
`gpt-5.4-mini-2026-03-17`, `reasoning_effort=none`, `temperature=0.0`,
`max_completion_tokens=1024`, and `stream=false`. `top_p` and the API sampling
seed are omitted. The dated snapshot reduces model-version drift. Temperature
zero reduces generation variability but does not guarantee perfect
determinism. Reasoning effort is disabled because the task is explanation of an
already-determined RCA result, not independent causal diagnosis.

The exact historical rendered prompt remains the user message. Every mode receives
the identical common developer message, which freezes the exact
`structured-explanation-v1` keys and types, forbids surrounding Markdown or extra
fields, preserves the supplied root cause, and enforces the existing
`ALLOWED_EVIDENCE_IDS` citation boundary. No mode receives hidden mode-specific
instructions. Static prompt contracts and the output contract are hashed in
`experiments/configs/final-v3/h3-llm-freeze.json` and checked without automatic
hash regeneration.

Prompt provenance deliberately separates four identities. `common_prompt_hash`
identifies the static developer instruction; `prompt_template_hash` identifies
the static template for the selected H3 mode; `prompt_hash` preserves the existing
dynamic `PromptPayload` identity; and `rendered_input_hash` identifies the exact
canonical developer-plus-user Chat Completions messages. Static hashes therefore
cannot be confused with incident-specific rendered input hashes.

The input budget is selected before live execution by rendering the actual
40×3 frozen request set and choosing the smallest of 8192, 16384, 32768, and
65536 for which the maximum deterministic tiktoken estimate is at most 80% of
the budget. The same budget is enforced before every network call. Excess input
fails closed: no truncation, summarization, retry, or provider call occurs. The
40×3 audit and budget were recalculated after freezing the final developer
message.

The explicit synthetic preflight performs exactly three calls, one per mode,
through the regular H3 explanation path. It is integration evidence only and is
excluded from scientific results. Default tests and the offline audits never
make paid API calls. Run it only with:

```bash
OPENAI_API_KEY='...' PYTHONPATH=.:correlation-engine \
  correlation-engine/.venv/bin/python \
  -m experiments.final_campaigns_v3.h3_live_preflight
```

No credential, Authorization header, or generated explanation is persisted by
the freeze manifest or token audit.
