"""
Deterministic validation of a part IR: what is missing, what cannot be built,
what violates a manufacturing rule.  Replaces the LLM-evaluated DFM prompt for
everything that is a number comparison (which is almost every rule in
data/**/rules.json).

    result = validate(ir_dict)
    result.questions  -> ask the user (missing required values / unbuildable)
    result.errors     -> geometry that cannot exist (feature outside its face...)
    result.warnings   -> DFM warnings shown with the confirmation ("do you want to continue?")
    result.ir, result.plan -> normalised IR + geometry plan when buildable
"""
from __future__ import annotations

import copy
import os
import sys
from dataclasses import dataclass, field
from typing import List, Optional

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from FreeCadUtil import ir_frames as F                   # noqa: E402
from src.core.ir import missing_required                # noqa: E402

# thickness limits per material and process: (min, max) - LC_05..07, B_01, SM_01..06
THICKNESS_LIMITS = {
    "steel": (0.5, 25.0),
    "stainless": (0.5, 20.0),
    "aluminum": (0.5, 20.0),
}
BENDING_LIMITS = (0.6, 25.0)

# B_03 reference table: thickness -> (min_flange, hole_to_bend, z_bend_dist)
_B03 = [
    (0.6, 5.0, 3.0, 8.5), (0.8, 6.0, 3.0, 9.1), (1.0, 6.0, 3.0, 9.5), (1.5, 7.5, 4.0, 11.0),
    (2.0, 9.0, 5.0, 13.5), (2.5, 10.0, 5.0, 14.5), (3.0, 12.0, 6.0, 16.0), (4.0, 16.0, 8.0, 21.0),
    (5.0, 20.0, 10.0, 26.0), (6.0, 25.0, 12.5, 30.5), (8.0, 35.0, 17.5, 40.0), (10.0, 45.0, 38.0, 60.0),
    (12.0, 59.0, 48.0, 74.0), (15.0, 70.0, 52.0, 85.0),
]


def b03_row(t: float):
    """Smallest table row whose thickness is >= t (conservative)."""
    for row in _B03:
        if t <= row[0] + 1e-9:
            return row
    return _B03[-1]


def material_key(material: Optional[str]) -> str:
    m = (material or "steel").lower()
    if "inox" in m or "stainless" in m or "304" in m or "316" in m:
        return "stainless"
    if "alu" in m:
        return "aluminum"
    return "steel"


@dataclass
class ValidationResult:
    questions: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    ir: Optional[dict] = None
    plan: Optional[object] = None

    @property
    def ok(self) -> bool:
        return not self.questions and not self.errors and self.plan is not None


