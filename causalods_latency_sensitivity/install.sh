#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $# -ne 1 ]]; then
  echo "Uso: $0 /caminho/para/CausalODS"
  exit 2
fi

SOURCE_ROOT="$(
  cd "$(dirname "${BASH_SOURCE[0]}")" &&
  pwd
)"
TARGET_ROOT="$(
  cd "$1" &&
  pwd
)"

if [[ ! -f "${TARGET_ROOT}/experiments/e2e_runner.py" ]]; then
  echo "Erro: experiments/e2e_runner.py não encontrado em ${TARGET_ROOT}"
  exit 2
fi

if [[ ! -f "${TARGET_ROOT}/experiments/e2e_campaign.py" ]]; then
  echo "Erro: experiments/e2e_campaign.py não encontrado em ${TARGET_ROOT}"
  exit 2
fi

mkdir -p \
  "${TARGET_ROOT}/experiments/tests" \
  "${TARGET_ROOT}/docs"

install -m 0755 \
  "${SOURCE_ROOT}/experiments/latency_sensitivity_campaign.py" \
  "${TARGET_ROOT}/experiments/latency_sensitivity_campaign.py"

install -m 0755 \
  "${SOURCE_ROOT}/experiments/analyze_latency_sensitivity.py" \
  "${TARGET_ROOT}/experiments/analyze_latency_sensitivity.py"

install -m 0755 \
  "${SOURCE_ROOT}/experiments/run_latency_sensitivity.sh" \
  "${TARGET_ROOT}/experiments/run_latency_sensitivity.sh"

install -m 0644 \
  "${SOURCE_ROOT}/experiments/tests/test_latency_sensitivity_analysis.py" \
  "${TARGET_ROOT}/experiments/tests/test_latency_sensitivity_analysis.py"

install -m 0644 \
  "${SOURCE_ROOT}/docs/latency-sensitivity-methodology.md" \
  "${TARGET_ROOT}/docs/latency-sensitivity-methodology.md"

PYTHON="${TARGET_ROOT}/.venv/bin/python"
if [[ ! -x "${PYTHON}" ]]; then
  PYTHON="$(command -v python3)"
fi

"${PYTHON}" -m py_compile \
  "${TARGET_ROOT}/experiments/latency_sensitivity_campaign.py" \
  "${TARGET_ROOT}/experiments/analyze_latency_sensitivity.py"

if "${PYTHON}" -c 'import pytest' >/dev/null 2>&1; then
  cd "${TARGET_ROOT}"
  "${PYTHON}" -m pytest \
    experiments/tests/test_latency_sensitivity_analysis.py \
    -q
else
  echo "AVISO: pytest não encontrado; testes não executados."
fi

echo
echo "Instalação concluída em ${TARGET_ROOT}"
