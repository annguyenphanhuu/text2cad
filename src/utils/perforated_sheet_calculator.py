"""
perforated_sheet_calculator.py
══════════════════════════════════════════════════════════════════════════════
Utility module for Perforated Sheet open-area (pourcentage de vide) calculation.

Supports ALL notation types from data/Info/Perforated_Sheet/info.json:
  SHAPES  : R<D>            Round,  D = diameter (mm)
            C<S>            Square, S = side (mm)
            LC<W>x<L>       Rectangular slot (sharp corners), W × L (mm)
            LR<W>x<L>       Stadium / rounded oblong, W × L total length (mm)
  PITCHES : U<P>            Square grid,         pitch_x = pitch_y = P, stagger = 0
            U<py>x<px>      Rectangular grid,    pitch_y = py, pitch_x = px, stagger = 0
            T<P>            Staggered 60°,        pitch_x = P, pitch_y = P × sin(60°)
            Z<py>x<px>      Staggered (generic),  pitch_y = py, pitch_x = px,
                            stagger_offset = pitch_x / 2

TWO CALCULATION MODES:
  1. FORWARD  — notation + sheet dims → % vide + hole count
  2. REVERSE  — notation + % vide target + one known param → infer the missing param

% VIDE — two definitions:
  • Theoretical (RMIG / maille infinie): hole_area / unit_cell_area × 100 — same idea as
    https://rmigsolutions.com/fr/perforation/formules/calcul-du-pourcentage-de-vide/
  • Actual (plaque finie): same grid rules as generated CAD in
    data/Example/plate/Perforated_R12xT16.txt, Perforated_C20_U40.txt,
    Perforated_LR5x20.txt — centered pattern, n = floor((edge − hole_span)/pitch)+1,
    stagger on odd rows for T/Z, then clip holes outside the plate.

Author  : Tolery AI DFM Team
Version : 1.0.0
"""

import math
import re
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List


# ─────────────────────────────────────────────────────────────────────────────────
# CUSTOM EXCEPTIONS
# ─────────────────────────────────────────────────────────────────────────────────

class UnderdeterminedError(ValueError):
    """
    Raised when a reverse calculation has more unknowns than equations.

    Different from ValueError (parse failure):
    - ValueError       : notation string is malformed or unrecognised.
    - UnderdeterminedError : notation is valid but the system is under-constrained
      (e.g. LR/LC in reverse_D — 2 unknowns W and L, only 1 area equation).

    Callers (e.g. compute_perforated_sheet) catch this separately so they can
    return a structured {"error": "underdetermined", ...} dict instead of
    propagating an unhandled exception.
    """

# ─────────────────────────────────────────────────────────────────────────────
# DATA CLASSES
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PerfNotation:
    """Parsed perforation notation."""
    # Shape
    shape_type: str            # "round" | "square" | "oblong_rounded" (LR) | "oblong_rect" (LC)
    hole_d: Optional[float]    # Round: diameter (mm)
    hole_s: Optional[float]    # Square: side (mm)
    hole_w: Optional[float]    # Oblong: width (mm)
    hole_l: Optional[float]    # Oblong: total length (mm)

    # Pitch
    pitch_type: str            # "U" | "T" | "Z"
    pitch_x: float             # Center-to-center in X
    pitch_y: float             # Center-to-center in Y
    stagger: float             # Offset (0 for U, pitch_x/2 for T/Z)

    raw: str                   # Original notation string (e.g. "R12 T16")


@dataclass
class ForwardResult:
    """Result of forward calculation."""
    open_area_pct: float          # Theoretical % vide from RMIG formula
    actual_open_area_pct: float   # Real % vide based on actual hole count on sheet
    hole_count: int               # Total holes on sheet
    hole_area_each: float         # mm² per hole
    total_hole_area: float        # mm² total holes
    sheet_area: float             # mm² sheet surface
    margins: Dict[str, float]     # {"x": margin_x, "y": margin_y}
    dfm: Dict[str, Any]           # DFM warnings/errors
    formula_used: str             # Human-readable formula
    notation: PerfNotation


@dataclass
class ReverseResult:
    """Result of reverse calculation."""
    target_pct: float
    inferred_param: str           # Which param was inferred ("pitch" | "hole_size")
    inferred_value: float         # Inferred value (mm)
    formula_used: str
    note: str


# ─────────────────────────────────────────────────────────────────────────────
# NOTATION PARSER
# ─────────────────────────────────────────────────────────────────────────────

