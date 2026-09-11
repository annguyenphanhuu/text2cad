"""
Isometric snapshots of built parts, to eyeball a sweep for bizarre shapes.

    python tests/ir_snapshots.py outputs/ir_sweep_20260909_172150 [--cols 8] [--size 220]

Step 1 runs freecadcmd once over every <case>/<case>.step in the directory:
TechDraw.projectEx gives the visible edges of an isometric view, which are
discretised into 2D polylines (no GUI needed).  Step 2 tiles them into
`_snapshots/contact_sheet.png` with the case id under each picture (PIL only).
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FREECADCMD = os.environ.get("FREECADCMD", r"C:\Program Files\FreeCAD 1.0\bin\freecadcmd.exe")

PROJECT_SCRIPT = r'''
import os, json, math
import FreeCAD as App, Part, TechDraw
job = json.load(open(os.environ["TOLERY_SNAP_JOB"], encoding="utf-8"))
out = {}
view_dir = App.Vector(1, -1, 0.8)          # from the part toward the viewer
rot = App.Rotation(view_dir, App.Vector(0, 0, 1))
for step in job["steps"]:
    try:
        shape = Part.Shape(); shape.read(step)
        comps = TechDraw.projectEx(shape, view_dir)     # V, V1, VN, VO, VI, H, H1, HN, HO, HI
        lines = []
        for comp in comps[:5]:                           # visible sets only
            for e in comp.Edges:
                try:
                    pts = e.discretize(Number=24) if e.Length > 1e-6 else []
                except Exception:
                    continue
                poly = []
                for p in pts:
                    q = rot.multVec(p)
                    poly.append((round(q.x, 3), round(q.y, 3)))
                if len(poly) >= 2:
                    lines.append(poly)
        out[step] = {"lines": lines}
    except Exception as exc:
        out[step] = {"error": str(exc)}
json.dump(out, open(job["out"], "w", encoding="utf-8"))
'''


def draw_part(lines, size):
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (size, size), "white")
    if not lines:
        return im
    xs = [p[0] for poly in lines for p in poly]
    ys = [p[1] for poly in lines for p in poly]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    w, h = max(x1 - x0, 1e-6), max(y1 - y0, 1e-6)
    scale = (size * 0.86) / max(w, h)
    ox = (size - w * scale) / 2.0
    oy = (size - h * scale) / 2.0
    d = ImageDraw.Draw(im)
    for poly in lines:
        pts = [(ox + (x - x0) * scale, size - (oy + (y - y0) * scale)) for x, y in poly]
        d.line(pts, fill="black", width=1)
    return im


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sweep_dir")
    ap.add_argument("--cols", type=int, default=8)
    ap.add_argument("--size", type=int, default=220)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    sweep = Path(args.sweep_dir)
    steps = sorted(str(p) for p in sweep.glob("*/*.step"))
    if not steps:
        sys.exit("no STEP files under %s" % sweep)
    snap_dir = sweep / "_snapshots"
    snap_dir.mkdir(exist_ok=True)
    script = snap_dir / "project.py"
    script.write_text(PROJECT_SCRIPT, encoding="utf-8")
    job = snap_dir / "job.json"
    job.write_text(json.dumps({"steps": steps, "out": str(snap_dir / "proj.json")}), encoding="utf-8")
    env = dict(os.environ, TOLERY_SNAP_JOB=str(job), PYTHONIOENCODING="utf-8")
    subprocess.run([FREECADCMD, str(script)], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=1800)
    proj = json.loads((snap_dir / "proj.json").read_text(encoding="utf-8"))
    from PIL import Image, ImageDraw
    items = [(Path(s).parent.name, proj.get(s, {})) for s in steps]
    cols, cell = args.cols, args.size
    rows = (len(items) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * cell, rows * (cell + 18)), "white")
    draw = ImageDraw.Draw(sheet)
    for i, (name, d) in enumerate(items):
        x, y = (i % cols) * cell, (i // cols) * (cell + 18)
        if "lines" in d:
            sheet.paste(draw_part(d["lines"], cell - 8), (x + 4, y + 4))
        else:
            draw.text((x + 8, y + cell // 2), "no projection", fill="red")
        draw.rectangle([x, y, x + cell - 1, y + cell - 1], outline=(200, 200, 200))
        draw.text((x + 3, y + cell + 2), name[:36], fill="black")
    out = Path(args.out) if args.out else snap_dir / "contact_sheet.png"
    sheet.save(out)
    print("wrote", out, "(%d parts)" % len(items))


if __name__ == "__main__":
    main()
