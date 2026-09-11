"""
The part IR: what the LLM extracts, what the validator checks, what the FreeCAD
builder (FreeCadUtil/build_from_ir.py) turns into a solid.

Geometry semantics are documented once, in FreeCadUtil/ir_frames.py; this module
is the typed envelope around them plus the "what is still missing?" check that
decides whether we ask the user a question.

Everything is optional at this level on purpose: the extractor is told to put
`null` for anything the user did not say, and `missing_required()` turns those
nulls into the questions we ask.  Structural problems (a bend on an unknown
face, a feature with no size...) are reported by ir_frames.normalize_ir.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, ConfigDict, Field


class _Loose(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)


class Position(_Loose):
    from_: Optional[str] = Field(None, alias="from")   # edge name or "center"
    dist: Optional[float] = 0.0
    of: Optional[str] = None                             # "center" | "edge"


class At(_Loose):
    u: Optional[Union[Position, float]] = None
    v: Optional[Union[Position, float]] = None


class Pattern(_Loose):
    type: Optional[str] = None            # linear | grid | polar
    count: Optional[Union[int, List[int]]] = None
    pitch: Optional[Union[float, List[float]]] = None
    axis: Optional[str] = None            # u | v
    circle_diameter: Optional[float] = None
    start_angle: Optional[float] = None


class Feature(_Loose):
    type: str = "hole"
    face: str = "base"
    diameter: Optional[float] = None
    size: Optional[List[float]] = None    # slot/rect: [long, short]; keyhole: [length, d_large, d_small]
    thread: Optional[str] = None
    pitch: Optional[float] = None
    cs_diameter: Optional[float] = None
    cs_angle: Optional[float] = None
    cs_side: Optional[str] = None
    across_flats: Optional[float] = None
    orientation: Optional[str] = None
    at: Optional[At] = None
    positions: Optional[List[Any]] = None
    pattern: Optional[Pattern] = None
    mirror: Optional[Union[str, List[str]]] = None
    depth: Optional[float] = None
    side: Optional[str] = None
    through: Optional[str] = None
    corner: Optional[str] = None
    cut_length: Optional[float] = None
    cut_width: Optional[float] = None
    distance_from_corner: Optional[float] = None
    radius: Optional[float] = None
    corners: Optional[Union[str, List[str]]] = None
    note: Optional[str] = None


class Bend(_Loose):
    name: Optional[str] = None
    on: str = "base"
    edge: Optional[str] = None
    length: Optional[float] = None
    angle: Optional[float] = 90.0
    direction: Optional[str] = None
    radius: Optional[float] = None
    offset: Optional[float] = None


class Triangle(_Loose):
    kind: Optional[str] = None
    side: Optional[float] = None
    base: Optional[float] = None
    equal_side: Optional[float] = None
    legs: Optional[List[float]] = None
    hypotenuse: Optional[float] = None
    leg: Optional[float] = None
    sides: Optional[List[float]] = None


class Blank(_Loose):
    type: str = "rect"
    x: Optional[float] = None
    y: Optional[float] = None
    diameter: Optional[float] = None
    inner_diameter: Optional[float] = None
    band_width: Optional[float] = None
    points: Optional[List[List[float]]] = None
    triangle: Optional[Triangle] = None
    corner_radius: Optional[float] = None
    corner_chamfer: Optional[float] = None
    corners: Optional[Dict[str, Dict[str, float]]] = None


class Tube(_Loose):
    section: str = "rect"
    width: Optional[float] = None
    height: Optional[float] = None
    diameter: Optional[float] = None
    length: Optional[float] = None
    wall: Optional[float] = None
    solid: bool = False
    corner_radius: Optional[float] = None
    end_cuts: Optional[Dict[str, Optional[float]]] = None
    tabs: Optional[List[Dict[str, Any]]] = None


class PartIR(_Loose):
    family: str = "sheet"                 # sheet | tube
    name: Optional[str] = None            # short title, e.g. "L-bracket 160x50 wall 80"
    material: Optional[str] = None        # steel | stainless | aluminum
    material_note: Optional[str] = None   # "S235", "304L", "DX51D+Z", finish...
    thickness: Optional[float] = None
    bend_radius: Optional[float] = None
    blank: Optional[Blank] = None
    bends: List[Bend] = Field(default_factory=list)
    features: List[Feature] = Field(default_factory=list)
    tube: Optional[Tube] = None


class Extraction(_Loose):
    """What the extract_ir prompt returns."""
    intent: str = "cad"                   # cad | assembly | unsupported | info | chat
    part: Optional[PartIR] = None
    questions: List[str] = Field(default_factory=list)
    assumptions: List[str] = Field(default_factory=list)
    unsupported_reason: Optional[str] = None
    reply: Optional[str] = None           # for intent info/chat: the text to show


# ------------------------------------------------------------------ helpers

def part_to_dict(part: Union[PartIR, dict, None]) -> dict:
    if part is None:
        return {}
    if isinstance(part, PartIR):
        return part.model_dump(by_alias=True, exclude_none=True)
    return dict(part)


def missing_required(ir: dict) -> List[str]:
    """Questions for the values the user must still give.  Empty = nothing missing.

    Only REQUIRED numbers are asked for.  Angles, bend radius, positions and
    directions have defaults (90 deg, thickness, centred, up) and are never asked.
    """
    q: List[str] = []
    fam = (ir.get("family") or "sheet").lower()
    if fam == "tube":
        tube = ir.get("tube") or {}
        sec = (tube.get("section") or "rect").lower()
        if sec in ("rect", "rectangular", "rectangle", "square"):
            if tube.get("width") is None:
                q.append("the tube section width (mm)")
            if tube.get("height") is None and tube.get("width") is None:
                q.append("the tube section height (mm)")
        else:
            if tube.get("diameter") is None:
                q.append("the tube outer diameter (mm)")
        if tube.get("length") is None:
            q.append("the tube length (mm)")
        if not tube.get("solid") and tube.get("wall") is None and ir.get("thickness") is None:
            q.append("the tube wall thickness (mm)")
        for tb in tube.get("tabs") or []:
            size = tb.get("size") or []
            width = tb.get("width", tb.get("across", size[0] if len(size) > 0 else None))
            prot = tb.get("protrusion", tb.get("length", size[1] if len(size) > 1 else None))
            if prot is None:
                q.append("how far the tenon(s) protrude beyond the tube end (mm)")
        for f in ir.get("features") or []:
            q.extend(_feature_missing(f))
        return _dedupe(q)

    if ir.get("thickness") is None:
        q.append("the sheet thickness (mm)")
    blank = ir.get("blank") or {}
    bt = (blank.get("type") or "rect").lower()
    if bt in ("rect", "rectangle", "plate", "sheet", "stadium", "oblong"):
        if blank.get("x") is None:
            q.append("the length of the plate (mm)")
        if blank.get("y") is None:
            q.append("the width of the plate (mm)")
    elif bt in ("disc", "disk", "circle", "circular", "round", "ring", "annulus", "half_disc", "semicircle", "semi_disc",
                "half_moon", "quarter_disc", "quarter_circle", "sector"):
        if blank.get("diameter") is None and blank.get("radius") is None:
            q.append("the plate diameter (mm)")
        if bt in ("ring", "annulus") and blank.get("inner_diameter") is None and blank.get("band_width") is None:
            q.append("the inner diameter of the ring (mm)")
    elif bt in ("hexagon", "hexagonal", "pentagon", "octagon", "octagonal", "heptagon", "regular_polygon", "regular"):
        if bt in ("regular_polygon", "regular") and not (blank.get("sides") or blank.get("n")):
            q.append("the number of sides of the polygon")
        if all(blank.get(k) is None for k in ("across_flats", "af", "circumradius", "diameter", "circumscribed_diameter", "across_corners", "side", "width")):
            q.append("the size of the polygonal plate (across flats or across corners, mm)")
    elif bt == "triangle":
        tri = blank.get("triangle") or {}
        kind = (tri.get("kind") or "").lower()
        if not kind:
            q.append("the triangle type and which dimension is which (equilateral side, isosceles base + equal side, "
                     "two right-angle legs, hypotenuse + one leg, or three named sides)")
        elif kind == "equilateral" and tri.get("side") is None:
            q.append("the triangle side length (mm)")
        elif kind == "isosceles" and (tri.get("base") is None or tri.get("equal_side") is None):
            q.append("the triangle base and equal-side lengths (mm)")
        elif kind in ("right", "right_angle") and not (tri.get("legs") or (tri.get("hypotenuse") and tri.get("leg"))):
            q.append("the two perpendicular legs of the right triangle, or the hypotenuse plus one leg (mm)")
        elif kind in ("right_isosceles", "isosceles_right") and tri.get("leg") is None and tri.get("hypotenuse") is None:
            q.append("one leg or the hypotenuse of the right-isosceles triangle (mm)")
        elif kind == "scalene" and not tri.get("sides"):
            q.append("the three side lengths of the triangle (mm)")
    elif bt == "polygon" and not blank.get("points"):
        q.append("the outline points of the plate")
    is_disc = bt in ("disc", "disk", "circle", "circular", "round", "ring", "annulus") or (
        bt in ("half_disc", "semicircle", "semi_disc", "half_moon", "quarter_disc", "quarter_circle", "sector") and (blank.get("arc") or 0) >= 360)
    for b in ir.get("bends") or []:
        edge = str(b.get("edge") or "").lower().replace(" ", "_").replace("-", "_")
        on_base = str(b.get("on") or "base").lower() == "base"
        fold = on_base and (b.get("offset") is not None or edge in _FOLD_EDGE_WORDS
                            or (is_disc and edge not in _RIM_EDGE_WORDS))
        if fold:
            if b.get("offset") is None and b.get("length") is None:
                q.append("where the %s bend line is (distance from the centre of the plate, mm) or the height of that flange (mm)"
                         % (b.get("name") or "").replace("_", " ").strip() or "the")
            continue
        if b.get("length") is None:
            q.append("the height of the %s flange (mm)" % (b.get("name") or "bent"))
    for f in ir.get("features") or []:
        q.extend(_feature_missing(f))
    return _dedupe(q)


_FOLD_EDGE_WORDS = ("fold", "fold_line", "line", "chord", "bend_line", "across", "offset", "middle", "center", "centre", "diameter_line")
_RIM_EDGE_WORDS = ("rim", "perimeter", "circumference", "outer", "outer_edge", "all_around", "around", "border", "periphery",
                   "outside", "edge", "outer_rim", "circular_edge", "round_edge", "skirt",
                   "inner_rim", "inner", "bore", "inner_edge", "hole", "inside", "neck", "collar", "spigot", "central_hole", "inner_circle")


def _feature_missing(f: dict) -> List[str]:
    t = (f.get("type") or "hole").lower()
    face = f.get("face") or "base"
    where = "" if face == "base" else " on the %s" % face
    if t in ("hole", "circle", "round_hole", "drill", "drilling", "blind_hole") and f.get("diameter") is None:
        return ["the diameter of the hole(s)%s (mm)" % where]
    if t in ("thread", "tapped", "tapped_hole", "threaded", "threaded_hole") and not (f.get("thread") or f.get("size") or f.get("designation")):
        return ["the thread size of the tapped hole(s)%s (e.g. M6)" % where]
    if t in ("countersink", "countersunk", "csk") and (f.get("diameter") is None or f.get("cs_diameter") is None):
        return ["the hole diameter and countersink diameter%s (mm)" % where]
    if t in ("counterbore", "counterbored", "cbore", "lamage") and (f.get("diameter") is None or (f.get("cb_diameter") is None and f.get("cs_diameter") is None)
                                                                  or (f.get("cb_depth") is None and f.get("depth") is None)):
        return ["the hole diameter, counterbore diameter and counterbore depth%s (mm)" % where]
    if t in ("slot", "oblong", "rect", "rectangle", "rectangular", "cutout", "window", "notch", "mortise") and not f.get("size"):
        return ["the dimensions of the %s%s (mm)" % ("slot" if t in ("slot", "oblong") else "cutout", where)]
    if t in ("hex", "hexagon", "hexagonal") and f.get("across_flats") is None and f.get("diameter") is None and f.get("size") is None:
        return ["the size of the hexagonal hole(s)%s (mm)" % where]
    if t == "keyhole" and not f.get("size"):
        return ["the keyhole dimensions%s (total length, large diameter, small diameter in mm)" % where]
    if t in ("corner_cut", "diagonal_cut") and (f.get("cut_length") is None or f.get("cut_width") is None):
        return ["the length and width of the diagonal corner cut (mm)"]
    if t in ("corner_fillet", "fillet") and f.get("radius") is None:
        return ["the corner radius (mm)"]
    if t in ("boss", "bushing", "standoff", "pin", "stud", "spacer") and (f.get("diameter") is None or (f.get("height") is None and f.get("length") is None)):
        return ["the outer diameter and height of the %s%s (mm)" % (t, where)]
    return []


def _dedupe(items: List[str]) -> List[str]:
    out: List[str] = []
    for it in items:
        if it not in out:
            out.append(it)
    return out


def format_questions(questions: List[str]) -> str:
    """The house format for a clarification reply."""
    if not questions:
        return ""
    return "**Please specify:**\n" + "\n".join("- " + q[0].upper() + q[1:] if q else q for q in questions)


PERFORATED_RE = re.compile(r"perfor|t[oô]le\s+perfor|%\s*(open|vide|ajour)|\bR\s?\d+(\.\d+)?\s?[TUZ]\s?\d", re.I)


def looks_perforated(text: str) -> bool:
    """Perforated sheets keep their dedicated legacy path (calculator + template)."""
    return bool(PERFORATED_RE.search(text or ""))
