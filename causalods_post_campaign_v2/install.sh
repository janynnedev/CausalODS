#!/usr/bin/env bash
set -Eeuo pipefail

PACKAGE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPOSITORY_ROOT="${1:-$(pwd)}"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_ROOT="${REPOSITORY_ROOT}/.causalods-backup/${TIMESTAMP}"

if [[ ! -d "${REPOSITORY_ROOT}/experiments" ]]; then
  echo "Diretório experiments/ não encontrado em: ${REPOSITORY_ROOT}" >&2
  exit 2
fi

if command -v python >/dev/null 2>&1; then
  PYTHON_BIN="$(command -v python)"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="$(command -v python3)"
else
  echo "Python não encontrado no PATH." >&2
  exit 2
fi

copy_file() {
  local relative="$1"
  local source="${PACKAGE_ROOT}/${relative}"
  local destination="${REPOSITORY_ROOT}/${relative}"

  if [[ -f "${destination}" ]]; then
    mkdir -p "${BACKUP_ROOT}/$(dirname "${relative}")"
    cp -p "${destination}" "${BACKUP_ROOT}/${relative}"
  fi

  mkdir -p "$(dirname "${destination}")"
  cp -p "${source}" "${destination}"
  echo "Instalado: ${relative}"
}

while IFS= read -r relative; do
  copy_file "${relative}"
done < <(
  cd "${PACKAGE_ROOT}"
  find experiments docs -type f \
    ! -path '*/__pycache__/*' \
    ! -name '*.pyc' \
    -print | sort
)

cd "${REPOSITORY_ROOT}"

echo "Python usado: ${PYTHON_BIN}"

"${PYTHON_BIN}" -m py_compile \
  experiments/analysis_utils.py \
  experiments/compare_e2e_results.py \
  experiments/export_rca_evidence.py \
  experiments/run_baselines.py \
  experiments/post_campaign_analysis.py \
  experiments/build_experimental_report.py \
  experiments/baselines/*.py

"${PYTHON_BIN}" -m unittest discover \
  -s experiments/tests \
  -v

if [[ -d "${BACKUP_ROOT}" ]]; then
  echo "Backup dos arquivos substituídos: ${BACKUP_ROOT}"
fi

echo "Instalação e validação concluídas."
