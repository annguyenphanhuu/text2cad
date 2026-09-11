"""
Render a part IR (normalised, with its geometry plan) as the texts the chatbot
shows: the confirmation message and the compact description stored in history.

Deterministic on purpose: the user confirms exactly what will be built.
"""
from __future__ import annotations

from typing import List, Optional

CONFIRM_FOOTER = '<span style="color:#8023ff">✅ **Reply yes/ok to generate the CAD file, or tell me what to change.**</span>'

_EDGE_WORDS = {"x-": "left", "x+": "right", "y-": "front", "y+": "back", "tip": "tip", "rim": "outer", "inner_rim": "inner",
               "e0": "base (A-B)", "e1": "hypotenuse / B-C", "e2": "left (C-A)"}
_REF_WORDS_BASE = {"u-": "the left edge", "u+": "the right edge", "v-": "the front edge", "v+": "the back edge", "center": "centre"}
_REF_WORDS_WALL = {"u-": "the start end", "u+": "the far end", "v-": "the bend", "v+": "the free edge", "center": "centre"}


def _num(x) -> str:
    if x is None:
        return "?"
    x = float(x)
    return ("%d" % round(x)) if abs(x - round(x)) < 1e-6 else ("%.2f" % x).rstrip("0").rstrip(".")


def _face_word(face_name: str, plan) -> str:
    if face_name == "base":
        return "base plate"
    if face_name.startswith("edge:"):
        return "%s edge face (drilled into the thickness)" % _EDGE_WORDS.get(face_name[5:], face_name[5:])
    w = plan.faces.get(face_name)
    if w is None:
        return face_name
    label = face_name.replace("_", " ")
    if w.kind == "tube":
        return "%s face" % label
    if any(label.endswith(k) for k in ("wall", "flange", "leg", "return", "tab", "wing", "lip", "shelf", "side", "back", "front", "left", "right", "top", "bottom")):
        return label
    return "%s wall" % label if w.parent == "base" else "%s return" % label


def _position_text(f, face, plan) -> str:
    if f.get("positions"):
        pts = ", ".join("(%s, %s)" % (_num(u), _num(v)) for u, v in f["positions"])
        return "at %s mm" % pts
    at = f["at"]
    words = _REF_WORDS_BASE if face.kind == "base" else _REF_WORDS_WALL
    parts = []
    spans = (("u", "length" if face.kind == "base" else "width"), ("v", "width" if face.kind == "base" else "height"))
    if face.kind == "rim":
        spans = (("u", "circumference"), ("v", "height"))
    for axis, span_name in spans:
        spec = at[axis]
        if face.kind == "rim" and axis == "u":
            parts.append("%s mm along the circumference from the +x direction" % _num(spec["dist"]) if spec["dist"]
                         else "starting from the +x direction")
        elif spec["from"] == "center":
            parts.append("centred" + (" (offset %s mm)" % _num(spec["dist"]) if spec["dist"] else "") + " along the " + span_name)
        else:
            what = "centre" if spec["of"] == "center" else "edge"
            parts.append("%s mm (%s) from %s" % (_num(spec["dist"]), what, words[spec["from"]]))
    return ", ".join(parts)


def _pattern_text(f) -> str:
    pat = f.get("pattern")
    if not pat:
        n = 1
    elif pat["type"] == "linear":
        n = pat["count"]
    elif pat["type"] == "grid":
        n = pat["count"][0] * pat["count"][1]
    else:
        n = pat["count"]
    if f.get("mirror"):
        n *= 2 ** len(f["mirror"])
    if f.get("positions"):
        n = len(f["positions"])
    txt = ""
    if pat:
        if pat["type"] == "linear" and pat["count"] > 1:
            txt = ", %d in a row, %s along the %s" % (pat["count"], ("evenly distributed" if pat.get("even") else "pitch %s mm" % _num(pat["pitch"])),
                                                     "length" if pat["axis"] == "u" else "width")
        elif pat["type"] == "grid":
            txt = ", %dx%d grid, pitch %s x %s mm" % (pat["count"][0], pat["count"][1], _num(pat["pitch"][0]), _num(pat["pitch"][1]))
        elif pat["type"] == "polar":
            txt = ", %d on a Ø%s mm circle" % (pat["count"], _num(pat["circle_diameter"]))
    if f.get("mirror"):
        txt += ", mirrored across the " + " and ".join("length" if m == "u" else "width" for m in f["mirror"])
    return n, txt


