#!/usr/bin/env python3
"""The ceiling: let the LLM RANK the whole catalog rather than pick one entry.

Top-1 accuracy conflates two different failures — not recognising the shape at all, and
recognising it but ranking a neighbour first. Asking for an ordered top-k over all 102
cards separates them. recall@k here is the golden set: no selection stage placed in front
of the reranker can do better than this, and the gap between recall@1 and recall@5 is
exactly what a tie-breaking stage could recover.

    python3 tests/shape_rank_eval.py [topk]
    SHAPE_RERANK_MODEL=openai/gpt-oss-120b python3 tests/shape_rank_eval.py
"""
import os, sys, json, yaml, collections
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "tests"))
import llm
from shape_eval import card

TOPK = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 8
MODEL = os.environ.get("SHAPE_RERANK_MODEL") or llm.rerank_model()

SYSTEM = (
 "You classify a data question by its STRUCTURE against a catalog of query shapes.\n"
 "You are given the question and numbered shape cards. Each card gives what the shape asks, "
 "its structural facets, its distinguishing fields, and how it differs from its nearest "
 "neighbours. Neighbouring shapes describe near-identical questions and differ by one "
 "quantifier, one direction, one cardinality or one sign — those distinctions are the point.\n"
 f"Return the {TOPK} cards that BEST match, ordered most likely first. Be honest about "
 "uncertainty: if you are unsure between neighbours, rank them adjacently rather than "
 "committing.\n"
 "If the question is not a data query over a population or entity at all — a definition, an "
 "instruction, a how-to, chit-chat — return an empty list.\n"
 f'Return JSON: {{"ranked": [<card numbers, best first, at most {TOPK}>]}}')

def rank(q, entries):
    user = "QUESTION: " + q + "\n\nCANDIDATE SHAPES:\n" + "\n".join(
        card(e, i + 1) for i, e in enumerate(entries))
    for attempt in range(5):
        try:
            raw = llm.chat(SYSTEM, user, json_mode=True, model=MODEL, stage="rerank",
                           max_tokens=1200 + 400 * attempt)
            if raw is None: continue
            r = json.loads(raw).get("ranked", [])
            if not isinstance(r, list): continue
            out = [entries[n - 1]["id"] for n in r
                   if isinstance(n, int) and 1 <= n <= len(entries)]
            return out[:TOPK]
        except Exception:
            pass
    return "ERROR"

def main():
    entries = yaml.safe_load(open(os.path.join(ROOT, "shapes/query-shapes.yaml")))["shapes"]
    cases = json.load(open(os.path.join(ROOT, "tests/shape_queries.json")))["cases"]
    print(f"{len(entries)} entries, {len(cases)} cases, top-{TOPK}, model={MODEL}")
    with ThreadPoolExecutor(max_workers=5) as pool:
        got = list(pool.map(lambda c: rank(c["q"], entries), cases))

    pos = [i for i, c in enumerate(cases) if c["gold"]]
    neg = [i for i, c in enumerate(cases) if not c["gold"]]
    err = [i for i in range(len(cases)) if got[i] == "ERROR"]
    scor = [i for i in pos if i not in err]
    print(f"\nerrored: {len(err)}")
    print("--- recall@k over the WHOLE catalog (the ceiling) ---")
    for k in (1, 2, 3, 5, TOPK):
        r = sum(1 for i in scor if cases[i]["gold"] in got[i][:k]) / len(scor)
        print(f"  recall@{k:<3} {r:6.1%}")
    miss = [i for i in scor if cases[i]["gold"] not in got[i]]
    print(f"  never ranked at all: {len(miss)}  ({len(miss)/len(scor):.1%})")
    ok = sum(1 for i in neg if i not in err and got[i] == [])
    print(f"--- no-shape: returned empty {ok}/{len(neg)} ---")
    fam = collections.defaultdict(lambda: [0, 0, 0])
    for i in scor:
        f = cases[i]["gold"].split(".")[0]
        fam[f][2] += 1
        fam[f][0] += cases[i]["gold"] == (got[i][0] if got[i] else None)
        fam[f][1] += cases[i]["gold"] in got[i][:3]
    print("--- per family: @1 / @3 ---")
    for f, (h1, h3, n) in sorted(fam.items(), key=lambda x: x[1][0]/x[1][2]):
        print(f"  {f:12s} @1 {h1/n:6.1%}   @3 {h3/n:6.1%}   ({n})")
    print("--- gold ranked 2nd or 3rd: what beat it ---")
    c = collections.Counter((cases[i]["gold"], got[i][0]) for i in scor
                            if got[i] and got[i][0] != cases[i]["gold"]
                            and cases[i]["gold"] in got[i][:3])
    for (g, w), n in c.most_common(12): print(f"  {n}x  {g:32s} lost to {w}")
    json.dump({"ranked": got, "gold": [c["gold"] for c in cases],
               "q": [c["q"] for c in cases]}, open("/tmp/shape_rank_eval.json", "w"))
    print("\nartifact: /tmp/shape_rank_eval.json")

if __name__ == "__main__":
    main()
