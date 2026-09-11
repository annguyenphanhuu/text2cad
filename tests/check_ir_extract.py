"""
End-to-end check of the IR pipeline WITHOUT the API server:

    prompt -> IRFlow.extract (LLM) -> validate -> build in local freecadcmd
           -> compare against the legacy baseline geometry (when we have one)

Case sets
    en     : the 84 English prompts of outputs/test_rerun_20260818 (baseline bbox in summary.csv)
    fr     : the 169 French OK prompts of the client deck (tests/fixtures/pptx_ok_cases.json)
    notok  : the deck's NOT OK cases (tests/fixtures/pptx_notok_cases.json)
    none   : French queries the old shape classifier could not label (tests/fixtures/expander_none_queries.json)

Usage (conda env `freecad`, needs OPENAI_API_KEY in .env and FreeCAD 1.0):

    python tests/check_ir_extract.py --set en --limit 10
    python tests/check_ir_extract.py --set en,notok,none --out report.json

Nothing is answered on the customer's behalf: a case that ends in a question is
reported as "question", which is a legitimate outcome, not a failure.
"""
import argparse
import asyncio
import csv
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

RERUN = ROOT / "outputs" / "test_rerun_20260818"


def load_cases(names):
    cases = []
    for name in names:
        if name == "en":
            sel = json.loads((RERUN / "selection.json").read_text(encoding="utf-8"))["run"]
            bbox = {}
            with open(RERUN / "summary.csv", encoding="utf-8-sig") as fh:
                for row in csv.DictReader(fh):
                    if row["run"] == "1" and row.get("bbox_mm"):
                        bbox[row["case_dir"]] = sorted(float(x) for x in row["bbox_mm"].split("x"))
            dirs = sorted(p.name for p in RERUN.iterdir() if p.is_dir() and p.name[:3].isdigit())
            by_num = {}
            for d in dirs:
                parts = d.split("_")
                by_num.setdefault((parts[1] + "." + parts[2], int(parts[-1][1:])), d)
            for c in sel:
                key = (c["section"].split(" ")[0], c["num"])
                d = by_num.get(key)
                cases.append({"id": d or "s%d" % c["slide"], "set": "en", "section": c["section"], "prompt": c["prompt"],
                              "baseline_bbox": bbox.get(d), "baseline_step": str(RERUN / d / "run1" / "model.step") if d else None})
        elif name == "fr":
            for c in json.loads((ROOT / "tests/fixtures/pptx_ok_cases.json").read_text(encoding="utf-8"))["cases"]:
                cases.append({"id": c["id"], "set": "fr", "section": c["section"], "prompt": c["prompt"]})
        elif name == "notok":
            for c in json.loads((ROOT / "tests/fixtures/pptx_notok_cases.json").read_text(encoding="utf-8"))["cases"]:
                cases.append({"id": "notok_s%d_%d" % (c["slide"], c["num"]), "set": "notok", "section": c["section"], "prompt": c["prompt"]})
        elif name == "none":
            for c in json.loads((ROOT / "tests/fixtures/expander_none_queries.json").read_text(encoding="utf-8"))["cases"]:
                cases.append({"id": c["id"], "set": "none", "section": "", "prompt": c["prompt"]})
        else:
            raise SystemExit("unknown set %r" % name)
    return cases


async def extract_all(flow, cases, concurrency):
    sem = asyncio.Semaphore(concurrency)

    async def one(c):
        async with sem:
            t0 = time.time()
            history = "[USER]: %s\n" % c["prompt"]
            try:
                env = await flow.extract(history, "irsweep_" + c["id"])
                c["envelope"] = env
                prep = flow.prepare(env)
                c["kind"] = prep["kind"]
                c["message"] = prep.get("message")
                if prep["kind"] == "ready":
                    c["ir"] = prep["ir"]
                    c["warnings"] = prep["validation"].warnings
                    c["label"] = prep["validation"].plan.label
                    c["assumptions"] = prep.get("assumptions")
            except Exception as exc:
                c["kind"] = "error"
                c["message"] = "%s: %s" % (type(exc).__name__, exc)
            c["extract_s"] = round(time.time() - t0, 1)
            print("  %-28s %-9s %s" % (c["id"], c["kind"], (c.get("label") or (c.get("message") or "")[:70]).replace("\n", " ")))

    await asyncio.gather(*(one(c) for c in cases))