def feature_lines(ir, plan) -> List[str]:
    lines = []
    for f in ir.get("features") or []:
        if f.get("note_only"):
            lines.append("%s (%s) - noted, not modelled" % (f["type"], f.get("note") or "engraving"))
            continue
        face = plan.faces.get(f["face"])
        where = "on the " + _face_word(f["face"], plan)
        if f["type"] == "corner_cut":
            lines.append("Diagonal corner cut %s x %s mm at the %s corner%s" % (
                _num(f["cut_length"]), _num(f["cut_width"]), f["corner"].replace("x-", "left ").replace("x+", "right ").replace("y-", "front").replace("y+", "back"),
                (", %s mm from the corner" % _num(f["distance_from_corner"])) if f.get("distance_from_corner") else ""))
            continue
        if f["type"] == "perforation":
            lay = f.get("layout") or {}
            shape = {"R": "round holes Ø%s mm" % _num(f["hole_w"]), "C": "square holes %s mm" % _num(f["hole_w"]),
                     "LR": "oblong holes %s x %s mm" % (_num(f["hole_w"]), _num(f["hole_l"])),
                     "LC": "rectangular slots %s x %s mm" % (_num(f["hole_w"]), _num(f["hole_l"]))}[f["shape"]]
            pitch = {"T": "staggered 60°, pitch %s mm" % _num(f["pitch_x"]), "Z": "Z stagger, pitch %s x %s mm" % (_num(f["pitch_y"]), _num(f["pitch_x"])),
                     "U": "square grid, pitch %s mm" % _num(f["pitch_x"]) if abs(f["pitch_x"] - f["pitch_y"]) < 1e-6
                     else "rectangular grid, pitch %s x %s mm" % (_num(f["pitch_y"]), _num(f["pitch_x"]))}[f["pitch_type"]]
            txt = "Perforation %s: %s, %s, %s%% open area (theoretical)" % (f["notation"], shape, pitch, _num(round(f["pct_theoretical"], 1)))
            if lay:
                txt += ", %d holes on the plate (%s%% actual), margins %s / %s mm" % (
                    lay["count"], _num(round(lay["pct_actual"], 1)), _num(round(lay["margin_x"], 1)), _num(round(lay["margin_y"], 1)))
            if f.get("_resolved_from_pct"):
                txt += " - %s derived from the requested %s%% open area" % ("pitch" if f["_resolved_from_pct"] == "reverse_C" else "hole size", _num(f["open_area_pct"]))
            lines.append(txt)
            continue
        n, ptxt = _pattern_text(f)
        t = f["type"]
        if t in ("hole", "blind_hole"):
            head = "%d hole%s Ø%s mm" % (n, "s" if n > 1 else "", _num(f["diameter"]))
            if f.get("depth"):
                head += " blind, depth %s mm" % _num(f["depth"])
        elif t == "thread":
            head = "%d tapped hole%s %s" % (n, "s" if n > 1 else "", f["thread"])
            if f.get("pitch"):
                head += " x %s" % _num(f["pitch"])
            head += " (drill Ø%s)" % _num(f["diameter"])
        elif t == "countersink":
            head = "%d countersunk hole%s Ø%s mm, countersink Ø%s at %s° on the %s side" % (
                n, "s" if n > 1 else "", _num(f["diameter"]), _num(f["cs_diameter"]), _num(f["cs_angle"]), f["cs_side"])
        elif t == "counterbore":
            head = "%d counterbored hole%s Ø%s mm, counterbore Ø%s x %s mm deep on the %s side" % (
                n, "s" if n > 1 else "", _num(f["diameter"]), _num(f["cb_diameter"]), _num(f["cb_depth"]), f["cb_side"])
        elif t == "hex":
            head = "%d hexagonal hole%s, %s mm across flats" % (n, "s" if n > 1 else "", _num(f["circumradius"] * 3 ** 0.5))
        elif t == "slot":
            head = "%d oblong slot%s %s x %s mm, long axis along the %s" % (
                n, "s" if n > 1 else "", _num(f["size"][0]), _num(f["size"][1]), "length" if f["orientation"] == "u" else "width")
        elif t == "rect":
            head = "%d rectangular cutout%s %s x %s mm (long side along the %s)" % (
                n, "s" if n > 1 else "", _num(f["size"][0]), _num(f["size"][1]), "length" if f["orientation"] == "u" else "width")
        elif t == "keyhole":
            head = "%d keyhole%s %s mm long, Ø%s / Ø%s" % (n, "s" if n > 1 else "", _num(f["size"][0]), _num(f["size"][1]), _num(f["size"][2]))
        elif t == "boss":
            head = "%d welded boss%s Ø%s mm, %s mm high%s" % (n, "es" if n > 1 else "", _num(f["diameter"]), _num(f["height"]),
                                                            (", bore Ø%s" % _num(f["inner_diameter"])) if f.get("inner_diameter") else "")
        elif t == "corner_fillet":
            lines.append("Corner radius %s mm on the free corners of the %s" % (_num(f["radius"]), _face_word(f["face"], plan)))
            continue
        elif t == "half_moon":
            side = {"u-": "left", "u+": "right", "v-": "front" if face.kind == "base" else "bend", "v+": "back" if face.kind == "base" else "free edge"}[f["flat"]]
            head = "%d half-moon cutout%s Ø%s mm (straight edge toward the %s, position = centre of the full circle)" % (n, "s" if n > 1 else "", _num(f["diameter"]), side)
        else:
            head = "%d %s" % (n, t)
        lines.append("%s %s, %s%s" % (head, where, _position_text(f, face, plan), ptxt))
    return lines


