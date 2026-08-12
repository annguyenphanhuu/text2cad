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

COMPARED = ("shape_type", "missing_info", "complexity_level",
            "skip_questions_requested", "step_by_step_requested", "design_type")

# What the (OK) label in the source deck actually certifies: the GEOMETRY the
# pipeline produced was correct. It says nothing about whether the chain asked
# the right clarifying questions along the way. So only the fields that select
# what gets built count as a regression against that baseline.
#
# shape_type picks both the description_confirm template (build_confirm_template)
# and the FreeCAD builder, so a flip here changes the part. design_type splits
# part vs assembly.
GEOMETRY = ("shape_type", "design_type")

# Conversation-flow fields. Deliberately NOT treated as regressions:
#  - The (OK) label does not certify them (see above).
#  - This harness cannot reproduce their production values anyway: it passes
#    rules_context="" and does not run the DFM chain, whereas in production
#    _invoke_unified_with_rag injects retrieved rules and the DFM agent merges
#    violations into questions/missing_info afterwards. So missing_info measured
#    here is not the value a user would see.
#  - They are known to be unstable in the product already, independent of any
#    prompt change.
FLOW = ("missing_info", "skip_questions_requested", "step_by_step_requested")

# complexity_level is only ever branched on as `== 0` (pure-information request)
# — text_to_cad_agent.py:2363 and :4938 — so 1 vs 2 vs 5 takes the identical
# path. (agent_chains.py's step-planner docstring mentions a
# "complexity_level >= threshold" check that does not exist in code.)
COSMETIC = ("complexity_level",)


def flipped_fields(fingerprints):
    """Field names whose value differs across runs."""
    fps = [fp for fp in fingerprints if len(fp) > 1]     # drop ERROR/PARSE markers
    if len(fps) < 2:
        return []
    out = []
    for i, field in enumerate(COMPARED):
        if len({fp[i][1] for fp in fps}) > 1:
            out.append(field)
    return out


