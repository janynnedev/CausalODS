#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $# -ne 1 ]]; then
  echo "Uso: $0 /caminho/para/CausalODS-explanations"
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

BACKUP_ROOT="${TARGET_ROOT}/.causalods-backup/explanations-v1.1-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "${BACKUP_ROOT}"

copy_with_backup() {
  local source="$1"
  local target="$2"

  mkdir -p "$(dirname "${target}")"

  if [[ -f "${target}" ]]; then
    local relative="${target#${TARGET_ROOT}/}"
    mkdir -p "${BACKUP_ROOT}/$(dirname "${relative}")"
    cp -a "${target}" "${BACKUP_ROOT}/${relative}"
  fi

  install -m 0644 "${source}" "${target}"
}

copy_with_backup \
  "${SOURCE_ROOT}/correlation-engine/explanations/__init__.py" \
  "${TARGET_ROOT}/correlation-engine/explanations/__init__.py"

copy_with_backup \
  "${SOURCE_ROOT}/correlation-engine/explanations/contracts.py" \
  "${TARGET_ROOT}/correlation-engine/explanations/contracts.py"

copy_with_backup \
  "${SOURCE_ROOT}/correlation-engine/explanations/evidence_adapter.py" \
  "${TARGET_ROOT}/correlation-engine/explanations/evidence_adapter.py"

copy_with_backup \
  "${SOURCE_ROOT}/correlation-engine/explanations/deterministic_generator.py" \
  "${TARGET_ROOT}/correlation-engine/explanations/deterministic_generator.py"

copy_with_backup \
  "${SOURCE_ROOT}/correlation-engine/tests/test_explanations.py" \
  "${TARGET_ROOT}/correlation-engine/tests/test_explanations.py"

copy_with_backup \
  "${SOURCE_ROOT}/experiments/generate_rca_explanations.py" \
  "${TARGET_ROOT}/experiments/generate_rca_explanations.py"

copy_with_backup \
  "${SOURCE_ROOT}/experiments/evaluate_rca_explanations.py" \
  "${TARGET_ROOT}/experiments/evaluate_rca_explanations.py"

copy_with_backup \
  "${SOURCE_ROOT}/docs/rca-explanation-methodology.md" \
  "${TARGET_ROOT}/docs/rca-explanation-methodology.md"

chmod +x \
  "${TARGET_ROOT}/experiments/generate_rca_explanations.py" \
  "${TARGET_ROOT}/experiments/evaluate_rca_explanations.py"

PYTHON="${TARGET_ROOT}/.venv-host/bin/python"
if [[ ! -x "${PYTHON}" ]]; then
  PYTHON="${TARGET_ROOT}/.venv/bin/python"
fi
if [[ ! -x "${PYTHON}" ]]; then
  PYTHON="$(command -v python3)"
fi

"${PYTHON}" -m py_compile \
  "${TARGET_ROOT}/correlation-engine/explanations/"*.py \
  "${TARGET_ROOT}/experiments/generate_rca_explanations.py" \
  "${TARGET_ROOT}/experiments/evaluate_rca_explanations.py"

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
echo "Instalação concluída."
echo "Backup: ${BACKUP_ROOT}"
