#!/usr/bin/env python3
"""Run the test-query corpus against a running harness and report by shape.

    ./run.sh                                   # harness must be up on :8099
    python3 tests/run_queries.py --sample 20   # quick smoke across shapes
    python3 tests/run_queries.py --shape ranking
    python3 tests/run_queries.py               # everything (slow: minutes to hours)

A case passes when the engine does what the corpus DECLARES it should:

    expect answer  -> an answer came back, and the classified shape matched
    expect refuse  -> it was refused (no fabricated result)
    expect either  -> anything except a crash; shape still recorded

Shape mismatches are reported separately from outright failures, because a question
answered correctly via a different-but-valid plan is a weaker problem than a wrong answer
or a fabricated one. Results are written to tests/results.json for diffing between runs.
"""
import argparse, json, os, subprocess, sys, time, urllib.request, urllib.error
from collections import defaultdict
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))          # importable without PYTHONPATH=. from anywhere
CORPUS = os.path.join(HERE, "queries.json")
RESULTS = os.path.join(HERE, "results.json")
URL = os.getenv("ARD_URL", "http://127.0.0.1:8099/ask")

REFUSAL_MARKERS = ("needs a source that can", "needs a capability none", "no source could answer",
                   "cannot rank", "not implemented", "could not decompose", "too expensive")
REQUIRED_CREDENTIALS = ("AZURE_OPENAI_ENDPOINT", "OPENAI_API_KEY", "GOOGLE_CLOUD_PROJECT",
                        "CENSUS_API_KEY", "DATA_GOV_API_KEY")


def deployment_manifest(url=URL, opener=urllib.request.urlopen):
    """Record environment identity without ever recording credential values."""
    base = url.rsplit("/", 1)[0]
    health, health_error = {}, None
    try:
        with opener(base + "/healthz", timeout=10) as response:
            health = json.load(response)
    except Exception as exc:
        health_error = str(exc)[:160]
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=os.path.dirname(HERE),
                                check=True, capture_output=True, text=True).stdout.strip()
    except Exception:
        commit = "unknown"
    import llm
    return {"created_at": datetime.now(timezone.utc).isoformat(), "target": url,
            "local_commit": commit, "health": health, "health_error": health_error,
            "provider": llm.provider(), "chat_model": llm.chat_model(),
            "embedding_model": llm.embed_model(),
            "credentials_present": {name: bool(os.getenv(name)) for name in REQUIRED_CREDENTIALS}}


def select_deployment_smoke(cases):
    """One case per source plus one per complex execution shape, preserving corpus order."""
    selected, seen_sources, seen_shapes = [], set(), set()
    complex_shapes = {"comparison", "timeseries", "ranking", "aggregate", "filtered-subset",
                      "ratio", "correlation"}
    for case in cases:
        sources = set(case.get("dirs") or [])
        adds_source = bool(sources - seen_sources)
        adds_shape = case.get("shape") in complex_shapes and case.get("shape") not in seen_shapes
        if adds_source or adds_shape:
            selected.append(case)
            seen_sources.update(sources)
            if case.get("shape") in complex_shapes:
                seen_shapes.add(case["shape"])
    return selected


def ask(q, timeout):
    # `streaming: false`, because /ask streams Server-Sent Events by DEFAULT and `json.load` on a
    # `data: {...}` body raises immediately. Every query then failed identically at the transport
    # layer, which reads as the whole corpus regressing at once rather than as one wrong request
    # -- the same decode error on unrelated shapes is the tell that it is the client, not the code.
    body = json.dumps({"question": q, "streaming": False}).encode()
    req = urllib.request.Request(URL, data=body, headers={"Content-Type": "application/json"})
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.load(r)
        if isinstance(d, dict) and isinstance(d.get("messages"), list):
            terminal = next((message.get("content") for message in reversed(d["messages"])
                             if message.get("message_type") == "nlws"), None)
            error = next((message.get("content") for message in reversed(d["messages"])
                          if message.get("message_type") == "error"), None)
            d = terminal if isinstance(terminal, dict) else {"error": error or "no answer"}
        return d, round(time.monotonic() - t0, 1), None
    except Exception as e:
        return None, round(time.monotonic() - t0, 1), str(e)[:120]


