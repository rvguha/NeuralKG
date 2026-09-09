#!/usr/bin/env python3
"""Retrieval eval over the query-shape catalog.

Two stages, mirroring source discovery:
  prefilter  embedding cosine over the entry cards -> top K
  rerank     an LLM sees the full cards for those K and picks one, or none

The embedded text is `title + asks + examples` and deliberately EXCLUDES `not:`.
Measured 2026-09-07: folding `not:` into the embedding raises mean nearest-neighbour
cosine from 0.664 to 0.701, because naming your neighbour imports their vocabulary.
It goes in the rerank card, where a model reads it, and nowhere near the embedder.

    python3 tests/shape_eval.py [K] [--limit N]
"""
import os, sys, json, yaml, collections
import numpy as np
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import llm
from registry.index import embed

K = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 12
LIMIT = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else None

SYSTEM = (
 "You classify a data question by its STRUCTURE, choosing among candidate query shapes.\n"
 "You are given the question and numbered shape cards. Each card says what the shape asks "
 "and, under 'not', how it differs from its nearest neighbours. Those distinctions are the "
 "point: neighbouring shapes describe near-identical questions and differ by one quantifier, "
 "one direction, or one sign.\n"
 "Pick the ONE card whose structure the question instantiates. If the question is not a data "
 "query over a population or entity at all — a definition, an instruction, a how-to, chit-chat "
 "— return null; returning a shape for such a question is a worse error than returning null "
 "for a real one.\n"
 'Return JSON: {"pick": <card number or null>, "why": "<10 words>"}')

def card(e, n):
    """The card the reranker reads.

    `disc` and `requires` were absent until 2026-09-07 and that was the single largest
    measured defect: the `join` family scored 33-43% under FIVE different retrieval
    strategies (vector, ungated, facet v2/v3/v4) without moving, because
    join.multi-hop / join.inner-enrichment / join.aggregate-each-side are separated ONLY by
    joinCardinality, hops and aggregationBoundary — fields the card never showed. The model
    was being asked to tell apart three entries whose visible text was nearly identical.
    """
    nots = "".join(f"\n     not {k}: {v}" for k, v in (e.get("not") or {}).items())
    f = e.get("facets") or {}
    struct = ("\n     structure: " + ", ".join(f"{k}={v}" for k, v in f.items() if v != "unknown")) if f else ""
    disc = ("\n     distinguishing: " + ", ".join(f"{k}={v}" for k, v in (e.get("disc") or {}).items())
            ) if e.get("disc") else ""
    req = ("\n     requires: " + ", ".join(f"{k}={v}" for k, v in (e.get("requires") or {}).items())
           ) if e.get("requires") else ""
    return (f"[{n}] {e['id']} — {e['title']}\n     {e['asks']}\n"
            f"     e.g. {' | '.join(e['examples'])}{struct}{disc}{req}{nots}")

ERRS = collections.Counter()

def rerank(q, cands):
    user = "QUESTION: " + q + "\n\nCANDIDATE SHAPES:\n" + "\n".join(
        card(e, i + 1) for i, e in enumerate(cands))
    for attempt in range(5):
        try:
            # gpt-oss-20b returns content=None under load and when the budget leaves no
            # room after reasoning. Both are retryable; a swallowed None became 19% of the
            # first run and would have been reported as engine failure.
            raw = llm.chat(SYSTEM, user, json_mode=True, model=os.environ.get("SHAPE_RERANK_MODEL") or llm.rerank_model(),
                           stage="rerank", max_tokens=900 + 300 * attempt)
            if raw is None:
                ERRS["null-completion"] += 1
                continue
            pick = json.loads(raw).get("pick")
            if pick is None: return None
            if isinstance(pick, int) and 1 <= pick <= len(cands): return cands[pick - 1]["id"]
        except Exception as e:
            ERRS[f"{type(e).__name__}: {str(e)[:70]}"] += 1
            if attempt == 2: return "ERROR"
    ERRS["no-valid-pick"] += 1
    return "ERROR"