def parse_open_area_pct_from_text(text: str) -> Optional[float]:
    """
    Extract an open-area percentage from user text.

    Accepts both dot and comma decimal separators, e.g. "22.68%" and
    French-style "22,68%".
    """
    pct_number_pattern = r'\d+(?:[\.,]\d+)?'
    match = re.search(
        rf'(?:vide|open\s+area|pourcentage)[^\d]*({pct_number_pattern})\s*%'
        rf'|({pct_number_pattern})\s*%\s*(?:vide|open|ouvert)',
        text,
        re.IGNORECASE,
    )
    if not match:
        return None

    raw_pct = match.group(1) or match.group(2)
    return float(raw_pct.replace(',', '.')) if raw_pct else None


def _fmt_number(value: Any) -> str:
    """Format numeric values for compact notation tokens."""
    value = float(value)
    text = f"{value:.3f}".rstrip('0').rstrip('.')
    return text or "0"


def _coerce_optional_float(value: Any) -> Optional[float]:
    """Coerce LLM/regex numeric values, accepting decimal commas."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(',', '.').rstrip('%')
    return float(text) if text else None


def _resolved_reverse_notation(notation_str: str, inferred_param: str, inferred_value: float) -> str:
    """Build a fully resolved notation for reverse calculations."""
    if inferred_param == "pitch":
        m_shape = re.search(
            r'\b(?:LR\d+(?:\.\d+)?x\d+(?:\.\d+)?|LC\d+(?:\.\d+)?x\d+(?:\.\d+)?|R\d+(?:\.\d+)?|C\d+(?:\.\d+)?)\b',
            notation_str,
            re.IGNORECASE,
        )
        m_pitch = re.search(r'\b([TUZ])\b', notation_str, re.IGNORECASE)
        shape_part = m_shape.group(0).upper() if m_shape else "R?"
        pitch_letter = m_pitch.group(1).upper() if m_pitch else "T"
        return f"{shape_part} {pitch_letter}{_fmt_number(inferred_value)}"

    m_pitch_full = re.search(
        r'\b(?:T\d+(?:\.\d+)?|U\d+(?:\.\d+)?(?:x\d+(?:\.\d+)?)?|Z\d+(?:\.\d+)?x\d+(?:\.\d+)?)\b',
        notation_str,
        re.IGNORECASE,
    )
    m_shape_letter = re.search(r'\b(LR|LC|R|C)\b', notation_str, re.IGNORECASE)
    shape_letter = m_shape_letter.group(1).upper() if m_shape_letter else "R"
    pitch_part = m_pitch_full.group(0).upper() if m_pitch_full else notation_str
    return f"{shape_letter}{_fmt_number(inferred_value)} {pitch_part}"


def compute_perforated_sheet_from_extracted_params(
    shape_notation: Optional[str],
    pitch_notation: Optional[str],
    pct_vide: Any,
    calc_mode: str,
    sheet_length: Optional[float] = None,
    sheet_width: Optional[float] = None,
    sheet_thickness: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Compute directly from structured perforated parameters extracted by the LLM.

    This keeps the math/parsing contract in this calculator module and avoids
    rebuilding a synthetic user sentence just so the agent can regex-parse it again.
    """
    shape = (shape_notation or "").strip()
    pitch = (pitch_notation or "").strip()
    notation = f"{shape} {pitch}".strip()
    target_pct = _coerce_optional_float(pct_vide)

    if calc_mode == "forward":
        if not shape or not pitch:
            return {
                "error": "missing_notation",
                "message": "Forward perforated calculation requires shape_notation and pitch_notation.",
            }
        result = compute_perforated_sheet(
            notation,
            sheet_length=sheet_length,
            sheet_width=sheet_width,
            sheet_thickness=sheet_thickness,
        )
        result["resolved_notation"] = result.get("input_notation", notation)
        return result

    if calc_mode == "reverse_C":
        if not shape or not pitch or target_pct is None:
            return {
                "error": "missing_reverse_pitch_params",
                "message": "Reverse pitch calculation requires shape_notation, pitch_notation, and pct_vide.",
            }
        result = compute_perforated_sheet(
            notation,
            sheet_length=sheet_length,
            sheet_width=sheet_width,
            sheet_thickness=sheet_thickness,
            target_pct=target_pct,
            reverse_mode="pitch",
        )
        result["_display_mode"] = "reverse_C"
        if not result.get("error"):
            result["resolved_notation"] = _resolved_reverse_notation(
                result.get("input_notation", notation),
                result.get("inferred_param", ""),
                result.get("inferred_value_mm", 0),
            )
        return result

    if calc_mode == "reverse_D":
        if not shape or not pitch or target_pct is None:
            return {
                "error": "missing_reverse_hole_params",
                "message": "Reverse hole-size calculation requires shape_notation, pitch_notation, and pct_vide.",
            }
        result = compute_perforated_sheet(
            notation,
            sheet_length=sheet_length,
            sheet_width=sheet_width,
            sheet_thickness=sheet_thickness,
            target_pct=target_pct,
            reverse_mode="hole_size",
        )
        result["_display_mode"] = "reverse_D"
        if not result.get("error"):
            result["resolved_notation"] = _resolved_reverse_notation(
                result.get("input_notation", notation),
                result.get("inferred_param", ""),
                result.get("inferred_value_mm", 0),
            )
        return result

    return {
        "error": "unsupported_calc_mode",
        "message": f"Unsupported perforated calc_mode: {calc_mode}",
    }


