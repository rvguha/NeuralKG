#!/usr/bin/env python3
"""Derive each entry's facets by CONSENSUS over many questions of that shape.

History, because each change was made against a measured failure:

  v1  facets read off the hand-authored `axes` through hand-written mapping tables.
      46.4% agreement on `subject`, 59.2% on `join` — one side was a 120B model reading a
      question, the other was a person assigning axis values. Nothing forced them to agree.
  v2  derived with the same model and prompt, but from the entry's 2 examples only.
      Better in aggregate (gate recall 72.5 -> 86.3%) but noisy per entry: 87 of 102 entries
      had their two examples disagree on at least one facet, so consensus was a coin flip.
      It lost values the authored axes had right — `filter.conjunction-all` came out
      `quantifier: none` when both the author and the extractor say `all`.
  v3  (this) consensus over the 2 examples plus N freshly generated questions.

The fresh questions are generated separately from tests/shape_queries.json and are NOT the
test set; deriving an entry's facets from the questions used to score it would be leakage.
Exact-string collisions with the test corpus are dropped.

    python3 tests/derive_shape_facets.py [n_fresh]
"""
import os, sys, json, re, collections
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "tests"))
import yaml, llm
from shape_facet_eval import FACETS, extract

YAML_PATH = os.path.join(ROOT, "shapes/query-shapes.yaml")
NFRESH = int(sys.argv[1]) if len(sys.argv) > 1 else 8

GEN_SYSTEM = ("You write example questions for a query-shape catalog. Each must instantiate "
              "the given structure exactly, using real US organisations, companies, "
              "universities, counties, states, agencies and real measures. Return JSON only.")

def gen(entry, n):
    nots = "\n".join(f"  - {k}: {v}" for k, v in (entry.get("not") or {}).items())
    user = (f"SHAPE: {entry['title']}\nWHAT IT ASKS: {entry['asks']}\n"
            + (f"MUST NOT BE CONFUSED WITH:\n{nots}\n" if nots else "")
            + f"EXISTING EXAMPLES:\n" + "\n".join(f"  - {e}" for e in entry["examples"])
            + f"\n\nWrite {n} MORE questions of this same shape, differently phrased and about "
              f'different subjects. Under 20 words each. JSON: {{"questions": ["..."]}}')
    for attempt in range(4):
        try:
            raw = llm.chat(GEN_SYSTEM, user, json_mode=True, model=llm.chat_model(),
                           stage="gen", max_tokens=1400 + 400 * attempt)
            if raw is None: continue
            qs = json.loads(raw).get("questions", [])
            if qs: return [q.strip() for q in qs if isinstance(q, str) and q.strip()][:n]
        except Exception:
            pass
    return []

def main():
    entries = yaml.safe_load(open(YAML_PATH))["shapes"]
    testq = {c["q"].strip().lower()
             for c in json.load(open(os.path.join(ROOT, "tests/shape_queries.json")))["cases"]}
    print(f"generating {NFRESH} fresh questions for each of {len(entries)} entries…", flush=True)
    with ThreadPoolExecutor(max_workers=8) as pool:
        fresh = list(pool.map(lambda e: gen(e, NFRESH), entries))
    leaked = sum(1 for qs in fresh for q in qs if q.strip().lower() in testq)
    fresh = [[q for q in qs if q.strip().lower() not in testq] for qs in fresh]
    print(f"  dropped {leaked} that collided with the test corpus", flush=True)

    jobs = [(i, q) for i, e in enumerate(entries) for q in (e["examples"] + fresh[i])]
    print(f"extracting facets from {len(jobs)} questions…", flush=True)
    with ThreadPoolExecutor(max_workers=8) as pool:
        got = list(pool.map(lambda j: (j[0], extract(j[1])), jobs))
    votes = collections.defaultdict(list)
    for i, f in got: votes[i].append(f)

    facets, conf = {}, {}
    for i, e in enumerate(entries):
        v = votes[i]; f, c = {}, {}
        for k in FACETS:
            vals = [x[k] for x in v if x.get(k) not in (None, "unknown")]
            if not vals: f[k], c[k] = "unknown", 0.0; continue
            top, n = collections.Counter(vals).most_common(1)[0]
            f[k], c[k] = top, n / len(vals)
        facets[e["id"]], conf[e["id"]] = f, c
    print(f"votes per entry: median {sorted(len(v) for v in votes.values())[len(votes)//2]}")
    for k in FACETS:
        m = sum(conf[i][k] for i in conf) / len(conf)
        print(f"  {k:11s} mean consensus {m:.0%}   unknown on "
              f"{sum(1 for i in facets if facets[i][k]=='unknown')}")

    src = open(YAML_PATH).read().split("\n")
    out, cur = [], None
    for ln in src:
        m = re.match(r"- id: (\S+)$", ln)
        if m: cur = m.group(1)
        if re.match(r"  facets: \{", ln): continue    # drop ANY existing line; `cur` is
                                                     # already None by the time the old one
                                                     # is reached, which stacked 204 lines
                                                     # onto 102 entries on the v3 run
        out.append(ln)
        if cur and ln.startswith("  operands:"):
            f = facets[cur]
            out.append("  facets: {%s}" % ", ".join(f"{k}: {f[k]}" for k in FACETS))
            cur = None
    open(YAML_PATH, "w").write("\n".join(out))
    json.dump(conf, open("/tmp/shape_facet_consensus.json", "w"))
    d = yaml.safe_load(open(YAML_PATH))["shapes"]
    print(f"wrote facets to {sum(1 for s in d if 'facets' in s)} of {len(d)} entries")

if __name__ == "__main__":
    main()
