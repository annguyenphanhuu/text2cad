"""
Re-run the deck's OK cases through the IR pipeline and write the same evidence
layout + report.html as the August run, in a NEW folder, plus a side-by-side
compare.html (August legacy pipeline vs IR pipeline).

    SKIP_DB=true python tests/rerun_deck_ir.py [--limit N] [--only 034] [--workers 4]

Cases and prompts are the 84 of outputs/test_rerun_20260818/selection.json, so
the two reports line up case by case.  The agent runs in-process with
CAD_PIPELINE=ir; the part is built and the drawing is produced by the local
freecadcmd (no FreeCAD server / MQTT needed).  Nothing is answered on the
customer's behalf: like in August, only "ok" is ever sent back, so a case that
asks a real question ends as "no model" and is reported as such.

Output: outputs/test_rerun_<date>_ir/
    <case dir>/run1/turnN_response.md, result.json, model.step/.obj/.pdf/.svg,
                     drawing-1.png, render_3d.png, metadata.json, model_ir.json
    report.html  summary.csv  results.jsonl  compare.html  README.md
"""
import argparse
import asyncio
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
import types
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("SKIP_DB", "true")
os.environ["CAD_PIPELINE"] = "ir"

OLD = ROOT / "outputs" / "test_rerun_20260818"
WORKER_ROOT = ROOT.parent / "tolery-freecad"
FREECADCMD = os.environ.get("FREECADCMD", r"C:\Program Files\FreeCAD 1.0\bin\freecadcmd.exe")
RENDERER = OLD / "harness" / "render_obj.py"
SUCCESS_TEXT = "Your part generation successful."
CONFIRM_RE = re.compile(r"reply\s+yes\s*/\s*ok\s+to\s+generate", re.I)
MAX_TURNS = 5


def classify(turn1_text):
    t = turn1_text or ""
    if CONFIRM_RE.search(t):
        return "standard_confirmation"
    if "?" in t or "Please specify" in t:
        return "asked_clarification"
    return "no_confirmation_prompt"


# ----------------------------------------------------------------- conversation

