#!/usr/bin/env python3
"""Facet-gated shape classification.

Vector retrieval fails here (recall@1 24.2%, recall@12 76.8% over 102 entries) because it
matches a question's SUBJECT MATTER against a description of STRUCTURE. This replaces it:

  1. an LLM reads the question alone and emits structural facets — no catalog shown
  2. the catalog is filtered by facet agreement, not cosine
  3. the survivors are reranked

Facets come from fields the entries already carry (`axes`, `operands`), so no new
annotation was needed. Every facet may be "unknown": a wrong hard filter drops the gold
entry, which is the exact failure vector retrieval had, so gating is by AGREEMENT SCORE
with a widening fallback rather than by equality.

    python3 tests/shape_facet_eval.py [--limit N]
"""
import os, sys, json, yaml, collections
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import llm
from shape_eval import card, SYSTEM as RERANK_SYSTEM   # same card + rerank prompt, for comparability

LIMIT = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else None
MIN_POOL = 6
# `subject` is excluded by default: 49.7% agreement even when derived, i.e. it is not a
# function of the question. Every high-recall subset drops it.
SUBSET = (os.environ.get("SHAPE_FACETS") or "join,subject,result,time,quantifier,operands").split(",")

FACET_SYSTEM = """You extract the STRUCTURE of a data question. You never answer it and you
never name a data source. Return JSON with exactly these keys:

operands   1 if the question involves one measure or population; 2 if it relates two
           (two measures, two populations, a ratio, an association); 3 if more.
join       How the operands are brought together. Two measures OF THE SAME ENTITY combined
           in any way — a ratio, a difference, a per-unit rate, two predicates on one
           population — is "same-entity", NOT "none". Use "none" only when a single measure
           is involved and nothing is combined.
             none                  one measure, nothing combined
             same-entity           measures of the same entity or population combined
             related-entity        a parent/subsidiary/containing-area relation is traversed
             entity-to-population  one named entity is placed against its whole population
             across-populations    two separately specified populations are matched
             cross-publisher       two data publishers are compared WITH EACH OTHER
             spatial               matching is by location, containment or distance
subject    WHETHER THE QUESTION NAMES ITS ENTITIES. A generic noun is NOT a named entity:
           "a nonprofit", "nonprofits", "a company", "universities", "counties" name no
           entity. Deciding this by whether an entity TYPE is mentioned is wrong; decide it
           by whether a PARTICULAR one is identified.
             named-one           exactly one entity is named ("Harvard", "Apple", "Ohio")
             named-many          two or more are named ("Harvard or MIT", "Ford and GM")
             unnamed-population  no entity is named; the question quantifies over a type
                                 ("which university gets the most", "universities over $1B",
                                 "the average across counties") — this holds even when the
                                 ANSWER is a single entity, and even when a data programme
                                 or agency is named ("which company gets the most from SEC")
             none                no entity and no population; a topic or a definition
           "Which university has the largest endowment?" is unnamed-population with an
           entity result. "How much does Harvard get?" is named-one with a number result.
result     What one answer looks like. Distinguish ONE from SEVERAL:
             number, boolean, category, time, ordinal   a single value of that kind
             entity            exactly ONE named thing ("which university has the most")
             entity-list       SEVERAL named things ("which universities have more than")
             record            one thing's full set of attributes
             record-list       several records
             ranked-list       several, where the ORDER is part of the answer
             grouped           one value per group, the grouping being part of the answer
             series            one value per period
time       none          no time dimension is asked about
           point         one stated period
           range         EVERY period across an interval is wanted
           two-endpoints only the start and end matter; the middle need not exist
           window-pair   two periods compared at matching calendar positions
           duration      how long something lasted
           event         the period is fixed by a named event rather than a date
           vintage       which RELEASE of the data, as distinct from which period
quantifier none | threshold | all | any | negation | universal | existential | count
           Use "all" when several predicates must hold together, "any" when one suffices,
           "threshold" for a single numeric cut-off, "count" when a cardinality is required.

Use "unknown" for any facet the question does not settle. Guessing is worse than "unknown":
a wrong facet removes the correct shape from consideration entirely."""

