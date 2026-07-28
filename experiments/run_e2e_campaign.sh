#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(
  cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
  pwd
)"
REPOSITORY_ROOT="$(
  cd -- "${SCRIPT_DIR}/.."
  pwd
)"
CONTROL_DIR="${REPOSITORY_ROOT}/results/e2e/campaign-control"
PID_FILE="${CONTROL_DIR}/campaign.pid"
LOG_POINTER="${CONTROL_DIR}/latest-log.txt"

mkdir -p "${CONTROL_DIR}"

usage() {
  cat <<'EOF'
Uso:
  ./experiments/run_e2e_campaign.sh start [argumentos do e2e_campaign.py]
  ./experiments/run_e2e_campaign.sh status
  ./experiments/run_e2e_campaign.sh logs
  ./experiments/run_e2e_campaign.sh stop

Exemplo padrão (v1 e v2, 50 repetições):
  ./experiments/run_e2e_campaign.sh start

Exemplo de validação curta:
  ./experiments/run_e2e_campaign.sh start --repeat 1
EOF
}

read_pid() {
  if [[ -f "${PID_FILE}" ]]; then
    tr -d '[:space:]' < "${PID_FILE}"
  fi
}

process_is_running() {
  local pid="${1:-}"
  [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null
}

find_latest_status() {
  find "${REPOSITORY_ROOT}/results/e2e/final" \
    -mindepth 2 \
    -maxdepth 2 \
    -name status.json \
    -printf '%T@ %p\n' 2>/dev/null \
    | sort -nr \
    | head -1 \
    | cut -d' ' -f2-
}

start_campaign() {
  local existing_pid
  existing_pid="$(read_pid || true)"

  if process_is_running "${existing_pid}"; then
    echo "Já existe uma campanha em execução: PID=${existing_pid}" >&2
    exit 1
  fi

  local active_runner
  active_runner="$(
    pgrep -af 'python(3)? .*experiments/e2e_runner.py' || true
  )"

  if [[ -n "${active_runner}" ]]; then
    echo "Existe um e2e_runner.py em execução." >&2
    echo "Aguarde a finalização antes de iniciar a campanha:" >&2
    echo "${active_runner}" >&2
    exit 1
  fi

  python3 -m py_compile \
    "${REPOSITORY_ROOT}/experiments/e2e_campaign.py" \
    "${REPOSITORY_ROOT}/experiments/e2e_runner.py"

  local timestamp
  timestamp="$(date -u +%Y%m%dT%H%M%SZ)"

  local log_path
  log_path="${CONTROL_DIR}/campaign-${timestamp}.log"

  cd "${REPOSITORY_ROOT}"

  nohup python3 -u \
    experiments/e2e_campaign.py \
    --repeat 50 \
    --versions v1 v2 \
    --final-runtime v2 \
    "$@" \
    > "${log_path}" 2>&1 &

  local pid=$!

  printf '%s\n' "${pid}" > "${PID_FILE}"
  printf '%s\n' "${log_path}" > "${LOG_POINTER}"

  sleep 1

  if ! process_is_running "${pid}"; then
    echo "A campanha encerrou logo após iniciar." >&2
    echo "Log: ${log_path}" >&2
    tail -n 80 "${log_path}" >&2 || true
    exit 1
  fi

  echo "Campanha iniciada em segundo plano."
  echo "PID: ${pid}"
  echo "Log: ${log_path}"
  echo
  echo "Acompanhar:"
  echo "  ./experiments/run_e2e_campaign.sh status"
  echo "  ./experiments/run_e2e_campaign.sh logs"
}

show_status() {
  local pid
  pid="$(read_pid || true)"

  if process_is_running "${pid}"; then
    echo "Processo: RUNNING | PID=${pid}"
  elif [[ -n "${pid}" ]]; then
    echo "Processo: FINISHED | último PID=${pid}"
  else
    echo "Processo: nenhum PID registrado"
  fi

  local status_path
  status_path="$(find_latest_status || true)"

  if [[ -z "${status_path}" || ! -f "${status_path}" ]]; then
    echo "Nenhum status.json encontrado."
    exit 0
  fi

  echo "Status: ${status_path}"

  python3 - "${status_path}" <<'PY'
import json
import sys

path = sys.argv[1]
with open(path, encoding="utf-8") as file:
    status = json.load(file)

print("campaign_id:", status.get("campaign_id"))
print("state:", status.get("state"))
print("current_version:", status.get("current_version"))
print("current_step:", status.get("current_step"))
print("started_at:", status.get("started_at"))
print("finished_at:", status.get("finished_at"))

for version, phase in (status.get("phases") or {}).items():
    print(
        f"{version}: state={phase.get('state')} "
        f"rows={phase.get('actual_rows')} "
        f"passed={phase.get('passed_count')} "
        f"failed={phase.get('failed_count')}"
    )

errors = status.get("errors") or []
if errors:
    print("errors:")
    for error in errors:
        print(" -", error.get("type"), error.get("message"))
PY
}

show_logs() {
  if [[ ! -f "${LOG_POINTER}" ]]; then
    echo "Nenhum log registrado." >&2
    exit 1
  fi

  local log_path
  log_path="$(cat "${LOG_POINTER}")"

  if [[ ! -f "${log_path}" ]]; then
    echo "Log não encontrado: ${log_path}" >&2
    exit 1
  fi

  echo "Exibindo: ${log_path}"
  tail -n 120 -f "${log_path}"
}

stop_campaign() {
  local pid
  pid="$(read_pid || true)"

  if ! process_is_running "${pid}"; then
    echo "Nenhuma campanha ativa."
    exit 0
  fi

  echo "Enviando SIGTERM para PID=${pid}..."
  kill -TERM "${pid}"

  for _ in $(seq 1 30); do
    if ! process_is_running "${pid}"; then
      echo "Campanha encerrada."
      exit 0
    fi
    sleep 1
  done

  echo "O processo não encerrou após 30 segundos." >&2
  echo "Use kill -KILL ${pid} somente como último recurso." >&2
  exit 1
}

main() {
  local action="${1:-}"

  case "${action}" in
    start)
      shift
      start_campaign "$@"
      ;;
    status)
      show_status
      ;;
    logs)
      show_logs
      ;;
    stop)
      stop_campaign
      ;;
    -h|--help|help|"")
      usage
      ;;
    *)
      echo "Ação inválida: ${action}" >&2
      usage >&2
      exit 2
      ;;
  esac
}

main "$@"