class Harness:
    def __init__(self, agent, out):
        self.agent = agent
        self.out = out
        self.transcripts = {}
        self.captured = {}
        agent.save_outputs = self._fake_save_outputs
        agent._build_user_text_with_history = types.MethodType(
            lambda a, sid, cur: self.transcripts.get(sid, "") + ("[USER]: %s\n" % cur if cur else ""), agent)

    async def _fake_save_outputs(self, code, requirements, base_filename="generated_cad", user_text="",
                                 session_id=None, priority=0, skip_metadata=False):
        self.captured[session_id] = {"code": code, "shape_type": requirements.shape_type,
                                     "title": requirements.title, "description": requirements.description}
        return (None, None, None)

    async def turn(self, sid, msg):
        t0 = time.time()
        final, steps = None, []
        async for upd in self.agent.process_request_with_progress(msg, is_edit_request=False, session_id=sid,
                                                                 material_choice="STEEL"):
            if "final_result" in upd:
                final = upd["final_result"]
            elif upd.get("step") and (not steps or steps[-1] != upd["step"]):
                steps.append(upd["step"])
        final = final or {}
        if final.get("error"):
            resp = str(final["error"])
        elif final.get("message") and not final.get("code"):
            resp = final["message"]
        elif final.get("code"):
            resp = SUCCESS_TEXT
        else:
            resp = ""
        try:
            cost = self.agent._get_cost_tracker(sid).get_request_summary().get("request_total_cost_usd", 0.0)
        except Exception:
            cost = None
        self.transcripts[sid] = self.transcripts.get(sid, "") + "[USER]: %s\n[CHATBOT]: %s\n" % (msg, resp)
        return {"sent": msg, "duration_s": round(time.time() - t0, 1), "steps": steps,
                "error": final.get("error"), "chat_response": resp, "cost_usd": round(cost or 0.0, 6),
                "built": bool(final.get("code"))}

    async def run_case(self, case, sem, run=1):
        async with sem:
            run_dir = "run%d" % run
            rundir = self.out / case["dir"] / run_dir
            rundir.mkdir(parents=True, exist_ok=True)
            sid = "irrerun_%s" % case["uid"] if run == 1 else "irrerun_%s_r%d" % (case["uid"], run)
            rec = dict(uid=case["uid"], section=case["section"], num=case["num"], run=run, session_id=sid,
                       prompt=case["prompt"], started=time.strftime("%Y-%m-%d %H:%M:%S"), pipeline="ir",
                       _dir=case["dir"], _run_dir=run_dir)
            turns, msg = [], case["prompt"]
            for n in range(1, MAX_TURNS + 1):
                try:
                    t = await self.turn(sid, msg)
                except Exception as exc:
                    t = {"sent": msg, "duration_s": 0.0, "steps": [], "error": "%s: %s" % (type(exc).__name__, exc),
                         "chat_response": "", "cost_usd": 0.0, "built": False}
                t["turn"] = n
                turns.append(t)
                (rundir / ("turn%d_response.md" % n)).write_text(
                    "# Turn %d - sent\n\n> %s\n\n## Bot reply\n\n%s\n" % (n, msg, t["chat_response"]), encoding="utf-8")
                if n == 1:
                    rec["bot_asked"] = classify(t["chat_response"])
                if t["built"] or t["error"]:
                    break
                if n >= 2 and t["chat_response"].strip() and t["chat_response"].strip() == turns[-2]["chat_response"].strip():
                    rec["blocked_on_question"] = True
                    break
                msg = "ok"
            rec["turns"] = turns
            rec["n_turns"] = len(turns)
            at = next((t["turn"] for t in turns if CONFIRM_RE.search(t["chat_response"] or "")), None)
            rec["clarifications_before_confirm"] = None if at is None else at - 1
            rec["final_reply"] = turns[-1]["chat_response"]
            rec["total_cost_usd"] = round(sum(t["cost_usd"] or 0 for t in turns), 6)
            state = self.agent._get_session_state(sid)
            rec["ir"] = state.get("latest_ir")
            cap = self.captured.get(sid) or {}
            rec["shape_type"] = cap.get("shape_type")
            rec["description"] = cap.get("description")
            if cap.get("code") and not rec["ir"]:
                # legacy path (perforated sheets): the old code generator produced a script
                rec["legacy_code"] = True
                rec["final_reply"] = (rec["final_reply"] or "") + ("\n\n(legacy pipeline: this shape is still built by the old "
                                                                    "code generator; the script is not executed in this offline harness)")
            rec["exports"] = {}
            rec["files"] = {}
            rec["generated"] = False
            print("  %s %-6s r%d %s | %d turns | %s" % ("BUILD" if rec["ir"] else "-----", case["uid"], run, rec.get("bot_asked"),
                                                    len(turns), (rec["final_reply"] or "")[:70].replace("\n", " ")), flush=True)
            return rec


# ----------------------------------------------------------------- build / draw / render

def _key(r):
    return "%s/%s" % (r["_dir"], r["_run_dir"])


def build_all(recs, out):
    from tests.check_ir_builder import run_jobs
    jobs = [{"ir": r["ir"], "title": "model", "output_dir": str(out / r["_dir"] / r["_run_dir"]), "write_obj": True}
            for r in recs if r.get("ir")]
    if not jobs:
        return {}
    tmp = out / "_build"
    tmp.mkdir(exist_ok=True)
    results = run_jobs(jobs, tmp)
    by_dir = {}
    for j, res in zip(jobs, results):
        p = Path(j["output_dir"])
        by_dir["%s/%s" % (p.parent.name, p.name)] = res
    return by_dir


DRAW_SCRIPT = r'''
import os, sys, json, time
sys.path.insert(0, %(utils)r)
from drawing.build import build, DEFAULT_TEMPLATE
job = json.load(open(os.environ["TOLERY_DRAW_BATCH"], encoding="utf-8"))
out = {}
for item in job:
    t0 = time.time()
    try:
        rep = build(item["step"], item["svg"], meta={"title": item["title"]}, template=DEFAULT_TEMPLATE)
        rep["seconds"] = round(time.time() - t0, 1)
        out[item["step"]] = rep
    except Exception as exc:
        out[item["step"]] = {"ok": False, "error": str(exc)}
json.dump(out, open(job[0]["report"], "w", encoding="utf-8"), default=str)
'''