def parse_notation(notation_str: str) -> PerfNotation:
    """
    Parse a perforation notation string into a PerfNotation dataclass.

    Accepted formats (case-insensitive):
        "R12 T16"          Round Ø12mm, Staggered 60° pitch 16mm
        "R10 U15"          Round Ø10mm, Grid/Inline pitch 15mm
        "C20 U40"          Square 20mm, Grid pitch 40mm
        "C20 U25x60"       Square 20mm, rectangular grid pY=25 pX=60
        "LC5x20 Z9x24"     Rect slot W5 L20mm, Z-stagger pitch_y=9 pitch_x=24
        "LR5x20 T12"       Oblong W5 L20mm, Staggered 60° pitch 12mm

    Raises:
        ValueError if the notation cannot be parsed.
    """
    s = notation_str.strip()

    # ── 1. Shape ─────────────────────────────────────────────────────────────
    shape_type = None
    hole_d = hole_s = hole_w = hole_l = None

    # Oblong: LC (rectangular slot) or LR (rounded/stadium)  — must check before R
    m = re.search(r'\b(LC|LR)(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)\b', s, re.IGNORECASE)
    if m:
        prefix = m.group(1).upper()
        # LR = rounded ends (stadium); LC = rectangular slot (sharp corners)
        shape_type = "oblong_rounded" if prefix == "LR" else "oblong_rect"
        hole_w = float(m.group(2))   # width (shorter dim)
        hole_l = float(m.group(3))   # total length (longer dim)
    else:
        # Round: R<D>
        m = re.search(r'\bR(\d+(?:\.\d+)?)\b', s, re.IGNORECASE)
        if m:
            shape_type = "round"
            hole_d = float(m.group(1))   # NOTE: R = diameter in RMIG notation
        else:
            # Square: C<S>
            m = re.search(r'\bC(\d+(?:\.\d+)?)\b', s, re.IGNORECASE)
            if m:
                shape_type = "square"
                hole_s = float(m.group(1))
            else:
                raise ValueError(
                    f"Cannot parse shape from notation '{notation_str}'. "
                    "Expected R<D>, C<S>, LC<W>x<L>, or LR<W>x<L>."
                )

    # ── 2. Pitch ─────────────────────────────────────────────────────────────
    pitch_type = None
    pitch_x = pitch_y = stagger = 0.0

    # Z<py>x<px>  (must check before T to avoid T matching Z string)
    m_z = re.search(r'\bZ(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)\b', s, re.IGNORECASE)
    if m_z:
        pitch_type = "Z"
        pitch_y = float(m_z.group(1))
        pitch_x = float(m_z.group(2))
        stagger  = pitch_x / 2.0
    else:
        # T<P>  Staggered 60°
        m_t = re.search(r'\bT(\d+(?:\.\d+)?)\b', s, re.IGNORECASE)
        if m_t:
            pitch_type = "T"
            pitch_x = float(m_t.group(1))
            pitch_y = pitch_x * math.sin(math.radians(60))   # = P × 0.8660
            stagger  = pitch_x / 2.0
        else:
            # U<py>x<px> rectangular grid — MUST match before U<P> (so U25x60 is not read as U25)
            m_u2 = re.search(r'\bU(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)\b', s, re.IGNORECASE)
            if m_u2:
                pitch_type = "U"
                pitch_y = float(m_u2.group(1))
                pitch_x = float(m_u2.group(2))
                stagger = 0.0
            else:
                # U<P> square grid
                m_u = re.search(r'\bU(\d+(?:\.\d+)?)\b', s, re.IGNORECASE)
                if m_u:
                    pitch_type = "U"
                    pitch_x = float(m_u.group(1))
                    pitch_y = pitch_x
                    stagger = 0.0
                else:
                    raise ValueError(
                        f"Cannot parse pitch from notation '{notation_str}'. "
                        "Expected U<P>, U<py>x<px>, T<P>, or Z<py>x<px>."
                    )

    return PerfNotation(
        shape_type=shape_type,
        hole_d=hole_d, hole_s=hole_s, hole_w=hole_w, hole_l=hole_l,
        pitch_type=pitch_type,
        pitch_x=pitch_x, pitch_y=pitch_y, stagger=stagger,
        raw=notation_str,
    )