def parameter_lines(ir, plan) -> List[str]:
    lines = []
    if ir["family"] == "profile":
        p = ir["profile"]
        lines.append("%s profile: flange %s mm wide, web %s mm high%s, thickness %s mm, length %s mm" % (
            p["section"], _num(p["width"]), _num(p["height"]), " between the flanges" if p["section"] == "I" else "", _num(p["thickness"]), _num(p["length"])))
        if p.get("radius"):
            lines.append("Inner radius %s mm at the junctions" % _num(p["radius"]))
        return lines
    if ir["family"] == "tube":
        tube = ir["tube"]
        if tube["section"] == "rect":
            lines.append("Rectangular tube section: %s x %s mm" % (_num(tube["width"]), _num(tube["height"])))
        else:
            lines.append("Round tube outer diameter: %s mm" % _num(tube["diameter"]))
        lines.append("Length: %s mm" % _num(tube["length"]))
        lines.append("Wall thickness: %s mm" % _num(tube["wall"]) if not tube["solid"] else "Solid bar")
        ec = tube["end_cuts"]
        for k, word in (("start", "first end"), ("end", "second end")):
            if ec.get(k) is not None and ec[k] < 89.5:
                lines.append("Cut at %s° on the %s" % (_num(ec[k]), word))
        for tb in tube.get("tabs") or []:
            lines.append("Tenon(s) %s, protruding %s mm, on the %s face(s) at the %s end(s)" % (
                ("%s mm wide" % _num(tb["across"])) if tb.get("across") is not None else "full face width",
                _num(tb["protrusion"]), "/".join(tb["faces"]), tb["end"]))
        return lines
    blank = ir["blank"]
    lines.append("Thickness: %s mm" % _num(ir["thickness"]))
    if blank["type"] == "rect":
        if blank.get("dims") == "inside" and blank.get("x_inside") is not None:
            lines.append("Base plate: %s x %s mm inside the walls (outside %s x %s mm)" % (
                _num(blank["x_inside"]), _num(blank["y_inside"]), _num(blank["x"]), _num(blank["y"])))
        else:
            lines.append("Base plate: %s x %s mm (length x width)" % (_num(blank["x"]), _num(blank["y"])))
    elif blank["type"] == "stadium":
        lines.append("Oblong plate: %s x %s mm" % (_num(blank["x"]), _num(blank["y"])))
    elif blank["type"] == "disc":
        arc = blank.get("arc", 360.0)
        if arc >= 360.0:
            what = "Circular plate"
        elif abs(arc - 180.0) < 1e-6:
            what = "Half-disc plate"
        elif abs(arc - 90.0) < 1e-6:
            what = "Quarter-disc plate"
        else:
            what = "Circular sector plate (%s°)" % _num(arc)
        lines.append("%s: Ø%s mm" % (what, _num(blank["diameter"])) + (", inner Ø%s mm" % _num(blank["inner_diameter"]) if blank.get("inner_diameter") else ""))
    elif blank["type"] == "polygon":
        pts = blank["points"]
        if len(pts) == 3:
            import math
            a = math.dist(pts[0], pts[1]); b = math.dist(pts[1], pts[2]); c = math.dist(pts[2], pts[0])
            lines.append("Triangular plate, sides %s / %s / %s mm" % (_num(a), _num(b), _num(c)))
        elif blank.get("regular"):
            name = {5: "Pentagonal", 6: "Hexagonal", 8: "Octagonal"}.get(blank["regular"], "Regular %d-sided" % blank["regular"])
            lines.append("%s plate, %s mm across flats" % (name, _num(blank["across_flats"])))
        else:
            lines.append("Polygonal plate with %d sides" % len(pts))
    corner = {}
    for c, spec in (blank.get("corners") or {}).items():
        for k, v in spec.items():
            corner.setdefault((k, v), []).append(c)
    for (k, v), cs in corner.items():
        lines.append("Corner %s %s mm on %s" % ("radius" if k == "radius" else "chamfer", _num(v), "all corners" if len(cs) == 4 else ", ".join(cs)))
    for name in plan.order[1:]:
        w = plan.faces[name]
        b = w.bend
        dword = {"up": "upward", "down": "downward", "in": "inward", "out": "outward"}[b["direction"]]
        if b.get("fold"):
            what = "hem (crushed fold)" if b["rho"] >= 175 else "bent %s° %s" % (_num(b["angle"]), dword)
            lines.append("%s: %s along a straight line %s mm from the centre of the plate (measured to the outside of the flange); flange %s mm high, inner radius %s mm"
                         % (name.replace("_", " ").capitalize(), what, _num(b["offset"]), _num(w.V), _num(b["radius"])))
            continue
        if b.get("rim"):
            where = "all around the edge of the disc" if b["rim"] == "outer" else "all around the central hole (neck)"
            lines.append("%s: %s mm flange %s, bent %s° %s, inner radius %s mm (outside Ø%s mm)"
                         % (name.replace("_", " ").capitalize(), _num(w.V), where, _num(b["angle"]), dword, _num(b["radius"]), _num(2 * b["R_edge"])))
            continue
        if b["rho"] >= 175:
            lines.append("%s: hem (crushed fold) %s mm on the %s" % (name.replace("_", " ").capitalize(), _num(w.V), _face_word(w.parent, plan)))
            continue
        edge = _EDGE_WORDS.get(w.edge_of_parent, w.edge_of_parent)
        direction = b["direction"]
        lines.append("%s: %s mm flange on the %s edge of the %s, bent %s° %s, inner radius %s mm" % (
            name.replace("_", " ").capitalize(), _num(w.V), edge, _face_word(w.parent, plan), _num(b["angle"]),
            {"up": "upward", "down": "downward", "in": "inward", "out": "outward"}[direction], _num(b["radius"])))
    return lines


