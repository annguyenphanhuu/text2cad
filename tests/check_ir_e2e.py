"""Real end-to-end check: API (SSE) -> FreeCAD worker container -> exports.

    SKIP_DB=true python run.py                      # API on :8124 (in another terminal)
    docker compose up -d --build                    # in ../tolery-freecad: redis + mqtt + freecad on :8020
    python tests/check_ir_e2e.py [--base http://localhost:8124] [--only capot,disc]

Each prompt is sent, then "ok"; the STEP / OBJ / PDF exports of the final SSE event are
downloaded to outputs/e2e_<date>/<case>/ and the outcome is printed.  Nothing is answered
on the customer's behalf.
"""
import argparse
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PROMPTS = {
    "plate": "Plate 200 x 150 x 4 mm with 4 blind holes Ø10, 2 mm deep, in the corners 20 mm from the edges.",
    "capot": "je souhaite un capot avec une base rectangulaire de 400x300mm d'épaisseur 2mm, 4 plis à 90° de 151.5mm qui remontent.",
    "disc_fold": "Round plate Ø200, 2 mm steel, bent at 90° along a line 60 mm from the centre, flange upward, 2 Ø8 holes on the flange 15 mm from the free edge, 40 mm apart.",
    "perforated": "perforated sheet 300x200x2 R8 T12",
    "tube": "Tube 40x40x2, length 600 mm, one end cut at 45°, 20x2 tenons centred on the two faces at the straight end, protruding 30 mm.",
    "triangle": "Right-triangle gusset in 4 mm steel, legs 150 and 100 mm, Ø9 hole at the centroid, 15 mm flanges bent up on both legs.",
}
CONFIRM_RE = re.compile(r"reply\s+yes\s*/\s*ok\s+to\s+generate", re.I)


def token():
    for line in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("API_SECRET_TOKEN="):
            return line.split("=", 1)[1].strip().strip('"')
    return ""


def turn(base, tok, message, session_id=None, timeout=900, is_edit=False):
    p = {"message": message, "is_edit_request": "true" if is_edit else "false", "material_choice": "STEEL", "token": tok}
    if session_id:
        p["session_id"] = session_id
    url = base.rstrip("/") + "/api/generate-cad-stream?" + urllib.parse.urlencode(p)
    t0, steps, final, sid, err = time.time(), [], None, session_id, None
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            for raw in r:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                try:
                    d = json.loads(line[5:].strip() or "{}")
                except json.JSONDecodeError:
                    continue
                sid = d.get("session_id") or sid
                if d.get("step") and (not steps or steps[-1] != d["step"]):
                    steps.append(d["step"])
                if d.get("error"):
                    err = str(d["error"])[:500]
                if d.get("final_response"):
                    final = d["final_response"]
                    sid = final.get("session_id") or sid
                    break
    except Exception as exc:
        err = "%s: %s" % (type(exc).__name__, exc)
    return {"message": message, "session_id": sid, "steps": steps, "final": final, "error": err, "duration_s": round(time.time() - t0, 1)}


def fetch(url, dest, tok):
    try:
        parts = urllib.parse.urlsplit(url)
        url = urllib.parse.urlunsplit(parts._replace(path=urllib.parse.quote(parts.path)))   # file names may contain spaces
        req = urllib.request.Request(url, headers={"Authorization": "Bearer " + tok})
        with urllib.request.urlopen(req, timeout=180) as r:
            dest.write_bytes(r.read())
        return dest.stat().st_size
    except Exception as exc:
        print("      ! download failed %s: %s" % (url, type(exc).__name__))
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8124")
    ap.add_argument("--only")
    ap.add_argument("--edit-session", help="send --edit-text as an edit request on this existing session (restart-recovery check) and exit")
    ap.add_argument("--edit-text", default="Add a Ø10 hole in the centre of the base.")
    args = ap.parse_args()
    tok = token()
    out = ROOT / "outputs" / ("e2e_%s" % time.strftime("%Y%m%d_%H%M%S"))
    out.mkdir(parents=True, exist_ok=True)
    if args.edit_session:
        t = turn(args.base, tok, args.edit_text, args.edit_session, is_edit=True)
        f = t["final"] or {}
        reply = f.get("chat_response") or f.get("message") or ""
        print("edit turn %.0fs error=%s\n%s" % (t["duration_s"], t["error"], reply[:1200]))
        if t["error"]:
            return 1
        if f.get("step_export"):
            t2, f2 = t, f                     # the IR edit flow builds straight away, no confirm round
        elif CONFIRM_RE.search(reply):
            t2 = turn(args.base, tok, "ok", args.edit_session)
            f2 = t2["final"] or {}
        else:
            return 1
        sizes = {}
        for key, ext in (("step_export", "step"), ("obj_export", "obj"), ("technical_drawing_export", "pdf")):
            url = f2.get(key)
            if url:
                sizes[ext] = fetch(url if url.startswith("http") else args.base.rstrip("/") + "/" + url.lstrip("/"), out / ("edited." + ext), tok)
        print("ok turn %.0fs error=%s exports=%s -> %s" % (t2["duration_s"], t2["error"], sizes, out))
        return 0 if sizes.get("step") else 1
    wanted = [w.strip() for w in args.only.split(",")] if args.only else list(PROMPTS)
    ok_count = 0
    for name in wanted:
        prompt = PROMPTS[name]
        print("=== %s" % name)
        case_dir = out / name
        case_dir.mkdir(exist_ok=True)
        t1 = turn(args.base, tok, prompt)
        f1 = t1["final"] or {}
        reply1 = f1.get("chat_response") or f1.get("response") or f1.get("message") or ""
        print("  turn1 %.0fs steps=%s | %s" % (t1["duration_s"], t1["steps"][-1:] or "-", (t1["error"] or reply1)[:120].replace("\n", " ")))
        if t1["error"] or not CONFIRM_RE.search(reply1):
            (case_dir / "turns.json").write_text(json.dumps([t1], ensure_ascii=False, indent=1, default=str), encoding="utf-8")
            continue
        t2 = turn(args.base, tok, "ok", t1["session_id"])
        final = t2["final"] or {}
        exports = {k: final.get(k) for k in ("step_export", "obj_export", "technical_drawing_export") if final.get(k)}
        sizes = {}
        for key, url in exports.items():
            ext = {"step_export": "step", "obj_export": "obj", "technical_drawing_export": "pdf"}[key]
            sizes[ext] = fetch(url if url.startswith("http") else args.base.rstrip("/") + "/" + url.lstrip("/"), case_dir / ("model." + ext), tok)
        print("  turn2 %.0fs steps=%s | error=%s | exports=%s" % (t2["duration_s"], t2["steps"][-1:] or "-", t2["error"], sizes))
        (case_dir / "turns.json").write_text(json.dumps([t1, t2], ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        if sizes.get("step"):
            ok_count += 1
    print("\n%d/%d cases returned a STEP through the real stack | %s" % (ok_count, len(wanted), out))
    return 0 if ok_count == len(wanted) else 1


if __name__ == "__main__":
    sys.exit(main())
