#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
CONTROL_DIR="$ROOT/results/robustness/live-control"
PID_FILE="$CONTROL_DIR/live.pid"
LOG_FILE="$CONTROL_DIR/latest-log.txt"
mkdir -p "$CONTROL_DIR"

case "${1:-}" in
  start)
    shift
    if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
      echo "Campanha live já está ativa: PID=$(cat "$PID_FILE")" >&2
      exit 1
    fi
    stamp="$(date -u +%Y%m%dT%H%M%SZ)"
    log="$CONTROL_DIR/robustness-live-$stamp.log"
    cd "$ROOT"
    nohup python3 -u experiments/run_robustness_live.py "$@" >"$log" 2>&1 &
    echo $! > "$PID_FILE"
    echo "$log" > "$LOG_FILE"
    echo "Campanha iniciada | PID=$! | log=$log"
    ;;
  status)
    if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
      echo "RUNNING | PID=$(cat "$PID_FILE")"
    else
      echo "NOT RUNNING"
    fi
    ;;
  logs)
    test -f "$LOG_FILE" || { echo "Nenhum log registrado"; exit 1; }
    tail -f "$(cat "$LOG_FILE")"
    ;;
  stop)
    test -f "$PID_FILE" || exit 0
    kill -INT "$(cat "$PID_FILE")" 2>/dev/null || true
    ;;
  *)
    echo "Uso: $0 {start|status|logs|stop} [args]" >&2
    exit 2
    ;;
esac