# ─────────────────────────────────────────────────────────────────────────────
# HOLE AREA HELPER
# ─────────────────────────────────────────────────────────────────────────────

def _hole_area(n: PerfNotation) -> float:
    """Return the area of ONE hole in mm²."""
    if n.shape_type == "round":
        return math.pi * (n.hole_d / 2) ** 2     # π·(D/2)²
    elif n.shape_type == "square":
        return n.hole_s ** 2                       # S²
    elif n.shape_type == "oblong_rounded":
        # LR — Stadium shape: rectangle (L-W)×W + 2 semicircles of diameter W
        W, L = n.hole_w, n.hole_l
        return math.pi * (W / 2) ** 2 + (L - W) * W
    elif n.shape_type == "oblong_rect":
        # LC — Rectangular slot: simple L×W (sharp corners)
        return n.hole_l * n.hole_w
    raise ValueError(f"Unknown shape_type: {n.shape_type}")


def _hole_min_dim(n: PerfNotation) -> float:
    """Return the critical 'blocking' dimension of the hole (for DFM checks)."""
    if n.shape_type == "round":
        return n.hole_d
    elif n.shape_type == "square":
        return n.hole_s
    elif n.shape_type in ("oblong_rounded", "oblong_rect"):
        return min(n.hole_w, n.hole_l)   # narrowest span
    return 0.0


def _hole_span_xy_mm(n: PerfNotation) -> tuple[float, float]:
    """
    Axis-aligned footprint of one hole on the plate (X = sheet length, Y = sheet width).

    Matches example scripts: round/square use the same span along X and Y; LR/LC use
    long axis L along X and width W along Y (see Perforated_LR5x20.txt).
    """
    if n.shape_type in ("oblong_rounded", "oblong_rect"):
        return (n.hole_l, n.hole_w)
    d = _hole_min_dim(n)
    return (d, d)


def _count_holes_finite_plate_centered_grid(
    sheet_length: float,
    sheet_width: float,
    half_x: float,
    half_y: float,
    pitch_x: float,
    pitch_y: float,
    stagger: float,
    tol: float = 1e-6,
) -> tuple[int, float, float, int, int]:
    """
    Hole count and margins for a finite rectangular plate.

    Algorithm aligned with:
      - Perforated_R12xT16.txt  (round, T stagger)
      - Perforated_C20_U40.txt  (square, U grid)
      - Perforated_LR5x20.txt   (LR oblong, Z stagger)

    Returns:
        (hole_count, margin_x, margin_y, n_cols, n_rows)
    """
    hole_span_x = half_x * 2.0
    hole_span_y = half_y * 2.0

    if sheet_length >= hole_span_x:
        n_cols = int((sheet_length - hole_span_x) / pitch_x) + 1
        span_x = (n_cols - 1) * pitch_x + hole_span_x
        margin_x = (sheet_length - span_x) / 2.0
    else:
        n_cols = 0
        margin_x = sheet_length / 2.0

    if sheet_width >= hole_span_y:
        n_rows = int((sheet_width - hole_span_y) / pitch_y) + 1
        span_y = (n_rows - 1) * pitch_y + hole_span_y
        margin_y = (sheet_width - span_y) / 2.0
    else:
        n_rows = 0
        margin_y = sheet_width / 2.0

    count = 0
    for row in range(n_rows):
        cy = margin_y + half_y + row * pitch_y
        x_off = stagger if (row % 2 == 1) else 0.0
        for col in range(n_cols):
            cx = margin_x + half_x + col * pitch_x + x_off
            if (
                cx - half_x >= -tol
                and cx + half_x <= sheet_length + tol
                and cy - half_y >= -tol
                and cy + half_y <= sheet_width + tol
            ):
                count += 1

    return count, margin_x, margin_y, n_cols, n_rows


def _pitch_cell_area(n: PerfNotation) -> float:
    """Return the unit cell area (mm²) for the pitch pattern."""
    if n.pitch_type == "U":
        return n.pitch_x * n.pitch_y
    elif n.pitch_type == "T":
        # Staggered 60°: cell = px × py (2 holes per 2 rows → same formula)
        return n.pitch_x * n.pitch_y
    elif n.pitch_type == "Z":
        return n.pitch_x * n.pitch_y
    return n.pitch_x * n.pitch_y


# ─────────────────────────────────────────────────────────────────────────────
# FORMULA LABEL
# ─────────────────────────────────────────────────────────────────────────────

