#!/usr/bin/env bash
set -euo pipefail
STATE_DIR="${INSTANCE_STATE_DIR:-/tmp/neuralkg-two-instances}"
for name in our-app our-ard atlas-app atlas-ard; do
  file="$STATE_DIR/$name.pid"
  [ -f "$file" ] || continue
  pid="$(sed -n '1p' "$file")"
  command="$(ps -p "$pid" -o command= 2>/dev/null || true)"
  case "$command" in
    *agent_finder.py*|*uvicorn*app:app*) kill "$pid" 2>/dev/null || true ;;
  esac
done
