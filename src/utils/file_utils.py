"""
File Utilities for Robust JSON I/O Operations
==============================================
This module provides utility functions for safe file operations with:
- Retry logic for handling file locks and race conditions
- Atomic writes to prevent data corruption
- File size monitoring

Author: AI Assistant
Date: 2026-01-27
"""

import json
import os
import time
import tempfile
import shutil
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
import logging

logger = logging.getLogger(__name__)


def get_file_size_mb(file_path: str) -> float:
    """
    Get file size in megabytes.
    
    Args:
        file_path: Path to the file
        
    Returns:
        File size in MB, or 0 if file doesn't exist
    """
    try:
        if os.path.exists(file_path):
            size_bytes = os.path.getsize(file_path)
            return size_bytes / (1024 * 1024)
        return 0.0
    except OSError as e:
        logger.warning(f"Could not get file size for {file_path}: {e}")
        return 0.0


def safe_read_json_with_retry(
    file_path: str,
    max_retries: int = 5,
    retry_delay: float = 1.0,
    validate_structure: bool = True
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    Safely read a JSON file with retry logic for handling file locks and race conditions.
    
    Args:
        file_path: Path to the JSON file
        max_retries: Maximum number of retry attempts
        retry_delay: Delay between retries in seconds
        validate_structure: If True, validate that the JSON is a dict
        
    Returns:
        Tuple of (data, error): 
            - (dict, None) on success
            - (None, error_message) on failure
    """
    last_error = None
    
    for attempt in range(max_retries):
        try:
            # Check if file exists
            if not os.path.exists(file_path):
                return None, f"File not found: {file_path}"
            
            # Check if file is empty
            if os.path.getsize(file_path) == 0:
                if attempt < max_retries - 1:
                    logger.debug(f"File is empty, retrying in {retry_delay}s... (attempt {attempt + 1}/{max_retries})")
                    time.sleep(retry_delay)
                    continue
                return None, f"File is empty after {max_retries} attempts: {file_path}"
            
            # Read and parse JSON
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # Validate structure if required
            if validate_structure:
                if not isinstance(data, dict):
                    return None, f"Invalid JSON structure: expected dict, got {type(data).__name__}"
            
            # Success
            return data, None
            
        except json.JSONDecodeError as e:
            last_error = f"JSON decode error: {e}"
            if attempt < max_retries - 1:
                logger.debug(f"JSON decode error, retrying in {retry_delay}s... (attempt {attempt + 1}/{max_retries})")
                time.sleep(retry_delay)
                continue
                
        except PermissionError as e:
            last_error = f"Permission denied: {e}"
            if attempt < max_retries - 1:
                logger.debug(f"Permission denied, retrying in {retry_delay}s... (attempt {attempt + 1}/{max_retries})")
                time.sleep(retry_delay)
                continue
                
        except IOError as e:
            last_error = f"I/O error: {e}"
            if attempt < max_retries - 1:
                logger.debug(f"I/O error, retrying in {retry_delay}s... (attempt {attempt + 1}/{max_retries})")
                time.sleep(retry_delay)
                continue
                
        except Exception as e:
            last_error = f"Unexpected error: {type(e).__name__}: {e}"
            if attempt < max_retries - 1:
                logger.debug(f"Unexpected error, retrying in {retry_delay}s... (attempt {attempt + 1}/{max_retries})")
                time.sleep(retry_delay)
                continue
    
    return None, f"Failed after {max_retries} attempts. Last error: {last_error}"


def safe_write_json_atomic(
    file_path: str,
    data: Dict[str, Any],
    indent: int = 2,
    ensure_ascii: bool = False
) -> Tuple[bool, Optional[str]]:
    """
    Safely write JSON to a file using atomic write pattern.
    
    This prevents data corruption by:
    1. Writing to a temporary file first
    2. Flushing and syncing to disk
    3. Atomically renaming temp file to target file
    
    Args:
        file_path: Target file path
        data: Data to write as JSON
        indent: JSON indentation level
        ensure_ascii: If False, allow non-ASCII characters
        
    Returns:
        Tuple of (success, error):
            - (True, None) on success
            - (False, error_message) on failure
    """
    try:
        # Ensure parent directory exists
        parent_dir = os.path.dirname(file_path)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)
        
        # Create temporary file in same directory for atomic rename
        fd, temp_path = tempfile.mkstemp(
            suffix='.tmp',
            prefix='json_',
            dir=parent_dir or '.'
        )
        
        try:
            # Write to temp file
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=indent, ensure_ascii=ensure_ascii)
                f.flush()
                os.fsync(f.fileno())  # Ensure data is written to disk
            
            # Atomic rename (on Windows, need to remove target first)
            if os.name == 'nt' and os.path.exists(file_path):
                # Windows: remove target before rename
                try:
                    os.remove(file_path)
                except OSError:
                    pass  # Target may not exist
            
            shutil.move(temp_path, file_path)
            return True, None
            
        except Exception as e:
            # Clean up temp file on error
            try:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
            except OSError:
                pass
            raise e
            
    except PermissionError as e:
        return False, f"Permission denied: {e}"
        
    except IOError as e:
        return False, f"I/O error: {e}"
        
    except Exception as e:
        return False, f"Unexpected error: {type(e).__name__}: {e}"


def wait_for_file_ready(
    file_path: str,
    max_wait_seconds: int = 60,
    stability_window: float = 2.0,
    check_interval: float = 0.5
) -> Tuple[bool, Optional[str]]:
    """
    Wait for a file to be fully written and ready for reading.
    
    This function helps prevent race conditions by:
    1. Waiting for file to exist
    2. Ensuring file size is stable (not changing)
    3. Verifying file is not locked
    
    Args:
        file_path: Path to the file to wait for
        max_wait_seconds: Maximum time to wait in seconds (default: 60)
        stability_window: Time window to check size stability in seconds (default: 2.0)
        check_interval: Interval between checks in seconds (default: 0.5)
        
    Returns:
        Tuple of (success, error):
            - (True, None) if file is ready
            - (False, error_message) if timeout or error
    """
    start_time = time.time()
    last_size = -1
    stable_since = None
    
    # Adaptive stability_window: large JSON files flush slower on busy servers
    # Caller can override; if not overridden (default 2.0) we auto-scale by file size
    _adaptive_window = stability_window  # will be refined once file size is known
    
    logger.debug(f"Waiting for file to be ready: {file_path}")
    logger.debug(f"Max wait: {max_wait_seconds}s, Stability window (initial): {stability_window}s")
    
    while True:
        elapsed = time.time() - start_time
        
        # Check timeout
        if elapsed > max_wait_seconds:
            return False, f"Timeout waiting for file after {max_wait_seconds}s: {file_path}"
        
        # Check if file exists
        if not os.path.exists(file_path):
            logger.debug(f"File not found yet, waiting... ({elapsed:.1f}s elapsed)")
            time.sleep(check_interval)
            continue
        
        # Check file size
        try:
            current_size = os.path.getsize(file_path)
            
            # File is empty - wait for content
            if current_size == 0:
                logger.debug(f"File is empty, waiting for content... ({elapsed:.1f}s elapsed)")
                stable_since = None
                last_size = current_size
                time.sleep(check_interval)
                continue
            
            # Size changed - reset stability timer
            if current_size != last_size:
                logger.debug(f"File size changed: {last_size} -> {current_size} bytes ({elapsed:.1f}s elapsed)")
                stable_since = time.time()
                last_size = current_size
                time.sleep(check_interval)
                continue
            
            # Size is stable - check if stable long enough
            if stable_since is None:
                stable_since = time.time()
                # Now that we know the file size, compute adaptive stability window
                # Large files (JSON with many faces) flush slower on busy multi-user servers
                if stability_window == 2.0:  # only auto-scale if caller uses default
                    file_size_mb = current_size / (1024 * 1024)
                    if file_size_mb > 20:
                        _adaptive_window = 6.0
                    elif file_size_mb > 5:
                        _adaptive_window = 4.0
                    else:
                        _adaptive_window = 2.0
                    if _adaptive_window != stability_window:
                        logger.debug(
                            f"Adaptive stability window: {_adaptive_window}s "
                            f"(file={file_size_mb:.1f}MB)"
                        )
            
            stable_duration = time.time() - stable_since
            
            if stable_duration < _adaptive_window:
                logger.debug(f"File size stable for {stable_duration:.1f}s, waiting for {_adaptive_window}s... ({elapsed:.1f}s elapsed)")
                time.sleep(check_interval)
                continue
            
            # File size is stable - try to open it to check if locked
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    # Try to read first byte to ensure file is accessible
                    f.read(1)
                
                # Success - file is ready
                logger.info(f"✅ File ready after {elapsed:.1f}s: {file_path} ({current_size} bytes)")
                return True, None
                
            except PermissionError:
                logger.debug(f"File is locked, waiting... ({elapsed:.1f}s elapsed)")
                stable_since = None  # Reset stability on lock
                time.sleep(check_interval)
                continue
                
            except IOError as e:
                logger.debug(f"I/O error reading file, waiting... ({elapsed:.1f}s elapsed): {e}")
                stable_since = None  # Reset stability on I/O error
                time.sleep(check_interval)
                continue
                
        except OSError as e:
            logger.debug(f"OS error checking file, waiting... ({elapsed:.1f}s elapsed): {e}")
            time.sleep(check_interval)
            continue
