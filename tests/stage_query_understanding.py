#!/usr/bin/env python3
"""Measure the two query-understanding calls without ARD or publisher access.

Shape is scored from existing corpus labels. Entity and measure/period outputs are retained for
inspection until those fields receive reviewed labels; they are never scored against source dirs.

Pass `--repeat N` for the number that counts. The provider routes by throughput, so one run
samples a distribution rather than measuring the code, and a case that is correct 3 times in 5 is
a failing case. `--repeat` reports strict accuracy (correct in every run) alongside what a single
run would have claimed; the gap between them is this stage's nondeterminism.
"""
import argparse, asyncio, collections, json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import harness
import rig_manifest
from query_context import QueryContext

CORPUS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "queries.json")


def _cases(limit=None, shape=None):
    raw = json.load(open(CORPUS, encoding="utf-8"))
    cases = raw if isinstance(raw, list) else (raw.get("cases") or raw.get("queries") or [])
    cases = [case for case in cases if case.get("q") and case.get("shape")]
    if shape:
        cases = [case for case in cases if case["shape"] == shape]
    return cases[:limit] if limit else cases


async def _understand(case, semaphore, context):
    async with semaphore:
        try:
            result = await harness.query_understanding_async(case["q"], context=context)
            return {**case, "got": result.get("shape"), "subshape": result.get("subshape"),
                    "entity": result.get("entity"),
                    "entity_description": result.get("entity_description"),
                    "attribute": result.get("attribute"), "period": result.get("period")}
        except Exception as exc:
            return {**case, "got": None, "error": f"{type(exc).__name__}: {exc}"[:120]}


async def _one_pass(cases, concurrency):
    context = QueryContext.with_timeout(60 * 30)
    semaphore = asyncio.Semaphore(concurrency)
    return await asyncio.gather(*[_understand(case, semaphore, context) for case in cases])


def _report(results):
    correct = [row for row in results if row["got"] == row["shape"]]
    errors = [row for row in results if row.get("error")]
    print(f"\n  shape accuracy: {len(correct)}/{len(results)} = "
          f"{len(correct) / max(len(results), 1):.1%} ({len(errors)} understanding errors)\n")
    per = collections.defaultdict(lambda: [0, 0])
    confusion = collections.Counter()
    for row in results:
        per[row["shape"]][1] += 1
        if row["got"] == row["shape"]:
            per[row["shape"]][0] += 1
        else:
            confusion[(row["shape"], row["got"])] += 1
    for shape, (good, total) in sorted(per.items()):
        print(f"  {shape:<18} {good:>4}/{total:<4} {good / max(total, 1):>8.0%}")
    if confusion:
        print("\n  most common shape misreadings:")
        for (wanted, got), count in confusion.most_common(12):
            print(f"    {count:>3}x  {wanted:<16} -> {got}")


async def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int)
    parser.add_argument("--shape")
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--repeat", type=int, default=1,
                        help="run the corpus N times; score only cases correct in EVERY run")
    parser.add_argument("--json")
    args = parser.parse_args(argv)
    cases = _cases(args.limit, args.shape)

    runs = []
    for attempt in range(args.repeat):
        if args.repeat > 1:
            print(f"\n═══ run {attempt + 1} of {args.repeat} ═══")
        results = await _one_pass(cases, args.concurrency)
        _report(results)
        runs.append(results)

    stability = {}
    if args.repeat > 1:
        stability = rig_manifest.stability(
            runs, key=lambda r: r["q"], verdict=lambda r: r["got"] == r["shape"],
            answer=lambda r: r.get("got"))
        rig_manifest.print_stability(stability)

    if args.json:
        report = {"manifest": rig_manifest.manifest(
            "query-understanding shape accuracy against corpus labels; entity, attribute and "
            "period are captured but UNSCORED -- they have no reviewed labels yet",
            repeat=args.repeat),
            "stability": stability, "runs": runs}
        json.dump(report, open(args.json, "w"), indent=1)
        print(f"\n  wrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
