"""
Perforated Sheet FreeCAD Generation Time Estimator
===================================================
Linear model: T(seconds) = base + rate × n_holes

Fitted from benchmark data (plate 200×200, 500×500, 1000×1000, thickness=2mm):

  Shape R (makeCylinder):
    200×200 →  168 holes →  47.0s
    500×500 → 1116 holes → 206.0s
    1000×1000 → 4464 holes → 923.6s
    Fit: T = 12.7 + 0.204 × n   (R² ≈ 0.9997)

  Shape C (makeBox square):
    200×200 →   25 holes →  35.7s
    500×500 →  169 holes →  43.9s
    1000×1000 → 625 holes →  95.4s
    Fit: T = 33.2 + 0.100 × n   (R² ≈ 0.997)

  Shape LR: same model as R  (makeOblong ≈ makeCylinder cost)
  Shape LC: same model as C  (makeBox rect = makeBox square cost)
"""

# ── Timing models per shape letter ──────────────────────────────────────────
# Format: shape_letter -> (base_seconds, rate_seconds_per_hole)
MODELS: dict[str, tuple[float, float]] = {
    "R":  (12.7, 0.204),   # ✅ Measured
    "C":  (33.2, 0.100),   # ✅ Measured
    "LR": (12.7, 0.204),   # ≡ R  (makeOblong ≈ makeCylinder)
    "LC": (33.2, 0.100),   # ≡ C  (makeBox rect = makeBox square)
}

# Fallback for unknown shapes
_DEFAULT_MODEL = (20.0, 0.180)


def estimate_freecad_seconds(n_holes: int, shape_letter: str) -> int:
    """
    Estimate FreeCAD boolean cut time in seconds for a perforated sheet.

    This is the numeric counterpart of estimate_freecad_time(), used by SSE
    progress events where the frontend needs a countdown value.
    """
    if n_holes <= 0:
        return 30

    base, rate = MODELS.get(shape_letter.upper(), _DEFAULT_MODEL)
    seconds = base + rate * n_holes

    # Keep the same buffer policy as estimate_freecad_time().
    if n_holes <= 300:
        seconds += 20
    elif n_holes <= 900:
        seconds += 90
    elif n_holes <= 2000:
        seconds += 180
    else:
        seconds += 360

    return max(1, int(round(seconds)))


def format_duration(seconds: int) -> str:
    """Format seconds as a compact human-readable duration."""
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"~{seconds} sec"
    if seconds < 3600:
        mins = seconds // 60
        secs = seconds % 60
        return f"~{mins} min {secs:02d} sec"
    hrs = seconds // 3600
    mins = (seconds % 3600) // 60
    return f"~{hrs}h {mins:02d} min"


def estimate_freecad_time(n_holes: int, shape_letter: str, lang: str = "fr") -> str:
    """
    Estimate FreeCAD boolean cut time for a perforated sheet.

    Args:
        n_holes:      Number of holes (from perf_calc_result['open_area']['hole_count'])
        shape_letter: Shape type letter — 'R', 'C', 'LR', or 'LC'
        lang:         Kept for backward compatibility; no language-specific suffix is added.

    Returns:
        Human-readable string, e.g. "~2 min 15 sec"
    """
    return format_duration(estimate_freecad_seconds(n_holes, shape_letter))


def extract_shape_letter(shape_notation: str) -> str:
    """
    Extract shape letter from notation string.
    Examples:
      "R12 T16"    → "R"
      "C20 U40"    → "C"
      "LR5x20 Z9x24" → "LR"
      "LC5x20 Z9x24" → "LC"
    """
    import re
    if not shape_notation:
        return "R"
    # Try LR/LC first (2-letter prefix) then R/C (1-letter)
    m = re.match(r"^(LR|LC|R|C)", shape_notation.strip(), re.IGNORECASE)
    return m.group(1).upper() if m else "R"