def classify(case, d, err):
    """-> (status, note). status in pass | fail | shape | error"""
    if err or d is None:
        return "error", err or "no response"
    answer, error = d.get("answer"), d.get("error")
    refused = bool(error) or (answer and any(m in answer for m in REFUSAL_MARKERS))
    got_shape = d.get("shape")
    want = case["expect"]
    if want == "refuse":
        return ("pass", "refused as expected") if refused else ("fail", "ANSWERED a question no source can serve")
    if want == "either":
        return "pass", ("refused" if refused else "answered")
    if refused:
        return "fail", (error or answer or "")[:110]
    # source-routing check: a query tagged with expected dirs must land in one of them
    want_dirs = case.get("dirs")
    got_pub = ((d.get("source") or {}).get("publisher") or "")
    if want_dirs and got_pub and got_pub not in want_dirs:
        return "route", f"routed to {got_pub}, expected {want_dirs}"
    # point / status / entity-list are all single-entity direct lookups that route identically;
    # the classifier's choice among them is a label quibble, not a wrong plan. Only flag a shape
    # mismatch that crosses into a genuinely different execution path (ranking, ratio, correlation…).
    SINGLE = {"point", "status", "entity-list"}
    if got_shape and got_shape != case["shape"] and not (got_shape in SINGLE and case["shape"] in SINGLE):
        return "shape", f"expected {case['shape']}, planned {got_shape}"
    return "pass", ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, help="run N cases spread across shapes")
    ap.add_argument("--shape", help="only this shape")
    ap.add_argument("--expect", help="only cases with this expectation")
    ap.add_argument("--timeout", type=int, default=600)   # a complicated plan may fan out for minutes
    ap.add_argument("--workers", type=int, default=4, help="concurrent client requests")
    ap.add_argument("--stop-on-fail", action="store_true")
    ap.add_argument("--coverage", action="store_true", help="one point/entity-list/topical query per source")
    ap.add_argument("--deployment-smoke", action="store_true",
                    help="representative deployment cases: each source and complex shape")
    ap.add_argument("--preflight-only", action="store_true")
    a = ap.parse_args()

    corpus = json.load(open(CORPUS))
    cases = corpus["cases"]
    if a.shape:
        cases = [c for c in cases if c["shape"] == a.shape]
    if a.expect:
        cases = [c for c in cases if c["expect"] == a.expect]
    if a.coverage:                                  # exactly one tagged query per source dir
        picked, seen = [], set()
        for c in cases:
            for d in c.get("dirs", []):
                if d not in seen:
                    seen.add(d); picked.append(c); break
        cases = picked
    if a.deployment_smoke:
        cases = select_deployment_smoke(cases)
    if a.sample:                                    # round-robin the shapes so a sample stays broad
        by = defaultdict(list)
        for c in cases:
            by[c["shape"]].append(c)
        picked, i = [], 0
        while len(picked) < min(a.sample, len(cases)):
            for s in sorted(by):
                if i < len(by[s]) and len(picked) < a.sample:
                    picked.append(by[s][i])
            i += 1
        cases = picked

    manifest = deployment_manifest()
    if a.preflight_only:
        print(json.dumps(manifest, indent=2))
        return 1 if manifest["health_error"] else 0
    workers = 1 if a.stop_on_fail else max(1, a.workers)   # early-stop only makes sense sequentially
    n = len(cases)
    print(f"running {n} of {corpus['n']} cases against {URL}  ({workers} worker{'s' * (workers > 1)})\n")
    tally, results = defaultdict(int), {}
    MARK = {"pass": "ok  ", "fail": "FAIL", "shape": "shape", "route": "route", "error": "ERR "}

    def run_one(idx, c):
        d, secs, err = ask(c["q"], a.timeout)
        status, note = classify(c, d, err)
        return idx, c, d, secs, status, note

    from concurrent.futures import ThreadPoolExecutor, as_completed
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(run_one, i, c) for i, c in enumerate(cases, 1)]
        for fut in as_completed(futs):
            idx, c, d, secs, status, note = fut.result()
            done += 1
            tally[status] += 1
            results[idx] = {**c, "status": status, "note": note, "secs": secs,
                            "got_shape": (d or {}).get("shape"),
                            "answer": ((d or {}).get("answer") or (d or {}).get("error") or "")[:200]}
            # [done/N] tracks completions (order varies with workers); the case's own index is shown too
            print(f"[{done:>3}/{n}] {MARK[status]} {secs:>6.1f}s  {c['shape']:<16} {c['q'][:60]}", flush=True)
            if note and status != "pass":
                print(f"                            -> {note}", flush=True)
            if a.stop_on_fail and status in ("fail", "error"):
                break

    out = [results[i] for i in sorted(results)]           # write results.json in original case order
    json.dump({"manifest": manifest, "ran": len(out), "tally": dict(tally), "cases": out},
              open(RESULTS, "w"), indent=1)
    print("\n" + "=" * 72)
    total = sum(tally.values()) or 1
    for k in ("pass", "shape", "route", "fail", "error"):
        print(f"  {k:<6} {tally[k]:>4}   {tally[k] / total * 100:>5.1f}%")
    per = defaultdict(lambda: [0, 0])
    for r in out:
        per[r["shape"]][1] += 1
        per[r["shape"]][0] += r["status"] == "pass"
    print("\n  by shape:")
    for s, (p, n) in sorted(per.items()):
        print(f"    {s:<18} {p:>3}/{n:<3}")
    print(f"\n  wrote {RESULTS}")
    return 1 if tally["fail"] or tally["error"] else 0


if __name__ == "__main__":
    sys.exit(main())