def draw_all(recs, out):
    items = []
    for r in recs:
        step = out / r["_dir"] / r["_run_dir"] / "model.step"
        if step.exists():
            items.append({"step": str(step), "svg": str(step.with_suffix(".svg")),
                          "title": "%s #%s" % (r["section"], r["num"]), "report": str(out / "_build" / "drawings.json")})
    if not items:
        return {}
    (out / "_build").mkdir(exist_ok=True)
    script = out / "_build" / "draw_batch.py"
    script.write_text(DRAW_SCRIPT % {"utils": str(WORKER_ROOT / "src" / "utils")}, encoding="utf-8")
    job = out / "_build" / "draw_jobs.json"
    job.write_text(json.dumps(items), encoding="utf-8")
    env = dict(os.environ, TOLERY_DRAW_BATCH=str(job), PYTHONIOENCODING="utf-8")
    with open(out / "_build" / "draw.log", "w", encoding="utf-8", errors="replace") as log:
        subprocess.run([FREECADCMD, str(script)], env=env, stdout=log, stderr=subprocess.STDOUT, timeout=3600, cwd=str(ROOT))
    rep_path = out / "_build" / "drawings.json"
    reports = json.loads(rep_path.read_text(encoding="utf-8")) if rep_path.exists() else {}
    import cairosvg
    for item in items:
        svg = Path(item["svg"])
        if not svg.exists():
            continue
        try:
            cairosvg.svg2pdf(url=str(svg), write_to=str(svg.with_name("model.pdf")))
            cairosvg.svg2png(url=str(svg), write_to=str(svg.with_name("drawing-1.png")), output_width=1280)
        except Exception as exc:
            print("  ! svg conversion failed for %s: %s" % (svg, exc))
    return reports


def render_one(obj, png, title):
    try:
        r = subprocess.run([sys.executable, str(RENDERER), str(obj), str(png), title], capture_output=True, text=True, timeout=300)
        return (r.stdout or "").strip() if r.returncode == 0 else None
    except Exception:
        return None


def finish_records(recs, out, build_res, draw_res):
    with ThreadPoolExecutor(max_workers=4) as pool:
        futs = {}
        for r in recs:
            rundir = out / r["_dir"] / r["_run_dir"]
            obj = rundir / "model.obj"
            if obj.exists():
                futs[_key(r)] = pool.submit(render_one, obj, rundir / "render_3d.png", "%s #%s run %d" % (r["section"], r["num"], r["run"]))
        renders = {k: f.result() for k, f in futs.items()}
    for r in recs:
        rundir = out / r["_dir"] / r["_run_dir"]
        files = {}
        for ext in ("step", "obj", "pdf"):
            p = rundir / ("model." + ext)
            if p.exists():
                files[ext] = {"file": p.name, "bytes": p.stat().st_size}
        if "pdf" in files and (rundir / "drawing-1.png").exists():
            files["pdf"]["png_pages"] = ["drawing-1.png"]
        if "obj" in files and renders.get(_key(r)):
            files["obj"]["render"] = "render_3d.png"
            files["obj"]["mesh_info"] = renders[_key(r)]
        r["files"] = files
        r["generated"] = bool(files.get("step"))
        b = build_res.get(_key(r))
        if b:
            r["build"] = {"ok": b.get("ok"), "error": b.get("error"),
                          **({k: b["report"].get(k) for k in ("label", "bbox", "volume", "faces_count", "solids", "valid", "warnings")} if b.get("ok") else {})}
            if b.get("ok") and b["report"].get("warnings"):
                r["final_reply"] = (r.get("final_reply") or "") + "\n\nBuilder warnings: " + "; ".join(b["report"]["warnings"])
            if not b.get("ok"):
                r["final_reply"] = (r.get("final_reply") or "") + "\n\nBUILD FAILED: " + str(b.get("error"))
        d = draw_res.get(str(rundir / "model.step"))
        if d:
            r["drawing"] = {"ok": d.get("ok"), "qa": (d.get("qa") or {}).get("ok") if isinstance(d.get("qa"), dict) else None, "seconds": d.get("seconds")}
        (rundir / "result.json").write_text(json.dumps(r, ensure_ascii=False, indent=1, default=str), encoding="utf-8")


# ----------------------------------------------------------------- reports

def write_report(out, runs=1):
    spec = importlib.util.spec_from_file_location("legacy_report", str(OLD / "harness" / "report.py"))
    rep = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rep)
    rep.OUT = out
    rep.main()
    html_path = out / "report.html"
    s = html_path.read_text(encoding="utf-8")
    s = s.replace("<title>OK test-case re-run</title>", "<title>OK test-case re-run - IR pipeline</title>")
    s = s.replace("<h1>OK test-case re-run</h1>", "<h1>OK test-case re-run &mdash; IR pipeline (%s)</h1>" % out.name)
    s = s.replace("local API on :8124 with local FreeCAD &middot; 3 runs per case.",
                  "same 84 prompts as <code>test_rerun_20260818</code>, plus chapter 9 (triangular, circular and "
                  "circular-bent plates, perforated sheets, T/I profiles, DFM rule cases that are not in the deck) &middot; "
                  "agent in-process with <code>CAD_PIPELINE=ir</code>, part built and drawn by local freecadcmd &middot; "
                  "%d run(s) per case. Side by side with August: <a href=\"compare.html\">compare.html</a>." % runs)
    html_path.write_text(s, encoding="utf-8")


