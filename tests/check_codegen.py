"""
Compare generated FreeCAD code before and after a change to the codegen prompts.

WHY A SEPARATE CHECK: check_stability.py compares unified_processing's JSON. The
code-generation and code-editing prompts produce Python source, so a prompt edit
there has to be judged on the code itself.

WHY NOT TEXT DIFF: the generated code varies harmlessly in comments, variable
names, statement order and whitespace on every call. A textual diff reports 100%
change and tells you nothing. What determines the part is:

  1. which builder/boolean functions get called  (Part.makeTub, makeZShape, cut,
     fuse, resolveLBracketCrossBendHoles, ...)
  2. the numbers handed to them                  (dimensions, radii, counts)

So a fingerprint is (multiset of called function names, multiset of numeric
literals). Two programs with the same fingerprint build the same solid even if
they read differently. Parsing is via `ast`, which also means a prompt edit that
makes the model emit invalid Python shows up immediately as a parse failure.

Numbers are rounded to 3 decimals before comparison so 20.0 and 20.000000001 do
not read as a difference.

USAGE (needs langchain — conda env or container; FreeCAD is NOT needed, this only
generates source, it does not execute it):

    python tests/check_codegen.py --cases 12 --runs 2 --out before.json
    # edit the prompt
    python tests/check_codegen.py --cases 12 --runs 2 --compare before.json

Each call is ~15-18k input and ~1-1.5k output on the expensive tier, and output
tokens are not helped by prompt caching, so keep --cases modest. The script
prints its cost estimate and refuses to exceed --max-cost without --yes.
"""
import argparse
import ast
import asyncio
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FIXTURE = ROOT / "tests" / "fixtures" / "pptx_ok_cases.json"


