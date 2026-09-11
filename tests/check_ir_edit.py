"""
Edit-mode check for the IR pipeline: apply natural-language edits to an existing
IR through IRFlow.patch and verify the resulting IR builds and changed as asked.

    python tests/check_ir_edit.py
"""
import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BASE_IR = {
    "family": "sheet", "name": "Motor mount", "material": "stainless", "thickness": 4,
    "blank": {"type": "rect", "x": 160, "y": 50},
    "bends": [{"name": "wall", "on": "base", "edge": "x+", "length": 80, "angle": 90, "direction": "up", "radius": 4}],
    "features": [
        {"type": "hole", "face": "wall", "diameter": 12, "at": {"u": {"from": "center"}, "v": {"from": "bend", "dist": 50}}},
        {"type": "hole", "face": "base", "diameter": 8, "at": {"u": {"from": "center"}, "v": {"from": "center"}},
         "pattern": {"type": "linear", "count": 2, "pitch": 120, "axis": "u"}},
    ],
}

EDITS = [
    ("déplace le trou Ø12 à 30 mm du pli", lambda ir: any(f.get("diameter") == 12 and f["at"]["v"].get("dist") == 30 for f in ir["features"])),
    ("add a Ø5 hole in the centre of the base", lambda ir: any(f.get("diameter") == 5 for f in ir["features"])),
    ("remove the two Ø8 holes", lambda ir: not any(f.get("diameter") == 8 for f in ir["features"])),
    ("make the wall 100 mm high and the sheet 3 mm thick", lambda ir: ir["bends"][0]["length"] == 100 and ir["thickness"] == 3),
    ("change the Ø8 holes to M8 tapped holes", lambda ir: any(str(f.get("type")) in ("thread", "tapped_hole", "tapped", "threaded_hole") for f in ir["features"])),
    ("add a 20 mm return folded inward on top of the wall", lambda ir: any(b.get("on") == "wall" for b in ir["bends"])),
]


async def main():
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    from src.core.chatbot import text_to_cad_agent as agent
    flow = agent._ir_flow
    ok = 0
    for i, (text, check) in enumerate(EDITS):
        env = await flow.patch(BASE_IR, "[USER]: %s\n" % text, "", "iredit_%d" % i)
        prep = flow.prepare(env)
        new_ir = (env.get("part") or {})
        passed = prep["kind"] == "ready" and check(new_ir)
        ok += passed
        print("%s %-55s -> %s | %s" % ("ok  " if passed else "FAIL", text, prep["kind"], env.get("summary") or prep.get("message", "")[:80]))
        if not passed:
            print("     ", json.dumps(new_ir.get("features"), ensure_ascii=False)[:300])
    print("%d/%d edits OK" % (ok, len(EDITS)))


if __name__ == "__main__":
    asyncio.run(main())