# `subject` was removed after measuring 49.7% agreement between the question side and the
# entry side even when both were derived by the same model from the same prompt — i.e. it is
# not a function of the question. Every high-recall facet subset dropped it.
# `subject` was removed after scoring 49.7%, then RESTORED: that measurement was of a
# broken definition. The vocabulary never said "named" means the question literally names
# the entity, so `population` was read as `named-set` 47 times. The rule that fixes it is
# already in the engine — _structure_understanding_system() rule 2, "A GENERIC NOUN IS NOT
# A NAMED ENTITY", worth 6.5 points on the 308-case corpus — and is quoted above.
FACETS = ("operands", "join", "subject", "result", "time", "quantifier")

# entry axes -> facet vocabulary
JOINMAP = {"none": "none", "same-entity-measures": "same-entity", "related-entity": "related-entity",
           "entity-to-population": "entity-to-population", "population-pair": "across-populations",
           "cross-publisher": "cross-publisher", "cross-type": "across-populations"}
SUBJMAP = {"named-entity": "one-entity", "named-set": "named-set", "open-population": "population",
           "filtered-population": "filtered-population", "no-entity": "none",
           "entity-and-relations": "one-entity", "population-pair": "population"}
TIMEMAP = {"unspecified": "none", "point": "point", "range": "range", "two-endpoints": "two-endpoints",
           "window-pair": "window-pair", "duration": "duration", "event-anchored": "event",
           "vintage": "vintage"}
QMAP = {"none": "none", "threshold": "threshold", "all": "all", "any": "any",
        "not-closed": "negation", "not-open": "negation", "universal": "universal",
        "existential": "existential", "count-predicate": "count"}

def entry_facets(e):
    # Prefer the DERIVED facets (tests/derive_shape_facets.py): same model, same prompt as the
    # question side, so agreement holds by construction. The hand-mapped axes below are the
    # fallback and scored 46.4% on `subject` against the extractor.
    if e.get("facets"):
        return {k: e["facets"][k] for k in FACETS}
    a = e["axes"]
    j = JOINMAP.get(a["join"], a["join"])
    if (e.get("disc") or {}).get("joinRelation") == "spatial": j = "spatial"
    return {"operands": e["operands"], "join": j, "subject": SUBJMAP.get(a["subject"], a["subject"]),
            "result": a["result"], "time": TIMEMAP.get(a["time"], a["time"]),
            "quantifier": QMAP.get(a["quantifier"], a["quantifier"])}

def extract(q):
    for attempt in range(4):
        try:
            raw = llm.chat(FACET_SYSTEM, "QUESTION: " + q, json_mode=True,
                           model=llm.chat_model(), stage="facet", max_tokens=600 + 200 * attempt)
            if raw is None: continue
            f = json.loads(raw)
            if isinstance(f.get("operands"), str) and f["operands"].isdigit():
                f["operands"] = int(f["operands"])
            return {k: f.get(k, "unknown") for k in FACETS}
        except Exception:
            pass
    return {k: "unknown" for k in FACETS}

def gate(pred, entries, efac):
    """Score every entry by facet agreement; keep the best band, widening until MIN_POOL."""
    use = SUBSET or FACETS
    known = [k for k in use if pred.get(k) not in (None, "unknown")]
    if not known: return entries
    score = [sum(1 for k in known if efac[i][k] == pred[k]) for i in range(len(entries))]
    best = max(score)
    for cut in range(best, -1, -1):
        pool = [entries[i] for i in range(len(entries)) if score[i] >= cut]
        if len(pool) >= MIN_POOL: return pool
    return entries