def validate(ir: dict) -> ValidationResult:
    res = ValidationResult()
    res.questions = missing_required(ir or {})
    if res.questions:
        return res
    ir = _resolve_perforations(copy.deepcopy(ir or {}), res)
    if res.questions:
        return res
    try:
        nir, plan = F.plan_part(ir)
    except F.IRError as exc:
        res.errors.append(str(exc))
        return res
    res.ir, res.plan = nir, plan
    t = nir.get("thickness")
    mat = material_key(nir.get("material"))

    # ---- thickness limits (laser / bending) and standard thickness list
    if t is not None:
        lo, hi = THICKNESS_LIMITS[mat]
        if not (lo <= t <= hi):
            res.warnings.append("Thickness %g mm is outside the %s range we cut by laser (%g-%g mm). Do you want to continue?"
                                % (t, mat, lo, hi))
        if nir["family"] == "sheet" and nir.get("bends") and not (BENDING_LIMITS[0] <= t <= BENDING_LIMITS[1]):
            res.warnings.append("In industrial sheet metal work, it will be difficult to bend below %g mm and beyond %g mm (thickness %g mm). Do you want to continue?"
                                % (BENDING_LIMITS[0], BENDING_LIMITS[1], t))
        std = set(STANDARD_THICKNESS) | (STANDARD_THICKNESS_EXTRA[mat] if nir.get("material") else
                                          set().union(*STANDARD_THICKNESS_EXTRA.values()))
        if nir["family"] != "tube" and not any(abs(t - s) < 1e-6 for s in std):
            res.warnings.append("Warning! This thickness is not standard, do you want to continue? Do you want to know the standard thicknesses?")

    # ---- threads: size vs thickness (TH_04), nominal outside the ISO tables (TH_08), non-ISO pitch (TH_09)
    for f in nir.get("features") or []:
        if f.get("type") == "thread" and t is not None:
            _check_thread(f, t, mat, res)

    if nir["family"] == "tube":
        _check_tube(nir, plan, res)
        return res
    if nir["family"] == "profile":
        _check_face_positions(plan, res)
        return res

    # ---- flange lengths (B_04a) and bend radius sanity
    row = b03_row(t)
    for name in plan.order[1:]:
        w = plan.faces[name]
        if w.bend["rho"] >= 175:
            continue
        if w.V < row[1]:
            res.warnings.append("The %s flange is %g mm; the minimum flange length for %g mm sheet is %g mm. Do you want to continue?"
                                % (name, w.V, t, row[1]))
        if w.bend["radius"] < 0.5 * t:
            res.warnings.append("Inner bend radius %g mm on the %s bend is below half the thickness; we recommend a radius equal to the thickness."
                                % (w.bend["radius"], name))

    # ---- Z counter-bends: distance between two opposite bends of the base (B_06)
    _check_counter_bends(nir, plan, t, row[3], res)

    # ---- perforation pattern (PERF-001..004 of the perforated sheet calculator)
    for f in nir.get("features") or []:
        if f.get("type") == "perforation":
            _check_perforation(f, t, res)

    # ---- re-interpretations made while normalising (shown so the user can object)
    for f in nir.get("features") or []:
        lbl = _feature_label(f) if f.get("type") not in ("engrave",) else "note"
        if f.get("_swapped_axes"):
            res.warnings.append("%s: the position was given on the wrong axis of the %s; I swapped it (check the position in the summary)." % (lbl, f.get("face")))
        if f.get("_axis_flipped"):
            res.warnings.append("%s: the repeat direction did not fit the %s; I placed the row along the other direction." % (lbl, f.get("face")))

    # ---- features
    seen_edge_warn = set()
    for inst in plan.instances:
        f = inst["feature"]
        if f["type"] in ("corner_cut", "corner_fillet", "perforation"):
            continue
        face = plan.faces[inst["face"]]
        u, v, hu, hv = inst["u"], inst["v"], inst["hu"], inst["hv"]
        label = _feature_label(f)
        if face.kind == "edge":
            if f.get("depth") is None:
                res.questions.append("the depth of the %s drilled into the edge of the plate (mm)" % label.lower())
            if 2 * hv >= t:
                res.errors.append("%s does not fit in the %g mm plate thickness (edge drilling)." % (label, t))
            continue
        # centre must be on the face
        if not (0 <= u <= face.U and 0 <= v <= face.V):
            res.errors.append("%s on the %s is positioned at (%.1f, %.1f) mm, outside the face (%g x %g mm). Please check its position."
                              % (label, face.name, u, v, face.U, face.V))
            continue
        if face.kind == "base":
            blank = nir["blank"]
            if blank["type"] != "rect" and not F.point_on_blank(blank, u, v):
                res.errors.append("%s on the base plate is positioned at (%.1f, %.1f) mm, outside the plate outline. Please check its position."
                                  % (label, u, v))
                continue
            if _check_fold_and_rim_zones(nir, plan, face, label, u, v, hu, hv, t, res):
                continue
        # crossing an edge -> notch (allowed, but say so); u wraps around on a rim
        crossing = v - hv < -1e-6 or v + hv > face.V + 1e-6
        if not face.closed_u:
            crossing = crossing or u - hu < -1e-6 or u + hu > face.U + 1e-6
        if crossing and (face.name, label) not in seen_edge_warn:
            seen_edge_warn.add((face.name, label))
            res.warnings.append("%s on the %s reaches past the edge of the face and will be cut as an open notch." % (label, face.name))
            continue
        # size vs thickness (LC_01 / LC_03)
        if f["type"] in ("hole", "thread", "blind_hole", "countersink", "counterbore", "half_moon") and f["diameter"] < 0.7 * t:
            res.warnings.append("%s: diameter %g mm is below 0.7 x thickness (%g mm), difficult to laser cut. Do you want to continue?"
                                % (label, f["diameter"], 0.7 * t))
        if f["type"] == "slot" and f["size"][1] < 0.7 * t:
            res.warnings.append("%s: slot width %g mm is below 0.7 x thickness (%g mm). Do you want to continue?"
                                % (label, f["size"][1], 0.7 * t))
        if f["type"] == "thread":
            try:
                nominal = float(f["thread"][1:])
                if nominal < 3:
                    res.warnings.append("Tapping %s is not recommended; we recommend a minimum tapping of M3." % f["thread"])
            except ValueError:
                pass
        # distance to free edges (LC_02 / TH_02) and to bends (B_05)
        dists = {"u-": u - hu, "u+": face.U - u - hu, "v-": v - hv, "v+": face.V - v - hv}
        if face.closed_u:
            dists = {"v-": v - hv, "v+": face.V - v - hv}
        for side, d in dists.items():
            bent = _side_is_bent(face, side, plan)
            if bent:
                # hole edge to the theoretical corner minus the bend zone must leave >= t
                eff = d - face.shrink.get(side, 0.0)
                if eff < t - 1e-6:
                    res.warnings.append("%s on the %s is %.1f mm from the bend zone; at least %g mm (one thickness) of flat material is needed between a cutout and a bend. Do you want to continue?"
                                        % (label, face.name, max(eff, 0.0), t))
            elif d < t - 1e-6:
                res.warnings.append("%s on the %s leaves only %.1f mm to the free edge; the minimum is %g mm (one thickness). Do you want to continue?"
                                    % (label, face.name, d, t))
    _dedupe_inplace(res.warnings)
    return res


