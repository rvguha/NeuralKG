"""Provenance and stability for the live-LLM measurement rigs.

A number from these rigs is only meaningful with the conditions attached. Today three separate
"findings" were retracted because a report was read as a property of the code when it was a
property of the run:

  * 34 queries called flaky were ASK_LIMIT_PER_DAY 429s.
  * A "net -2 regression" was single-run noise.
  * A measurement was byte-identical to the previous one because the edit under test had been
    reverted underneath it -- nothing in the report said which commit produced it.

So every report carries a manifest, and accuracy is reported over REPEATED runs. Under
`provider: {sort: throughput}` OpenRouter may route the same request to different backends, so a
single run measures one sample of a distribution. A case that is correct 3 times out of 5 is a
failing case, not a 60% case, and `stability()` reports it that way.
"""
import hashlib
import json
import os
import subprocess
from collections import Counter
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Recorded because each one silently changes what the rig measures.
ENV_KNOBS = ("LLM_PROVIDER", "ARD_PREFILTER", "ARD_ENDPOINT", "ASK_LIMIT_PER_DAY",
             "CHAT_MODEL", "RERANK_MODEL", "SYNTHESIS_MODEL", "EMBED_MODEL")


def _git(*args, default="unknown"):
    try:
        return subprocess.run(("git",) + args, cwd=ROOT, check=True,
                              capture_output=True, text=True).stdout.strip()
    except Exception:
        return default


def corpus_digest():
    """Which ARD produced this number.

    The harness must be pointable at a different ARD, so the corpus is an INPUT to a measurement,
    not a constant. Without this, a report from a 40-source ARD and one from a 12-source ARD are
    indistinguishable, and the difference reads as a code regression.
    """
    root = os.path.join(ROOT, "sources")
    if not os.path.isdir(root):
        return {"sources": 0, "descriptors": 0, "digest": "absent"}
    digest, sources, descriptors = hashlib.sha256(), 0, 0
    for directory in sorted(os.listdir(root)):
        path = os.path.join(root, directory)
        if not os.path.isdir(path):
            continue
        # every OKF descriptor, not just a top-level manifest: a source is defined by its
        # per-capability .md files, and editing one changes what retrieval can find
        files = sorted(f for f in os.listdir(path) if f.endswith((".md", ".yaml", ".yml")))
        if not files:
            continue
        sources += 1
        digest.update(directory.encode())
        for name in files:
            descriptors += 1
            digest.update(name.encode())
            with open(os.path.join(path, name), "rb") as stream:
                digest.update(hashlib.sha256(stream.read()).digest())
    return {"sources": sources, "descriptors": descriptors,
            "digest": digest.hexdigest()[:16]}


def manifest(measurement, **extra):
    """`measurement` states in words what the number means -- and what it does NOT.

    route_eval's says "source recall; leaf recall is unmeasured". That sentence is the difference
    between a reader concluding retrieval works and concluding retrieval routes to the right
    publisher, which are not the same claim.
    """
    # the manifest is written AFTER the run, so nothing in here may raise: losing a completed
    # measurement to a missing key would be the most expensive possible way to fail
    models = {}
    try:
        import llm
        models = {"provider": llm.provider(), "chat_model": llm.chat_model()}
    except Exception as exc:
        models = {"provider": f"unavailable: {type(exc).__name__}"}
    out = {"created_at": datetime.now(timezone.utc).isoformat(),
           "commit": _git("rev-parse", "HEAD"),
           "dirty": bool(_git("status", "--porcelain", default="")),
           **models,
           "live": True, "measurement": measurement, "corpus": corpus_digest(),
           "env": {k: os.environ.get(k) for k in ENV_KNOBS if os.environ.get(k)}}
    out.update(extra)
    return out


def stability(runs, key=lambda r: r["id"], verdict=lambda r: bool(r.get("ok")),
              answer=lambda r: r.get("actual")):
    """Collapse N runs into one honest number.

    `always` is the headline: correct in EVERY run. `flaky` cases are counted as failures, not
    averaged into a fractional score -- averaging hides them, and a case that answers differently
    on identical input is broken whichever answer you got this time.
    """
    if not runs:
        return {}
    verdicts, answers = {}, {}
    for run in runs:
        for row in run:
            verdicts.setdefault(key(row), []).append(verdict(row))
            answers.setdefault(key(row), []).append(json.dumps(answer(row), sort_keys=True,
                                                              default=str))
    total = len(verdicts)
    always = [k for k, v in verdicts.items() if all(v)]
    never = [k for k, v in verdicts.items() if not any(v)]
    flaky = [k for k, v in verdicts.items() if any(v) and not all(v)]
    unstable = [k for k, v in answers.items() if len(set(v)) > 1]
    return {"runs": len(runs), "cases": total,
            "always_correct": len(always), "never_correct": len(never), "flaky": len(flaky),
            "accuracy_strict": round(100.0 * len(always) / total, 1) if total else 0.0,
            "accuracy_best_run": round(100.0 * max(sum(1 for k in verdicts if verdicts[k][i])
                                                   for i in range(len(runs))) / total, 1)
            if total else 0.0,
            "answer_unstable": len(unstable),
            "flaky_ids": sorted(flaky)[:40], "failing_ids": sorted(never)[:40]}


def print_stability(report):
    if not report:
        return
    print(f"\nstability over {report['runs']} runs ({report['cases']} cases):")
    print(f"  strict   {report['accuracy_strict']}%   correct in every run")
    print(f"  best run {report['accuracy_best_run']}%   what a single run would have reported")
    print(f"  flaky    {report['flaky']}    correct sometimes -- counted as failures")
    print(f"  hard     {report['never_correct']}    never correct")
    if report["flaky_ids"]:
        print("  flaky: " + ", ".join(str(i) for i in report["flaky_ids"][:12]))