def main():
    entries = yaml.safe_load(open(os.path.join(ROOT, "shapes/query-shapes.yaml")))["shapes"]
    ids = [e["id"] for e in entries]
    corpus = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                         "shape_queries.json")))
    cases = corpus["cases"][:LIMIT] if LIMIT else corpus["cases"]
    print(f"{len(entries)} entries, {len(cases)} cases, K={K}, "
          f"embed={llm.embed_model()} rerank={llm.rerank_model()}")

    E = np.array(embed([f"{e['title']}. {e['asks']} Examples: {' | '.join(e['examples'])}"
                        for e in entries]))
    E = E / np.linalg.norm(E, axis=1, keepdims=True)
    Q = np.array(embed([c["q"] for c in cases]))
    Q = Q / np.linalg.norm(Q, axis=1, keepdims=True)
    S = Q @ E.T

    order = np.argsort(-S, axis=1)
    def run(i):
        cands = [entries[j] for j in order[i][:K]]
        return rerank(cases[i]["q"], cands)
    with ThreadPoolExecutor(max_workers=5) as pool:
        picks = list(pool.map(run, range(len(cases))))

    pos = [i for i, c in enumerate(cases) if c["gold"]]
    neg = [i for i, c in enumerate(cases) if not c["gold"]]
    err = [i for i, p in enumerate(picks) if p == "ERROR"]
    rank = {i: (list(order[i]).index(ids.index(cases[i]["gold"])) + 1) for i in pos}

    print(f"\nerrored (excluded from accuracy): {len(err)}")
    for k, v in ERRS.most_common(6): print(f"    {v:4d}x {k}")
    print("--- prefilter (embedding only) ---")
    for k in (1, 3, 5, K):
        print(f"  recall@{k:<3} {sum(1 for i in pos if rank[i] <= k) / len(pos):6.1%}")
    print(f"  median gold rank {int(np.median([rank[i] for i in pos]))}")
    scor = [i for i in pos if i not in err]
    print("--- rerank (top-1 accuracy) ---")
    hit = sum(1 for i in scor if picks[i] == cases[i]["gold"])
    inpool = [i for i in scor if rank[i] <= K]
    print(f"  overall            {hit / len(scor):6.1%}  ({hit}/{len(scor)})")
    print(f"  gold in pool only  {sum(1 for i in inpool if picks[i] == cases[i]['gold']) / len(inpool):6.1%}")
    print(f"  said null on a real question: {sum(1 for i in scor if picks[i] is None)}")
    print("--- no-shape (out-of-catalog) ---")
    ok = sum(1 for i in neg if picks[i] is None)
    print(f"  correctly returned null {ok}/{len(neg)} = {ok / len(neg):.1%}")
    for i in neg:
        if picks[i] is not None: print(f"    FALSE ACCEPT  {cases[i]['q'][:52]:54s} -> {picks[i]}")

    fam = collections.defaultdict(lambda: [0, 0])
    for i in scor:
        fam[cases[i]["family"]][1] += 1
        fam[cases[i]["family"]][0] += picks[i] == cases[i]["gold"]
    print("--- per family ---")
    for f, (h, n) in sorted(fam.items(), key=lambda x: x[1][0] / x[1][1]):
        print(f"  {f:12s} {h/n:6.1%}  ({h}/{n})")
    conf = collections.Counter((cases[i]["gold"], picks[i]) for i in scor
                               if picks[i] != cases[i]["gold"] and picks[i])
    print("--- top confusions ---")
    for (g, p), n in conf.most_common(15): print(f"  {n}x  {g:32s} -> {p}")
    json.dump({"picks": picks, "gold": [c["gold"] for c in cases],
               "q": [c["q"] for c in cases], "rank": {str(k): v for k, v in rank.items()}},
              open("/tmp/shape_eval.json", "w"))
    print("\nartifact: /tmp/shape_eval.json")

if __name__ == "__main__":
    main()