def build_all(cases, out_dir):
    from tests.check_ir_builder import run_jobs
    jobs = [{"ir": c["ir"], "title": c["id"], "output_dir": str(out_dir / c["id"]), "write_obj": False}
            for c in cases if c.get("kind") == "ready"]
    if not jobs:
        return
    results = run_jobs(jobs, out_dir)
    by_title = {r["title"]: r for r in results}
    for c in cases:
        r = by_title.get(c["id"])
        if not r:
            continue
        if r.get("ok"):
            rep = r["report"]
            c["build"] = {"ok": True, "bbox": rep["bbox"], "volume": rep["volume"], "valid": rep["valid"], "solids": rep["solids"],
                          "faces": rep["faces_count"], "tools": rep["tools"], "failed_cuts": rep["metadata"].get("failed"),
                          "seconds": r["seconds"]}
            ext = sorted([rep["bbox"][1] - rep["bbox"][0], rep["bbox"][3] - rep["bbox"][2], rep["bbox"][5] - rep["bbox"][4]])
            c["build"]["extents"] = [round(x, 2) for x in ext]
            if c.get("baseline_bbox"):
                diffs = [abs(a - b) for a, b in zip(ext, c["baseline_bbox"])]
                c["build"]["baseline_match"] = all(d <= max(0.5, 0.01 * b) for d, b in zip(diffs, c["baseline_bbox"]))
                c["build"]["baseline_diff"] = [round(d, 2) for d in diffs]
        else:
            c["build"] = {"ok": False, "error": r.get("error")}


def summarize(cases):
    from collections import Counter
    kinds = Counter(c.get("kind") for c in cases)
    built = [c for c in cases if c.get("build", {}).get("ok")]
    bad = [c for c in cases if c.get("kind") == "ready" and not c.get("build", {}).get("ok")]
    invalid = [c for c in built if not c["build"]["valid"] or c["build"]["solids"] != 1 or c["build"].get("failed_cuts")]
    with_base = [c for c in built if "baseline_match" in c["build"]]
    match = [c for c in with_base if c["build"]["baseline_match"]]
    print("\n=== SUMMARY (%d cases) ===" % len(cases))
    print("extraction outcome:", dict(kinds))
    print("built OK: %d | build failed: %d | invalid/failed cuts: %d" % (len(built), len(bad), len(invalid)))
    if with_base:
        print("baseline bbox match: %d/%d" % (len(match), len(with_base)))
    for c in bad:
        print("  BUILD FAIL %-28s %s" % (c["id"], c["build"]["error"]))
    for c in invalid:
        print("  INVALID    %-28s valid=%s solids=%s failed_cuts=%s" % (c["id"], c["build"]["valid"], c["build"]["solids"], c["build"].get("failed_cuts")))
    for c in with_base:
        if not c["build"]["baseline_match"]:
            print("  BBOX DIFF  %-28s new=%s baseline=%s label=%s" % (c["id"], c["build"]["extents"], c["baseline_bbox"], c.get("label")))
    for c in cases:
        if c.get("kind") in ("questions", "message", "error"):
            print("  %-9s  %-28s %s" % (c["kind"].upper(), c["id"], (c.get("message") or "").replace("\n", " | ")[:160]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", default="en", help="comma list of en,fr,notok,none")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--only", help="substring filter on case id")
    ap.add_argument("--section", help="comma list of section prefixes to keep, e.g. 3.,2.17")
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--no-build", action="store_true")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    if not os.getenv("OPENAI_API_KEY"):
        sys.exit("OPENAI_API_KEY not set")

    cases = load_cases(args.set.split(","))
    if args.only:
        cases = [c for c in cases if args.only in c["id"]]
    if args.section:
        prefixes = [p.strip() for p in args.section.split(",") if p.strip()]
        cases = [c for c in cases if any((c.get("section") or "").startswith(p) for p in prefixes)]
    if args.limit:
        cases = cases[:args.limit]
    print("%d cases" % len(cases))

    from src.core.chatbot import text_to_cad_agent as agent
    flow = agent._ir_flow
    t0 = time.time()
    asyncio.run(extract_all(flow, cases, args.concurrency))
    print("extraction done in %.0fs" % (time.time() - t0))

    out_dir = ROOT / "outputs" / ("ir_sweep_%s" % time.strftime("%Y%m%d_%H%M%S"))
    out_dir.mkdir(parents=True, exist_ok=True)
    if not args.no_build:
        t1 = time.time()
        build_all(cases, out_dir)
        print("build done in %.0fs" % (time.time() - t1))
    summarize(cases)
    report = args.out or str(out_dir / "report.json")
    Path(report).write_text(json.dumps(cases, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print("report:", report)


if __name__ == "__main__":
    main()
