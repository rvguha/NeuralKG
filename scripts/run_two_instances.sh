#!/usr/bin/env bash
# Run two isolated deployments from this one checkout:
#   NeuralKG + its ARD on 8100/8088
#   Atlas    + its ARD on 8200/8188
set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-$PWD/.venv/bin/python}"
if [ ! -x "$PYTHON" ]; then PYTHON=python3; fi
if [ -f set_keys.sh ]; then set -a; source set_keys.sh; set +a; fi

OUR_APP_PORT="${OUR_APP_PORT:-8100}"
OUR_ARD_PORT="${OUR_ARD_PORT:-8088}"
ATLAS_APP_PORT="${ATLAS_APP_PORT:-8200}"
ATLAS_ARD_PORT="${ATLAS_ARD_PORT:-8188}"
STATE_DIR="${INSTANCE_STATE_DIR:-/tmp/neuralkg-two-instances}"
mkdir -p "$STATE_DIR"

stop_owned() {
  local name="$1" pid_file="$STATE_DIR/$1.pid" pid command
  [ -f "$pid_file" ] || return 0
  pid="$(sed -n '1p' "$pid_file")"
  command="$(ps -p "$pid" -o command= 2>/dev/null || true)"
  case "$command" in
    *agent_finder.py*|*uvicorn*app:app*) kill "$pid" 2>/dev/null || true ;;
  esac
  for _ in $(seq 1 30); do
    if ! kill -0 "$pid" 2>/dev/null; then return 0; fi
    sleep .1
  done
  echo "Process $pid on port $port did not stop cleanly." >&2
  exit 1
}

stop_repo_listener() {
  local port="$1" pid command cwd
  pid="$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null | sed -n '1p' || true)"
  [ -n "$pid" ] || return 0
  command="$(ps -p "$pid" -o command= 2>/dev/null || true)"
  cwd="$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p')"
  if [ "$cwd" != "$PWD" ]; then
    echo "Port $port is occupied by a process outside $PWD; refusing to stop it." >&2
    exit 1
  fi
  case "$command" in
    *agent_finder.py*|*uvicorn*app:app*) kill "$pid" 2>/dev/null || true ;;
    *) echo "Port $port is occupied by an unrecognized process; refusing to stop it." >&2; exit 1 ;;
  esac
}

for service in our-app our-ard atlas-app atlas-ard; do stop_owned "$service"; done
for port in "$OUR_APP_PORT" "$OUR_ARD_PORT" "$ATLAS_APP_PORT" "$ATLAS_ARD_PORT"; do
  stop_repo_listener "$port"
done

# The checked-in primary registry is a release artifact. Atlas has an independent index over its
# imported public OKF documents and mechanically crawled table schemas. Rebuild only when that
# corpus no longer verifies.
"$PYTHON" scripts/prepare_atlas_tables.py
"$PYTHON" scripts/prepare_atlas_sec.py
if ! "$PYTHON" scripts/sync_atlas_catalog.py --verify >/dev/null 2>&1; then
  "$PYTHON" scripts/sync_atlas_catalog.py
fi
if ! ARD_DESCRIPTOR_ROOTS="instances/atlas/catalog/attested-computations:instances/atlas/catalog/bigquery" \
     ARD_INDEX_DIR="$PWD/instances/atlas/registry" \
     "$PYTHON" registry/index.py verify >/dev/null 2>&1; then
  ARD_DESCRIPTOR_ROOTS="instances/atlas/catalog/attested-computations:instances/atlas/catalog/bigquery" \
  ARD_INDEX_DIR="$PWD/instances/atlas/registry" \
  "$PYTHON" registry/index.py build
fi

AGENT_FINDER_PORT="$OUR_ARD_PORT" AGENT_FINDER_SELF="http://127.0.0.1:$OUR_ARD_PORT/" \
  nohup "$PYTHON" agent_finder.py >"$STATE_DIR/our-ard.log" 2>&1 &
echo $! >"$STATE_DIR/our-ard.pid"

ARD_REQUIRE_RELEASE=0 \
ARD_DESCRIPTOR_ROOTS="instances/atlas/catalog/attested-computations:instances/atlas/catalog/bigquery" \
ARD_INDEX_DIR="$PWD/instances/atlas/registry" \
AGENT_FINDER_PORT="$ATLAS_ARD_PORT" AGENT_FINDER_SELF="http://127.0.0.1:$ATLAS_ARD_PORT/" \
  nohup "$PYTHON" agent_finder.py >"$STATE_DIR/atlas-ard.log" 2>&1 &
echo $! >"$STATE_DIR/atlas-ard.pid"

INSTANCE_CONFIG="$PWD/instance.yaml" AGENT_FINDER_URL="http://127.0.0.1:$OUR_ARD_PORT" \
  nohup "$PYTHON" -m uvicorn app:app --host 127.0.0.1 --port "$OUR_APP_PORT" --workers 1 \
  >"$STATE_DIR/our-app.log" 2>&1 &
echo $! >"$STATE_DIR/our-app.pid"

INSTANCE_CONFIG="$PWD/instances/atlas.yaml" AGENT_FINDER_URL="http://127.0.0.1:$ATLAS_ARD_PORT" \
  nohup "$PYTHON" -m uvicorn app:app --host 127.0.0.1 --port "$ATLAS_APP_PORT" --workers 1 \
  >"$STATE_DIR/atlas-app.log" 2>&1 &
echo $! >"$STATE_DIR/atlas-app.pid"

ready() {
  local name="$1" url="$2" port="$3" expected actual
  expected="$(sed -n '1p' "$STATE_DIR/$name.pid")"
  for _ in $(seq 1 60); do
    actual="$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null | sed -n '1p' || true)"
    if [ "$actual" = "$expected" ] && curl -fsS --max-time 2 "$url" >/dev/null 2>&1; then return 0; fi
    if ! kill -0 "$expected" 2>/dev/null; then return 1; fi
    sleep 1
  done
  return 1
}

if ! ready our-ard "http://127.0.0.1:$OUR_ARD_PORT/healthz" "$OUR_ARD_PORT" \
   || ! ready atlas-ard "http://127.0.0.1:$ATLAS_ARD_PORT/healthz" "$ATLAS_ARD_PORT" \
   || ! ready our-app "http://127.0.0.1:$OUR_APP_PORT/health" "$OUR_APP_PORT" \
   || ! ready atlas-app "http://127.0.0.1:$ATLAS_APP_PORT/health" "$ATLAS_APP_PORT"; then
  echo "A service failed to become ready. Logs are in $STATE_DIR" >&2
  tail -n 20 "$STATE_DIR"/*.log >&2 || true
  exit 1
fi

echo "NeuralKG: http://127.0.0.1:$OUR_APP_PORT  ARD: http://127.0.0.1:$OUR_ARD_PORT"
echo "Atlas:    http://127.0.0.1:$ATLAS_APP_PORT  ARD: http://127.0.0.1:$ATLAS_ARD_PORT"
echo "Logs/PIDs: $STATE_DIR"

if [ "${1:-}" = "--wait" ]; then
  cleanup() {
    "$PWD/scripts/stop_two_instances.sh" || true
  }
  trap cleanup EXIT INT TERM
  echo "Supervisor is attached; interrupt it to stop all four services."
  wait "$(sed -n '1p' "$STATE_DIR/our-app.pid")"
fi
