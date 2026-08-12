"""
File Manager Module

This module provides utility functions for managing files in the dfm-ShapeChatBot project.
It handles file operations like saving, loading, and converting files.
"""

import os
import json
import shutil
import logging
import subprocess
from pathlib import Path
from typing import Optional, Union, List, Dict, Any, Tuple

from src.utils.path_manager import (
    get_output_path,
    get_unique_filepath,
    sanitize_filename,
    PROJECT_ROOT
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


def clean_existing_file(filepath: Union[str, Path]) -> bool:
    """
    Clean an existing Python file by removing problematic Unicode characters.

    Args:
        filepath: Path to the Python file to clean

    Returns:
        bool: True if file was modified, False if no changes were needed

    Raises:
        FileNotFoundError: If the file doesn't exist
        IOError: If there's an error reading/writing the file
    """
    filepath = Path(filepath)

    if not filepath.exists():
        raise FileNotFoundError(f"File not found: {filepath}")

    # Read the original content
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            original_content = f.read()
    except UnicodeDecodeError:
        # Try with different encodings if UTF-8 fails
        for encoding in ['latin-1', 'cp1252', 'iso-8859-1']:
            try:
                with open(filepath, 'r', encoding=encoding) as f:
                    original_content = f.read()
                break
            except UnicodeDecodeError:
                continue
        else:
            raise IOError(f"Could not read file {filepath} with any supported encoding")

    # Clean the content with aggressive sanitization for FreeCAD
    cleaned_content = sanitize_for_freecad(original_content)

    # Check if any changes were made
    if cleaned_content == original_content:
        return False

    # Write the cleaned content back
    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(cleaned_content)
        return True
    except Exception as e:
        raise IOError(f"Error writing cleaned content to {filepath}: {e}")


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

def save_threaded_metadata_file(
    code_path: Path,
    shape_type: str,
    dimensions: Union[str, Dict[str, float]],
    design_requirements: Optional[Dict] = None,
    cost_tracker = None
) -> Optional[Path]:
    """
    Analyze FreeCAD code and save feature metadata (sync wrapper).
    
    This is a synchronous wrapper around save_threaded_metadata_file_async.
    For better performance in async contexts, use the async version directly.
    """
    import asyncio
    
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Already in async context - this shouldn't happen
            # but handle it gracefully
            import nest_asyncio
            nest_asyncio.apply()
            return loop.run_until_complete(
                save_threaded_metadata_file_async(
                    code_path, shape_type, dimensions, design_requirements, cost_tracker
                )
            )
        else:
            return loop.run_until_complete(
                save_threaded_metadata_file_async(
                    code_path, shape_type, dimensions, design_requirements, cost_tracker
                )
            )
    except RuntimeError:
        return asyncio.run(
            save_threaded_metadata_file_async(
                code_path, shape_type, dimensions, design_requirements, cost_tracker
            )
        )

async def execute_freecad_script(script_path: Path) -> Tuple[bool, str]:
    """
    Execute a FreeCAD script asynchronously with automatic Unicode character cleaning.

    This function automatically cleans problematic Unicode characters from the script
    before execution and runs the FreeCAD process in a separate thread to avoid
    blocking the asyncio event loop.

    Args:
        script_path: Path to the script

    Returns:
        Tuple of (success, message)
    """
    import asyncio

    try:
        # Clean the script file before execution to prevent encoding issues
        try:
            was_cleaned = clean_existing_file(script_path)
            if was_cleaned:
                logger.info(f"Cleaned Unicode characters from script: {script_path}")
        except Exception as clean_error:
            logger.warning(f"Could not clean script file {script_path}: {clean_error}")
            # Continue with execution anyway

        def run_subprocess():
            # Execute FreeCAD command with explicit UTF-8 encoding
            return subprocess.run(
                ["freecadcmd", str(script_path)],
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',  # Replace problematic characters instead of failing
                check=False
            )

        # Run the blocking subprocess in a separate thread
        result = await asyncio.to_thread(run_subprocess)
        print("SCRIPT PATH:", script_path)

        # Check if execution was successful
        # FreeCAD may return 0 even with errors, so check for "Exception" in output
        has_exception = ("Exception" in result.stdout or
                        "Exception" in result.stderr or
                        "Error" in result.stderr or
                        "Traceback" in result.stdout or
                        "Traceback" in result.stderr)

        if result.returncode == 0 and not has_exception:
            logger.info(f"Successfully executed FreeCAD script: {script_path}")
            return True, result.stdout
        else:
            error_message = result.stderr if result.stderr else result.stdout
            logger.error(f"Error executing FreeCAD script: {error_message}")
            return False, error_message
    except Exception as e:
        logger.error(f"Exception executing FreeCAD script: {e}")
        return False, str(e)



def find_output_files(
    shape_type: str,
    dimensions: Union[str, Dict[str, float]],
    output_types: List[str] = ["step", "obj", "py", "json"]
) -> Dict[str, Optional[Path]]:
    """
    Find output files for a specific shape.
    
    Args:
        shape_type: The type of shape
        dimensions: Key dimensions
        output_types: Types of output files to find
        
    Returns:
        Dictionary mapping output types to file paths
    """
    result = {}
    
    for output_type in output_types:
        # Get base directory for this output type
        base_dir = get_output_path(shape_type, dimensions, output_type).parent.parent
        
        # Get sanitized shape type and dimensions for pattern matching
        sanitized_shape = sanitize_filename(shape_type)
        sanitized_dims = sanitize_filename(str(dimensions))
        
        # Pattern to match
        pattern = f"{sanitized_shape}_{sanitized_dims}_*.{output_type}"
        
        # Search for matching files in all date directories
        matching_files = []
        for date_dir in base_dir.iterdir():
            if date_dir.is_dir():
                matching_files.extend(date_dir.glob(pattern))
        
        # Sort by modification time (newest first)
        matching_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        
        # Get the newest file if any
        result[output_type] = matching_files[0] if matching_files else None
    
    return result

def move_legacy_outputs():
    """
    Move legacy output files from the old location to the new structure.
    """
    # Old output directory
    old_output_dir = PROJECT_ROOT.parent / "cad_outputs_generated"
    
    if not old_output_dir.exists():
        logger.info("No legacy output directory found.")
        return
    
    logger.info(f"Moving legacy outputs from {old_output_dir}")
    
    # Create a directory for today
    today = get_output_path("legacy", "migration", "step").parent
    
    # Process each file in the old directory
    for file_path in old_output_dir.iterdir():
        if not file_path.is_file():
            continue
        
        # Determine file type from extension
        extension = file_path.suffix.lower()[1:]  # Remove the dot
        
        if extension in ["py"]:
            dest_dir = today.parent.parent / "code" / today.name
        elif extension in ["json"]:
            dest_dir = today.parent.parent / "metadata" / today.name
        elif extension in ["step"]:
            dest_dir = today.parent.parent / "cad" / today.name
        elif extension in ["obj"]:
            dest_dir = today.parent.parent / "obj" / today.name

        else:
            # Skip unknown file types
            logger.warning(f"Skipping unknown file type: {file_path}")
            continue
        
        # Ensure destination directory exists
        dest_dir.mkdir(parents=True, exist_ok=True)
        
        # Destination path
        dest_path = dest_dir / file_path.name
        
        # Ensure destination path is unique
        dest_path = get_unique_filepath(dest_path)
        
        try:
            # Copy the file
            shutil.copy2(file_path, dest_path)
            logger.info(f"Copied {file_path} to {dest_path}")
        except Exception as e:
            logger.error(f"Error copying {file_path}: {e}")
    
    logger.info("Legacy output migration completed.")