def _formula_label(n: PerfNotation, pct: float) -> str:
    """Build a human-readable description of the formula used."""
    shape_desc = {
        "round":          f"Round Ø{n.hole_d}mm",
        "square":         f"Square {n.hole_s}×{n.hole_s}mm",
        "oblong_rounded": f"Oblong-LR (rounded) {n.hole_w}×{n.hole_l}mm",
        "oblong_rect":    f"Oblong-LC (rectangular) {n.hole_w}×{n.hole_l}mm",
    }[n.shape_type]

    if n.pitch_type == "U" and abs(n.pitch_x - n.pitch_y) > 1e-6:
        u_pitch_desc = f"Rectangular grid pY={n.pitch_y}mm pX={n.pitch_x}mm"
    elif n.pitch_type == "U":
        u_pitch_desc = f"Square grid pX=pY={n.pitch_x}mm"
    else:
        u_pitch_desc = ""

    pitch_desc = {
        "U": u_pitch_desc or f"Grid/Inline pX={n.pitch_x}mm pY={n.pitch_y}mm",
        "T": f"Staggered 60° pX={n.pitch_x:.3f}mm pY={n.pitch_y:.3f}mm",
        "Z": f"Staggered Z  pX={n.pitch_x}mm pY={n.pitch_y}mm",
    }[n.pitch_type]

    cell_area = _pitch_cell_area(n)
    hole_area = _hole_area(n)
    return (
        f"shape={shape_desc} | pitch={pitch_desc} | "
        f"hole_area={hole_area:.3f}mm² | cell_area={cell_area:.3f}mm² | "
        f"% théorique = hole_area/cell_area × 100 = {pct:.2f}%"
    )


# ─────────────────────────────────────────────────────────────────────────────
# FORWARD CALCULATION
# ─────────────────────────────────────────────────────────────────────────────

def calculate_open_area(
    notation_str: str,
    sheet_length: Optional[float] = None,
    sheet_width: Optional[float] = None,
    sheet_thickness: Optional[float] = None,
) -> ForwardResult:
    """
    FORWARD MODE: notation → % vide.

    Args:
        notation_str    : e.g. "R12 T16", "C20 U40", "LC5x20 Z9x24"
        sheet_length    : X dimension (mm). None → only theoretical % returned.
        sheet_width     : Y dimension (mm). None → only theoretical % returned.
        sheet_thickness : Z dimension (mm). Required for DFM checks only.

    Returns:
        ForwardResult with theoretical + actual % vide, hole count, DFM warnings.
    """
    n = parse_notation(notation_str)

    hole_area  = _hole_area(n)
    cell_area  = _pitch_cell_area(n)
    theo_pct   = (hole_area / cell_area) * 100.0
    formula    = _formula_label(n, theo_pct)

    # ── DFM checks ────────────────────────────────────────────────────────────
    dfm: Dict[str, Any] = {"warnings": [], "errors": []}
    min_dim = _hole_min_dim(n)

    # PERF-001: D >= T
    if sheet_thickness is not None and min_dim < sheet_thickness:
        dfm["warnings"].append(
            f"PERF-001: Hole min dim ({min_dim}mm) < thickness ({sheet_thickness}mm). "
            "Laser cutting may be difficult."
        )

    # PERF-002: pitch > hole span along each axis (matches Example plate edge-gap logic;
    # oblong needs pitch_x > L and pitch_y > W, not merely > min(W,L))
    span_x, span_y = _hole_span_xy_mm(n)
    if n.pitch_x <= span_x or n.pitch_y <= span_y:
        dfm["errors"].append(
            f"PERF-002: Pitch ({n.pitch_x}mm x {n.pitch_y:.3f}mm) <= hole span "
            f"({span_x}mm x {span_y}mm). Holes would OVERLAP -- geometry invalid!"
        )

    # PERF-003: % vide limits
    if theo_pct > 65:
        dfm["errors"].append(
            f"PERF-003: Open area {theo_pct:.1f}% > 65% — structural integrity insufficient. "
            "Increase pitch or reduce hole size."
        )
    elif theo_pct > 50:
        dfm["warnings"].append(
            f"PERF-003: Open area {theo_pct:.1f}% > 50% — plate rigidity may be reduced. "
            "Consider laser-cutting constraints before bending."
        )

    # ── Actual hole count (if sheet dims given) ───────────────────────────────
    hole_count       = 0
    actual_pct       = theo_pct   # fallback = theoretical
    total_hole_area  = 0.0
    sheet_area       = (sheet_length or 0) * (sheet_width or 0)
    margins          = {"x": 0.0, "y": 0.0}

    if sheet_length is not None and sheet_width is not None:
        half_x = span_x / 2.0
        half_y = span_y / 2.0

        hole_count, margin_x, margin_y, n_cols, n_rows = (
            _count_holes_finite_plate_centered_grid(
                sheet_length,
                sheet_width,
                half_x,
                half_y,
                n.pitch_x,
                n.pitch_y,
                n.stagger,
            )
        )

        margins = {"x": round(margin_x, 3), "y": round(margin_y, 3)}

        # PERF-004: margin check
        min_margin = min_dim / 2 + (sheet_thickness or 0)
        if margin_x < min_margin or margin_y < min_margin:
            dfm["warnings"].append(
                f"PERF-004: Margin ({margin_x:.1f}mm / {margin_y:.1f}mm) may be too small "
                f"(recommended ≥ {min_margin:.1f}mm = hole_half_dim + thickness)."
            )

        total_hole_area = hole_count * hole_area
        actual_pct = (
            (total_hole_area / sheet_area * 100) if sheet_area > 0 else theo_pct
        )

        formula = (
            f"{formula} | [Plaque finie — grille centrée comme data/Example/plate] "
            f"% réel = {actual_pct:.2f}% "
            f"({hole_count} trous × {hole_area:.3f} mm² / {sheet_area:.0f} mm²; "
            f"grille {n_cols}×{n_rows} brute avant clip bord)"
        )

    return ForwardResult(
        open_area_pct=round(theo_pct, 2),
        actual_open_area_pct=round(actual_pct, 2),
        hole_count=hole_count,
        hole_area_each=round(hole_area, 3),
        total_hole_area=round(total_hole_area, 3),
        sheet_area=round(sheet_area, 3),
        margins=margins,
        dfm=dfm,
        formula_used=formula,
        notation=n,
    )