def load_all_results(out, run_dir="run1"):
    recs = []
    for p in sorted(out.glob("*/%s/result.json" % run_dir)):
        try:
            r = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        r["_dir"] = p.parent.parent.name
        r["_run_dir"] = run_dir
        recs.append(r)
    return recs


def write_compare(recs, out):
    import html as H
    recs = load_all_results(out)          # every case in the folder, not only the ones just re-run
    rows = []
    for r in sorted(recs, key=lambda x: x["_dir"]):
        d = r["_dir"]
        old_dir = OLD / d / "run1"
        old_res = {}
        if (old_dir / "result.json").exists():
            try:
                old_res = json.loads((old_dir / "result.json").read_text(encoding="utf-8"))
            except Exception:
                old_res = {}
        old_info = ((old_res.get("files") or {}).get("obj") or {}).get("mesh_info") or ""
        new_info = ((r.get("files") or {}).get("obj") or {}).get("mesh_info") or ""
        old_reply = (old_res.get("turn2") or {}).get("chat_response") or (old_res.get("turns") or [{}])[-1].get("chat_response", "") if old_res else ""

        def col(title, run_rel, res, info, reply, built):
            imgs = ""
            if not built and not (OLD / d).exists() and run_rel.startswith("../"):
                return ('<div class="col"><h4>%s &mdash; <span class="flag">not in the August deck</span></h4>'
                        '<p>This case was added in September to test triangular / circular / circular-bent plates.</p></div>' % title)
            base = run_rel
            draw = base + "/drawing-1.png"
            rend = base + "/render_3d.png"
            exists = lambda rel: (out / rel).exists()
            if exists(draw):
                imgs += '<a href="%s"><img src="%s"></a>' % (draw, draw)
            if exists(rend):
                imgs += '<a href="%s"><img src="%s"></a>' % (rend, rend)
            badge = '<span class="ok">model</span>' if built else '<span class="bad">no model</span>'
            return ('<div class="col"><h4>%s &mdash; %s <span class="flag">%s</span></h4>%s'
                    '<details><summary>last reply</summary><pre>%s</pre></details></div>'
                    % (title, badge, H.escape(info[:40]), imgs, H.escape(reply or "")))

        rows.append('<div class="case"><h2><span class="uid">%s</span>%s #%s</h2><div class="prompt">%s</div><div class="cols">%s%s</div></div>' % (
            r["uid"], H.escape(str(r["section"])), r["num"], H.escape(r["prompt"]),
            col("August 2026 (legacy pipeline)", "../test_rerun_20260818/%s/run1" % d, old_res, old_info, old_reply, bool(old_res.get("generated"))),
            col("Now (IR pipeline)", "%s/run1" % d, r, new_info, r.get("final_reply"), bool(r.get("generated")))))
    css = """<style>body{font:15px/1.5 Calibri,Arial,sans-serif;max-width:1600px;margin:0 auto;padding:24px;color:#16202b}
h1{font-size:26px}.case{border:1px solid #dde4ec;border-radius:12px;padding:14px 16px;margin:0 0 20px;background:#f7f9fc}
.prompt{font-size:14px;background:#fff;border:1px solid #dde4ec;border-radius:8px;padding:8px 10px;margin:6px 0 12px}
.cols{display:grid;grid-template-columns:1fr 1fr;gap:14px}.col{border:1px solid #dde4ec;border-radius:10px;padding:10px;background:#fff}
.col h4{margin:0 0 6px;font-size:15px}img{max-width:100%;border:1px solid #dde4ec;border-radius:6px;margin:4px 0;background:#fff}
.ok{color:#0f7b4f;font-weight:700}.bad{color:#b3261e;font-weight:700}.flag{font-size:12px;border:1px solid #dde4ec;border-radius:99px;padding:1px 7px;margin-left:6px;font-weight:400}
.uid{display:inline-block;font:13px Consolas,monospace;background:#1b4f8f;color:#fff;padding:1px 8px;border-radius:6px;margin-right:8px}
details{margin:6px 0}summary{cursor:pointer;color:#1b4f8f;font-size:13px}pre{white-space:pre-wrap;font:12px/1.5 Consolas,monospace;background:#f7f9fc;border:1px solid #dde4ec;border-radius:6px;padding:8px}
</style>"""
    n_old = sum(1 for r in recs if (OLD / r["_dir"] / "run1" / "model.step").exists())
    n_new = sum(1 for r in recs if r.get("generated"))
    head = ("<title>Legacy vs IR pipeline</title>%s<h1>Legacy pipeline (August) vs IR pipeline (now)</h1>"
            "<p>%d cases &middot; August run1 produced a model in %d, the IR pipeline in %d. Left column images come from "
            "<code>../test_rerun_20260818</code>; open this file from its own folder.</p>" % (css, len(recs), n_old, n_new))
    (out / "compare.html").write_text(head + "\n".join(rows), encoding="utf-8")