def description(ir, plan) -> str:
    """Compact text kept in the conversation history / DB."""
    lines = ["Type: %s" % plan.label]
    if ir.get("material") or ir.get("material_note"):
        lines.append("• Material: %s" % " ".join(x for x in (ir.get("material"), ir.get("material_note")) if x))
    lines += ["• " + l for l in parameter_lines(ir, plan)]
    feats = feature_lines(ir, plan)
    lines.append("Operations:")
    lines += ["• " + l for l in feats]
    return "\n".join(lines)


def confirm_message(ir, plan, warnings: Optional[List[str]] = None, assumptions: Optional[List[str]] = None) -> str:
    out = ["📋 **Here is how I understand your request:**",
           "**Important**: Have you correctly described your part according to the orientation cube?",
           "**Type**: %s" % plan.label]
    if ir.get("material") or ir.get("material_note"):
        out.append("**Material**: %s" % " ".join(x for x in (ir.get("material"), ir.get("material_note")) if x))
    out.append("**Parameters**:")
    out += ["  • %s" % l for l in parameter_lines(ir, plan)]
    feats = feature_lines(ir, plan)
    out.append("**Operations**:")
    out += ["  • %s" % l for l in feats] if feats else ["  • none"]
    if assumptions:
        out.append("**Assumptions I made** (tell me if wrong):")
        out += ["  • %s" % a for a in assumptions]
    if warnings:
        out.append("⚠️ **Manufacturing warnings**:")
        out += ["  • %s" % w for w in warnings]
    out.append(CONFIRM_FOOTER)
    return "\n".join(out)
