"""
Check query expansion against a cheaper model.

WHY: expansion runs once per retrieval and was the largest remaining cost in the
RAG path — ~4.3k input tokens on the default tier, even with its prompt already
cached at 95%.

WHAT MATTERS: `detected_shape_type`. The retriever merges it with its own regex
pass (retriever.py STEP 1c) and uses the result to filter which code examples
reach code generation, so a wrong shape type here feeds the generator examples
for the wrong kind of part. The prompt's other job — normalising capot face
synonyms ("the top plane" -> "Back face") — only rewrites the retrieval query,
so it is compared but weighted as advisory.

Nothing here asserts an absolute right answer: it compares the cheap model
against the incumbent on the same inputs, which is the question that decides
whether the swap is safe.

USAGE (needs langchain — conda env or container):

    python tests/check_expansion.py --model gpt-5.1-2025-11-13 --out old.json
    python tests/check_expansion.py --compare old.json
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


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int)
    ap.add_argument("--model", help="override model (default: the wired expansion_llm)")
    ap.add_argument("--concurrency", type=int, default=6)
    ap.add_argument("--out", default="expansion_report.json")
    ap.add_argument("--compare", metavar="OLD_REPORT")
    args = ap.parse_args()

    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    import os
    if not os.getenv("OPENAI_API_KEY"):
        sys.exit(f"OPENAI_API_KEY not set and not found in {ROOT / '.env'}")

    cases = json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]
    if args.limit:
        cases = cases[:args.limit]

    from src.core.chatbot import text_to_cad_agent as agent
    from src.rag.query_expander import expand_query_with_llm
    from src.utils.cost_tracker import resolve_model_name

    if args.model:
        from langchain_openai import ChatOpenAI
        llm = ChatOpenAI(model=args.model, temperature=0)
    else:
        llm = agent.expansion_llm
    model_name = resolve_model_name(llm)
    print(f"model: {model_name}\ncases: {len(cases)}\n")

    sem = asyncio.Semaphore(args.concurrency)

    async def one(c):
        async with sem:
            sid = f"expchk_{c['id']}"
            try:
                r = await expand_query_with_llm(
                    c["prompt"], llm=llm,
                    cost_tracker=agent._get_cost_tracker(sid))
                return c["id"], {
                    "shape": r.get("detected_shape_type"),
                    # Only the shape suffix is compared verbatim; the rest of the
                    # expanded query is free text whose exact wording does not
                    # gate anything downstream.
                    "expanded_len": len(r.get("expanded_query") or ""),
                }
            except Exception as exc:
                return c["id"], {"shape": f"ERROR:{type(exc).__name__}",
                                 "expanded_len": 0}

    out = dict(await asyncio.gather(*(one(c) for c in cases)))
    dist = Counter(v["shape"] for v in out.values())
    print("detected_shape_type distribution:")
    for k, v in dist.most_common():
        print(f"  {str(k):<26} {v}")

    report = {"model": model_name, "results": out}
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=1),
                             encoding="utf-8")
    print(f"\n-> {args.out}")

    if args.compare:
        old = json.loads(Path(args.compare).read_text(encoding="utf-8"))
        shared = sorted(set(old["results"]) & set(out))
        print("\n" + "=" * 74)
        print(f"COMPARE vs {old.get('model')}")
        print("=" * 74)
        diffs = []
        for k in shared:
            a, b = old["results"][k]["shape"], out[k]["shape"]
            if a != b:
                diffs.append((k, a, b))
        for k, a, b in diffs:
            print(f"  SHAPE DIFF {k}: {a!r} -> {b!r}")
        print(f"\nsame detected_shape_type: {len(shared) - len(diffs)}/{len(shared)}")
        return 1 if diffs else 0
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
