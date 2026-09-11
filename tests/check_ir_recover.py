"""The IR embedded in a stub script comes back unchanged (what the edit flow relies on after an API restart).

    python tests/check_ir_recover.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.core.ir_flow import ir_from_stub_script, make_stub_script  # noqa: E402


def main():
    ir = json.loads((ROOT / "tests" / "fixtures" / "ir_cases" / "capot_inside_dims.json").read_text(encoding="utf-8"))
    ir.pop("_expect", None)
    ir["name"] = "Capot d'essai \"quoted\" \\ back-slash"          # awkward characters must survive the repr round trip
    code = make_stub_script(ir, "Capot_d_essai", "steel")
    back, title = ir_from_stub_script(code)
    assert back == ir, "IR changed through the stub script"
    assert title == "Capot_d_essai", title
    assert ir_from_stub_script("import Part\nbox = Part.makeBox(1, 2, 3)") == (None, None), "legacy code must not be mistaken for a stub"
    assert ir_from_stub_script(None) == (None, None)
    print("check_ir_recover OK")


if __name__ == "__main__":
    main()