# ─────────────────────────────────────────────────────────────────────────────
# REVERSE CALCULATION
# ─────────────────────────────────────────────────────────────────────────────

def infer_pitch_from_pct(
    notation_str: str,
    target_pct: float,
) -> ReverseResult:
    """
    REVERSE MODE A: Given notation (shape only) + target % vide → infer pitch.

    The notation_str must contain the SHAPE part (R<D>, C<S>, LC<W>x<L>)
    and optionally the pitch TYPE letter (U, T, Z) without a value.
    If no pitch type letter found, defaults to U (grid/inline).

    Example:
        infer_pitch_from_pct("R12 T", 51.0)  → pitch ≈ 16.05mm (T-staggered)
        infer_pitch_from_pct("R12",   40.0)  → pitch ≈ 19.80mm (U-grid default)
        infer_pitch_from_pct("C20 U", 25.0)  → pitch ≈ 40.00mm (U-grid)
    """
    s = notation_str.strip()
    n_dummy = parse_notation(s + " U9999")   # parse shape only, dummy pitch

    hole_area = _hole_area(n_dummy)

    # Detect pitch type from string (if any letter present)
    pitch_type = "U"  # default
    if re.search(r'\bT\b', s, re.IGNORECASE):
        pitch_type = "T"
    elif re.search(r'\bZ\b', s, re.IGNORECASE):
        pitch_type = "Z"  # generic staggered — cannot fully solve without ratio

    # % = hole_area / cell_area × 100
    # → cell_area = hole_area / (target_pct/100)
    cell_area_needed = hole_area / (target_pct / 100.0)

    if pitch_type == "U":
        # cell = P × P  → P = sqrt(cell_area)
        pitch = math.sqrt(cell_area_needed)
        formula = f"cell_area = P² | P = sqrt({cell_area_needed:.3f}) = {pitch:.3f}mm"
        note = f"Grid/Inline pitch U = {pitch:.2f}mm gives {target_pct}% open area"

    elif pitch_type == "T":
        # cell = P × (P × sin60°) = P² × 0.866
        # → P = sqrt(cell_area / 0.866)
        pitch = math.sqrt(cell_area_needed / math.sin(math.radians(60)))
        formula = (
            f"cell_area = P² × sin(60°) | "
            f"P = sqrt({cell_area_needed:.3f} / 0.866) = {pitch:.3f}mm"
        )
        note = (
            f"Staggered 60° pitch T = {pitch:.2f}mm "
            f"(pX={pitch:.2f}mm, pY={pitch * math.sin(math.radians(60)):.2f}mm) "
            f"gives {target_pct}% open area"
        )
    else:
        # Z staggered — need to know ratio px/py to solve; assume square cell
        pitch = math.sqrt(cell_area_needed)
        formula = f"Z generic: assume pX=pY=P | P = sqrt({cell_area_needed:.3f}) = {pitch:.3f}mm"
        note = f"Z generic assumed square cell: pX = pY = {pitch:.2f}mm (verify ratio)"

    return ReverseResult(
        target_pct=target_pct,
        inferred_param="pitch",
        inferred_value=round(pitch, 3),
        formula_used=formula,
        note=note,
    )


