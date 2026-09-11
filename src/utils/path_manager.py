"""Output locations: outputs/<kind>/<YYYY-MM-DD>/<file>."""
import datetime
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()

OUTPUT_DIR = PROJECT_ROOT / "outputs"
CAD_OUTPUT_DIR = OUTPUT_DIR / "cad"
OBJ_OUTPUT_DIR = OUTPUT_DIR / "obj"
PDF_OUTPUT_DIR = OUTPUT_DIR / "pdf"
CODE_OUTPUT_DIR = OUTPUT_DIR / "code"

for _d in (CAD_OUTPUT_DIR, OBJ_OUTPUT_DIR, PDF_OUTPUT_DIR, CODE_OUTPUT_DIR):
    _d.mkdir(parents=True, exist_ok=True)


def get_date_directory(base_dir: Path) -> Path:
    date_dir = base_dir / datetime.datetime.now().strftime("%Y-%m-%d")
    date_dir.mkdir(parents=True, exist_ok=True)
    return date_dir


def sanitize_filename(name: str) -> str:
    """ASCII letters, digits, '_' and '-' only; spaces become underscores."""
    sanitized = re.sub(r"[^\w\s-]", "", str(name or "")).strip()
    sanitized = "".join(c for c in sanitized if ord(c) < 128).replace(" ", "_")
    return sanitized or "unnamed"


def get_unique_filepath(filepath: Path) -> Path:
    """Append _1, _2, ... until the path is free."""
    if not filepath.exists():
        return filepath
    counter = 1
    while True:
        candidate = filepath.with_name(f"{filepath.stem}_{counter}{filepath.suffix}")
        if not candidate.exists():
            return candidate
        counter += 1
