#!/usr/bin/env bash
set -euo pipefail
TARGET="${1:-}"
[[ -n "$TARGET" ]] || { echo "Uso: $0 /caminho/CausalODS" >&2; exit 2; }
[[ -d "$TARGET/experiments" && -d "$TARGET/correlation-engine/robustness" ]] || {
  echo "Repositório alvo inválido ou fundação de robustez ausente: $TARGET" >&2
  exit 1
}
SOURCE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
mkdir -p "$TARGET/experiments/robustness_live" "$TARGET/experiments/tests" "$TARGET/docs"
cp -a "$SOURCE/experiments/robustness_live/." "$TARGET/experiments/robustness_live/"
cp "$SOURCE/experiments/run_robustness_live.py" "$TARGET/experiments/"
cp "$SOURCE/experiments/analyze_robustness_live.py" "$TARGET/experiments/"
cp "$SOURCE/experiments/run_robustness_live.sh" "$TARGET/experiments/"
cp "$SOURCE/experiments/tests/"test_robustness_live_*.py "$TARGET/experiments/tests/"
cp "$SOURCE/docs/"robustness-live-*.md "$TARGET/docs/"
chmod +x "$TARGET/experiments/run_robustness_live.py" "$TARGET/experiments/analyze_robustness_live.py" "$TARGET/experiments/run_robustness_live.sh"
echo "Robustez live instalada em $TARGET"
