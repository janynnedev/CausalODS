#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(
  cd "$(dirname "${BASH_SOURCE[0]}")/.." &&
  pwd
)"
CONTROL_DIR="${ROOT}/results/e2e/sensitivity-control"
mkdir -p "${CONTROL_DIR}"

usage() {
  cat <<'EOF'
Uso:
  ./experiments/run_latency_sensitivity.sh start [argumentos]
  ./experiments/run_latency_sensitivity.sh status
  ./experiments/run_latency_sensitivity.sh logs
  ./experiments/run_latency_sensitivity.sh stop

Exemplo final:
  ./experiments/run_latency_sensitivity.sh start \
    --campaign-id latency-sensitivity-v1-v2 \
    --repeat 10 \
    --delays 250 500 750 1000 1500 2000 2500
EOF
}

latest_value() {
  local file="$1"
  [[ -f "${file}" ]] && cat "${file}" || true
}

case "${1:-}" in
  start)
    shift
    campaign_id=""
    previous=""
    for argument in "$@"; do
      if [[ "${previous}" == "--campaign-id" ]]; then
        campaign_id="${argument}"
        break
      fi
      previous="${argument}"
    done
    if [[ -z "${campaign_id}" ]]; then
      campaign_id="latency-sensitivity-$(date -u +%Y%m%dT%H%M%SZ)"
      set -- --campaign-id "${campaign_id}" "$@"
    fi

    pid_file="${CONTROL_DIR}/${campaign_id}.pid"
    log_file="${CONTROL_DIR}/${campaign_id}.log"

    if [[ -f "${pid_file}" ]] && kill -0 "$(cat "${pid_file}")" 2>/dev/null; then
      echo "Campanha já ativa. PID=$(cat "${pid_file}")"
      exit 1
    fi

    cd "${ROOT}"
    nohup "${ROOT}/.venv/bin/python" \
      experiments/latency_sensitivity_campaign.py \
      "$@" \
      > "${log_file}" 2>&1 &
    pid=$!
    echo "${pid}" > "${pid_file}"
    echo "${campaign_id}" > "${CONTROL_DIR}/latest-campaign.txt"
    echo "${pid_file}" > "${CONTROL_DIR}/latest-pid-file.txt"
    echo "${log_file}" > "${CONTROL_DIR}/latest-log-file.txt"

    echo "Campanha iniciada"
    echo "Campaign ID: ${campaign_id}"
    echo "PID: ${pid}"
    echo "Log: ${log_file}"
    ;;

  status)
    pid_file="$(latest_value "${CONTROL_DIR}/latest-pid-file.txt")"
    campaign_id="$(latest_value "${CONTROL_DIR}/latest-campaign.txt")"
    if [[ -z "${campaign_id}" ]]; then
      echo "Nenhuma campanha registrada."
      exit 1
    fi
    echo "Campaign ID: ${campaign_id}"
    if [[ -n "${pid_file}" && -f "${pid_file}" ]]; then
      pid="$(cat "${pid_file}")"
      if kill -0 "${pid}" 2>/dev/null; then
        echo "Processo: RUNNING | PID=${pid}"
      else
        echo "Processo: STOPPED | PID=${pid}"
      fi
    fi
    status_file="${ROOT}/results/e2e/sensitivity/${campaign_id}/status.json"
    if [[ -f "${status_file}" ]]; then
      "${ROOT}/.venv/bin/python" - <<PY
import json
from pathlib import Path
p = Path(${status_file@Q})
d = json.loads(p.read_text(encoding="utf-8"))
print("State:", d.get("state"))
print("Current version:", d.get("current_version"))
print("Current condition:", d.get("current_condition"))
for version, phase in d.get("phases", {}).items():
    print(
        version,
        "| rows=", phase.get("rows"),
        "| valid=", phase.get("technical_valid"),
        "| detected=", phase.get("detected"),
        "| passed=", phase.get("passed"),
    )
PY
    else
      echo "Status ainda não criado: ${status_file}"
    fi
    ;;

  logs)
    log_file="$(latest_value "${CONTROL_DIR}/latest-log-file.txt")"
    if [[ -z "${log_file}" || ! -f "${log_file}" ]]; then
      echo "Log não encontrado."
      exit 1
    fi
    tail -f "${log_file}"
    ;;

  stop)
    pid_file="$(latest_value "${CONTROL_DIR}/latest-pid-file.txt")"
    if [[ -z "${pid_file}" || ! -f "${pid_file}" ]]; then
      echo "PID não encontrado."
      exit 1
    fi
    pid="$(cat "${pid_file}")"
    if kill -0 "${pid}" 2>/dev/null; then
      kill -INT "${pid}"
      echo "SIGINT enviado para PID=${pid}"
    else
      echo "Processo já encerrado."
    fi
    ;;

  *)
    usage
    exit 2
    ;;
esac