# ----------------------------------------------------------------- main

async def main_async(args):
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    from src.core.chatbot import text_to_cad_agent as agent
    cases = []
    for name in (args.cases or "deck").split(","):
        name = name.strip()
        if not name:
            continue
        cases_file = OLD / "selection.json" if name == "deck" else Path(name)
        cases += json.loads(cases_file.read_text(encoding="utf-8"))["run"]
    if args.only:
        wanted = [w.strip() for w in args.only.split(",") if w.strip()]
        cases = [c for c in cases if any(w in c["uid"] or w in c["dir"] for w in wanted)]
    if args.limit:
        cases = cases[:args.limit]
    out = ROOT / "outputs" / (args.out or ("test_rerun_%s_ir" % time.strftime("%Y%m%d")))
    out.mkdir(parents=True, exist_ok=True)
    runs = list(range(args.from_run, args.from_run + args.runs))
    print("%d cases x %d run(s) -> %s" % (len(cases), len(runs), out))
    h = Harness(agent, out)
    sem = asyncio.Semaphore(args.workers)
    t0 = time.time()
    recs = await asyncio.gather(*(h.run_case(c, sem, run=n) for n in runs for c in cases))
    print("conversations done in %.0fs, %d with an IR to build" % (time.time() - t0, sum(1 for r in recs if r.get("ir"))))
    t1 = time.time()
    build_res = build_all(recs, out)
    print("build done in %.0fs" % (time.time() - t1))
    t2 = time.time()
    draw_res = draw_all(recs, out)
    print("drawings done in %.0fs" % (time.time() - t2))
    finish_records(recs, out, build_res, draw_res)
    tag = "" if not args.cases or args.cases == "deck" else "_" + "_".join(Path(n.strip()).stem for n in args.cases.split(",") if n.strip())
    with open(out / ("results%s.jsonl" % tag), "w", encoding="utf-8") as fh:
        for r in recs:
            fh.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
    (out / ("selection%s.json" % tag)).write_text(json.dumps({"run": cases}, ensure_ascii=False, indent=1), encoding="utf-8")
    write_report(out, runs=max(runs))
    write_compare(recs, out)
    (out / "README.md").write_text(
        "# OK test-case re-run - IR pipeline (%s)\n\nThe 84 deck prompts of `test_rerun_20260818` (selection.json) plus the chapter-9 "
        "prompts of tests/fixtures/shape_cases_*.json, %d run(s) each, through the IR pipeline (`CAD_PIPELINE=ir`). The agent ran "
        "in-process; the part was built and drawn with the local freecadcmd (no FreeCAD server / MQTT). Only `ok` was ever sent back, "
        "never an answer to a question.\n\nOpen `report.html` (same layout as August, one column per run) or `compare.html` "
        "(August vs now run 1, side by side) from this folder.\n" % (time.strftime("%Y-%m-%d"), max(runs)), encoding="utf-8")
    gen = sum(1 for r in recs if r.get("generated"))
    print("done: %d/%d runs produced a model | %s" % (gen, len(recs), out / "report.html"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int)
    ap.add_argument("--only")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--out")
    ap.add_argument("--cases", help="comma list of JSON {\"run\": [{uid, dir, section, num, prompt}]} files; the word 'deck' = the "
                                    "August selection (default). Results land in the same --out folder")
    ap.add_argument("--runs", type=int, default=1, help="runs per case (the August report had 3)")
    ap.add_argument("--from-run", type=int, default=1, help="first run number, to add runs to an existing folder")
    args = ap.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