def rerank(q, cands):
    user = "QUESTION: " + q + "\n\nCANDIDATE SHAPES:\n" + "\n".join(
        card(e, i + 1) for i, e in enumerate(cands))
    for attempt in range(5):
        try:
            raw = llm.chat(RERANK_SYSTEM, user, json_mode=True, model=llm.rerank_model(),
                           stage="rerank", max_tokens=900 + 300 * attempt)
            if raw is None: continue
            pick = json.loads(raw).get("pick")
            if pick is None: return None
            if isinstance(pick, int) and 1 <= pick <= len(cands): return cands[pick - 1]["id"]
        except Exception:
            pass
    return "ERROR"

def main():
    entries = yaml.safe_load(open(os.path.join(ROOT, "shapes/query-shapes.yaml")))["shapes"]
    efac = [entry_facets(e) for e in entries]
    corpus = json.load(open(os.path.join(ROOT, "tests/shape_queries.json")))
    cases = corpus["cases"][:LIMIT] if LIMIT else corpus["cases"]
    print(f"{len(entries)} entries, {len(cases)} cases, facet gate, model={llm.chat_model()}")

    with ThreadPoolExecutor(max_workers=6) as pool:
        preds = list(pool.map(lambda c: extract(c["q"]), cases))
    pools = [gate(preds[i], entries, efac) for i in range(len(cases))]
    with ThreadPoolExecutor(max_workers=5) as pool:
        picks = list(pool.map(lambda i: rerank(cases[i]["q"], pools[i]), range(len(cases))))

    pos = [i for i, c in enumerate(cases) if c["gold"]]
    neg = [i for i, c in enumerate(cases) if not c["gold"]]
    scor = [i for i in pos if picks[i] != "ERROR"]
    survived = [i for i in pos if cases[i]["gold"] in {e["id"] for e in pools[i]}]
    sizes = sorted(len(pools[i]) for i in pos)

    print(f"\nerrored: {sum(1 for p in picks if p == 'ERROR')}")
    print("--- facet gate ---")
    print(f"  gold survives the gate   {len(survived)/len(pos):6.1%}   (vector recall@12 was 76.8%)")
    print(f"  pool size  median {sizes[len(sizes)//2]}  p90 {sizes[int(len(sizes)*.9)]}  max {sizes[-1]}  (of {len(entries)})")
    print("--- per-facet accuracy (predicted vs gold entry) ---")
    for k in FACETS:
        n = [i for i in pos if preds[i][k] != "unknown"]
        hit = sum(1 for i in n if preds[i][k] == efac[entries.index(next(e for e in entries if e['id']==cases[i]['gold']))][k])
        unk = sum(1 for i in pos if preds[i][k] == "unknown")
        print(f"  {k:11s} {hit/max(len(n),1):6.1%}  (unknown on {unk})")
    hit = sum(1 for i in scor if picks[i] == cases[i]["gold"])
    print("--- final ---")
    print(f"  top-1 accuracy           {hit/len(scor):6.1%}  ({hit}/{len(scor)})")
    print(f"  top-1 when gold survived {sum(1 for i in scor if i in survived and picks[i]==cases[i]['gold'])/max(len([i for i in scor if i in survived]),1):6.1%}")
    ok = sum(1 for i in neg if picks[i] is None)
    print(f"  no-shape correct         {ok}/{len(neg)}")
    fam = collections.defaultdict(lambda: [0, 0])
    for i in scor:
        f = cases[i]["gold"].split(".")[0]; fam[f][1] += 1; fam[f][0] += picks[i] == cases[i]["gold"]
    print("--- per family ---")
    for f, (h, n) in sorted(fam.items(), key=lambda x: x[1][0]/x[1][1]):
        print(f"  {f:12s} {h/n:6.1%}  ({h}/{n})")
    json.dump({"picks": picks, "gold": [c["gold"] for c in cases], "preds": preds,
               "pool_sizes": [len(p) for p in pools]}, open("/tmp/shape_facet_eval.json", "w"))
    print("\nartifact: /tmp/shape_facet_eval.json")

if __name__ == "__main__":
    main()
