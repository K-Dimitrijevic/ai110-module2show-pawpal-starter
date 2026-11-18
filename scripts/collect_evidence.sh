#!/usr/bin/env bash
# Runs every PawPal+ command and saves the real output to evidence/*.txt.
# Usage (from the project folder):  bash scripts/collect_evidence.sh
# Uses the pre-listed answers (no key needed). With ANTHROPIC_API_KEY set it also records Claude runs (05, 06).
set -u
cd "$(dirname "$0")/.."
PY=python
[ -x .venv/bin/python ] && PY=.venv/bin/python
mkdir -p evidence
# Never print the key itself, only whether one is set.
if [ -n "${ANTHROPIC_API_KEY:-}" ]; then AI_STATUS="API key set"; else AI_STATUS="no API key (pre-listed answers)"; fi

run() {  # run <output file> <command...>
  local out="evidence/$1"; shift
  {
    echo "\$ $*"
    echo "# date: $(date '+%Y-%m-%d %H:%M %Z') | $($PY --version) | $AI_STATUS"
    echo
    "$@" 2>&1 | grep -v " WARNING pawpal.rag: AI disabled"
  } | sed "s#$PWD#.#g" > "$out"   # hide the local folder path
  echo "wrote $out"
}

run 01_tests.txt        $PY -m pytest -v
run 02_evaluate_rag.txt $PY evaluate_rag.py
run 03_demo_rag.txt     $PY demo_rag.py
run 04_scheduler_main.txt $PY main.py

# Claude runs, only when a key is set.
if [ -n "${ANTHROPIC_API_KEY:-}" ]; then
  run 05_demo_rag_claude.txt     $PY demo_rag.py --claude
  run 06_evaluate_rag_claude.txt $PY evaluate_rag.py --claude
fi
