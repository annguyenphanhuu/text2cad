"""
File finder utilities for CAD outputs.
This module provides efficient methods to find recently created STEP and OBJ files.
"""

import time
import re
from pathlib import Path
from typing import Optional, List
from src.utils.path_manager import PROJECT_ROOT


def sanitize_title(title: str) -> str:
    """Sanitize title for file naming."""
    if not title:
        return ""
    
    sanitized = re.sub(r'[^\w\s-]', '', title).strip()
    sanitized = ''.join(c for c in sanitized if ord(c) < 128)
    sanitized = sanitized.replace(' ', '_')
    return sanitized


def find_step_file(design_requirements=None, time_window: int = 120) -> Optional[Path]:
    """
    Find the most recently created STEP file using improved logic.
    
    Args:
        design_requirements: Design requirements object with title
        time_window: Time window in seconds to consider files as "recent"
        
    Returns:
        Path to the found STEP file or None
    """
    cad_outputs_dir = PROJECT_ROOT / "outputs" / "code" / "cad_outputs_generated"
    
    if not cad_outputs_dir.exists():
        print(f"[WARNING] CAD outputs directory not found: {cad_outputs_dir}")
        return None
    
    # Strategy 1: Find STEP file matching the design title
    if design_requirements and hasattr(design_requirements, 'title') and design_requirements.title:
        sanitized_title = sanitize_title(design_requirements.title)
        
        if sanitized_title:
            matching_files = list(cad_outputs_dir.glob(f"{sanitized_title}*.step"))
            if matching_files:
                # Get the most recently modified file
                latest_file = max(matching_files, key=lambda p: p.stat().st_mtime)
                print(f"[DEBUG] Found STEP file matching title '{sanitized_title}': {latest_file}")
                return latest_file
    
    # Strategy 2: Find the most recently created STEP file (within time window)
    recent_time = time.time() - time_window
    recent_files = []
    
    for step_file in cad_outputs_dir.glob("*.step"):
        if step_file.stat().st_mtime > recent_time:
            recent_files.append(step_file)
    
    if recent_files:
        latest_file = max(recent_files, key=lambda p: p.stat().st_mtime)
        print(f"[DEBUG] Found recently created STEP file: {latest_file}")
        return latest_file
    
    # Strategy 3: Fallback to the newest STEP file in the directory
    all_step_files = list(cad_outputs_dir.glob("*.step"))
    if all_step_files:
        latest_file = max(all_step_files, key=lambda p: p.stat().st_mtime)
        print(f"[DEBUG] Fallback: Using newest STEP file: {latest_file}")
        return latest_file
    
    print(f"[WARNING] No STEP files found in {cad_outputs_dir}")
    return None


def find_obj_files(time_window: int = 300) -> List[Path]:
    """
    Find recently created OBJ files.
    
    Args:
        time_window: Time window in seconds to consider files as "recent"
        
    Returns:
        List of recently created OBJ files, sorted by modification time (newest first)
    """
    recent_time = time.time() - time_window
    obj_files = []
    
    # Search in project directory
    for obj_file in PROJECT_ROOT.rglob("*.obj"):
        if obj_file.stat().st_mtime > recent_time:
            obj_files.append(obj_file)
    
    # Sort by modification time, newest first
    obj_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    
    if obj_files:
        print(f"[DEBUG] Found {len(obj_files)} recent OBJ files")
    else:
        print(f"[WARNING] No recent OBJ files found")
    
    return obj_files


def get_cad_outputs_dir() -> Path:
    """Get the CAD outputs directory."""
    return PROJECT_ROOT / "outputs" / "code" / "cad_outputs_generated"


