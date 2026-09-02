#!/bin/sh
# Two processes, one container. The Agent Finder owns the ARD index and answers discovery on an
# internal port; the harness is the only thing Spaces exposes.
#
# `set -e` is not enough here: this shell has to outlive both children to supervise them, so a
# dead finder must take the container down rather than leave the harness up answering every
# question with "agent finder unreachable". Spaces restarts a container that exits; it does not
# notice one that is merely useless.
set -eu

: "${HARNESS_PORT:=7860}"
: "${AGENT_FINDER_PORT:=8088}"

if [ -z "${OPENROUTER_API_KEY:-}${OPENAI_API_KEY:-}${AZURE_OPENAI_API_KEY:-}${GEMINI_API_KEY:-}" ]; then
  echo "FATAL: no LLM credentials. Set OPENROUTER_API_KEY (chat) and OPENAI_API_KEY (embeddings)" >&2
  echo "       as Space secrets -- Settings > Variables and secrets." >&2
  exit 1
fi
# Embeddings and chat are separate providers here: chat goes to OpenRouter, the query embedding
# must come from the same model that built the shipped index or the vectors are meaningless.
if [ -z "${OPENAI_API_KEY:-}" ]; then
  echo "WARNING: OPENAI_API_KEY is unset. The shipped index was built with an OpenAI embedding" >&2
  echo "         model; discovery will fail without a key that can reproduce those vectors." >&2
fi

python agent_finder.py &
FINDER=$!

# Wait on readiness, not on a fixed sleep: the finder memory-maps a 52MB index and a cold Space
# is slower than a warm laptop.
i=0
while [ "$i" -lt 120 ]; do
  if python -c "import urllib.request,sys;\
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:${AGENT_FINDER_PORT}/healthz',timeout=2).status==200 else 1)" 2>/dev/null; then
    break
  fi
  kill -0 "$FINDER" 2>/dev/null || { echo "FATAL: agent finder exited during startup" >&2; exit 1; }
  i=$((i + 1)); sleep 1
done
[ "$i" -lt 120 ] || { echo "FATAL: agent finder did not become healthy in 120s" >&2; exit 1; }
echo "agent finder ready on :${AGENT_FINDER_PORT}"

uvicorn app:app --host 0.0.0.0 --port "${HARNESS_PORT}" --workers 1 &
HARNESS=$!

# Supervise. `wait -n` would be the obvious way to block until either child exits, but it is a
# bash builtin and /bin/sh here is dash, where it fails at RUNTIME while parsing cleanly -- so
# poll instead. Cheap: two kill -0 calls every five seconds.
while true; do
  if ! kill -0 "$FINDER" 2>/dev/null; then
    echo "FATAL: agent finder exited; stopping container" >&2
    kill "$HARNESS" 2>/dev/null || true
    exit 1
  fi
  if ! kill -0 "$HARNESS" 2>/dev/null; then
    echo "FATAL: harness exited; stopping container" >&2
    kill "$FINDER" 2>/dev/null || true
    exit 1
  fi
  sleep 5
done