def infer_hole_size_from_pct(
    notation_str: str,
    target_pct: float,
) -> ReverseResult:
    """
    REVERSE MODE B: Given notation (pitch fully known) + target % vide → infer hole size.

    Supports partial notation where shape has no number yet:
        "R T16"   → infer hole diameter for round + staggered 60° pitch=16
        "C U40"   → infer square side for square + grid pitch=40

    Example:
        infer_hole_size_from_pct("R T16", 51.0)  → D ≈ 12.0mm
        infer_hole_size_from_pct("C U40", 25.0)  → S ≈ 20.0mm
    """
    s = notation_str.strip()

    # Detect shape letter (no number required)
    shape_letter = None
    if re.search(r'\bLR\b', s, re.IGNORECASE):
        shape_letter = "LR"
    elif re.search(r'\bLC\b', s, re.IGNORECASE):
        shape_letter = "LC"
    elif re.search(r'\bR\b', s, re.IGNORECASE):
        shape_letter = "R"
    elif re.search(r'\bC\b', s, re.IGNORECASE):
        shape_letter = "C"

    # Build a parseable notation by injecting dummy shape value
    dummy_shape = {"R": "R9999", "C": "C9999", "LR": "LR9x99", "LC": "LC9x99", None: "R9999"}[shape_letter]
    # Strip bare shape letter (letter only, no digits following) before reparsing
    clean_pitch = re.sub(r'\b(R|C)\b(?!\d)', '', s, flags=re.IGNORECASE).strip()
    try:
        n_pitch = parse_notation(dummy_shape + " " + clean_pitch)
    except Exception:
        n_pitch = parse_notation(dummy_shape + " U16")   # last-resort dummy

    cell_area  = _pitch_cell_area(n_pitch)
    # hole_area = target_pct/100 × cell_area
    hole_area_needed = (target_pct / 100.0) * cell_area

    # Determine shape type from partial notation
    if re.search(r'\bR\b', s, re.IGNORECASE):
        # Round: π·(D/2)² = hole_area → D = 2·sqrt(hole_area/π)
        D = 2 * math.sqrt(hole_area_needed / math.pi)
        formula = f"π·(D/2)² = {hole_area_needed:.3f} → D = {D:.3f}mm"
        note = f"Round hole diameter D = {D:.2f}mm gives {target_pct}% open area"
        return ReverseResult(target_pct=target_pct, inferred_param="hole_diameter",
                             inferred_value=round(D, 3), formula_used=formula, note=note)

    elif re.search(r'\bC\b', s, re.IGNORECASE):
        # Square: S² = hole_area → S = sqrt(hole_area)
        S = math.sqrt(hole_area_needed)
        formula = f"S² = {hole_area_needed:.3f} → S = {S:.3f}mm"
        note = f"Square hole side S = {S:.2f}mm gives {target_pct}% open area"
        return ReverseResult(target_pct=target_pct, inferred_param="square_side",
                             inferred_value=round(S, 3), formula_used=formula, note=note)

    else:
        raise UnderdeterminedError(
            f"Reverse hole-size inference for oblong shape '{shape_letter}' "
            "requires both W (width) and L (total length). "
            "The system has only one equation (area = target_pct/100 × cell_area) "
            "but two unknowns — please fix one dimension so the other can be inferred. "
            "Example: 'LR5x? Z9x24 30%' fixes W=5mm and infers L; "
            "or 'LR?x20 Z9x24 30%' fixes L=20mm and infers W."
        )


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC CONVENIENCE FUNCTION (for chatbot integration)
# ─────────────────────────────────────────────────────────────────────────────