def _imported_names(tree) -> set:
    """Module names and aliases bound by imports in this source."""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                names.add(a.asname or a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                names.add(a.asname or a.name)
    return names


def _dotted(node, modules: set) -> str:
    """
    Name a call for fingerprinting.

    `Part.makeBox` keeps its module prefix — which module a function comes from is
    part of what the code does. But `box.cut(...)` and `plate.cut(...)` are the
    same operation on a differently-named local, and the generator renames locals
    freely between runs, so for a receiver that is not an imported module only the
    method name is kept.
    """
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        root = node.id
        if root in modules:
            parts.append(root)
        else:
            return parts[0] if parts else root   # method name only
    elif parts:
        return parts[0]              # call on a subscript/call result
    return ".".join(reversed(parts)) if parts else "<expr>"


def _is_builder(name: str) -> bool:
    """
    Whether a call actually constructs or combines geometry.

    Measured baseline noise (12 cases, two identical calls each, no prompt
    change): 7 of 12 differed on the full call/number fingerprint, but almost all
    of it was incidental — an `abs(...) < 0.01` tolerance guard appearing or not,
    stray `0.0` literals, a `recompute()`. Only the construction calls tracked a
    real difference in what was built (one CAPOT run used Part.makeTub, the other
    rebuilt it with SheetMetalCmd.SMBendWall).

    So this subset is the gate, and the full fingerprint is kept as detail.
    """
    leaf = name.split(".")[-1]
    return (
        leaf.startswith(("make", "resolve", "add"))
        or leaf in ("cut", "fuse", "common", "extrude", "revolve", "mirror",
                    "removeSplitter", "makeFillet", "makeChamfer", "makeThickness",
                    "SMBendWall", "SMUnfold")
    )


def fingerprint(code: str) -> dict:
    """
    Geometry-relevant fingerprint of generated code.

    Returns {"error": ...} when the code will not parse — that is itself a result
    worth reporting, not something to hide.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return {"error": f"SyntaxError line {exc.lineno}: {exc.msg}"}

    modules = _imported_names(tree)
    calls, nums, builders = Counter(), Counter(), Counter()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _dotted(node.func, modules)
            if _is_builder(name):
                builders[name] += 1
            # Bookkeeping calls say nothing about the shape.
            if name.split(".")[-1] not in (
                    "print", "append", "format", "join", "str", "int", "float",
                    "len", "range", "open", "write", "close", "makedirs", "exists"):
                calls[name] += 1
        elif isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) \
                and not isinstance(node.value, bool):
            nums[round(float(node.value), 3)] += 1

    return {"builders": dict(builders),
            "calls": dict(calls),
            "nums": {str(k): v for k, v in nums.items()}}


FORMAT = 2   # bump when the fingerprint shape changes


def compare(a: dict, b: dict) -> dict:
    """Difference between two fingerprints, split by kind."""
    if "error" in a or "error" in b:
        return {"parse": [a.get("error"), b.get("error")]}
    # A fingerprint written before `builders` existed has no builders key, and
    # defaulting it to {} would make every comparison against such a report
    # report "same construction" — a silent false pass. Refuse instead.
    for side, fp in (("before", a), ("after", b)):
        if "builders" not in fp:
            return {"format": f"{side} fingerprint predates the builder gate; "
                              f"regenerate that report with this version"}
    ba, bb = Counter(a["builders"]), Counter(b["builders"])
    ca, cb = Counter(a["calls"]), Counter(b["calls"])
    na, nb = Counter(a["nums"]), Counter(b["nums"])
    return {
        # gate
        "builders_only_before": dict(ba - bb),
        "builders_only_after": dict(bb - ba),
        # advisory detail — noisy at baseline, see _is_builder
        "calls_only_before": dict(ca - cb),
        "calls_only_after": dict(cb - ca),
        "nums_only_before": dict(na - nb),
        "nums_only_after": dict(nb - na),
    }


GATE_KEYS = ("parse", "builders_only_before", "builders_only_after")


def is_same(diff: dict) -> bool:
    """Same *construction*. Incidental call/number churn is not a difference."""
    return not any(diff.get(k) for k in GATE_KEYS)


def is_identical(diff: dict) -> bool:
    """Byte-for-byte equal fingerprints, including the noisy parts."""
    return not any(diff.values())


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", type=int, default=12,
                    help="how many cases (one per shape family, in order)")
    ap.add_argument("--runs", type=int, default=2,
                    help=">=2 so baseline variance is visible; codegen output is "
                         "long free-form text and varies more than a JSON label")
    ap.add_argument("--ids", help="comma-separated case ids instead of --cases")
    ap.add_argument("--concurrency", type=int, default=3)
    ap.add_argument("--max-cost", type=float, default=2.0)
    ap.add_argument("--yes", action="store_true")
    ap.add_argument("--out", default="codegen_report.json")
    ap.add_argument("--compare", metavar="OLD_REPORT")
    args = ap.parse_args()

    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    import os
    if not os.getenv("OPENAI_API_KEY"):
        sys.exit(f"OPENAI_API_KEY not set and not found in {ROOT / '.env'}")

    all_cases = json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]
    if args.ids:
        wanted = {i.strip() for i in args.ids.split(",") if i.strip()}
        cases = [c for c in all_cases if c["id"] in wanted]
    else:
        seen, cases = set(), []
        for c in all_cases:
            fam = (c.get("section") or "?").split()[0].split(".")[0]
            if fam not in seen:
                seen.add(fam)
                cases.append(c)
        # widen within families if there are fewer families than requested
        for c in all_cases:
            if len(cases) >= args.cases:
                break
            if c not in cases:
                cases.append(c)
        cases = cases[:args.cases]

    calls = len(cases) * args.runs
    from src.utils.cost_tracker import MODEL_PRICING
    pr = MODEL_PRICING.get("gpt-5.4-2026-03-05", MODEL_PRICING["unknown"])
    est = calls * (16000 * pr["input"] + 1300 * pr["output"]) / 1_000_000
    print(f"cases={len(cases)} runs={args.runs} -> {calls} codegen calls")
    print(f"estimated cost ~${est:.2f} (output tokens dominate and do not cache)")
    if est > args.max_cost and not args.yes:
        sys.exit(f"over --max-cost ${args.max_cost:.2f}; lower --cases or pass --yes")

    from src.core.chatbot import text_to_cad_agent as agent
    from src.core.rag_singleton import get_rag_split_context
    from src.rag.query_expander import expand_query_with_llm
    from src.utils.cost_tracking_wrapper import ainvoke_with_cost_tracking

    sem = asyncio.Semaphore(args.concurrency)

    async def gen(case, run):
        """One codegen call with realistic RAG context."""
        sid = f"codegenchk_{case['id']}_r{run}"
        tracker = agent._get_cost_tracker(sid)
        # Real retrieval, so the prompt carries the examples it would in
        # production. Retrieved once per (case, run) to keep both sides of a
        # before/after comparison on the same footing.
        er = await expand_query_with_llm(case["prompt"], llm=agent.expansion_llm,
                                         cost_tracker=tracker)
        rag = await get_rag_split_context(
            query=er["expanded_query"], k_rules=10, k_examples=4,
            reranking_llm=agent.reranking_llm, cost_tracker=tracker, session_id=sid,
            pre_expanded_query=er["expanded_query"],
            pre_detected_shape_type=er.get("detected_shape_type"))
        code = await ainvoke_with_cost_tracking(
            "code_generation", agent.rag_code_generation_chain.ainvoke,
            {"design_requirements_obj": None,
             "user_text": case["prompt"],
             "retrieved_context": rag.get("examples_context", ""),
             "material": "", "mapped_material": "steel", "session_id": sid},
            tracker, agent.model_names["advanced"])
        return code or ""

    async def one(case):
        async with sem:
            fps, codes = [], []
            for r in range(args.runs):
                try:
                    code = await gen(case, r)
                except Exception as exc:
                    fps.append({"error": f"{type(exc).__name__}: {str(exc)[:90]}"})
                    codes.append("")
                    continue
                codes.append(code)
                fps.append(fingerprint(code))
            self_same = all(is_same(compare(fps[0], f)) for f in fps[1:])
            bad = [f for f in fps if "error" in f]
            print(f"  {'stable' if self_same else 'VARIES'}"
                  f"{' PARSE-FAIL' if bad else ''} {case['id']} "
                  f"[{case.get('section')}]")
            return case["id"], {"section": case.get("section"),
                                "fingerprints": fps,
                                "self_consistent": self_same,
                                "parse_failures": len(bad),
                                "code_sample": codes[0][:600]}

    print(f"\ngenerating (concurrency={args.concurrency})...")
    results = dict(await asyncio.gather(*(one(c) for c in cases)))

    n = len(results)
    stable = sum(1 for v in results.values() if v["self_consistent"])
    pfail = sum(v["parse_failures"] for v in results.values())
    print("\n" + "=" * 74)
    print(f"self-consistent across {args.runs} runs: {stable}/{n}")
    print(f"parse failures (invalid Python generated): {pfail}")
    print("=" * 74)

    report = {"runs": args.runs, "cases": n, "self_consistent": stable,
              "parse_failures": pfail, "results": results}
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=1),
                              encoding="utf-8")
    print(f"\n-> {args.out}")

    changed = []
    if args.compare:
        old = json.loads(Path(args.compare).read_text(encoding="utf-8"))
        shared = sorted(set(old["results"]) & set(results))
        print("\n" + "=" * 74)
        print(f"COMPARE vs {args.compare} ({len(shared)} cases)")
        print("=" * 74)
        for cid in shared:
            # Compare run 0 to run 0. A case that is not self-consistent on
            # either side is flagged, since then a difference cannot be
            # attributed to the prompt change.
            a = old["results"][cid]["fingerprints"][0]
            b = results[cid]["fingerprints"][0]
            diff = compare(a, b)
            if is_same(diff):
                continue
            noisy = not (old["results"][cid]["self_consistent"]
                         and results[cid]["self_consistent"])
            tag = "DIFF (case is noisy — attribution unclear)" if noisy else "DIFF"
            changed.append(cid)
            print(f"\n[{tag}] {cid}  {results[cid]['section']}")
            for k, v in diff.items():
                if v:
                    print(f"    {k}: {v}")
        print(f"\nidentical {len(shared) - len(changed)}/{len(shared)}")

    if changed:
        print("\nCHANGED: " + ", ".join(changed))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
