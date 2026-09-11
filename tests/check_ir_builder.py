"""
Build every hand-written IR in tests/fixtures/ir_cases/ with the local
freecadcmd and check the geometry invariants each fixture declares under
"_expect" (bbox, volume, tool count, validity).

This is the builder's own regression: no LLM involved.  Run:

    python tests/check_ir_builder.py [--only name] [--keep]

Needs FreeCAD 1.0 (`FREECADCMD` env var or the default Windows install path).
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "ir_cases"
FREECADCMD = os.environ.get("FREECADCMD", r"C:\Program Files\FreeCAD 1.0\bin\freecadcmd.exe")


def run_jobs(jobs, out_dir):
    """Run a list of {"ir","title","output_dir"} jobs in ONE freecadcmd process."""
    job_path = Path(out_dir) / "jobs.json"
    result_path = Path(out_dir) / "results.json"
    if jobs:
        jobs[0]["report"] = str(result_path)
    job_path.write_text(json.dumps(jobs), encoding="utf-8")
    env = dict(os.environ, TOLERY_IR_JOB=str(job_path), PYTHONIOENCODING="utf-8")
    log_path = Path(out_dir) / "freecadcmd.log"
    with open(log_path, "w", encoding="utf-8", errors="replace") as log:
        proc = subprocess.run([FREECADCMD, str(ROOT / "FreeCadUtil" / "build_from_ir.py")],
                              stdout=log, stderr=subprocess.STDOUT, env=env, cwd=str(ROOT), timeout=1800)
    if not result_path.exists():
        tail = log_path.read_text(encoding="utf-8", errors="replace")[-3000:]
        raise RuntimeError("freecadcmd produced no results (exit %s)\n%s" % (proc.returncode, tail))
    return json.loads(result_path.read_text(encoding="utf-8"))


def check(expect, rep):
    problems = []
    if not rep.get("valid"):
        problems.append("shape invalid")
    if rep.get("solids") != 1:
        problems.append("solids=%s" % rep.get("solids"))
    if "bbox" in expect:
        for want, got, axis in zip(expect["bbox"], rep["bbox"], ("xmin", "xmax", "ymin", "ymax", "zmin", "zmax")):
            if abs(want - got) > 0.05:
                problems.append("%s %.2f != %.2f" % (axis, got, want))
    if "zmax" in expect and abs(rep["bbox"][5] - expect["zmax"]) > 0.05:
        problems.append("zmax %.2f != %.2f" % (rep["bbox"][5], expect["zmax"]))
    if "volume" in expect and abs(rep["volume"] - expect["volume"]) > max(0.5, 0.001 * expect["volume"]):
        problems.append("volume %.1f != %.1f" % (rep["volume"], expect["volume"]))
    if "volume_lt" in expect and not rep["volume"] < expect["volume_lt"]:
        problems.append("volume %.1f not < %.1f" % (rep["volume"], expect["volume_lt"]))
    if "tools" in expect and rep.get("tools") != expect["tools"]:
        problems.append("tools %s != %s" % (rep.get("tools"), expect["tools"]))
    if rep.get("metadata", {}).get("failed"):
        problems.append("failed cuts %s" % rep["metadata"]["failed"])
    return problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only")
    ap.add_argument("--keep", action="store_true", help="keep the output directory")
    ap.add_argument("--obj", action="store_true", help="also write OBJ files")
    args = ap.parse_args()

    out_dir = Path(tempfile.mkdtemp(prefix="ir_builder_"))
    jobs, expects = [], {}
    for f in sorted(FIXTURES.glob("*.json")):
        if args.only and args.only not in f.stem:
            continue
        ir = json.loads(f.read_text(encoding="utf-8"))
        expects[f.stem] = ir.pop("_expect", {})
        jobs.append({"ir": ir, "title": f.stem, "output_dir": str(out_dir / f.stem), "write_obj": args.obj})
    results = run_jobs(jobs, out_dir)
    ok = 0
    for res in results:
        name = res["title"]
        if not res.get("ok"):
            print("FAIL %-24s %s" % (name, res.get("error")))
            continue
        rep = res["report"]
        problems = check(expects.get(name, {}), rep)
        status = "ok  " if not problems else "DIFF"
        ok += not problems
        print("%s %-24s %-22s bbox=%s vol=%.1f faces=%d tools=%d %.1fs %s" % (
            status, name, rep["label"], rep["bbox"], rep["volume"], rep["faces_count"], rep["tools"], res["seconds"],
            "; ".join(problems)))
    print("%d/%d fixtures pass  (outputs in %s)" % (ok, len(results), out_dir))
    if not args.keep:
        pass  # leave it: STEP files are handy to inspect after a failure
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
