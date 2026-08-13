"""
File Manager Module

This module provides utility functions for managing files in the dfm-ShapeChatBot project.
It handles file operations like saving, loading, and converting files.
"""

import os
import json
import logging
from pathlib import Path
from typing import Optional, Union, Dict

from src.utils.path_manager import (
    get_output_path,
    get_unique_filepath,
)

def fix_unicode_chars(content: str) -> str:
    """
    Replace problematic Unicode characters with ASCII equivalents for FreeCAD compatibility.

    This function handles various Unicode characters that can cause encoding issues
    when running FreeCAD scripts on Windows systems with 'charmap' codec.

    Args:
        content: String content that may contain Unicode characters

    Returns:
        String with Unicode characters replaced by ASCII equivalents
    """
    # Dictionary of Unicode replacements
    replacements = {
        # Emoji and symbols that commonly cause issues
        '✅': '[SUCCESS]',   # check mark -> [SUCCESS]
        '❌': '[ERROR]',     # cross mark -> [ERROR]
        '⚠️': '[WARNING]',   # warning sign -> [WARNING]
        '⚠': '[WARNING]',    # warning sign (without variation selector) -> [WARNING]
        '🔧': '[TOOL]',      # wrench -> [TOOL]
        '📁': '[FOLDER]',    # folder -> [FOLDER]
        '📄': '[FILE]',      # document -> [FILE]
        '💾': '[SAVE]',      # floppy disk -> [SAVE]
        '🔍': '[SEARCH]',    # magnifying glass -> [SEARCH]
        '⭐': '[STAR]',      # star -> [STAR]
        '🎯': '[TARGET]',    # target -> [TARGET]
        '🚀': '[LAUNCH]',    # rocket -> [LAUNCH]
        '📦': '[BOX]',       # package -> [BOX]
        '💡': '[IDEA]',      # light bulb -> [IDEA]
        '🔥': '[FIRE]',      # fire -> [FIRE]
        '⚡': '[LIGHTNING]', # lightning -> [LIGHTNING]
        '🎨': '[ART]',       # artist palette -> [ART]
        '🔨': '[HAMMER]',    # hammer -> [HAMMER]
        '⚙️': '[GEAR]',      # gear -> [GEAR]
        '⚙': '[GEAR]',       # gear (without variation selector) -> [GEAR]

        # Mathematical and technical symbols
        '×': 'x',           # multiplication sign -> x
        '→': '->',          # right arrow -> ->
        '←': '<-',          # left arrow -> <-
        '↑': '^',           # up arrow -> ^
        '↓': 'v',           # down arrow -> v
        '–': '-',           # en dash -> hyphen
        '—': '--',          # em dash -> double hyphen
        '≥': '>=',          # greater than or equal -> >=
        '≤': '<=',          # less than or equal -> <=
        '≠': '!=',          # not equal -> !=
        '≈': '~=',          # approximately equal -> ~=
        'Ø': 'D',           # diameter symbol -> D
        '±': '+/-',         # plus-minus -> +/-
        '°': 'deg',         # degree symbol -> deg
        'µ': 'u',           # micro -> u
        'π': 'pi',          # pi -> pi
        '²': '^2',          # superscript 2 -> ^2
        '³': '^3',          # superscript 3 -> ^3
        '½': '1/2',         # one half -> 1/2
        '¼': '1/4',         # one quarter -> 1/4
        '¾': '3/4',         # three quarters -> 3/4

        # Quotation marks and punctuation
        '"': '"',           # left double quotation mark -> "
        '"': '"',           # right double quotation mark -> "
        ''': "'",           # left single quotation mark -> '
        ''': "'",           # right single quotation mark -> '
        '…': '...',         # horizontal ellipsis -> ...

        # Currency and other symbols
        '€': 'EUR',         # euro sign -> EUR
        '£': 'GBP',         # pound sign -> GBP
        '¥': 'JPY',         # yen sign -> JPY
        '©': '(c)',         # copyright -> (c)
        '®': '(R)',         # registered -> (R)
        '™': '(TM)',        # trademark -> (TM)
    }

    # Apply replacements
    for unicode_char, ascii_replacement in replacements.items():
        content = content.replace(unicode_char, ascii_replacement)

    # Additional fallback: remove any remaining non-ASCII characters
    # This is a more aggressive approach for characters not in our dictionary
    try:
        # Try to encode as ASCII, replace problematic characters
        content = content.encode('ascii', errors='replace').decode('ascii')
    except Exception:
        # If that fails, remove all non-ASCII characters
        content = ''.join(char for char in content if ord(char) < 128)

    return content


def sanitize_for_freecad(content: str) -> str:
    """
    Sanitize content specifically for FreeCAD execution to prevent charmap codec errors.

    This is a more aggressive sanitization that ensures compatibility with Windows charmap codec.
    """
    # First apply standard Unicode fixes
    content = fix_unicode_chars(content)

    # Then ensure all characters are ASCII-compatible
    try:
        # Test if content can be encoded with charmap (Windows default)
        content.encode('charmap')
        return content
    except UnicodeEncodeError:
        # If charmap encoding fails, convert to ASCII
        return content.encode('ascii', errors='replace').decode('ascii')


# Configure logging
logger = logging.getLogger(__name__)

