#!/usr/bin/env python3
"""Independent live evaluations for LLM stages not covered by the corpus rigs.

Every case has a fixed input and explicit expected structured output in
``fixtures/llm_stage_cases.json``. Run all cases, one stage, or one case:

    python tests/stage_llm_eval.py
    python tests/stage_llm_eval.py --stage adjudication
    python tests/stage_llm_eval.py --case wrong-measure

Only the named LLM stage is live. Candidate search, publisher access, and downstream retrieval are
fixed or injected, so a failure has one owner.
"""
import argparse
import asyncio
import json
import os
import sys
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import ard_client
import driver
import harness
import llm
from query_context import QueryContext

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "llm_stage_cases.json")


def context():
    return QueryContext.with_timeout(
        120, usage_ledger=llm.Ledger(), discovery_ledger=ard_client.DiscoveryUsage())


async def entity_selection(case):
    listing = "\n".join(
        f"{index}. {candidate['label']} — {candidate['description']}"
        for index, candidate in enumerate(case["candidates"]))
    raw = await llm.chat_async(
        harness._entity_selection_system(
            case["mention"], case["type"], case["question"], listing),
        case["mention"], context=context(), json_mode=True, stage="resolve-entity")
    actual = json.loads(raw).get("indices")
    return actual == case["expected_indices"], {"indices": actual}


async def concept_resolution(case):
    reported = list(enumerate(case["candidates"]))
    actual = await driver._pick_by_data_async(
        case["measure"], reported, context(), log=False)
    got = actual.get("concept") if isinstance(actual, dict) else None
    return got == case["expected_concept"], {"concept": got}


async def adjudication(case):
    actual, why = await harness._answers_async(
        case["question"], case["data"], context=context())
    return actual is case["expected_ok"], {"ok": actual, "why": why}


async def synthesis(case):
    actual = await harness.TK.synthesize_async(
        case["question"], case["data"], context=context())
    # Models commonly typeset quantities with NBSP or narrow-NBSP. Treat all Unicode whitespace as
    # one ordinary space while keeping every expected word, number, currency sign, and prohibition.
    normalize = lambda text: " ".join(str(text).split()).casefold()
    folded = normalize(actual)
    missing = [text for text in case.get("expected_contains", [])
               if normalize(text) not in folded]
    forbidden = [text for text in case.get("expected_absent", [])
                 if normalize(text) in folded]
    return not missing and not forbidden, {
        "answer": actual, "missing": missing, "forbidden": forbidden}


async def planning(case):
    async def fixed_retrieve(question, *, context):
        if "federal" in question.casefold():
            return {"value": 25, "source": "fixed federal fixture", "data": {"period": "FY2024"}}
        return {"value": 100, "source": "fixed revenue fixture", "data": {"period": "FY2024"}}

    with mock.patch.object(harness, "retrieve_for", side_effect=fixed_retrieve):
        actual = await harness._run_derive_async(
            case["question"], {"shape": "ratio"}, context=context())
    good = (actual.get("compute") == case["expected_compute"]
            and actual.get("computed") == case["expected_value"]
            and actual.get("unit") == case["expected_unit"])
    return good, {key: actual.get(key) for key in ("compute", "computed", "unit", "formula")}


RUNNERS = {
    "entity-selection": entity_selection,
    "concept-resolution": concept_resolution,
    "adjudication": adjudication,
    "synthesis": synthesis,
    "planning": planning,
}


async def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=RUNNERS)
    parser.add_argument("--case", help="run exactly one fixture ID")
    parser.add_argument("--json", help="write full results")
    args = parser.parse_args(argv)
    with open(FIXTURE, encoding="utf-8") as stream:
        fixtures = json.load(stream)
    selected = [(stage, case) for stage, cases in fixtures.items() for case in cases
                if (not args.stage or stage == args.stage)
                and (not args.case or case["id"] == args.case)]
    if args.case and not selected:
        parser.error(f"unknown case: {args.case}")
    results = []
    for stage, case in selected:
        try:
            passed, actual = await RUNNERS[stage](case)
            result = {"stage": stage, "id": case["id"], "passed": passed,
                      "expected": {key: value for key, value in case.items()
                                   if key.startswith("expected_")}, "actual": actual}
        except Exception as exc:
            result = {"stage": stage, "id": case["id"], "passed": False,
                      "error": f"{type(exc).__name__}: {exc}"[:240]}
        results.append(result)
        print(f"{'ok  ' if result['passed'] else 'FAIL'} {stage:<20} {case['id']}")
        if not result["passed"]:
            print("     " + json.dumps(result.get("actual") or result.get("error"), ensure_ascii=False))
    passed = sum(result["passed"] for result in results)
    print(f"\n{passed}/{len(results)} fixed LLM-stage cases passed")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as stream:
            json.dump({"passed": passed, "total": len(results), "results": results},
                      stream, indent=1, ensure_ascii=False)
        print(f"saved -> {args.json}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