def _check_fold_and_rim_zones(nir, plan, face, label, u, v, hu, hv, t, res) -> bool:
    """Base features vs bend lines across the plate and rims: True when an error was recorded."""
    import math
    for fn in plan.folds:
        fb = plan.faces[fn].bend
        q, hq = (u, hu) if fb["axis"] == "x" else (v, hv)
        lo, hi = sorted((fb["x0"], fb["x1"]))
        if (q - fb["x1"]) * fb["sign"] > 1e-6:
            res.errors.append("%s on the base plate lies on the part that is folded up as the %s; place it on the %s face instead."
                              % (label, fn.replace("_", " "), fn))
            return True
        if q + hq > lo - t + 1e-6 and q - hq < hi + t - 1e-6:
            res.warnings.append("%s on the base plate is within one thickness of the %s bend line; at least %g mm of flat material is needed between a cutout and a bend. Do you want to continue?"
                                % (label, fn.replace("_", " "), t))
    for rn in plan.rims:
        rb = plan.faces[rn].bend
        cx, cy = rb["center"]
        d = math.hypot(u - cx, v - cy)
        h = max(hu, hv)
        if rb["rim"] == "outer":
            if d - h > rb["x0"] + 1e-6:
                res.errors.append("%s on the base plate lies in the rim; place it on the %s face instead." % (label, rn))
                return True
            if d + h > rb["x0"] - t:
                res.warnings.append("%s on the base plate is within one thickness of the rim bend. Do you want to continue?" % label)
        else:
            if d + h < rb["x0"] - 1e-6:
                res.errors.append("%s on the base plate lies in the neck; place it on the %s face instead." % (label, rn))
                return True
            if d - h < rb["x0"] + t:
                res.warnings.append("%s on the base plate is within one thickness of the neck bend. Do you want to continue?" % label)
    return False


def _side_is_bent(face, side, plan) -> bool:
    if face.kind == "base":
        for n in plan.order[1:]:
            w = plan.faces[n]
            if w.parent == "base":
                e = w.edge_of_parent
                if (e == "x-" and side == "u-") or (e == "x+" and side == "u+") or (e == "y-" and side == "v-") or (e == "y+" and side == "v+"):
                    return True
        return False
    if side == "v-":
        return True                       # the wall's own bend
    if side == "v+":
        return any(plan.faces[c].parent == face.name for c in plan.order[1:])
    return False


