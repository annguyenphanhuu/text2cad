"""Save the script that is sent to the FreeCAD worker under outputs/code/<date>/."""
import datetime
import logging
from pathlib import Path

from src.utils.path_manager import CODE_OUTPUT_DIR, get_date_directory, get_unique_filepath, sanitize_filename

logger = logging.getLogger(__name__)


def save_code_file(code: str, shape_type: str, title: str) -> Path:
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    filepath = get_unique_filepath(get_date_directory(CODE_OUTPUT_DIR) / f"{sanitize_filename(shape_type)}_{sanitize_filename(title)}_{stamp}.py")
    filepath.write_text(code, encoding="utf-8")
    logger.debug(f"Saved code file to: {filepath}")
    return filepath
