"""report.html + summary.csv for a tests/rerun_deck_ir.py results folder.

Reads every <case dir>/run*/result.json under `out`; images are referenced relatively,
so open report.html from that folder.  The client's ground-truth drawings live in the
bank page (tests/deck2511.html), not here.
"""
import csv
import html
import json
import re
from pathlib import Path

CONFIRM_RE = re.compile(r"reply\s+yes\s*/\s*ok\s+to\s+generate", re.I)


def load(out):
    rows = []
    for p in sorted(out.glob("*/run*/result.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        d["_dir"], d["_run_dir"] = p.parent.parent.name, p.parent.name
        rows.append(d)
    return rows


def bbox_of(rec):
    info = ((rec.get("files") or {}).get("obj") or {}).get("mesh_info") or ""
    m = re.match(r"bbox ([\d.]+) ([\d.]+) ([\d.]+)", info)
    if m:
        return "x".join(m.groups())
    bb = (rec.get("build") or {}).get("bbox")          # perforated sheets have no OBJ
    return "x".join("%.1f" % (bb[i + 1] - bb[i]) for i in (0, 2, 4)) if bb and len(bb) == 6 else ""


def write(out, runs=1, cases_label="deck2511"):
    out = Path(out)
    rows = load(out)
    cases = {}
    for r in rows:
        cases.setdefault(r["_dir"], []).append(r)
    for v in cases.values():
        v.sort(key=lambda r: r.get("run", 0))

    with open(out / "summary.csv", "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["case_dir", "uid", "section", "num", "run", "generated", "bot_asked", "clarifications_before_confirm",
                    "blocked_on_question", "n_turns", "bbox_mm", "total_s", "cost_usd", "errors", "final_reply", "prompt"])
        for d, rs in cases.items():
            for r in rs:
                turns = r.get("turns") or []
                w.writerow([d, r.get("uid"), r.get("section"), r.get("num"), r.get("run"), r.get("generated"), r.get("bot_asked"),
                            r.get("clarifications_before_confirm"), bool(r.get("blocked_on_question")), r.get("n_turns"), bbox_of(r),
                            round(sum(t.get("duration_s") or 0 for t in turns), 1), r.get("total_cost_usd"),
                            "; ".join(str(t.get("error")) for t in turns if t.get("error")),
                            re.sub(r"\s+", " ", r.get("final_reply") or "")[:300], r.get("prompt")])

    total, gen = len(rows), sum(1 for r in rows if r.get("generated"))
    asked = sum(1 for r in rows if r.get("bot_asked") != "standard_confirmation")
    blocked = sum(1 for r in rows if r.get("blocked_on_question"))
    cost = sum(r.get("total_cost_usd") or 0 for r in rows)

    parts = ["""<title>Test-case re-run</title>
<style>
:root{--bg:#fff;--fg:#16202b;--muted:#5b6b7c;--line:#dde4ec;--card:#f7f9fc;--ok:#0f7b4f;--bad:#b3261e;--warn:#8a5a00;--accent:#1b4f8f}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#11161c;--fg:#e6edf5;--muted:#9bacbe;--line:#26313d;--card:#182028;--ok:#5fd39b;--bad:#ff8b82;--warn:#e0b25a;--accent:#7db4f5}}
:root[data-theme=dark]{--bg:#11161c;--fg:#e6edf5;--muted:#9bacbe;--line:#26313d;--card:#182028;--ok:#5fd39b;--bad:#ff8b82;--warn:#e0b25a;--accent:#7db4f5}
body{background:var(--bg);color:var(--fg);font:15px/1.55 Calibri,Arial,sans-serif;margin:0 auto;padding:28px;max-width:1600px}
h1{font-size:29px;margin:0 0 4px} h2{font-size:19px;margin:34px 0 6px}
.sub{color:var(--muted);margin-bottom:22px}
.stats{display:flex;flex-wrap:wrap;gap:14px;margin:18px 0 28px}
.stat{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 18px;min-width:130px}
.stat b{display:block;font-size:26px}
.case{border:1px solid var(--line);border-radius:12px;padding:16px 18px;margin:0 0 22px;background:var(--card)}
.uid{display:inline-block;font:13px Consolas,monospace;background:var(--accent);color:#fff;padding:1px 8px;border-radius:6px;margin-right:8px}
.prompt{font-size:14px;background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:10px 12px;margin:8px 0 14px}
.runs{display:grid;grid-template-columns:repeat(auto-fit,minmax(330px,1fr));gap:14px}
.run{border:1px solid var(--line);border-radius:10px;padding:10px;background:var(--bg)}
.run h4{margin:0 0 6px;font-size:15px}
img{max-width:100%;border:1px solid var(--line);border-radius:6px;background:#fff}
.ok{color:var(--ok);font-weight:700}.bad{color:var(--bad);font-weight:700}.warn{color:var(--warn);font-weight:700}
.meta{font-size:13px;color:var(--muted);margin:6px 0}
details{margin:6px 0}summary{cursor:pointer;color:var(--accent);font-size:13px}
pre{white-space:pre-wrap;font:12px/1.5 Consolas,monospace;background:var(--card);border:1px solid var(--line);border-radius:6px;padding:8px;overflow-x:auto}
a{color:var(--accent)}
.flag{display:inline-block;font-size:12px;padding:1px 7px;border-radius:99px;border:1px solid var(--line);margin-left:6px}
</style>
<h1>Test-case re-run</h1>"""]
    parts.append('<div class="sub">Cases <code>%s</code> &middot; IR pipeline, agent in-process, part built and drawn by local '
                 'freecadcmd &middot; %d run(s) per case &middot; only "ok" is ever sent back, never an answer to a question.</div>'
                 % (html.escape(cases_label), runs))
    parts.append('<div class="stats">'
                 '<div class="stat"><b>%d</b>cases</div><div class="stat"><b>%d</b>runs</div><div class="stat"><b>%d</b>produced a model</div>'
                 '<div class="stat"><b>%d</b>no model</div><div class="stat"><b>%d</b>bot asked first</div>'
                 '<div class="stat"><b>%d</b>blocked on a question</div><div class="stat"><b>$%.2f</b>LLM cost</div></div>'
                 % (len(cases), total, gen, total - gen, asked, blocked, cost))

    for d, rs in cases.items():
        r0 = rs[0]
        boxes = {bbox_of(r) for r in rs if bbox_of(r)}
        flag = ""
        if len(boxes) > 1:
            flag = '<span class="flag bad">runs disagree on size: %s</span>' % html.escape(", ".join(sorted(boxes)))
        elif boxes:
            flag = '<span class="flag">bbox %s mm</span>' % html.escape(list(boxes)[0])
        parts.append('<div class="case"><h2><span class="uid">%s</span>%s #%s%s</h2>' % (
            html.escape(str(r0.get("uid") or d[:3])), html.escape(str(r0.get("section"))), html.escape(str(r0.get("num"))), flag))
        parts.append('<div class="prompt">%s</div>' % html.escape(r0.get("prompt") or ""))
        parts.append('<div class="runs">')
        for r in rs:
            run_dir = "%s/%s" % (d, r["_run_dir"])
            badge = '<span class="ok">model produced</span>' if r.get("generated") else '<span class="bad">no model</span>'
            if r.get("blocked_on_question"):
                badge += '<span class="flag warn">blocked: repeated the same question</span>'
            askcls = "ok" if r.get("bot_asked") == "standard_confirmation" else "warn"
            parts.append('<div class="run"><h4>Run %s &mdash; %s</h4>' % (r.get("run"), badge))
            files = r.get("files") or {}
            for png in (files.get("pdf") or {}).get("png_pages", []):
                parts.append('<a href="%s/%s"><img src="%s/%s" alt="technical drawing"></a>' % (run_dir, png, run_dir, png))
            if (files.get("obj") or {}).get("render"):
                parts.append('<a href="%s/render_3d.png"><img src="%s/render_3d.png" alt="3D render"></a>' % (run_dir, run_dir))
            turns = r.get("turns") or []
            clar = r.get("clarifications_before_confirm")
            clar_txt = "summarised straight away" if clar == 0 else ("asked %d time(s) first" % clar if clar else "never reached the summary")
            parts.append('<div class="meta">turn 1: <span class="%s">%s</span> &mdash; %s<br>%d turns, %.0fs &middot; $%s &middot; session <code>%s</code></div>'
                         % (askcls, html.escape(r.get("bot_asked") or ""), clar_txt, len(turns),
                            sum(t.get("duration_s") or 0 for t in turns), r.get("total_cost_usd"), html.escape(r.get("session_id") or "")))
            for t in turns:
                if t.get("error"):
                    parts.append('<div class="meta bad">turn %s transport error: %s</div>' % (t.get("turn"), html.escape(str(t["error"]))))
                txt = t.get("chat_response") or ""
                if txt:
                    sent = t.get("sent") or ""
                    parts.append('<details><summary>turn %s &mdash; sent "%s"</summary><pre>%s</pre></details>'
                                 % (t.get("turn"), html.escape(sent if len(sent) < 40 else "the request"), html.escape(txt)))
            links = ['<a href="%s/model.%s">%s</a>' % (run_dir, ext, lbl) for ext, lbl in (("step", "STEP"), ("obj", "OBJ"), ("pdf", "drawing PDF")) if files.get(ext)]
            if links:
                parts.append('<div class="meta">files: %s</div>' % " &middot; ".join(links))
            parts.append("</div>")
        parts.append("</div></div>")

    (out / "report.html").write_text("\n".join(parts), encoding="utf-8")
    print("report.html + summary.csv written | %d cases, %d runs, %d with a model, $%.2f" % (len(cases), total, gen, cost))


if __name__ == "__main__":
    import sys
    write(sys.argv[1])