def _check_tube(nir, plan, res):
    tube = nir["tube"]
    for tb in tube.get("tabs") or []:
        if tb.get("protrusion_from_width"):
            res.warnings.append("Tenon %g mm: read as how far it protrudes beyond the tube end, over the full flat width of the %s face(s)."
                                % (tb["protrusion"], "/".join(tb["faces"])))
        elif tb.get("across_defaulted"):
            res.warnings.append("Tenon width was not given: the tenon takes the full flat width of the %s face(s)." % "/".join(tb["faces"]))
    if tube.get("length") and tube["length"] > 5800:
        res.warnings.append("Tube length %g mm exceeds our maximum of 5800 mm. Do you want to continue?" % tube["length"])
    _check_face_positions(plan, res)


def _check_face_positions(plan, res):
    for inst in plan.instances:
        f = inst["feature"]
        face = plan.faces[inst["face"]]
        if face.kind == "tube_round":
            continue
        u, v = inst["u"], inst["v"]
        if not (0 <= u <= face.U and 0 <= v <= face.V):
            res.errors.append("%s on the %s face is positioned outside the face (%g x %g mm)." % (_feature_label(f), face.name, face.U, face.V))


# standard thickness lists (legacy DFM step 3): common values + per-material additions
STANDARD_THICKNESS = (0.5, 0.6, 0.8, 1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10, 12, 15, 20, 25, 30, 40, 50, 60, 80, 100)
STANDARD_THICKNESS_EXTRA = {"steel": {35, 70, 90, 120, 150, 200}, "stainless": {0.4}, "aluminum": {0.3, 0.4, 150}}


def _thread_range(t: float, mat: str):
    """TH_04: (min nominal, max nominal) offered for a thickness, None below 1.5 mm."""
    if t < 1.5 - 1e-9:
        return None
    if t < 2.0:
        return (3, 5)
    if t <= 3.0 + 1e-9:
        return (3, 10)
    if t <= 12.0 + 1e-9:
        return (4 if mat == "stainless" else 3, 16)
    return (3, 24)


def _check_thread(f, t, mat, res):
    key = f.get("thread") or ""
    try:
        nominal = float(key[1:])
    except (ValueError, IndexError):
        return
    rng = _thread_range(t, mat)
    if rng is None:
        res.warnings.append("Tapped hole %s: we only add threads from 1.5 mm thickness (sheet %g mm). Do you want to continue?" % (key, t))
    elif not (rng[0] - 1e-9 <= nominal <= rng[1] + 1e-9):
        res.warnings.append("Tapped hole %s on %g mm sheet: our threading range for this thickness is M%d-M%d. Do you want to continue?"
                            % (key, t, rng[0], rng[1]))
    fine_keys = {k for k, _p in F.TAP_DRILL_FINE.keys()}
    if key not in F.TAP_DRILL and key not in fine_keys:
        res.warnings.append("Warning! This type of threading (%s) is outside the metric thread data table. Do you want to continue or see the metric thread data?" % key)
    elif f.get("pitch") is not None:
        p = float(f["pitch"])
        coarse = F.ISO_COARSE_PITCH.get(key)
        if (key, p) not in F.TAP_DRILL_FINE and not (coarse is not None and abs(coarse - p) < 1e-6):
            res.warnings.append("Warning! The entered data (%s x %g) does not correspond to standard or fine ISO pitch threading data. Do you want to continue or see this data?" % (key, p))


