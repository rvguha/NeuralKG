#!/usr/bin/env python3
"""Measure retrieval source recall without classification or publisher access.

The corpus labels source directories, not individual leaves, so this reports SOURCE recall only.
Embedding-prefilter recall is reported separately from reranker recall.
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


def evaluate(case):
    question, wanted = case["q"], set(case["dirs"])
    tally = ard_client.start_usage()
    try:
        prefilter = ard_client.search(question, k=max(RANKS), rerank=False)
        reranked = ard_client.search(question, k=max(RANKS), rerank=True)
    except Exception as exc:
        return {"q": question, "want": sorted(wanted), "shape": case.get("shape"),
                "error": str(exc)[:160], "discovery": tally.snapshot()}
    pre_pubs = [hit.get("publisher") for hit in prefilter]
    rerank_pubs = [hit.get("publisher") for hit in reranked]
    return {"q": question, "want": sorted(wanted), "shape": case.get("shape"),
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
    args = parser.parse_args(argv)
    cases = [case for case in json.load(open(os.path.join(HERE, "queries.json")))["cases"]
             if case.get("dirs")]
    if args.limit:
        cases = cases[:args.limit]
    print(f"retrieving {len(cases)} source-labelled cases…")
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        rows = list(pool.map(evaluate, cases))
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
            "source recall against the acceptable-source sets in queries.json; LEAF recall -- "
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