def load_cases(limit=None, per_section=None, section=None, ids=None):
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    cases = data["cases"]
    if ids:
        wanted = {i.strip() for i in ids.split(",") if i.strip()}
        cases = [c for c in cases if c["id"] in wanted]
        missing = wanted - {c["id"] for c in cases}
        if missing:
            raise SystemExit(f"unknown case ids: {sorted(missing)}")
        return cases
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
    ap.add_argument("--ids", help="comma-separated case ids; overrides other selectors")
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--max-cost", type=float, default=2.0,
                    help="refuse to start above this estimate without --yes")
    ap.add_argument("--yes", action="store_true", help="skip the cost prompt")
    ap.add_argument("--out", default="stability_report.json")
    ap.add_argument("--compare", metavar="OLD_REPORT",
                    help="diff this run's answers against an earlier report. "
                         "Stability alone cannot tell you a prompt edit left the "
                         "answers unchanged — it only says the chain agrees with "
                         "itself. Run once before the edit, once after, and "
                         "compare the two reports.")
    args = ap.parse_args()

    if args.runs < 2:
        sys.exit("--runs must be >= 2, otherwise there is nothing to compare.")
    if not FIXTURE.exists():
        sys.exit(f"missing fixture: {FIXTURE}")

    cases = load_cases(args.limit, args.per_section, args.section, args.ids)
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

    # Config lives in .env, same as src/core/chatbot.py — load it before checking
    # for the key, otherwise this exits on a shell that never exported it.
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    if not os.getenv("OPENAI_API_KEY"):
        sys.exit(f"OPENAI_API_KEY not set, and not found in {ROOT / '.env'}")

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
    errored = [(c, fps) for c, fps in results if any(len(fp) == 1 for fp in fps)]

    geom, flow, cosmetic = [], [], []
    for c, fps in unstable:
        flips = flipped_fields(fps)
        if any(f in GEOMETRY for f in flips):
            geom.append((c, fps, flips))
        elif any(f in FLOW for f in flips):
            flow.append((c, fps, flips))
        else:
            cosmetic.append((c, fps, flips))

    print("\n" + "=" * 78)
    print(f"STABLE      {len(results) - len(unstable)}/{len(results)}")
    print(f"UNSTABLE    {len(unstable)}/{len(results)}")
    print(f"  GEOMETRY  (real regression) {len(geom)}")
    print(f"  flow      (not OK-certified) {len(flow)}")
    print(f"  cosmetic  (complexity only)  {len(cosmetic)}")
    if errored:
        print(f"ERRORED     {len(errored)} case(s) had a failed/unparsed run")
    print("=" * 78)

    for label, group in (("GEOMETRY", geom), ("flow", flow), ("cosmetic", cosmetic)):
        for c, fps, flips in group:
            print(f"\n[{label}] {c['id']}  {c.get('section')}  #{c.get('num')}")
            print(f"  flipped: {', '.join(flips) or 'error/parse only'}")
            print(f"  prompt: {c['prompt'][:105]}...")
            for fp, n in Counter(fps).most_common():
                if len(fp) == 1:
                    print(f"    x{n}  {fp[0]}")
                else:
                    print(f"    x{n}  " + "  ".join(
                        f"{k}={v!r}" for k, v in fp if k in flips))

    report = {
        "runs": args.runs,
        "cases": len(results),
        "stable": len(results) - len(unstable),
        "unstable": len(unstable),
        "geometry_regressions": len(geom),
        "flow_only": len(flow),
        "cosmetic_only": len(cosmetic),
        "compared_fields": list(COMPARED),
        "geometry_fields": list(GEOMETRY),
        "flow_fields": list(FLOW),
        "unstable_detail": [
            {"id": c["id"], "section": c.get("section"), "prompt": c["prompt"],
             "flipped_fields": flips,
             "geometry": any(f in GEOMETRY for f in flips),
             "flow": any(f in FLOW for f in flips),
             "variants": [dict(fp) if len(fp) > 1 else {"error": fp[0]}
                          for fp in set(fps)]}
            for c, fps, flips in geom + flow + cosmetic
        ],
        # Every answer, not just the unstable ones — this is what makes the
        # report usable as a before/after baseline for a prompt edit.
        "answers": {
            c["id"]: sorted(
                [dict(fp) if len(fp) > 1 else {"error": fp[0]} for fp in set(fps)],
                key=lambda d: json.dumps(d, sort_keys=True))
            for c, fps in results
        },
    }
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=1),
                              encoding="utf-8")
    print(f"\n-> {args.out}")

    changed_behaviour = []
    if args.compare:
        old = json.loads(Path(args.compare).read_text(encoding="utf-8"))
        old_ans = old.get("answers") or {}
        if not old_ans:
            print(f"\n[compare] {args.compare} has no 'answers' block "
                  f"(written before --compare existed) — cannot diff.")
        else:
            shared = sorted(set(old_ans) & set(report["answers"]))
            print("\n" + "=" * 78)
            print(f"COMPARE vs {args.compare}  ({len(shared)} cases in both)")
            print("=" * 78)
            same = 0
            for cid in shared:
                a, b = old_ans[cid], report["answers"][cid]
                if a == b:
                    same += 1
                    continue
                # Which fields differ between the two runs' answer sets?
                fields = {k for d in a + b for k in d}
                diff = {f for f in fields
                        if {d.get(f) for d in a} != {d.get(f) for d in b}}
                if diff & set(GEOMETRY):
                    tag = "GEOMETRY CHANGED"
                    changed_behaviour.append(cid)
                elif diff & set(FLOW):
                    tag = "flow drift (not OK-certified)"
                else:
                    tag = "cosmetic drift"
                print(f"\n[{tag}] {cid}  fields: {', '.join(sorted(diff))}")
                print(f"   before: {a}")
                print(f"   after : {b}")
            print(f"\nidentical {same}/{len(shared)} | "
                  f"behaviour changed {len(changed_behaviour)}")

    if changed_behaviour:
        print("\nBEHAVIOUR CHANGED on: " + ", ".join(changed_behaviour))
        return 1
    # Gate on behavioural flips only. A cosmetic flip (complexity_level 1 vs 2)
    # would otherwise fail the run without any pipeline behaviour having changed.
    return 1 if regressions else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
