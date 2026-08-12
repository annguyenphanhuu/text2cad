"""
Path Manager Module

This module provides utility functions for managing file paths and directories
in the dfm-ShapeChatBot project. It centralizes path management to ensure
consistency across the application.
"""

import os
import re
import hashlib
import datetime
from pathlib import Path
from typing import Optional, Union, List, Dict, Tuple

# Get the project root directory
PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()

# Define output directories
OUTPUT_DIR = PROJECT_ROOT / "outputs"
CAD_OUTPUT_DIR = OUTPUT_DIR / "cad"

OBJ_OUTPUT_DIR = OUTPUT_DIR / "obj"
PDF_OUTPUT_DIR = OUTPUT_DIR / "pdf"
CODE_OUTPUT_DIR = OUTPUT_DIR / "code"
METADATA_OUTPUT_DIR = OUTPUT_DIR / "metadata"

# Define log directories
LOG_DIR = PROJECT_ROOT / "logs"
API_LOG_DIR = LOG_DIR / "api"
APP_LOG_DIR = LOG_DIR / "app"
ERROR_LOG_DIR = LOG_DIR / "error"

# Define data directories
DATA_DIR = PROJECT_ROOT / "data"
EXAMPLES_DIR = DATA_DIR / "examples"
GUIDE_DIR = DATA_DIR / "guide"
INDEX_DIR = DATA_DIR / "index"

# Define other directories
TESTS_DIR = PROJECT_ROOT / "tests"
TOOLS_DIR = PROJECT_ROOT / "tools"
CONFIG_DIR = PROJECT_ROOT / "config"
STATIC_DIR = PROJECT_ROOT / "static"
TEMPLATES_DIR = PROJECT_ROOT / "templates"

# Ensure all directories exist
def ensure_directories_exist():
    """Create all required directories if they don't exist."""
    directories = [
        OUTPUT_DIR, CAD_OUTPUT_DIR, OBJ_OUTPUT_DIR, PDF_OUTPUT_DIR,
        CODE_OUTPUT_DIR, METADATA_OUTPUT_DIR, LOG_DIR, API_LOG_DIR,
        APP_LOG_DIR, ERROR_LOG_DIR, DATA_DIR, EXAMPLES_DIR, GUIDE_DIR, 
        INDEX_DIR, TESTS_DIR, TOOLS_DIR
    ]
    
    for directory in directories:
        directory.mkdir(parents=True, exist_ok=True)

# Call this function to ensure directories exist
ensure_directories_exist()

def get_date_directory(base_dir: Path, create: bool = True) -> Path:
    """
    Get a directory for today's date under the specified base directory.
    
    Args:
        base_dir: The base directory
        create: Whether to create the directory if it doesn't exist
        
    Returns:
        Path to the date directory
    """
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    date_dir = base_dir / today
    
    if create and not date_dir.exists():
        date_dir.mkdir(parents=True, exist_ok=True)
        
    return date_dir

def sanitize_filename(name: str) -> str:
    """
    Sanitize a string to be used as a filename.
    
    Args:
        name: The string to sanitize
        
    Returns:
        A sanitized string suitable for use as a filename
    """
    # Remove non-alphanumeric, space, or hyphen characters
    sanitized = re.sub(r'[^\w\s-]', '', name).strip()
    
    # Keep only ASCII characters
    sanitized = ''.join(c for c in sanitized if ord(c) < 128)
    
    # Replace spaces with underscores
    sanitized = sanitized.replace(' ', '_')
    
    # Ensure the filename is not empty
    if not sanitized:
        sanitized = "unnamed"
        
    return sanitized

def generate_hash(data: Union[str, Dict, List]) -> str:
    """
    Generate a short hash from the provided data.
    
    Args:
        data: The data to hash (string, dict, or list)
        
    Returns:
        A short hash string (first 6 characters of SHA-256 hash)
    """
    if isinstance(data, (dict, list)):
        data_str = str(data)
    else:
        data_str = str(data)
        
    hash_obj = hashlib.sha256(data_str.encode())
    return hash_obj.hexdigest()[:6]

def generate_filename(
    shape_type: str,
    dimensions: Union[str, Dict[str, float]],
    design_requirements: Optional[Dict] = None,
    extension: str = "step"
) -> str:
    """
    Generate a filename based on the shape type, dimensions, and design requirements.
    
    Args:
        shape_type: The type of shape (e.g., "box", "cylinder")
        dimensions: Key dimensions as a string (e.g., "100x50x25") or dict
        design_requirements: Optional design requirements for hash generation
        extension: File extension (without the dot)
        
    Returns:
        A filename string
    """
    # Sanitize shape type
    shape_type = sanitize_filename(shape_type)
    
    # Format dimensions if provided as a dictionary
    if isinstance(dimensions, dict):
        dim_parts = []
        for key, value in dimensions.items():
            if key.lower() in ["length", "width", "height", "radius", "diameter"]:
                dim_parts.append(f"{value}")
        dimensions = "x".join(dim_parts) if dim_parts else "unknown"
    
    # Sanitize dimensions
    dimensions = sanitize_filename(str(dimensions))
    
    # Generate timestamp
    timestamp = datetime.datetime.now().strftime("%Y%m%d")
    
    # Generate hash if design requirements are provided
    hash_str = ""
    if design_requirements:
        hash_str = f"_{generate_hash(design_requirements)}"
    
    # Combine all parts
    filename = f"{shape_type}_{dimensions}_{timestamp}{hash_str}.{extension}"
    
    return filename

def get_output_path(
    shape_type: str,
    dimensions: Union[str, Dict[str, float]],
    output_type: str = "step",
    design_requirements: Optional[Dict] = None
) -> Path:
    """
    Get the full path for an output file.
    
    Args:
        shape_type: The type of shape (e.g., "box", "cylinder")
        dimensions: Key dimensions as a string (e.g., "100x50x25") or dict
        output_type: Type of output file ("step", "obj", "py", "json")
        design_requirements: Optional design requirements for hash generation
        
    Returns:
        Full path to the output file
    """
    # Map output type to directory and extension
    output_map = {
        "step": (CAD_OUTPUT_DIR, "step"),
        "obj": (OBJ_OUTPUT_DIR, "obj"),

        "py": (CODE_OUTPUT_DIR, "py"),
        "json": (METADATA_OUTPUT_DIR, "json"),
        "code": (CODE_OUTPUT_DIR, "py"),
        "metadata": (METADATA_OUTPUT_DIR, "json"),
        "cad": (CAD_OUTPUT_DIR, "step")
    }
    
    # Get directory and extension
    base_dir, extension = output_map.get(output_type.lower(), (OUTPUT_DIR, output_type))
    
    # Get date directory
    date_dir = get_date_directory(base_dir)
    
    # Generate filename
    filename = generate_filename(shape_type, dimensions, design_requirements, extension)
    
    # Return full path
    return date_dir / filename

def get_unique_filepath(filepath: Path) -> Path:
    """
    Ensure a filepath is unique by adding a counter if necessary.
    
    Args:
        filepath: The original filepath
        
    Returns:
        A unique filepath
    """
    if not filepath.exists():
        return filepath
    
    # Split path into directory, base name, and extension
    directory = filepath.parent
    basename = filepath.stem
    extension = filepath.suffix
    
    # Try adding incremental counter
    counter = 1
    while True:
        new_filepath = directory / f"{basename}_{counter}{extension}"
        if not new_filepath.exists():
            return new_filepath
        counter += 1
