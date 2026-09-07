#!/usr/bin/env python3
"""Measure retrieval source recall without classification or publisher access.

The corpus labels source directories, not individual leaves, so this reports SOURCE recall only.
Embedding-prefilter recall is reported separately from reranker recall.

Two modes, because they measure different things and only one of them is what users hit:

  default    one query text -- the bare question. This measures ARD as a SERVICE: given a
             question, does the right source come back.
  --engine   the call the engine actually makes: [attribute, question] pooled, with the question
             as the re-rank query. Query understanding runs first to obtain the attribute, so this
             costs two extra model calls per case.

The gap matters. The single-text mode cannot see a defect that only appears when several texts
are pooled, and there is one: pooling by max over RAW cosine compares incomparable scales, since a
short generic attribute sits closer to everything in embedding space than a long specific
question does. Measured on one failing query, the answering leaf was rank 2 by the question text
and rank 31 after pooling. That path runs whenever an entity resolves, which is most traffic, and
this rig ran green through months of it.
"""
import argparse, collections, json, os, sys
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ard_client
import rig_manifest
from registry import index

RANKS = (1, 5, 15)


def _recall(publishers, wanted):
    return {str(rank): any(p in wanted for p in publishers[:rank]) for rank in RANKS}


def engine_texts_for(questions, concurrency=6):
    """The texts discovery would send for each question, per harness.discover_async.

    Understanding runs for real rather than being approximated: the attribute it extracts IS the
    thing under test, and a hand-written stand-in would measure a path nobody executes.

    All of it happens in ONE event loop, before the search pool starts. Calling asyncio.run per
    case inside ThreadPoolExecutor workers builds a fresh loop each time while the HTTP clients
    stay bound to another, which produced 11 "Connection error." rows out of 193 -- an artefact
    that reads exactly like a recall regression and cost me a wrong conclusion before I checked
    whether the rows had errored or merely missed.
    """
    import asyncio
    import harness
    from query_context import QueryContext

    async def run():
        gate = asyncio.Semaphore(concurrency)

        async def one(question):
            async with gate:
                ctx = await harness.query_understanding_async(
                    question, context=QueryContext.with_timeout(180))
            attribute = (ctx.get("attribute") or "").strip()
            resolvable = bool((ctx.get("entity") or "").strip()) and \
                (ctx.get("entity_status") or "").strip().lower() != "none"
            primary = (attribute or question) if resolvable else question
            secondary = question if resolvable else (attribute or question)
            return [primary, secondary]

        return await asyncio.gather(*[one(q) for q in questions])

    return dict(zip(questions, asyncio.run(run())))


def evaluate(case, texts_by_question=None):
    question, wanted = case["q"], set(case["dirs"])
    tally = ard_client.start_usage()
    try:
        texts = (texts_by_question or {}).get(question) or [question]
        prefilter = ard_client.search_many(texts, k=max(RANKS), rerank=False,
                                           rerank_query=question)
        reranked = ard_client.search_many(texts, k=max(RANKS), rerank=True,
                                          rerank_query=question)
    except Exception as exc:
        return {"q": question, "want": sorted(wanted), "shape": case.get("shape"),
                "error": str(exc)[:160], "discovery": tally.snapshot()}
    pre_pubs = [hit.get("publisher") for hit in prefilter]
    rerank_pubs = [hit.get("publisher") for hit in reranked]
    return {"q": question, "want": sorted(wanted), "shape": case.get("shape"),
            "texts": texts,
            "prefilter": {"publishers": pre_pubs, "recall": _recall(pre_pubs, wanted)},
            "reranker": {"publishers": rerank_pubs, "recall": _recall(rerank_pubs, wanted)},
            "error": None, "discovery": tally.snapshot()}


def summarize(rows):
    good = [row for row in rows if not row.get("error")]
    result = {"n": len(rows), "errors": len(rows) - len(good)}
    for stage in ("prefilter", "reranker"):
        result[stage] = {f"recall@{rank}": sum(
            bool(row[stage]["recall"][str(rank)]) for row in good) for rank in RANKS}
    return result


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--json", default="")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--engine", action="store_true",
                        help="send the texts the engine sends: [attribute, question]")
    args = parser.parse_args(argv)
    cases = [case for case in json.load(open(os.path.join(HERE, "queries.json")))["cases"]
             if case.get("dirs")]
    if args.limit:
        cases = cases[:args.limit]
    print(f"retrieving {len(cases)} source-labelled cases…")
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        texts_by_question = (engine_texts_for([c["q"] for c in cases], args.workers)
                             if args.engine else None)
        rows = list(pool.map(lambda c: evaluate(c, texts_by_question), cases))
    summary, n = summarize(rows), len(rows)
    for stage in ("prefilter", "reranker"):
        print(f"\n{stage} source recall:")
        for rank in RANKS:
            count = summary[stage][f"recall@{rank}"]
            print(f"  @{rank:<2} {count}/{n} ({100.0 * count / (n or 1):.1f}%)")
    cost = sum((row.get("discovery") or {}).get("cost_usd", 0.0) for row in rows)
    print(f"discovery cost: ${cost:.4f}")
    misses = collections.Counter()
    for row in rows:
        if not row.get("error") and not row["reranker"]["recall"]["1"]:
            got = (row["reranker"]["publishers"] or [None])[0]
            misses[f"{'/'.join(row['want'])} -> {got}"] += 1
    for label, count in misses.most_common(12):
        print(f"  {count:>3}  {label}")
    if args.json:
        report = {"manifest": rig_manifest.manifest(
            ("ENGINE texts [attribute, question] — the call discovery actually makes. "
             if args.engine else "single query text, the bare question. ")
            + "source recall against the acceptable-source sets in queries.json; LEAF recall -- "
            "whether the right capability within the source was chosen -- is UNMEASURED",
            embedding_model=__import__("llm").embed_model(),
            prompt_versions=index._prompt_versions()),
            "summary": summary, "discovery_cost_usd": round(cost, 5), "rows": rows}
        with open(args.json, "w") as stream:
            json.dump(report, stream, indent=1)
        print(f"saved -> {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