def _check_counter_bends(nir, plan, t, z_min, res):
    """B_06: two opposite bends on the base (Z profile) need a web of at least z_bend_dist between them."""
    blank = nir["blank"]
    walls = [plan.faces[n] for n in plan.order[1:] if plan.faces[n].parent == "base" and plan.faces[n].kind == "wall"]
    seen = set()
    for a in walls:
        for b in walls:
            if a.name >= b.name or a.bend["direction"] == b.bend["direction"]:
                continue
            ea, eb = a.edge_of_parent, b.edge_of_parent
            web = None
            if a.bend.get("fold") and b.bend.get("fold") and a.bend["axis"] == b.bend["axis"]:
                web = abs(a.bend["x_c"] - b.bend["x_c"])
            elif {ea, eb} == {"x-", "x+"} and blank["type"] in ("rect", "stadium"):
                web = blank["x"]
            elif {ea, eb} == {"y-", "y+"} and blank["type"] in ("rect", "stadium"):
                web = blank["y"]
            if web is not None and web < z_min - 1e-6 and (a.name, b.name) not in seen:
                seen.add((a.name, b.name))
                res.warnings.append("The two opposite bends %s / %s are only %g mm apart (web); for %g mm sheet the minimum distance between counter-bends is %g mm. Do you want to continue?"
                                    % (a.name, b.name, web, t, z_min))


def _check_perforation(f, t, res):
    lay = f["layout"]
    if lay["count"] == 0:
        res.errors.append("Perforation %s: no hole fits on the plate with this pitch." % f["notation"])
        return
    pct = f["pct_theoretical"]
    if pct > 65:
        res.warnings.append("Perforation %s: open area %.1f%% is above 65%%, structural integrity is insufficient - increase the pitch or reduce the hole size. Do you want to continue?" % (f["notation"], pct))
    elif pct > 50:
        res.warnings.append("Perforation %s: open area %.1f%% is above 50%%, plate rigidity may be reduced. Do you want to continue?" % (f["notation"], pct))
    if f.get("open_area_pct") is not None and abs(f["open_area_pct"] - pct) > 0.5 and not f.get("_resolved_from_pct"):
        res.warnings.append("Perforation %s gives %.1f%% open area, not the %g%% requested; the notation was kept." % (f["notation"], pct, f["open_area_pct"]))
    min_dim = min(f["hole_w"], f["hole_l"])
    if min_dim < t:
        res.warnings.append("Perforation %s: hole size %g mm is below the sheet thickness (%g mm), laser cutting may be difficult. Do you want to continue?" % (f["notation"], min_dim, t))
    min_margin = min_dim / 2.0 + t
    if lay["margin_x"] < min_margin or lay["margin_y"] < min_margin:
        res.warnings.append("Perforation %s: the margins to the plate edges (%.1f / %.1f mm) are small; we recommend at least %.1f mm (half a hole + one thickness). Do you want to continue?"
                            % (f["notation"], lay["margin_x"], lay["margin_y"], min_margin))


_PERF_TYPES = ("perforation", "perforated", "perforations", "perf", "perforated_pattern", "hole_grid", "perforation_pattern")


def _resolve_perforations(ir: dict, res: ValidationResult) -> dict:
    """% open area -> the missing pitch (reverse_C) or hole size (reverse_D), with the perforated-sheet calculator."""
    for f in ir.get("features") or []:
        if str(f.get("type") or "").lower() not in _PERF_TYPES:
            continue
        shape = F.PERF_SHAPES.get(str(f.get("shape") or f.get("hole_shape") or "").strip().lower().replace(" ", "_").replace("-", "_"))
        size = f.get("size")
        if size is None:
            size = f.get("diameter") if shape == "R" else f.get("side")
        pitch, pct = f.get("pitch"), f.get("open_area_pct")
        if shape is None or (size is not None and pitch is not None) or pct is None:
            continue
        ptype = F.PERF_PITCHES.get(str(f.get("pitch_type") or f.get("pattern_type") or "").strip().lower().replace(" ", "_").replace("-", "_"))
        ptype = ptype or ("U" if isinstance(pitch, (list, tuple)) else "T")
        fmt = lambda v: ("%g" % float(v))
        if size is None:
            shape_tok = shape
        elif isinstance(size, (list, tuple)):
            shape_tok = "%s%sx%s" % (shape, fmt(min(size)), fmt(max(size)))
        else:
            shape_tok = "%s%s" % (shape, fmt(size))
        if pitch is None:
            pitch_tok = ptype
        elif isinstance(pitch, (list, tuple)):
            pitch_tok = "%s%sx%s" % (ptype, fmt(pitch[0]), fmt(pitch[1] if len(pitch) > 1 else pitch[0]))
        else:
            pitch_tok = "%s%s" % (ptype, fmt(pitch))
        mode = "reverse_C" if pitch is None else "reverse_D"
        try:
            from src.utils.perforated_sheet_calculator import compute_perforated_sheet_from_extracted_params
            blank = ir.get("blank") or {}
            result = compute_perforated_sheet_from_extracted_params(shape_tok, pitch_tok, pct, mode, blank.get("x"), blank.get("y"), ir.get("thickness"))
        except Exception as exc:
            result = {"error": "calc", "message": str(exc)}
        if result.get("error"):
            what = "pitch (centre to centre, mm)" if mode == "reverse_C" else "hole size (mm)"
            why = str(result.get("message") or result["error"]).split(". ")[0].rstrip(".")
            res.questions.append("the %s of the perforation - it cannot be derived from %g%% open area alone (%s)" % (what, pct, why))
            continue
        val = float(result["inferred_value_mm"])
        if mode == "reverse_C":
            f["pitch"] = round(val, 3)
        else:
            f["size"] = round(val, 3)
        f["pitch_type"] = ptype
        f["_resolved_from_pct"] = mode
    return ir


