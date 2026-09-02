# Neural KG on Hugging Face Spaces (Docker SDK).
#
# Spaces serves ONE port (7860) and runs the container as uid 1000, so this image does two
# things the local `run.sh` does not have to: it puts the Agent Finder on an internal port and
# fronts only the harness, and it makes every path the app writes to owned by that uid.
#
# Build context is the repository root:  docker build -f deploy/hf-space/Dockerfile .
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HARNESS_PORT=7860 \
    AGENT_FINDER_PORT=8088 \
    AGENT_FINDER_URL=http://127.0.0.1:8088

RUN useradd -m -u 1000 app
WORKDIR /app

# Dependencies first so a code edit does not re-resolve the whole tree. The BigQuery extra is
# dropped: those sources need a service-account JSON that a public Space has no safe way to hold,
# and the engine already refuses a source it cannot authenticate rather than guessing.
COPY requirements.txt ./
RUN grep -v "google-cloud-bigquery" requirements.txt > /tmp/req.txt \
 && pip install --no-cache-dir -r /tmp/req.txt

# Engine.
COPY --chown=app:app *.py *.js run.sh ./
COPY --chown=app:app accessor ./accessor
COPY --chown=app:app registry ./registry
COPY --chown=app:app tools ./tools

# The ARD corpus. Absent from the GitHub repository by design -- the engine holds no knowledge of
# any particular source -- but a Space has to answer questions, so the image carries one. Swap
# these three directories (and the index built from them) to point this same engine at a different
# ARD; nothing in the code changes.
COPY --chown=app:app sources ./sources
COPY --chown=app:app corpora ./corpora
COPY --chown=app:app catalog ./catalog

# Prebuilt embedding index. Building it at boot would re-embed ~11k descriptors on every cold
# start -- minutes of latency and a real bill each time -- so it ships with the image.
COPY --chown=app:app registry/vectors.npy registry/meta.json ./registry/

COPY --chown=app:app deploy/hf-space/entrypoint.sh ./entrypoint.sh

# store.py writes its SQLite conversation store to ROOT/cache with no env override, and SQLite
# needs to create WAL and shm siblings next to the database -- a read-only or root-owned parent
# fails at `PRAGMA journal_mode=WAL`, which is how the Azure deploy broke. Create it owned by the
# runtime uid rather than letting the first write discover the problem.
RUN mkdir -p /app/cache /app/data && chown -R app:app /app
RUN chmod +x /app/entrypoint.sh

USER app
EXPOSE 7860
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:7860/healthz',timeout=4).status==200 else 1)"
ENTRYPOINT ["/app/entrypoint.sh"]