def compute_perforated_sheet(
    notation_str: str,
    sheet_length: Optional[float] = None,
    sheet_width: Optional[float] = None,
    sheet_thickness: Optional[float] = None,
    target_pct: Optional[float] = None,
    reverse_mode: Optional[str] = None,   # "pitch" | "hole_size"
) -> Dict[str, Any]:
    """
    Unified entry point for the chatbot.

    FORWARD (default):
        compute_perforated_sheet("R12 T16", 200, 200, 2)

    REVERSE — infer pitch:
        compute_perforated_sheet("R12 T", target_pct=51.0, reverse_mode="pitch")

    REVERSE — infer hole size:
        compute_perforated_sheet("R T16", target_pct=51.0, reverse_mode="hole_size")

    Returns a plain dict ready for JSON serialization / LLM injection.
    """
    if target_pct is not None and reverse_mode in ("pitch", "hole_size"):
        # ── REVERSE ──────────────────────────────────────────────────────────
        try:
            if reverse_mode == "pitch":
                res = infer_pitch_from_pct(notation_str, target_pct)
            else:
                res = infer_hole_size_from_pct(notation_str, target_pct)
        except UnderdeterminedError as e:
            # Structured error: caller (agent) surfaces a helpful question instead
            # of crashing or silently returning None.
            # Shape has more unknowns than equations (e.g. LR/LC in reverse_D).
            return {
                "mode": "reverse",
                "reverse_mode": reverse_mode,
                "input_notation": notation_str,
                "target_open_area_pct": target_pct,
                "error": "underdetermined",
                "message": str(e),
            }
        # ValueError (parse errors) propagate normally to the agent's outer try/except

        resolved_notation = _resolved_reverse_notation(
            notation_str,
            res.inferred_param,
            res.inferred_value,
        )
        output = {
            "mode": "reverse",
            "input_notation": notation_str,
            "target_open_area_pct": target_pct,
            "inferred_param": res.inferred_param,
            "inferred_value_mm": res.inferred_value,
            "resolved_notation": resolved_notation,
            "formula": res.formula_used,
            "note": res.note,
        }
        if sheet_length and sheet_width:
            try:
                forward = compute_perforated_sheet(
                    resolved_notation,
                    sheet_length=sheet_length,
                    sheet_width=sheet_width,
                    sheet_thickness=sheet_thickness,
                )
                for key in ("sheet", "hole", "pitch", "open_area", "margins", "dfm"):
                    output[key] = forward.get(key)
            except Exception as e:
                output["resolved_geometry_error"] = str(e)
        return output

    else:
        # ── FORWARD ───────────────────────────────────────────────────────────
        res = calculate_open_area(
            notation_str,
            sheet_length=sheet_length,
            sheet_width=sheet_width,
            sheet_thickness=sheet_thickness,
        )
        n = res.notation
        output = {
            "mode": "forward",
            "input_notation": notation_str,
            "sheet": {
                "length_mm": sheet_length,
                "width_mm": sheet_width,
                "thickness_mm": sheet_thickness,
                "area_mm2": res.sheet_area,
            },
            "hole": {
                "shape": n.shape_type,
                "diameter_mm":    n.hole_d,
                "side_mm":        n.hole_s,
                "width_mm":       n.hole_w,
                "total_length_mm": n.hole_l,
                "area_each_mm2":  res.hole_area_each,
            },
            "pitch": {
                "type":    n.pitch_type,
                "pitch_x_mm": n.pitch_x,
                "pitch_y_mm": round(n.pitch_y, 4),
                "stagger_mm": n.stagger,
            },
            "open_area": {
                "theoretical_pct":  res.open_area_pct,
                "actual_pct":       res.actual_open_area_pct if sheet_length else None,
                "hole_count":       res.hole_count if sheet_length else None,
                "total_hole_area_mm2": res.total_hole_area if sheet_length else None,
            },
            "margins": res.margins if sheet_length else None,
            "formula": res.formula_used,
            "dfm": res.dfm,
        }
        return output


# ─────────────────────────────────────────────────────────────────────────────
# QUICK TEST (run directly: python perforated_sheet_calculator.py)
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import json

    scenarios = [
        # ── FORWARD — Round ──────────────────────────────────────────────────
        ("R12 T16",      200, 200, 2,   None, None),
        ("R10 U15",      300, 200, 1.5, None, None),
        # ── FORWARD — Square ─────────────────────────────────────────────────
        ("C20 U40",      500, 300, 3,   None, None),
        ("C12 U16",      200, 200, 2,   None, None),   # square, grid
        ("C20 U25x60",   500, 400, 2,   None, None),   # square, rectangular U grid
        ("C12 T16",      200, 200, 2,   None, None),   # square, staggered
        # ── FORWARD — Oblong LR (rounded/stadium) ────────────────────────────
        ("LR5x20 T12",   300, 150, 1.5, None, None),
        ("LR8x30 U20",   400, 200, 2,   None, None),
        # ── FORWARD — Oblong LC (rectangular slot) ───────────────────────────
        ("LC5x20 Z9x24", 400, 200, 2,   None, None),
        ("LC8x30 U20",   400, 200, 2,   None, None),
        # ── REVERSE — pitch ──────────────────────────────────────────────────
        ("R12 T",        None, None, None, 51.0, "pitch"),
        ("C20 U",        None, None, None, 25.0, "pitch"),
        ("C12 T",        None, None, None, 28.0, "pitch"),
        # ── REVERSE — hole size ──────────────────────────────────────────────
        ("R T16",        None, None, None, 51.0, "hole_size"),
        ("C U40",        None, None, None, 25.0, "hole_size"),
    ]

    for (notation, L, W, T, pct, rev) in scenarios:
        result = compute_perforated_sheet(
            notation,
            sheet_length=L, sheet_width=W, sheet_thickness=T,
            target_pct=pct, reverse_mode=rev,
        )
        print(f"\n{'='*70}")
        print(f"  {notation}  |  L={L} W={W} T={T}  |  target={pct}%  rev={rev}")
        print(json.dumps(result, indent=2, ensure_ascii=False))
