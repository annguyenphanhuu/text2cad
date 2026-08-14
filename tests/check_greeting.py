"""
Check the greeting/intent classifier against a cheaper model.

WHY: greeting_classification runs on every non-edit turn to produce a 4-way label
plus a confidence — roughly 40 output tokens off a ~2.7k-token prompt. It ran on
the default (expensive) tier. Moving it to the nano tier is only safe if the
labels do not change, so this measures that directly.

THE FAILURE THAT MATTERS: a real CAD request labelled greeting /
information_request / process_question is short-circuited out of the CAD pipeline
entirely (see the early returns in process_request_with_progress), so the user
gets chat instead of a part. Every prompt in tests/fixtures/pptx_ok_cases.json is
a real CAD request, which makes them a large, free, high-value negative set: all
of them must come back cad_request.

The reverse direction (a greeting sent into the CAD pipeline) is cheaper to
recover from but still tested, via a small built-in set. Those are deliberately
NOT copied from the prompt's own few-shot examples — paraphrases only, otherwise
the model is being asked to recite rather than classify.

USAGE (needs langchain, i.e. the conda env or the container):

    python tests/check_greeting.py --limit 30                 # quick
    python tests/check_greeting.py --model gpt-5.1-2025-11-13 --out old.json
    python tests/check_greeting.py --compare old.json         # nano vs old

Confidence thresholds that actually gate behaviour, from
process_request_with_progress: greeting > 0.8, process_question > 0.7,
information_request > 0.7. A label is only acted on above its threshold, so this
reports effective routing, not just the raw label.
"""
import argparse
import asyncio
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FIXTURE = ROOT / "tests" / "fixtures" / "pptx_ok_cases.json"

# Thresholds mirrored from process_request_with_progress. Below these the code
# falls through to the CAD pipeline regardless of the label.
ACT_THRESHOLD = {
    "greeting": 0.8,
    "process_question": 0.7,
    "information_request": 0.7,
}

# Non-CAD inputs, paraphrased rather than lifted from the prompt's examples.
NON_CAD = [
    ("greeting", "hey there"),
    ("greeting", "bonsoir, ça va ?"),
    ("greeting", "hey there"),
    ("greeting", "thanks, that's all for now"),
    ("information_request", "which materials do you offer?"),
    ("information_request", "which sheet thicknesses do you stock for stainless?"),
    ("information_request", "peux-tu me dire ce que ton outil sait faire ?"),
    ("process_question", "how much would this part cost?"),
    ("process_question", "I need the quote for the order"),
    ("process_question", "can you give me a PDF of this file?"),
    ("process_question", "can you export that as a STEP file for me?"),
]


def effective_route(label, conf):
    """The branch the pipeline would actually take for this label+confidence."""
    thr = ACT_THRESHOLD.get(label)
    if thr is None:          # cad_request has no threshold
        return "cad_request"
    return label if (conf or 0) > thr else "cad_request"


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, help="only the first N CAD prompts")
    ap.add_argument("--model", help="override the model (default: whatever the "
                                    "agent wires up for greeting)")
    ap.add_argument("--concurrency", type=int, default=6)
    ap.add_argument("--out", default="greeting_report.json")
    ap.add_argument("--compare", metavar="OLD_REPORT",
                    help="diff labels against an earlier report")
    args = ap.parse_args()

    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    import os
    if not os.getenv("OPENAI_API_KEY"):
        sys.exit(f"OPENAI_API_KEY not set and not found in {ROOT / '.env'}")

    cad = json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]
    if args.limit:
        cad = cad[:args.limit]

    from src.core.chatbot import text_to_cad_agent as agent
    from src.utils.cost_tracking_wrapper import ainvoke_with_cost_tracking
    from src.core.agent_chains import create_greeting_classification_chain
    from src.utils.cost_tracker import resolve_model_name

    if args.model:
        from langchain_openai import ChatOpenAI
        llm = ChatOpenAI(model=args.model, temperature=0)
        chain = create_greeting_classification_chain(llm)
        model_name = args.model
    else:
        chain = agent.greeting_classification_chain
        model_name = resolve_model_name(agent.greeting_llm)

    print(f"model: {model_name}")
    print(f"cases: {len(cad)} CAD prompts + {len(NON_CAD)} non-CAD\n")

    sem = asyncio.Semaphore(args.concurrency)

    async def classify(key, text, expected):
        async with sem:
            sid = f"greetchk_{key}"
            try:
                r = await ainvoke_with_cost_tracking(
                    "greeting_classification", chain.ainvoke, {"user_text": text},
                    agent._get_cost_tracker(sid), model_name)
                label = r.get("classification")
                conf = r.get("confidence")
            except Exception as exc:
                label, conf = f"ERROR:{type(exc).__name__}", 0.0
            route = effective_route(label, conf)
            ok = route == expected
            if not ok:
                print(f"  MISROUTED {key}: label={label} conf={conf} "
                      f"-> route={route} (expected {expected})")
                print(f"     {text[:100]}")
            return {"key": key, "expected": expected, "label": label,
                    "confidence": conf, "route": route, "ok": ok,
                    "text": text[:160]}

    jobs = [classify(c["id"], f"[USER]: {c['prompt']}", "cad_request") for c in cad]
    jobs += [classify(f"noncad{i}", t, exp)
             for i, (exp, t) in enumerate(NON_CAD)]
    results = await asyncio.gather(*jobs)

    cad_res = [r for r in results if r["expected"] == "cad_request"
               and r["key"].startswith("s")]
    non_res = [r for r in results if r["key"].startswith("noncad")]

    print("\n" + "=" * 74)
    print(f"CAD prompts routed to cad_request : "
          f"{sum(r['ok'] for r in cad_res)}/{len(cad_res)}")
    print(f"non-CAD routed as expected        : "
          f"{sum(r['ok'] for r in non_res)}/{len(non_res)}")
    print("=" * 74)
    print("label distribution (CAD prompts):",
          dict(Counter(r["label"] for r in cad_res)))
    print("label distribution (non-CAD)    :",
          dict(Counter(r["label"] for r in non_res)))

    report = {"model": model_name,
              "cad_ok": sum(r["ok"] for r in cad_res), "cad_total": len(cad_res),
              "noncad_ok": sum(r["ok"] for r in non_res),
              "noncad_total": len(non_res),
              "results": {r["key"]: {k: r[k] for k in
                                     ("label", "confidence", "route", "ok")}
                          for r in results}}
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=1),
                              encoding="utf-8")
    print(f"\n-> {args.out}")

    if args.compare:
        old = json.loads(Path(args.compare).read_text(encoding="utf-8"))
        print("\n" + "=" * 74)
        print(f"COMPARE vs {old.get('model')} ({args.compare})")
        print("=" * 74)
        shared = sorted(set(old["results"]) & set(report["results"]))
        same_route = same_label = 0
        for k in shared:
            a, b = old["results"][k], report["results"][k]
            if a["route"] == b["route"]:
                same_route += 1
            else:
                print(f"  ROUTE DIFF {k}: {a['route']} -> {b['route']} "
                      f"(label {a['label']} -> {b['label']})")
            same_label += a["label"] == b["label"]
        print(f"\nsame effective route: {same_route}/{len(shared)}")
        print(f"same raw label      : {same_label}/{len(shared)}")

    # Routing failures are the gate; a label change that does not change routing
    # is not a behaviour change.
    bad = [r for r in results if not r["ok"]]
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
