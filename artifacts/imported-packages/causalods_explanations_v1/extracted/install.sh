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

mkdir -p \
  "${TARGET_ROOT}/correlation-engine/explanations" \
  "${TARGET_ROOT}/correlation-engine/tests" \
  "${TARGET_ROOT}/experiments" \
  "${TARGET_ROOT}/docs"

install -m 0644 \
  "${SOURCE_ROOT}/correlation-engine/explanations/__init__.py" \
  "${TARGET_ROOT}/correlation-engine/explanations/__init__.py"

install -m 0644 \
  "${SOURCE_ROOT}/correlation-engine/explanations/contracts.py" \
  "${TARGET_ROOT}/correlation-engine/explanations/contracts.py"

install -m 0644 \
  "${SOURCE_ROOT}/correlation-engine/explanations/evidence_adapter.py" \
  "${TARGET_ROOT}/correlation-engine/explanations/evidence_adapter.py"

install -m 0644 \
  "${SOURCE_ROOT}/correlation-engine/explanations/deterministic_generator.py" \
  "${TARGET_ROOT}/correlation-engine/explanations/deterministic_generator.py"

install -m 0644 \
  "${SOURCE_ROOT}/correlation-engine/tests/test_explanations.py" \
  "${TARGET_ROOT}/correlation-engine/tests/test_explanations.py"

install -m 0755 \
  "${SOURCE_ROOT}/experiments/generate_rca_explanations.py" \
  "${TARGET_ROOT}/experiments/generate_rca_explanations.py"

install -m 0644 \
  "${SOURCE_ROOT}/docs/rca-explanation-methodology.md" \
  "${TARGET_ROOT}/docs/rca-explanation-methodology.md"

PYTHON="${TARGET_ROOT}/.venv/bin/python"
if [[ ! -x "${PYTHON}" ]]; then
  PYTHON="$(command -v python3)"
fi

"${PYTHON}" -m py_compile \
  "${TARGET_ROOT}/correlation-engine/explanations/"*.py \
  "${TARGET_ROOT}/experiments/generate_rca_explanations.py"

if "${PYTHON}" -c 'import pytest' >/dev/null 2>&1; then
  cd "${TARGET_ROOT}"
  PYTHONPATH=correlation-engine \
    "${PYTHON}" -m pytest \
    correlation-engine/tests/test_explanations.py \
    -q
else
  echo "AVISO: pytest não encontrado; testes não executados."
fi

echo
echo "Instalação concluída em ${TARGET_ROOT}"
