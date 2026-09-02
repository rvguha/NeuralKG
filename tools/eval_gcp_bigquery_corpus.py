#!/usr/bin/env python3
"""Evaluate semantic discovery over the standalone GCP BigQuery ARD corpus."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import llm


ROOT = Path(__file__).resolve().parent.parent
ARD_ROOT = ROOT / "corpora" / "gcp-bigquery" / "ard"
DEFAULT_QUERIES = ROOT / "tests" / "fixtures" / "gcp_bigquery_corpus_queries.json"


def entry_text(entry: dict) -> str:
    parts = [entry.get("displayName", ""), entry.get("description", "")]
    parts.extend(entry.get("representativeQueries") or [])
    parts.extend(entry.get("tags") or [])
    parts.extend([
        entry.get("okf:publisher", ""), entry.get("okf:resource", ""),
        entry.get("okf:datasetId", ""), entry.get("okf:entityType", ""),
    ])
    return ". ".join(str(part) for part in parts if part)


def normalized(vectors: np.ndarray) -> np.ndarray:
    return vectors / np.clip(np.linalg.norm(vectors, axis=1, keepdims=True), 1e-9, None)


def evaluate(query_path: Path = DEFAULT_QUERIES, top_k: int = 5) -> dict:
    entries = [json.loads(path.read_text(encoding="utf-8"))
               for path in sorted(ARD_ROOT.glob("*/*.json"))]
    cases = json.loads(query_path.read_text(encoding="utf-8"))
    corpus_vectors = normalized(np.asarray(llm.embed([entry_text(entry) for entry in entries]),
                                           dtype=np.float32))
    query_vectors = normalized(np.asarray(llm.embed([case["query"] for case in cases]),
                                          dtype=np.float32))
    results = []
    for case, query_vector in zip(cases, query_vectors):
        scores = corpus_vectors @ query_vector
        order = np.argsort(-scores)[:top_k]
        hits = [{
            "rank": rank,
            "identifier": entries[index]["identifier"],
            "displayName": entries[index]["displayName"],
            "score": round(float(scores[index]) * 100, 3),
        } for rank, index in enumerate(order, 1)]
        expected_rank = next((hit["rank"] for hit in hits
                              if hit["identifier"] == case["expected"]), None)
        results.append({**case, "expected_rank": expected_rank, "hits": hits})
    count = len(results)
    top1 = sum(result["expected_rank"] == 1 for result in results)
    top5 = sum(result["expected_rank"] is not None for result in results)
    return {
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "corpus_entries": len(entries),
        "queries": count,
        "embedding_model": llm.embed_model(),
        "top1": top1,
        "top1_accuracy": round(top1 / count, 4),
        f"top{top_k}": top5,
        f"top{top_k}_accuracy": round(top5 / count, 4),
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--queries", type=Path, default=DEFAULT_QUERIES)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = evaluate(args.queries, args.top_k)
    rendered = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "results"}, indent=2))
    for result in report["results"]:
        status = "PASS" if result["expected_rank"] is not None else "MISS"
        print(f"{status:4} rank={result['expected_rank'] or '-'} {result['query']}")
        if status == "MISS":
            print("     expected:", result["expected"])
            print("     top hit: ", result["hits"][0]["identifier"], result["hits"][0]["score"])


if __name__ == "__main__":
    main()