def _feature_label(f) -> str:
    t = f["type"]
    if t in ("hole", "blind_hole"):
        return "Hole Ø%g" % f["diameter"]
    if t == "half_moon":
        return "Half-moon cutout Ø%g" % f["diameter"]
    if t == "perforation":
        return "Perforation %s" % f.get("notation", "")
    if t == "thread":
        return "Tapped hole %s" % f["thread"]
    if t == "countersink":
        return "Countersunk hole Ø%g/Ø%g" % (f["diameter"], f["cs_diameter"])
    if t == "counterbore":
        return "Counterbored hole Ø%g/Ø%g" % (f["diameter"], f["cb_diameter"])
    if t == "hex":
        return "Hexagonal hole"
    if t == "slot":
        return "Slot %gx%g" % tuple(f["size"])
    if t == "rect":
        return "Rectangular cutout %gx%g" % tuple(f["size"])
    if t == "keyhole":
        return "Keyhole"
    if t == "boss":
        return "Boss Ø%g x %g" % (f["diameter"], f["height"])
    if t == "corner_fillet":
        return "Corner radius %g" % f["radius"]
    return t


def _dedupe_inplace(items: List[str]) -> None:
    seen = set()
    i = 0
    while i < len(items):
        if items[i] in seen:
            del items[i]
        else:
            seen.add(items[i])
            i += 1


if __name__ == "__main__":
    r = validate({"thickness": 4, "blank": {"type": "rect", "x": 160, "y": 50},
                  "bends": [{"name": "wall", "edge": "x+", "length": 80}],
                  "features": [{"type": "hole", "face": "wall", "diameter": 12, "at": {"u": {"from": "center"}, "v": {"from": "bend", "dist": 50}}},
                               {"type": "hole", "face": "base", "diameter": 8, "at": {"u": {"from": "center"}, "v": {"from": "center"}},
                                "pattern": {"type": "linear", "count": 2, "pitch": 120, "axis": "u"}}]})
    assert r.ok, (r.questions, r.errors)
    assert not r.warnings, r.warnings
    r2 = validate({"thickness": 4, "blank": {"type": "rect", "x": 160, "y": 50},
                   "features": [{"type": "hole", "diameter": 8, "at": {"u": {"from": "x-", "dist": 5}, "v": {"from": "center"}}}]})
    assert r2.warnings and "free edge" in r2.warnings[0], r2.warnings
    r3 = validate({"thickness": None, "blank": {"type": "rect", "x": 160, "y": None}})
    assert r3.questions == ["the sheet thickness (mm)", "the width of the plate (mm)"], r3.questions
    r4 = validate({"thickness": 2, "blank": {"type": "rect", "x": 100, "y": 50},
                   "features": [{"type": "hole", "diameter": 8, "at": {"u": {"from": "x-", "dist": 150}, "v": {"from": "center"}}}]})
    assert r4.errors and "outside the face" in r4.errors[0]
    print("ir_validate self-check OK")