def save_code_file(
    code: str,
    shape_type: str,
    dimensions: Union[str, Dict[str, float]],
    design_requirements: Optional[Dict] = None
) -> Path:
    """
    Save generated Python code to a file with Unicode character fixes for FreeCAD compatibility.

    Args:
        code: The Python code to save
        shape_type: The type of shape
        dimensions: Key dimensions
        design_requirements: Optional design requirements

    Returns:
        Path to the saved file
    """
    # Get output path
    filepath = get_output_path(shape_type, dimensions, "py", design_requirements)

    # Ensure filepath is unique
    filepath = get_unique_filepath(filepath)

    # Fix Unicode characters for FreeCAD compatibility with aggressive sanitization
    fixed_code = sanitize_for_freecad(code)

    # Log if any Unicode characters were replaced
    if fixed_code != code:
        logger.info(f"Sanitized Unicode characters in generated code for FreeCAD compatibility")

    # Save the file
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(fixed_code)
        logger.info(f"Saved code file to: {filepath}")
        return filepath
    except Exception as e:
        logger.error(f"Error saving code file: {e}")
        raise

def save_metadata_file(
    metadata: Dict,
    shape_type: str,
    dimensions: Union[str, Dict[str, float]],
    design_requirements: Optional[Dict] = None
) -> Path:
    """
    Save metadata to a JSON file.
    
    Args:
        metadata: The metadata to save
        shape_type: The type of shape
        dimensions: Key dimensions
        design_requirements: Optional design requirements
        
    Returns:
        Path to the saved file
    """
    # Get output path
    filepath = get_output_path(shape_type, dimensions, "json", design_requirements)
    
    # Ensure filepath is unique
    filepath = get_unique_filepath(filepath)
    
    # Save the file
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)
        logger.info(f"Saved metadata file to: {filepath}")
        return filepath
    except Exception as e:
        logger.error(f"Error saving metadata file: {e}")
        raise

async def save_threaded_metadata_file_async(
    code_path: Path,
    shape_type: str,
    dimensions: Union[str, Dict[str, float]],
    design_requirements: Optional[Dict] = None,
    cost_tracker = None
) -> Optional[Path]:
    """
    Analyze FreeCAD code and save feature metadata (async version).
    
    This function uses ThreadedMetadataAnalyzer to detect supported features
    from code comments and patterns, then saves the metadata in JSON format
    in the outputs/metadata/ folder.
    
    Args:
        code_path: Path to the generated FreeCAD code file
        shape_type: The type of shape (e.g., "plate", "box")
        dimensions: Key dimensions
        design_requirements: Optional design requirements
        cost_tracker: Optional CostTracker for tracking OpenAI costs
        
    Returns:
        Path to the saved metadata file, or None if no supported features found
        
    Example:
        code_path = Path("outputs/code/2025-12-25/plate_20251225_161215.py")
        metadata_path = await save_threaded_metadata_file_async(code_path, "plate", "30x25x4")
        # Returns: outputs/metadata/2025-12-25/plate_20251225_161215.json
    """
    if shape_type and 'perforated' in shape_type.lower():
        # Perforated Sheet box-hole metadata is not generated at all: the LLM pass
        # scales with hole count (confirmed to take 300-600+s and cause request
        # timeouts on dense sheets), and code-text parsing (regex) is not a safe
        # substitute either since the mandatory FACE+WIRE+EXTRUDE codegen path
        # (data/Info/Perforated_Sheet/info.json, PS_FACE_WIRE_EXTRUDE) has no
        # `.cut()` call for the regex analyzer to key off. Skipping entirely here
        # means neither path ever runs, so this step cannot time out for this shape.
        logger.info("[THREADED] Skipping metadata analysis for Perforated Sheet — not generated for this shape")
        return None

    try:
        from src.core.threaded_metadata_analyzer import ThreadedMetadataAnalyzer

        logger.debug(f"Analyzing code for threaded holes: {code_path}")

        # Create analyzer with cost tracking
        analyzer = ThreadedMetadataAnalyzer(cost_tracker=cost_tracker)
        
        # Generate metadata path in outputs/metadata/ folder
        # Extract timestamp from code filename: plate_20251225_161215.py -> 20251225_161215
        code_stem = code_path.stem  # "plate_20251225_161215"
        
        # Build metadata path: outputs/metadata/2025-12-25/plate_20251225_161215.json
        metadata_output_path = get_output_path(shape_type, dimensions, "json", design_requirements)
        
        # Use same filename as code but with .json extension
        metadata_path = metadata_output_path.parent / f"{code_stem}.json"
        
        # Ensure unique filepath
        metadata_path = get_unique_filepath(metadata_path)
        
        # Generate metadata file (async)
        await analyzer.generate_metadata_file_async(str(code_path), str(metadata_path), shape_type=shape_type)
        
        # Check if any supported features were found
        with open(metadata_path, 'r', encoding='utf-8') as f:
            metadata = json.load(f)
        
        threaded_count = len(metadata.get('threaded_holes', []))
        oblong_count = len(metadata.get('oblongs', []))
        bending_count = len(metadata.get('bending_features', []))
        countersink_count = len(metadata.get('countersinks', []))
        box_hole_count = len(metadata.get('box_holes', []))
        analysis_method = metadata.get('analysis_method', 'unknown')
        
        if threaded_count > 0 or oblong_count > 0 or bending_count > 0 or countersink_count > 0 or box_hole_count > 0:
            logger.info(
                f"💾 Saved feature metadata: {threaded_count} threaded hole(s), {oblong_count} oblong(s), {bending_count} bending feature(s), {countersink_count} countersink(s) | "
                f"{box_hole_count} box hole(s) | method={analysis_method} | {metadata_path}"
            )
            return metadata_path
        else:
            # No features found, remove empty metadata file
            if metadata_path.exists():
                metadata_path.unlink()
            logger.debug("No features detected, metadata not saved")
            return None
            
    except ImportError:
        logger.warning("ThreadedMetadataAnalyzer not available, skipping threaded metadata generation")
        return None
    except Exception as e:
        logger.warning(f"Failed to generate threaded metadata: {e}")
        return None


