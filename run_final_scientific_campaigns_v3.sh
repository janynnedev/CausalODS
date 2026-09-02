#!/usr/bin/env bash
set -euo pipefail

# Final-article defaults. Alternative protocols and seeds remain supported by
# the Python orchestrator, but are outside the current article scope.
FINAL_SEEDS="${FINAL_SEEDS:-42}"
H1_PROTOCOLS="${H1_PROTOCOLS:-within-dataset}"
H2_PROTOCOLS="${H2_PROTOCOLS:-within-dataset}"
H3_PROTOCOLS="${H3_PROTOCOLS:-within-dataset}"
H4_PROTOCOLS="${H4_PROTOCOLS:-within-dataset}"
H3_SEED="${H3_SEED:-42}"
H3_SAMPLE_SIZE="${H3_SAMPLE_SIZE:-40}"
H4_SEED="${H4_SEED:-42}"
H4_SAMPLE_SIZE="${H4_SAMPLE_SIZE:-40}"

if [[ "${1:-}" == "--print-plan" ]]; then
  printf '%s\n' \
    "FINAL_SEEDS=${FINAL_SEEDS}" \
    "H1_PROTOCOLS=${H1_PROTOCOLS}" \
    "H2_PROTOCOLS=${H2_PROTOCOLS}" \
    "H3_PROTOCOLS=${H3_PROTOCOLS}" \
    "H4_PROTOCOLS=${H4_PROTOCOLS}" \
    "H3_SEED=${H3_SEED}" \
    "H3_SAMPLE_SIZE=${H3_SAMPLE_SIZE}" \
    "H4_SEED=${H4_SEED}" \
    "H4_SAMPLE_SIZE=${H4_SAMPLE_SIZE}"
  exit 0
fi

echo "Refusing implicit execution: invoke the hypothesis-specific orchestrator after external approval." >&2
exit 2
