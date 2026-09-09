#!/usr/bin/env python3
"""Generate the shape-catalog test corpus.

One case per generated question:

    q       the question
    gold    the shape id it should classify as; [] means NO shape applies
    family  the entry's family prefix, for per-family reporting

The questions are generated FRESH. Every entry's `examples` are part of the text that
gets indexed, so testing on them would measure memorisation of the index rather than
discrimination. The generator is shown the entry's `asks` and its `not:` neighbours and
told to write questions that land on this entry and NOT on those, then explicitly
forbidden from reusing the examples.

    python3 tests/gen_shape_queries.py [n_per_entry]
"""
import os, sys, json, yaml, re
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import llm

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shape_queries.json")
N = int(sys.argv[1]) if len(sys.argv) > 1 else 3

ENTITIES = ("real US organisations, companies, universities, counties, cities, states and "
            "federal agencies — Apple, Pfizer, Stanford, the Red Cross, Cook County, Ohio, "
            "NIH, NSF, the SEC, the IRS, CDC, Census — and real measures such as revenue, "
            "headcount, NIH funding, obesity rate, uninsured rate, federal grants, complaints")

def prompt(entry, others):
    nots = "\n".join(f"  - {k}: {v}" for k, v in (entry.get("not") or {}).items())
    return (
        f"SHAPE: {entry['title']}\n"
        f"WHAT IT ASKS: {entry['asks']}\n"
        f"AXES: {json.dumps(entry['axes'])}\n"
        + (f"DISCRIMINATORS: {json.dumps(entry['disc'])}\n" if entry.get('disc') else "")
        + (f"MUST NOT BE CONFUSED WITH:\n{nots}\n" if nots else "")
        + f"\nDO NOT REUSE OR PARAPHRASE THESE (they are in the index already):\n"
        + "\n".join(f"  - {e}" for e in entry["examples"])
        + f"\n\nWrite {N} NEW questions a person would actually type, each unambiguously this "
          f"shape and not any of the shapes it must not be confused with. Vary the phrasing and "
          f"the subject matter across the {N}. Use {ENTITIES}. Keep each under 20 words. "
          f'Return JSON: {{"questions": ["...", "..."]}}')

SYSTEM = ("You write test questions for a query-shape classifier. Each question must be "
          "answerable in principle from public US data and must instantiate the given "
          "structure exactly. Return JSON only.")

def gen(entry, tries=4):
    """Retry: the first run lost 23 of 102 entries to truncation and empty completions,
    and a silently missing entry is an untested entry rather than a failed one."""
    for attempt in range(tries):
        try:
            raw = llm.chat(SYSTEM, prompt(entry, None), json_mode=True,
                           model=llm.chat_model(), stage="gen",
                           max_tokens=1400 + 400 * attempt)
            qs = json.loads(raw).get("questions", [])
            if qs: break
        except Exception as e:
            if attempt == tries - 1:
                print(f"  FAIL {entry['id']}: {e}", file=sys.stderr); return []
    else:
        return []
    return [{"q": q.strip(), "gold": entry["id"], "family": entry["id"].split(".")[0]}
            for q in qs if isinstance(q, str) and q.strip()][:N]

# Out-of-catalog questions: the correct outcome is NO shape. Without these, no-match
# behaviour is unmeasurable — retrieval always returns its top-k.
NEGATIVES = [
 "What is the capital of France?", "Who wrote Moby Dick?", "How do I file a 990 form?",
 "What time does the IRS office in Denver close?", "Explain what a 501(c)(3) is.",
 "Write me a grant proposal for a food bank.", "Is the stock market open on Juneteenth?",
 "What's the weather in Chicago?", "Translate 'nonprofit' into Spanish.",
 "How does the NIH peer review process work?", "Should I invest in Nvidia?",
 "What does EIN stand for?", "Give me the phone number for Feeding America.",
 "Summarise the Inflation Reduction Act.", "How do I apply to Stanford?",
 "What is machine learning?", "Draft an email to my board of directors.",
 "Who is the president of the United States?", "What are the office hours for the SEC?",
 "Recommend a good book about philanthropy.",
]

def main():
    entries = yaml.safe_load(open(os.path.join(ROOT, "shapes/query-shapes.yaml")))["shapes"]
    print(f"generating {N} questions for each of {len(entries)} entries…")
    with ThreadPoolExecutor(max_workers=8) as pool:
        cases = [c for batch in pool.map(gen, entries) for c in batch]
    cases += [{"q": q, "gold": [], "family": "no-shape"} for q in NEGATIVES]
    by = {}
    for c in cases:
        by[c["family"]] = by.get(c["family"], 0) + 1
    covered = {c["gold"] for c in cases if c["gold"]}
    missing = [e["id"] for e in entries if e["id"] not in covered]
    json.dump({"n": len(cases), "entries": len(entries), "by_family": by,
               "missing_entries": missing, "cases": cases}, open(OUT, "w"), indent=1)
    print(f"wrote {len(cases)} cases to {OUT}")
    print(f"entries with no question generated: {len(missing)} {missing}")

if __name__ == "__main__":
    main()
