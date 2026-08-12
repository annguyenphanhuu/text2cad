"""
Stability check for the unified_processing chain.

WHY: expert_llm drives unified_processing and was the only LLM in chatbot.py left
without temperature=0. Template I/O logs showed the same user_text returning
shape_type "Sheet" on some runs and "unknown"/"L-bracket" on others — which
changes the confirm template and the generated geometry. Before any prompt
reordering work, we need to know the chain answers the same way twice.

WHAT IT DOES: runs each case N times through unified_processing only (not the
full pipeline — no code generation, no FreeCAD export) and reports which cases
returned different answers across runs.

Cases come from tests/fixtures/pptx_ok_cases.json, extracted from the
2511_ATN_TEST_REPORT_Roman.pptx test report — only the cases the testers marked
(OK), so a flip here is a regression against known-good behaviour.

Compared fields are the structural decisions the rest of the pipeline branches
on. Free-text fields (description, title, questions wording) are deliberately
NOT compared — they are natural language and will differ harmlessly.

USAGE (must run where langchain is installed, i.e. inside the container):

    python tests/check_stability.py --runs 3 --limit 10        # cheap smoke test
    python tests/check_stability.py --runs 3 --per-section 1   # one per shape type
    python tests/check_stability.py --runs 3                   # all 169 cases

Start with --limit. Each run is one LLM call per case, so
--runs 3 over all 169 cases is ~507 calls. The script prints the measured cost
and refuses to start without --yes once the estimate exceeds --max-cost.
"""
import argparse
import asyncio
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FIXTURE = ROOT / "tests" / "fixtures" / "pptx_ok_cases.json"

# Fields the pipeline branches on. shape_type picks the confirm template and the
# FreeCAD builder; missing_info decides whether we ask the user or proceed;
# complexity_level gates the step planner. A flip in any of these is a real
# behaviour change, not phrasing noise.
COMPARED = ("shape_type", "missing_info", "complexity_level",
            "skip_questions_requested", "step_by_step_requested", "design_type")


def load_cases(limit=None, per_section=None, section=None):
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    cases = data["cases"]
    if section:
        cases = [c for c in cases if c.get("section") and
                 section.lower() in c["section"].lower()]
    if per_section:
        seen = defaultdict(int)
        picked = []
        for c in cases:
            key = c.get("section") or "?"
            if seen[key] < per_section:
                seen[key] += 1
                picked.append(c)
        cases = picked
    if limit:
        cases = cases[:limit]
    return cases


def fingerprint(obj):
    """Structural answer of the chain, as a comparable tuple."""
    return tuple((f, getattr(obj, f, None)) for f in COMPARED)


async def run_case(agent, case, runs):
    """Invoke unified_processing `runs` times on one case; return fingerprints."""
    from src.utils.cost_tracking_wrapper import ainvoke_with_cost_tracking

    results = []
    for i in range(runs):
        # Fresh session id per run so no session-level cache short-circuits the
        # call and makes an unstable chain look stable.
        session_id = f"stability_{case['id']}_r{i}"
        try:
            out = await ainvoke_with_cost_tracking(
                "unified_processing",
                agent.unified_processing_chain.ainvoke,
                {
                    "user_text": f"[USER]: {case['prompt']}",
                    "rules_context": "",
                    "examples_context": "",
                    "session_id": session_id,
                    "material": "",
                    "user_language": "French",
                },
                agent._get_cost_tracker(session_id),
                agent.model_names["expert"],
            )
            obj = out.get("unified_output_obj") if isinstance(out, dict) else None
            results.append(fingerprint(obj) if obj else ("PARSE_FAILED",))
        except Exception as exc:  # a failed call is itself instability
            results.append((f"ERROR: {type(exc).__name__}: {str(exc)[:80]}",))
    return results


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3,
                    help="invocations per case (>=2 to detect instability)")
    ap.add_argument("--limit", type=int, help="only the first N cases")
    ap.add_argument("--per-section", type=int,
                    help="at most N cases per section (good coverage, low cost)")
    ap.add_argument("--section", help="only sections matching this substring")
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--max-cost", type=float, default=2.0,
                    help="refuse to start above this estimate without --yes")
    ap.add_argument("--yes", action="store_true", help="skip the cost prompt")
    ap.add_argument("--out", default="stability_report.json")
    args = ap.parse_args()

    if args.runs < 2:
        sys.exit("--runs must be >= 2, otherwise there is nothing to compare.")
    if not FIXTURE.exists():
        sys.exit(f"missing fixture: {FIXTURE}")

    cases = load_cases(args.limit, args.per_section, args.section)
    if not cases:
        sys.exit("no cases selected")

    calls = len(cases) * args.runs
    # ~15.2k input + ~70 output tokens per unified call, measured from
    # logs/application.log. Uses the project's own pricing table, which still
    # holds estimates for gpt-5.x — treat as an order of magnitude.
    from src.utils.cost_tracker import MODEL_PRICING
    pr = MODEL_PRICING.get("gpt-5.4-2026-03-05", MODEL_PRICING["unknown"])
    est = calls * (15200 * pr["input"] + 70 * pr["output"]) / 1_000_000

    print(f"cases={len(cases)}  runs={args.runs}  ->  {calls} LLM calls")
    print(f"estimated cost ~${est:.2f} (unified_processing only)")
    if est > args.max_cost and not args.yes:
        sys.exit(f"estimate exceeds --max-cost ${args.max_cost:.2f}; "
                 f"narrow with --limit/--per-section or pass --yes")

    if not os.getenv("OPENAI_API_KEY"):
        sys.exit("OPENAI_API_KEY is not set")

    print("initialising agent (this imports the RAG singleton lazily)...")
    from src.core.chatbot import text_to_cad_agent as agent

    sem = asyncio.Semaphore(args.concurrency)

    async def guarded(c):
        async with sem:
            fps = await run_case(agent, c, args.runs)
            stable = len(set(fps)) == 1
            print(f"  {'OK  ' if stable else 'FLIP'} {c['id']} "
                  f"[{c.get('section')}] {len(set(fps))} distinct")
            return c, fps

    print(f"\nrunning (concurrency={args.concurrency})...")
    results = await asyncio.gather(*(guarded(c) for c in cases))

    unstable = [(c, fps) for c, fps in results if len(set(fps)) > 1]

    print("\n" + "=" * 78)
    print(f"STABLE   {len(results) - len(unstable)}/{len(results)}")
    print(f"UNSTABLE {len(unstable)}/{len(results)}")
    print("=" * 78)

    for c, fps in unstable:
        print(f"\n[{c['id']}] {c.get('section')}  #{c.get('num')}")
        print(f"  prompt: {c['prompt'][:110]}...")
        for fp, n in Counter(fps).most_common():
            if len(fp) == 1:
                print(f"    x{n}  {fp[0]}")
            else:
                print(f"    x{n}  " + "  ".join(f"{k}={v!r}" for k, v in fp))

    report = {
        "runs": args.runs,
        "cases": len(results),
        "stable": len(results) - len(unstable),
        "unstable": len(unstable),
        "compared_fields": list(COMPARED),
        "unstable_detail": [
            {"id": c["id"], "section": c.get("section"), "prompt": c["prompt"],
             "variants": [dict(fp) if len(fp) > 1 else {"error": fp[0]}
                          for fp in set(fps)]}
            for c, fps in unstable
        ],
    }
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=1),
                              encoding="utf-8")
    print(f"\n-> {args.out}")
    # Non-zero exit when anything flipped, so CI can gate on it.
    return 1 if unstable else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
