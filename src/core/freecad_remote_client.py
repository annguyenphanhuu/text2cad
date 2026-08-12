#!/usr/bin/env python3
"""
FreeCAD Remote Client
Handles communication with remote FreeCAD server for model generation.
Replaces local FreeCAD execution with remote API calls.
"""

import os
import json
import time
import logging
import asyncio
import aiohttp
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime
from dotenv import load_dotenv

from functools import wraps
try:
    import requests
except Exception:
    requests = None  # Optional: only used in legacy retry decorator

# Load environment variables
load_dotenv()

logger = logging.getLogger(__name__)


# Helper functions for compact logging
def _get_filename(path: str) -> str:
    """Extract filename from full path"""
    return os.path.basename(path) if path else ""


def _compact_log(msg: str, **kwargs) -> str:
    """Create compact log message by removing redundant info"""
    # Remove user_id if it's the same as session_id
    if 'user_id' in kwargs and 'session_id' in kwargs:
        if kwargs['user_id'] == kwargs.get('session_id'):
            kwargs.pop('session_id', None)
    
    # Convert file paths to filenames
    for key in ['file', 'script', 'script_path', 'filename']:
        if key in kwargs and kwargs[key]:
            kwargs[key] = _get_filename(str(kwargs[key]))
    
    # Format compact message
    parts = [msg]
    for key, value in kwargs.items():
        if value is not None:
            parts.append(f"{key}={value}")
    
    return " | ".join(parts)

# Custom Exception Classes for FreeCAD Server Communication
class FreeCADServerError(Exception):
    """Base exception for FreeCAD server related errors"""
    pass

class FreeCADConnectionError(FreeCADServerError):
    """Raised when unable to connect to FreeCAD server"""
    pass

class FreeCADTimeoutError(FreeCADServerError):
    """Raised when FreeCAD server request times out"""
    pass

class FreeCADServerNotAvailableError(FreeCADServerError):
    """Raised when FreeCAD server is not available or not responding"""
    pass

class FreeCADProcessingError(FreeCADServerError):
    """Raised when FreeCAD server encounters processing errors"""
    pass

def retry_on_failure(max_retries: int = 3, delay: float = 1.0, backoff: float = 2.0):
    """
    Decorator to retry function calls on failure with exponential backoff

    Args:
        max_retries: Maximum number of retry attempts
        delay: Initial delay between retries in seconds
        backoff: Multiplier for delay after each retry
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            current_delay = delay
            last_exception = None

            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except (FreeCADConnectionError, requests.exceptions.RequestException) as e:
                    last_exception = e
                    if attempt == max_retries:
                        logger.error(f"Function {func.__name__} failed after {max_retries} retries. Last error: {str(e)}")
                        raise

                    logger.warning(f"Attempt {attempt + 1} failed for {func.__name__}: {str(e)}. Retrying in {current_delay:.1f}s...")
                    time.sleep(current_delay)
                    current_delay *= backoff
                except Exception as e:
                    # Don't retry on non-network related errors
                    logger.error(f"Non-retryable error in {func.__name__}: {str(e)}")
                    raise

            # This should never be reached, but just in case
            if last_exception:
                raise last_exception

        return wrapper
    return decorator

class FreeCADRemoteClient:
    """Client for communicating with remote FreeCAD server"""

    def __init__(self, base_url: str = None, max_retries: int = 3, retry_delay: float = 1.0):
        """
        Initialize the remote client

        Args:
            base_url: Base URL of the remote FreeCAD server. If None, will use server_freecad from .env
            max_retries: Maximum number of retry attempts for failed requests
            retry_delay: Initial delay between retries in seconds
        """
        # Get base URL from environment variable if not provided
        if base_url is None:
            base_url = os.getenv("server_freecad", "https://toleryfreecad.vm.dfm-europe.com")
            if base_url:
                logger.info(f"[FreeCAD] Using server URL from env 'server_freecad': {base_url}")
            else:
                logger.warning(f"[FreeCAD] 'server_freecad' env variable not set, using default URL")

        self.base_url = base_url.rstrip('/')
        logger.debug(f"[FreeCAD] FreeCAD Remote Client initialized with base_url: {self.base_url}")
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.session = requests.Session()
        # Timeout for file downloads (5 minutes for large JSON files)
        self.download_timeout = 300

        # Configure session headers
        self.session.headers.update({
            'User-Agent': 'Tolery-API-AI/1.0',
            'Accept': 'application/json'
        })

        # Initialization completed - no logging needed

    def check_server_health(self) -> bool:
        """
        Check if the FreeCAD server is available and responding

        Returns:
            bool: True if server is healthy, False otherwise
        """
        try:
            health_url = f"{self.base_url}/health"
            response = self.session.get(health_url)

            if response.status_code == 200:
                # Removed health check success logger - not needed
                return True
            else:
                logger.warning(f"FreeCAD server health check failed with status code: {response.status_code}")
                return False

        except requests.exceptions.ConnectionError:
            logger.error("FreeCAD server is not reachable - connection failed")
            return False
        except Exception as e:
            logger.error(f"Unexpected error during FreeCAD server health check: {str(e)}")
            return False

    @retry_on_failure(max_retries=3, delay=1.0)
    def generate(self, script_path: str, user_id: str, auto_download: bool = False, metadata_path: Optional[str] = None, priority: int = 0) -> Dict[str, Any]:
        """
        Generate FreeCAD model using remote server with optional threaded holes metadata.

        **IMPORTANT**: The FreeCAD server ALWAYS returns immediately with job acknowledgment,
        regardless of the auto_download parameter. The server has been refactored to use
        async worker queue processing.

        Args:
            script_path: Path to the FreeCAD Python script
            user_id: User ID for job management
            auto_download: DEPRECATED - This parameter is no longer used by the server.
                          Server always returns immediately with job acknowledgment.
            metadata_path: Optional path to metadata.json file containing threaded holes info.
                          If provided, will be sent to the server along with the script.
                          If None or file doesn't exist, only the script will be sent.
            priority: FreeCAD queue priority from 0 to 100.

        Returns:
            Dictionary with job acknowledgment:
            {
                'user_id': str,
                'status': 'queued',
                'message': str,
                'created_at': str,
                'job_id': str,
                'check_status_url': str,
                'check_result_url': str,
                'mqtt_published': bool,
                'metadata_sent': bool  # NEW: indicates if metadata was sent
            }

        Recommended Workflow:
            1. Call generate() to submit job (returns immediately)
            2. Monitor progress via MQTT or poll /freecad/status/{user_id}
            3. When finished, call get_job_result(user_id, auto_download=True) to retrieve files

        Raises:
            FreeCADConnectionError: When unable to connect to server
            FreeCADTimeoutError: When request times out
            FreeCADProcessingError: When server encounters processing errors
            FreeCADServerNotAvailableError: When server is not available
        """
        try:
            # Validate script file exists
            if not os.path.exists(script_path):
                raise FileNotFoundError(f"Script file not found: {script_path}")

            # Validate metadata file if provided
            metadata_exists = False
            if metadata_path:
                if os.path.exists(metadata_path):
                    metadata_exists = True
                    # Log metadata info
                    try:
                        with open(metadata_path, 'r', encoding='utf-8') as f:
                            metadata_content = json.load(f)
                        threaded_count = len(metadata_content.get('threaded_holes', []))
                        if threaded_count > 0:
                            logger.info(
                                f"[FREECAD] user_id={user_id} | "
                                f"Using metadata file with {threaded_count} threaded hole(s)"
                            )
                    except Exception as e:
                        logger.warning(f"[FREECAD] user_id={user_id} | Could not read metadata: {e}")
                else:
                    logger.warning(f"[FREECAD] user_id={user_id} | Metadata file not found: {metadata_path}")
                    metadata_path = None  # Don't send non-existent file

            # Prepare the request
            url = f"{self.base_url}/freecad/generate"

            # Open both files in the same context to ensure they stay open during POST
            from contextlib import ExitStack
            
            with ExitStack() as stack:
                # Always open the script file
                script_file = stack.enter_context(open(script_path, 'rb'))
                files = {
                    'file': (os.path.basename(script_path), script_file, 'text/x-python')
                }
                
                # Add metadata file if available
                if metadata_path and os.path.exists(metadata_path):
                    metadata_file = stack.enter_context(open(metadata_path, 'rb'))
                    # CRITICAL: Field name must be 'metadata_file' (not 'metadata')
                    files['metadata_file'] = (
                        os.path.basename(metadata_path),
                        metadata_file,
                        'application/json'
                    )
                
                data = {
                    'user_id': user_id,
                    'auto_download': str(auto_download).lower(),  # Convert boolean to string
                    'priority': str(priority)
                }

                # Count files being sent
                file_count = len(files)  # 1 (script) or 2 (script + metadata)
                
                logger.debug(
                    f"[FreeCAD] Phase 1: Submitting | "
                    f"user_id={user_id} | "
                    f"file={os.path.basename(script_path)} | "
                    f"metadata={'yes' if 'metadata_file' in files else 'no'} | "
                    f"priority={priority}"
                )

                # Send request - both files will remain open until response is received
                response = self.session.post(url, files=files, data=data)
            
            # Track if metadata was sent
            metadata_was_sent = metadata_path is not None and metadata_exists

            # Handle different HTTP status codes
            if response.status_code == 503:
                raise FreeCADServerNotAvailableError("FreeCAD server is temporarily unavailable (503)")
            elif response.status_code >= 500:
                raise FreeCADProcessingError(f"FreeCAD server internal error (status: {response.status_code})")
            elif response.status_code == 404:
                raise FreeCADConnectionError(f"FreeCAD server endpoint not found (404): {url}")
            elif response.status_code >= 400:
                raise FreeCADProcessingError(f"FreeCAD server request error (status: {response.status_code})")

            response.raise_for_status()

            # Server ALWAYS returns job acknowledgment immediately (async queue processing)
            # The auto_download parameter is DEPRECATED and IGNORED by the server
            # Server always uses async worker queue, regardless of auto_download value
            # Expected response format:
            # {
            #   "user_id": "user_20251114_130555",
            #   "status": "queued",
            #   "message": "Script has been queued for processing...",
            #   "created_at": "2025-11-14T06:07:37+00:00",
            #   "job_id": "2f6b93f6-8eeb-4812-b528-02291d4fcba4",
            #   "check_status_url": "/freecad/status/user_20251114_130555",
            #   "check_result_url": "/freecad/result/user_20251114_130555",
            #   "mqtt_published": true
            # }
            try:
                job_ack = response.json()
            except ValueError as e:
                error_msg = f"Failed to parse server response as JSON: {response.text[:500]}"
                logger.error(f"[FreeCAD] {error_msg}")
                raise FreeCADProcessingError(error_msg)

            # Verify response contains required fields
            if not job_ack.get('job_id'):
                error_msg = f"Server response missing job_id: {job_ack}"
                logger.error(f"[FreeCAD] {error_msg}")
                raise FreeCADProcessingError(error_msg)

            if not job_ack.get('user_id'):
                error_msg = f"Server response missing user_id: {job_ack}"
                logger.error(f"[FreeCAD] {error_msg}")
                raise FreeCADProcessingError(error_msg)

            # Verify user_id matches
            if job_ack.get('user_id') != user_id:
                error_msg = f"Server returned different user_id. Expected: {user_id}, Got: {job_ack.get('user_id')}"
                logger.error(f"[FreeCAD] {error_msg}")
                raise FreeCADProcessingError(error_msg)

            # ============================================================
            # VALIDATE FILES RECEIVED BY SERVER
            # ============================================================
            files_received = job_ack.get('files_received', {})
            
            # Check if Python script was received
            python_script_info = files_received.get('python_script')
            if not python_script_info:
                logger.warning(f"[FreeCAD] ⚠️ Server response missing 'python_script' info")
            else:
                script_filename = python_script_info.get('filename', 'unknown')
                script_size = python_script_info.get('size_bytes', 0)
                logger.info(f"[FreeCAD] ✅ Server received script | file={script_filename} | size={script_size}B")
            
            # Check if metadata was received (if we sent it)
            metadata_info = files_received.get('metadata')
            if metadata_was_sent:
                # We sent metadata, verify server received it
                if not metadata_info:
                    logger.error(
                        f"[FreeCAD] ❌ METADATA VERIFICATION FAILED | "
                        f"We sent metadata but server did not acknowledge receiving it | "
                        f"files_received={files_received}"
                    )
                    raise FreeCADProcessingError(
                        "Server did not receive metadata file. "
                        "This may cause threaded holes to not be detected correctly."
                    )
                else:
                    # Server received metadata - log details
                    metadata_filename = metadata_info.get('filename', 'unknown')
                    metadata_size = metadata_info.get('size_bytes', 0)
                    threaded_count = metadata_info.get('threaded_holes_count', 0)
                    logger.info(
                        f"[FreeCAD] ✅ Server received metadata | "
                        f"file={metadata_filename} | "
                        f"size={metadata_size}B | "
                        f"threaded_holes={threaded_count}"
                    )
            else:
                # We didn't send metadata
                if metadata_info:
                    logger.warning(
                        f"[FreeCAD] ⚠️ Server reports receiving metadata but we didn't send any | "
                        f"metadata_info={metadata_info}"
                    )
                else:
                    logger.debug(f"[FreeCAD] No metadata sent or received (as expected)")

            # Add metadata flag to response
            job_ack['metadata_sent'] = metadata_was_sent
            job_ack['metadata_received'] = metadata_info is not None

            logger.info(
                f"[FreeCAD] ✅ Job queued successfully | "
                f"job_id={job_ack.get('job_id')} | "
                f"user_id={job_ack.get('user_id')} | "
                f"status={job_ack.get('status')} | "
                f"mqtt_published={job_ack.get('mqtt_published', False)} | "
                f"metadata_received={'yes' if metadata_info else 'no'}"
            )
            return job_ack

        except requests.exceptions.ConnectionError as e:
            logger.error(f"Connection to FreeCAD server failed: {e}")
            raise FreeCADConnectionError(f"Unable to connect to FreeCAD server at {self.base_url}: {e}")
        except requests.exceptions.HTTPError as e:
            logger.error(f"HTTP error from FreeCAD server: {e}")
            raise FreeCADProcessingError(f"HTTP error from FreeCAD server: {e}")
        except requests.exceptions.RequestException as e:
            logger.error(f"Request to FreeCAD server failed: {e}")
            raise FreeCADConnectionError(f"Failed to communicate with FreeCAD server: {e}")
        except (FreeCADConnectionError, FreeCADProcessingError, FreeCADServerNotAvailableError):
            # Re-raise our custom exceptions
            raise
        except Exception as e:
            logger.error(f"Unexpected error during FreeCAD generation: {e}")
            raise FreeCADProcessingError(f"Unexpected error during FreeCAD generation: {e}")
        except Exception as e:
            logger.error(f"Generation failed: {e}")
            raise

    @retry_on_failure(max_retries=3, delay=1.0)
    def get_job_status(self, user_id: str) -> Dict[str, Any]:
        """
        Get job status for a user

        Args:
            user_id: User ID to check status for

        Returns:
            Dictionary containing job status

        Raises:
            FreeCADConnectionError: When unable to connect to server
            FreeCADTimeoutError: When request times out
            FreeCADProcessingError: When server encounters processing errors
        """
        try:
            url = f"{self.base_url}/freecad/status/{user_id}"

            logger.debug(f"Checking job status for user {user_id}")

            response = self.session.get(url)

            # Handle different HTTP status codes
            if response.status_code == 404:
                raise FreeCADProcessingError(f"Job not found for user {user_id}")
            elif response.status_code >= 500:
                raise FreeCADProcessingError(f"FreeCAD server internal error (status: {response.status_code})")
            elif response.status_code >= 400:
                raise FreeCADProcessingError(f"FreeCAD server request error (status: {response.status_code})")

            response.raise_for_status()

            return response.json()

        except requests.exceptions.ConnectionError as e:
            logger.error(f"Connection to FreeCAD server failed during status check: {e}")
            raise FreeCADConnectionError(f"Unable to connect to FreeCAD server for status check: {e}")
        except requests.exceptions.RequestException as e:
            logger.error(f"Status check failed: {e}")
            raise FreeCADConnectionError(f"Failed to get job status from FreeCAD server: {e}")
        except (FreeCADConnectionError, FreeCADProcessingError):
            # Re-raise our custom exceptions
            raise
        except Exception as e:
            logger.error(f"Unexpected error during status check: {e}")
            raise FreeCADProcessingError(f"Unexpected error during status check: {e}")

    @retry_on_failure(max_retries=3, delay=1.0)
    def get_job_result(self, user_id: str, auto_download: bool = True, expect_obj: bool = True) -> Dict[str, Any]:
        """
        Get job result for a user

        Args:
            user_id: User ID to get results for
            auto_download: If True, server will copy files to outputs/code/cad_outputs_generated/
                          and return download links. If False, only return file information.

        Returns:
            Dictionary containing job results with structure:
            {
                'user_id': str,
                'job_id': str,
                'status': str,
                'message': str,
                'files': [
                    {
                        'type': 'step'|'obj'|'pdf'|'json',
                        'path': str,
                        'filename': str,
                        'download_url': str (if auto_download=True),
                        'local_path': str (if auto_download=True)
                    },
                    ...
                ],
                'output_directory': str (if auto_download=True),
                'completed_at': str
            }

        Raises:
            FreeCADConnectionError: When unable to connect to server
            FreeCADTimeoutError: When request times out
            FreeCADProcessingError: When server encounters processing errors
        """
        try:
            url = f"{self.base_url}/freecad/result/{user_id}"
            # Convert boolean to string for query parameter
            params = {'auto_download': str(auto_download).lower()}

            logger.debug(f"Getting job result for user {user_id} (auto_download={auto_download})")

            response = self.session.get(url, params=params)

            # Handle different HTTP status codes
            if response.status_code == 404:
                raise FreeCADProcessingError(f"Job result not found for user {user_id}")
            elif response.status_code >= 500:
                raise FreeCADProcessingError(f"FreeCAD server internal error (status: {response.status_code})")
            elif response.status_code >= 400:
                raise FreeCADProcessingError(f"FreeCAD server request error (status: {response.status_code})")

            response.raise_for_status()

            result = response.json()

            # Log result summary
            if auto_download:
                files = result.get('files', [])
                logger.info(
                    f"[RESULT] User {user_id}: Retrieved {len(files)} files - "
                    f"Output dir: {result.get('output_directory', 'N/A')}"
                )
            else:
                logger.info(f"[RESULT] User {user_id}: Retrieved file information (no download)")

            return result

        except requests.exceptions.ConnectionError as e:
            logger.error(f"Connection to FreeCAD server failed during result retrieval: {e}")
            raise FreeCADConnectionError(f"Unable to connect to FreeCAD server for result retrieval: {e}")
        except requests.exceptions.RequestException as e:
            logger.error(f"Result retrieval failed: {e}")
            raise FreeCADConnectionError(f"Failed to get job result from FreeCAD server: {e}")
        except (FreeCADConnectionError, FreeCADProcessingError):
            # Re-raise our custom exceptions
            raise
        except Exception as e:
            logger.error(f"Unexpected error during result retrieval: {e}")
            raise FreeCADProcessingError(f"Unexpected error during result retrieval: {e}")

    @retry_on_failure(max_retries=3, delay=1.0)
    def download_file(self, user_id: str, filename: str, output_dir: str) -> str:
        """
        Download a specific file from the server

        Args:
            user_id: User ID
            filename: Name of the file to download
            output_dir: Directory to save the downloaded file

        Returns:
            Path to the downloaded file

        Raises:
            FreeCADConnectionError: When unable to connect to server
            FreeCADTimeoutError: When request times out
            FreeCADProcessingError: When server encounters processing errors
        """
        try:
            url = f"{self.base_url}/freecad/download/{user_id}/{filename}"

            logger.debug(f"Downloading file {filename} for user {user_id}")

            response = self.session.get(url, stream=True)

            # Handle different HTTP status codes
            if response.status_code == 404:
                raise FreeCADProcessingError(f"File {filename} not found for user {user_id}")
            elif response.status_code >= 500:
                raise FreeCADProcessingError(f"FreeCAD server internal error (status: {response.status_code})")
            elif response.status_code >= 400:
                raise FreeCADProcessingError(f"FreeCAD server request error (status: {response.status_code})")

            response.raise_for_status()

            # Ensure output directory exists
            os.makedirs(output_dir, exist_ok=True)

            # Save file
            output_path = os.path.join(output_dir, filename)
            with open(output_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)


            return output_path

        except requests.exceptions.ConnectionError as e:
            logger.error(f"Connection to FreeCAD server failed during file download: {e}")
            raise FreeCADConnectionError(f"Unable to connect to FreeCAD server for file download: {e}")
        except requests.exceptions.RequestException as e:
            logger.error(f"Download failed: {e}")
            raise FreeCADConnectionError(f"Failed to download file from FreeCAD server: {e}")
        except (FreeCADConnectionError, FreeCADProcessingError):
            # Re-raise our custom exceptions
            raise
        except Exception as e:
            logger.error(f"Unexpected error during file download: {e}")
            raise FreeCADProcessingError(f"Unexpected error during file download: {e}")

    @retry_on_failure(max_retries=3, delay=1.0)
    def get_execution_status(self, user_id: str = None) -> Dict[str, Any]:
        """
        Get detailed execution status and progress from FreeCAD server

        This method calls the /freecad/status endpoint to get real-time information about
        FreeCAD execution progress, including current process, completion percentage,
        and any error messages.

        Args:
            user_id: User ID to get specific job status. Required for this endpoint.

        Returns:
            Dictionary containing detailed execution status:
            {
                'status': 'running|completed|error|idle',
                'progress': 0-100,  # completion percentage
                'current_process': 'description of current step',
                'message': 'status message',
                'timestamp': 'ISO timestamp',
                'user_id': 'user_id if applicable',
                'execution_time': 'seconds since start'
            }

        Raises:
            FreeCADConnectionError: When unable to connect to server
            FreeCADTimeoutError: When request times out
            FreeCADProcessingError: When server encounters processing errors
        """
        if not user_id:
            raise FreeCADProcessingError("user_id is required for execution status check")

        try:
            # Use the freecad/status endpoint from the FreeCAD server API
            url = f"{self.base_url}/freecad/status/{user_id}"
            logger.debug(f"Getting execution status for user {user_id}")

            response = self.session.get(url)

            # Handle different HTTP status codes
            if response.status_code == 404:
                raise FreeCADProcessingError(f"No execution status found for user {user_id}")
            elif response.status_code >= 500:
                raise FreeCADProcessingError(f"FreeCAD server internal error (status: {response.status_code})")
            elif response.status_code >= 400:
                raise FreeCADProcessingError(f"FreeCAD server request error (status: {response.status_code})")

            response.raise_for_status()

            status_data = response.json()

            # Normalize the response format to ensure consistency
            normalized_status = {
                'status': status_data.get('status', 'unknown'),
                'progress': status_data.get('progress', 0),
                'current_process': status_data.get('current_process', status_data.get('message', 'No process information')),
                'message': status_data.get('message', ''),
                'timestamp': status_data.get('timestamp', datetime.now().isoformat()),
                'user_id': status_data.get('user_id', user_id),
                'execution_time': status_data.get('execution_time', 0),
                'server_info': {
                    'server_url': self.base_url,
                    'endpoint_used': url
                }
            }

            logger.debug(f"Execution status retrieved: {normalized_status['status']} - {normalized_status['progress']}%")

            return normalized_status

        except requests.exceptions.ConnectionError as e:
            logger.error(f"Connection to FreeCAD server failed during status check: {e}")
            raise FreeCADConnectionError(f"Unable to connect to FreeCAD server for status check: {e}")
        except requests.exceptions.RequestException as e:
            logger.error(f"Status check failed: {e}")
            raise FreeCADConnectionError(f"Failed to get execution status from FreeCAD server: {e}")
        except (FreeCADConnectionError, FreeCADProcessingError):
            # Re-raise our custom exceptions
            raise
        except Exception as e:
            logger.error(f"Unexpected error during status check: {e}")
            raise FreeCADProcessingError(f"Unexpected error during status check: {e}")

    def monitor_execution_progress(self, user_id: str, poll_interval: int = 2, max_duration: int = None) -> Dict[str, Any]:
        """
        Monitor FreeCAD execution progress with real-time updates

        This method continuously polls the status endpoint to provide real-time
        progress updates during FreeCAD execution.

        Args:
            user_id: User ID to monitor
            poll_interval: Seconds between status checks (default: 2)
            max_duration: Maximum monitoring duration in seconds (default: None - no timeout)

        Returns:
            Dictionary containing final execution result and progress history

        Raises:
            FreeCADConnectionError: When unable to connect to server
            FreeCADProcessingError: When server encounters processing errors
        """
        start_time = time.time()
        progress_history = []
        last_progress = -1

        logger.info(f"Starting execution monitoring for user {user_id}")

        try:
            while True:
                current_time = time.time()
                elapsed_time = current_time - start_time

                # No timeout check - monitor indefinitely

                # Get current status
                try:
                    status = self.get_execution_status(user_id)
                    current_progress = status.get('progress', 0)
                    current_status = status.get('status', 'unknown')
                    current_process = status.get('current_process', '')

                    # Log progress changes
                    if current_progress != last_progress:
                        logger.info(f"Progress update for user {user_id}: {current_progress}% - {current_process}")
                        last_progress = current_progress

                    # Add to history
                    progress_history.append({
                        'timestamp': datetime.now().isoformat(),
                        'elapsed_time': round(elapsed_time, 2),
                        'progress': current_progress,
                        'status': current_status,
                        'process': current_process
                    })

                    # Check if execution is complete
                    if current_status in ['completed', 'success', 'finished']:
                        logger.info(f"Execution completed for user {user_id} after {elapsed_time:.2f}s")
                        return {
                            'success': True,
                            'final_status': status,
                            'total_time': round(elapsed_time, 2),
                            'progress_history': progress_history
                        }

                    # Check if execution failed
                    if current_status in ['error', 'failed', 'cancelled']:
                        logger.error(f"Execution failed for user {user_id}: {status.get('message', 'Unknown error')}")
                        return {
                            'success': False,
                            'final_status': status,
                            'total_time': round(elapsed_time, 2),
                            'progress_history': progress_history,
                            'error': status.get('message', 'Execution failed')
                        }

                except FreeCADConnectionError as e:
                    logger.warning(f"Temporary error during monitoring: {e}")
                    # Continue monitoring unless it's a persistent connection issue
                    if elapsed_time > 30:  # If we've been monitoring for more than 30s, re-raise
                        raise

                # Wait before next poll
                time.sleep(poll_interval)

        except KeyboardInterrupt:
            logger.info(f"Monitoring interrupted by user for {user_id}")
            return {
                'success': False,
                'final_status': {'status': 'interrupted', 'message': 'Monitoring interrupted by user'},
                'total_time': round(time.time() - start_time, 2),
                'progress_history': progress_history
            }
        except Exception as e:
            logger.error(f"Error during execution monitoring: {e}")
            return {
                'success': False,
                'final_status': {'status': 'error', 'message': str(e)},
                'total_time': round(time.time() - start_time, 2),
                'progress_history': progress_history,
                'error': str(e)
            }

    def _download_file_with_retry_sync(
        self,
        download_url: str,
        local_path: Path,
        file_type: str,
        filename: str,
        user_id: str = None,
        max_retries: int = 3
    ) -> str:
        """
        Download file with retry logic, Content-Length validation, and integrity checks (sync version)

        Args:
            download_url: URL to download file from
            local_path: Local path to save file to
            file_type: Type of file (step, obj, json, pdf)
            filename: Original filename
            user_id: User ID for logging context
            max_retries: Maximum number of retry attempts

        Returns:
            String path to downloaded file

        Raises:
            Exception: If download fails after all retries or file validation fails
        """
        last_error = None
        user_context = f"user_id={user_id} | " if user_id else ""

        # Only log start on first attempt, failures logged at end
        start_time = time.time()

        for attempt in range(max_retries):
            try:
                # Add timeout for large file downloads (especially JSON)
                response = self.session.get(download_url, timeout=self.download_timeout, stream=True)
                response.raise_for_status()

                # Get expected file size from Content-Length header
                expected_size = response.headers.get('Content-Length')
                if expected_size:
                    expected_size = int(expected_size)

                # Log for JSON files (important for debugging timeout issues)
                if file_type == 'json' and expected_size:
                    size_mb = expected_size / (1024 * 1024)
                    logger.info(f"[DOWNLOAD] {user_context}Starting {file_type.upper()} download | size={size_mb:.1f}MB | timeout={self.download_timeout}s")

                # Download file in chunks
                downloaded_size = 0
                with open(local_path, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        f.write(chunk)
                        downloaded_size += len(chunk)

                # Validate file size matches Content-Length
                if expected_size and downloaded_size != expected_size:
                    error_msg = f"File size mismatch: expected {expected_size:,} bytes, got {downloaded_size:,} bytes"
                    # Delete incomplete file
                    if os.path.exists(local_path):
                        os.remove(local_path)
                    raise Exception(error_msg)

                # Verify file exists and has content
                if not os.path.exists(local_path):
                    raise Exception(f"Downloaded file does not exist: {local_path}")

                actual_file_size = os.path.getsize(local_path)
                if actual_file_size == 0:
                    raise Exception(f"Downloaded file is empty: {local_path}")

                # Success - no logging here, will be logged in summary
                return str(local_path)

            except Exception as e:
                last_error = e
                error_str = str(e).lower()

                # Delete partial file if it exists
                if os.path.exists(local_path):
                    try:
                        os.remove(local_path)
                    except:
                        pass

                # Log timeout/large file issues
                is_timeout = any(kw in error_str for kw in ['timeout', 'timed out', 'read timed out', 'connection timeout'])
                if is_timeout and file_type == 'json':
                    logger.warning(f"[DOWNLOAD] {user_context}⏱️ JSON download timeout (attempt {attempt+1}/{max_retries}) | May be too large or network issue")

                # If not the last attempt, wait before retrying (exponential backoff)
                if attempt < max_retries - 1:
                    wait_time = 2 ** attempt  # 1s, 2s, 4s
                    time.sleep(wait_time)
                else:
                    # Final attempt failed - log error with user context
                    error_msg = f"Failed to download {file_type.upper()} '{filename}' after {max_retries} attempts"
                    if is_timeout:
                        error_msg += f" [TIMEOUT: {error_str[:100]}]"
                    else:
                        error_msg += f": {last_error}"
                    logger.error(f"[DOWNLOAD] {user_context}❌ {error_msg}")
                    raise Exception(error_msg)

        # Should never reach here, but just in case
        raise Exception(f"Failed to download {file_type.upper()} file after {max_retries} attempts")

    def _handle_json_response(self, json_response: Dict[str, Any], user_id: str, expect_obj: bool = True) -> Dict[str, Any]:
        """
        Handle JSON response from server containing file information (sync version)

        This method now includes:
        - Comprehensive logging of server response
        - Retry logic with exponential backoff for each file download
        - Content-Length validation to detect truncated downloads
        - File integrity checks (existence, non-zero size)
        - Validation that required CAD files (step, obj) are downloaded. JSON/PDF are optional
        - Immediate error raising if any file fails after retries

        Args:
            json_response: JSON response from server
            user_id: User ID for file management

        Returns:
            Dictionary containing processing result with required CAD files

        Raises:
            Exception: If any file download fails or validation fails
        """
        try:
            # Check if response indicates success
            status = json_response.get('status', '')
            if status != 'success':
                error_msg = f'Server returned non-success status: {status}'
                logger.error(f"[FILE_DOWNLOAD] user_id={user_id} | ❌ {error_msg}")
                return {
                    'success': False,
                    'message': error_msg,
                    'response': json_response
                }

            # Check if response contains files
            files_info = json_response.get('files', [])
            if not files_info:
                # If no files in response, try to get them from result endpoint
                logger.debug(f"[FILE_DOWNLOAD] user_id={user_id} | No files in initial response, checking result endpoint...")
                result_response = self.get_job_result(user_id)
                if result_response.get('success'):
                    files_info = result_response.get('files', [])

                if not files_info:
                    error_msg = 'Job completed successfully but no files were generated'
                    logger.warning(f"[FILE_DOWNLOAD] user_id={user_id} | ⚠️ {error_msg}")
                    return {
                        'success': True,
                        'message': error_msg,
                        'response': json_response
                    }

            # Create output directory
            output_dir = Path("outputs/code/cad_outputs_generated")
            output_dir.mkdir(parents=True, exist_ok=True)

            downloaded_files = {}
            file_sizes = {}
            total_size = 0
            download_list = []  # Collect file info for single log message
            prepared_files = []  # Store prepared file data for download

            # Prepare all files and collect info for logging
            for file_info in files_info:
                filename = file_info.get('filename')
                download_url = file_info.get('download_url', '').strip()
                file_type = file_info.get('type', '').lower()

                # If no download_url but has filename, construct it from user_id and filename
                if not download_url and filename:
                    download_url = f"{self.base_url}/freecad/download/{user_id}/{filename}"

                if not filename or not download_url:
                    error_msg = f"File info missing filename or download_url: {file_info}"
                    logger.error(f"[FILE_DOWNLOAD] user_id={user_id} | ❌ {error_msg}")
                    raise Exception(error_msg)

                # Fix localhost URL issue
                if 'localhost' in download_url:
                    import re
                    if download_url.startswith('http://localhost') or download_url.startswith('https://localhost'):
                        download_url = re.sub(r'https?://localhost:\d+', self.base_url, download_url)
                    else:
                        download_url = download_url.replace('localhost', self.base_url.replace('http://', '').replace('https://', ''))

                # Collect file info for logging (include full URL)
                download_list.append({
                    'type': file_type.upper(),
                    'filename': filename,
                    'url': download_url
                })

                # Store prepared data for download
                prepared_files.append({
                    'filename': filename,
                    'download_url': download_url,
                    'file_type': file_type
                })

            # Log all files to download in one message with URLs
            if download_list:
                file_list = ", ".join([f"{f['type']}({f['filename']}): {f['url']}" for f in download_list])
                logger.info(f"[FILE_DOWNLOAD] Downloading {len(download_list)} files | user_id={user_id} | {file_list}")

            # Download each file using retry logic
            for file_data in prepared_files:
                filename = file_data['filename']
                download_url = file_data['download_url']
                file_type = file_data['file_type']

                # Download file with retry logic
                local_path = output_dir / filename

                try:
                    # Use the new retry helper method
                    downloaded_path = self._download_file_with_retry_sync(
                        download_url=download_url,
                        local_path=local_path,
                        file_type=file_type,
                        filename=filename,
                        user_id=user_id,
                        max_retries=3
                    )

                    downloaded_files[file_type] = downloaded_path
                    file_size = os.path.getsize(downloaded_path) if os.path.exists(downloaded_path) else 0
                    file_sizes[file_type] = file_size
                    total_size += file_size

                except Exception as e:
                    # Critical files (STEP, OBJ) must succeed
                    if file_type in ['step', 'obj']:
                        logger.error(f"[FILE_DOWNLOAD] user_id={user_id} | Failed to download critical file {file_type.upper()}: {e}")
                        raise
                    # Optional files (JSON, PDF) - log and continue
                    else:
                        logger.warning(f"[FILE_DOWNLOAD] user_id={user_id} | Failed to download optional file {file_type.upper()} (will continue): {e}")

            # JSON can take longer to generate on the server when it's large. If it wasn't
            # in the initial file listing, poll the result endpoint a few times before
            # giving up, instead of failing immediately.
            if 'json' not in downloaded_files:
                max_json_checks = 3
                for attempt in range(1, max_json_checks + 1):
                    logger.info(f"[FILE_DOWNLOAD] user_id={user_id} | JSON not ready yet, re-checking ({attempt}/{max_json_checks}) in 5s...")
                    time.sleep(5)
                    try:
                        retry_result = self.get_job_result(user_id, auto_download=True, expect_obj=expect_obj)
                        json_file_info = next(
                            (f for f in retry_result.get('files', []) if f.get('type', '').lower() == 'json'),
                            None
                        )
                        if not json_file_info:
                            continue

                        filename = json_file_info.get('filename')
                        download_url = json_file_info.get('download_url', '').strip()
                        if not download_url and filename:
                            download_url = f"{self.base_url}/freecad/download/{user_id}/{filename}"
                        if 'localhost' in download_url:
                            import re
                            if download_url.startswith('http://localhost') or download_url.startswith('https://localhost'):
                                download_url = re.sub(r'https?://localhost:\d+', self.base_url, download_url)
                            else:
                                download_url = download_url.replace('localhost', self.base_url.replace('http://', '').replace('https://', ''))

                        downloaded_path = self._download_file_with_retry_sync(
                            download_url=download_url,
                            local_path=output_dir / filename,
                            file_type='json',
                            filename=filename,
                            user_id=user_id,
                            max_retries=3
                        )
                        downloaded_files['json'] = downloaded_path
                        file_size = os.path.getsize(downloaded_path) if os.path.exists(downloaded_path) else 0
                        file_sizes['json'] = file_size
                        total_size += file_size
                        logger.info(f"[FILE_DOWNLOAD] user_id={user_id} | ✅ JSON became available on check {attempt}/{max_json_checks}")
                        break
                    except Exception as e:
                        logger.warning(f"[FILE_DOWNLOAD] user_id={user_id} | JSON check {attempt}/{max_json_checks} failed: {e}")

            # Validate required FreeCAD output files. STEP and JSON are always
            # mandatory. OBJ is mandatory too UNLESS the caller told us this job
            # is a Perforated Sheet (expect_obj=False) -- Perforated Sheet jobs
            # intentionally skip OBJ generation (see templates.py). Every OTHER
            # shape type keeps the old hard requirement, so a real OBJ-generation
            # bug for a non-perforated shape still fails the job loudly instead
            # of silently succeeding without it.
            required_file_types = {'step', 'json'} | ({'obj'} if expect_obj else set())
            optional_file_types = {'pdf'} | (set() if expect_obj else {'obj'})
            downloaded_types = set(downloaded_files.keys())
            missing_required = required_file_types - downloaded_types
            missing_optional = optional_file_types - downloaded_types

            if missing_required:
                error_msg = f"Missing required files: {', '.join(sorted(missing_required))}. Expected {'+'.join(sorted(t.upper() for t in required_file_types))} but got {list(downloaded_types)}"
                logger.error(f"[FILE_DOWNLOAD] user_id={user_id} | ❌ VALIDATION FAILED: {error_msg}")
                raise Exception(error_msg)

            # Log warning if optional PDF/OBJ are missing, but do not fail the export
            if missing_optional:
                logger.warning(f"[FILE_DOWNLOAD] user_id={user_id} | ⚠️ Optional files missing: {', '.join(sorted(missing_optional))} | Proceeding with {', '.join(sorted(downloaded_types))}")

            # All validations passed - Single consolidated log entry
            file_details = ", ".join([f"{ft.upper()}({file_sizes.get(ft, 0):,}B)" for ft in sorted(downloaded_files.keys())])
            logger.info(f"[FILE_DOWNLOAD] Completed | user_id={user_id} | files={len(downloaded_files)}/{len(files_info)} | total={total_size:,}B | {file_details}")

            return {
                'success': True,
                'message': f'Successfully downloaded and validated required CAD files',
                'files': downloaded_files,
                'user_id': user_id,
                'completed_at': json_response.get('completed_at')
            }

        except Exception as e:
            error_msg = f"Error handling JSON response: {e}"
            logger.error(f"[FILE_DOWNLOAD] ❌ {error_msg}")

            # Re-raise the exception to propagate it up
            raise Exception(error_msg)

    def _handle_zip_response(self, response, user_id: str) -> Dict[str, Any]:
        """
        Handle ZIP file response from server

        Args:
            response: Response object containing ZIP data
            user_id: User ID for organizing files

        Returns:
            Dictionary containing extracted file paths
        """
        try:
            # Create output directory
            today = datetime.now().strftime("%Y-%m-%d")
            output_base_dir = Path("outputs")

            # Create directories for each format
            step_dir = output_base_dir / "step" / today
            obj_dir = output_base_dir / "obj" / today
            json_dir = output_base_dir / "json" / today

            for dir_path in [step_dir, obj_dir, json_dir]:
                dir_path.mkdir(parents=True, exist_ok=True)

            # Save ZIP file temporarily
            temp_zip_path = f"temp_{user_id}_{int(time.time())}.zip"
            with open(temp_zip_path, 'wb') as f:
                f.write(response.content)

            # Extract ZIP file
            extracted_files = {}
            with zipfile.ZipFile(temp_zip_path, 'r') as zip_ref:
                for file_info in zip_ref.filelist:
                    filename = file_info.filename
                    file_ext = Path(filename).suffix.lower()

                    # Determine target directory based on file extension
                    if file_ext == '.step':
                        target_dir = step_dir
                        file_type = 'step'
                    elif file_ext == '.obj':
                        target_dir = obj_dir
                        file_type = 'obj'
                    elif file_ext == '.json':
                        target_dir = json_dir
                        file_type = 'json'
                    else:
                        logger.warning(f"Unsupported file type: {filename}")
                        continue

                    # Extract file to appropriate directory
                    target_path = target_dir / filename
                    with zip_ref.open(file_info) as source, open(target_path, 'wb') as target:
                        target.write(source.read())

                    extracted_files[f"{file_type}_path"] = str(target_path)
                    logger.info(f"Extracted {file_type.upper()} file: {target_path}")

            # Clean up temporary ZIP file
            os.remove(temp_zip_path)

            return {
                "success": True,
                "message": "Model generated successfully using remote FreeCAD server",
                **extracted_files
            }

        except Exception as e:
            logger.error(f"Failed to handle ZIP response: {e}")
            # Clean up temporary file if it exists
            if os.path.exists(temp_zip_path):
                os.remove(temp_zip_path)
            raise Exception(f"Failed to process server response: {e}")

    def wait_for_completion(self, user_id: str, max_wait_time: int = None, poll_interval: int = 5) -> Dict[str, Any]:
        """
        Wait for job completion and return results (polling-based)

        Args:
            user_id: User ID to wait for
            max_wait_time: Maximum time to wait in seconds (default: None - no timeout)
            poll_interval: Polling interval in seconds

        Returns:
            Dictionary containing job results with files downloaded
        """
        start_time = time.time()

        while True:
            try:
                status = self.get_job_status(user_id)

                if status.get('status') == 'completed':
                    logger.info(f"Job completed for user {user_id}")
                    # Get results with auto_download=True to retrieve files
                    return self.get_job_result(user_id, auto_download=True)
                elif status.get('status') == 'failed':
                    raise Exception(f"Job failed for user {user_id}")

                logger.debug(f"Job still processing for user {user_id}, waiting...")
                time.sleep(poll_interval)

            except Exception as e:
                logger.error(f"Error while waiting for completion: {e}")
                raise

    def wait_for_mqtt_completion(
        self,
        user_id: str,
        mqtt_broker: str = None,
        max_wait_time: int = None,
        timeout: int = None
    ) -> Dict[str, Any]:
        """
        Wait for job completion via MQTT notification (event-based)

        This method subscribes to MQTT topic for the specific user_id and waits
        for a "finished" status message. More efficient than polling.

        Args:
            user_id: User ID to wait for
            mqtt_broker: MQTT broker URL (default: from environment)
            max_wait_time: Maximum time to wait in seconds (default: None - no timeout)
            timeout: MQTT connection timeout in seconds (default: None - no timeout)

        Returns:
            Dictionary containing job results

        Raises:
            FreeCADProcessingError: If job fails or MQTT connection fails
        """
        import paho.mqtt.client as mqtt
        from src.services.mqtt_config import get_mqtt_config

        config = get_mqtt_config()

        # Override broker if provided
        if mqtt_broker:
            parts = mqtt_broker.replace("mqtt://", "").split(":")
            broker_host = parts[0]
            broker_port = int(parts[1]) if len(parts) > 1 else 1883
        else:
            broker_host = config.broker_host
            broker_port = config.broker_port

        # State tracking
        job_completed = {'status': None, 'data': None, 'error': None}

        def on_connect(client, userdata, flags, rc):
            if rc == 0:
                # Subscribe to status topic for this user
                topic = f"freecad/status/{user_id}"
                client.subscribe(topic, qos=1)
                logger.info(f"[MQTT] Subscribed to {topic} for user {user_id}")
            else:
                job_completed['error'] = f"MQTT connection failed with code {rc}"

        def on_message(client, userdata, msg):
            try:
                # Validate that message is for the correct user_id
                topic_parts = msg.topic.split('/')
                if len(topic_parts) < 3 or topic_parts[2] != user_id:
                    logger.debug(f"[MQTT] Ignoring message from topic {msg.topic} (expected user_id={user_id})")
                    return

                import json
                data = json.loads(msg.payload.decode())
                status = data.get('status', '').lower()

                logger.debug(f"[MQTT] Received message for {user_id}: {status}")

                if status in ['finished', 'completed', 'success', 'complete']:
                    job_completed['status'] = 'completed'
                    job_completed['data'] = data
                    client.disconnect()
                elif status in ['error', 'failed']:
                    job_completed['status'] = 'failed'
                    # Extract error information from new format
                    error_obj = data.get('error', {})
                    if isinstance(error_obj, dict):
                        # New format: extract specific_exception or error_hint
                        error_msg = error_obj.get('specific_exception') or error_obj.get('error_hint') or data.get('message', 'Job failed')
                    else:
                        # Fallback for old format
                        error_msg = error_obj if error_obj else data.get('message', 'Job failed')
                    job_completed['error'] = error_msg
                    client.disconnect()
                elif status in ['partial_success', 'warning']:
                    logger.warning(f"[MQTT] ⚠️ Partial success / timeout for {user_id}: {data.get('message', '')}")
                    job_completed['status'] = 'failed'
                    job_completed['error'] = f"TIMEOUT:{data.get('message', 'Job completed with partial success')}"
                    client.disconnect()
            except Exception as e:
                logger.error(f"[MQTT] Error processing message: {e}")
                job_completed['error'] = str(e)
                client.disconnect()

        # Create MQTT client
        client = mqtt.Client(client_id=f"freecad_wait_{user_id}_{int(time.time())}")
        client.on_connect = on_connect
        client.on_message = on_message

        try:
            logger.info(f"[MQTT] Connecting to {broker_host}:{broker_port} for user {user_id}")
            if timeout is not None:
                client.connect(broker_host, broker_port, timeout)
            else:
                client.connect(broker_host, broker_port)
            client.loop_start()

            # Wait for completion (no timeout)
            start_time = time.time()
            while True:
                if job_completed['status'] == 'completed':
                    logger.info(f"[MQTT] Job completed for user {user_id}")
                    # Download results using auto_download=True
                    return self.get_job_result(user_id, auto_download=True)
                elif job_completed['status'] == 'failed':
                    error_msg = job_completed['error'] or 'Job failed'
                    logger.error(f"[MQTT] Job failed for user {user_id}: {error_msg}")
                    raise FreeCADProcessingError(f"Job failed: {error_msg}")
                elif job_completed['error']:
                    raise FreeCADConnectionError(f"MQTT error: {job_completed['error']}")

                time.sleep(0.5)

        except (FreeCADProcessingError, FreeCADConnectionError):
            raise
        except Exception as e:
            logger.error(f"[MQTT] Error waiting for completion: {e}")
            raise FreeCADConnectionError(f"MQTT wait error: {e}")
        finally:
            try:
                client.loop_stop()
                client.disconnect()
            except:
                pass


import asyncio
import aiohttp

class AsyncFreeCADClient:
    """Async version of FreeCAD Remote Client for non-blocking operations"""

    def __init__(self, base_url: str = None, max_retries: int = 3, retry_delay: float = 1.0, max_concurrent: int = 20):
        """
        Initialize the async remote client

        Args:
            base_url: Base URL of the remote FreeCAD server
            max_retries: Maximum number of retry attempts for failed requests
            retry_delay: Initial delay between retries in seconds
            max_concurrent: Maximum number of concurrent requests
        """
        # Get base URL from environment variable if not provided
        if base_url is None:
            base_url = os.getenv("server_freecad", "https://toleryfreecad.vm.dfm-europe.com")
            if base_url:
                logger.debug(f"[FreeCAD] Using server URL: {base_url}")
            else:
                logger.warning(f"[FreeCAD] 'server_freecad' env variable not set, using default URL")

        self.base_url = base_url.rstrip('/')
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.session = None  # Will be initialized in __aenter__
        self.semaphore = asyncio.Semaphore(max_concurrent)

        logger.debug(f"[FreeCAD] Client initialized | url={self.base_url} | max_concurrent={max_concurrent}")

    async def __aenter__(self):
        """Async context manager entry"""
        # Set timeout for file downloads (5 minutes for large JSON files)
        timeout = aiohttp.ClientTimeout(total=300, connect=10, sock_read=60)
        self.session = aiohttp.ClientSession(
            timeout=timeout,
            headers={
                'User-Agent': 'Tolery-API-AI/1.0',
                'Accept': 'application/json'
            }
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        if self.session:
            await self.session.close()

    async def check_server_health(self) -> bool:
        """
        Async check if the FreeCAD server is available and responding

        Returns:
            bool: True if server is healthy, False otherwise
        """
        try:
            health_url = f"{self.base_url}/health"
            async with self.session.get(health_url) as response:
                if response.status == 200:

                    return True
                else:
                    logger.warning(f"FreeCAD server health check failed with status code: {response.status}")
                    return False

        except aiohttp.ClientConnectorError:
            logger.error("FreeCAD server is not reachable - connection failed (async)")
            return False
        except asyncio.TimeoutError:
            logger.error("FreeCAD server health check timed out (async)")
            return False
        except Exception as e:
            logger.error(f"Unexpected error during FreeCAD server health check (async): {str(e)}")
            return False

    async def generate(self, script_path: str, user_id: str, auto_download: bool = False, metadata_path: str = None, priority: int = 0) -> Dict[str, Any]:
        """
        Async generate FreeCAD model using remote server

        This method:
        1. Validates the script file exists
        2. Sends file + user_id to server's /freecad/generate endpoint
        3. Optionally sends metadata file for threaded holes detection
        4. Verifies the response contains job_id and user_id
        5. Returns job acknowledgment for MQTT listening

        Args:
            script_path: Path to the FreeCAD Python script (REQUIRED)
            user_id: User ID for job management and MQTT listening (REQUIRED)
            auto_download: DEPRECATED - Server always returns immediately
            metadata_path: Optional path to threaded holes metadata JSON file
            priority: FreeCAD queue priority from 0 to 100

        Returns:
            Dictionary with job acknowledgment containing:
            - job_id: Unique job identifier
            - user_id: User ID for MQTT listening
            - status: Job status (usually 'queued')
            - mqtt_published: Whether MQTT notification was sent
            - metadata_sent: Whether metadata file was included (if provided)

        Raises:
            FileNotFoundError: If script file doesn't exist
            FreeCADProcessingError: If server response is invalid or missing required fields
        """
        async with self.semaphore:  # Limit concurrent requests
            try:
                # ============================================================
                # STEP 1: VALIDATE SCRIPT FILE EXISTS
                # ============================================================
                if not os.path.exists(script_path):
                    error_msg = f"Script file not found: {script_path}"
                    logger.error(f"[FreeCAD] ❌ {error_msg}")
                    raise FileNotFoundError(error_msg)

                # Log file info (compact)
                file_size = os.path.getsize(script_path)
                filename = _get_filename(script_path)
                logger.debug(f"[FreeCAD] Preparing script | file={filename} | size={file_size}B | user_id={user_id}")

                # ============================================================
                # STEP 2: PREPARE REQUEST WITH FILE + USER_ID + METADATA
                # ============================================================
                url = f"{self.base_url}/freecad/generate"

                # Read file content completely before creating FormData
                # This ensures the file content is available when aiohttp sends the request
                with open(script_path, 'rb') as f:
                    file_content = f.read()
                    filename = os.path.basename(script_path)

                # Verify file content was read
                if not file_content:
                    error_msg = f"Script file is empty: {script_path}"
                    logger.error(f"[FreeCAD] ❌ {error_msg}")
                    raise FreeCADProcessingError(error_msg)

                logger.debug(f"[FreeCAD] File read | file={filename} | size={len(file_content)}B")

                # Create form data with proper field ordering
                # Important: Add form fields BEFORE file field (Flask/Werkzeug requirement)
                data = aiohttp.FormData()
                data.add_field('user_id', user_id)
                data.add_field('auto_download', str(auto_download).lower())
                data.add_field('priority', str(priority))

                # Add file content (not file object) to avoid file closing issues
                data.add_field(
                    'file',
                    file_content,
                    filename=filename,
                    content_type='text/x-python'
                )

                # Add metadata file if provided
                metadata_sent = False
                if metadata_path:
                    logger.debug(f"[FreeCAD] Checking metadata | path={metadata_path} | exists={os.path.exists(metadata_path)}")
                    
                    if os.path.exists(metadata_path):
                        try:
                            with open(metadata_path, 'rb') as meta_f:
                                metadata_content = meta_f.read()
                                metadata_filename = os.path.basename(metadata_path)
                            
                            if not metadata_content:
                                logger.warning(f"[FreeCAD] Metadata file is empty: {metadata_path}")
                            else:
                                # CRITICAL: Field name must be 'metadata_file' (not 'metadata')
                                # to match FreeCAD server API expectation
                                data.add_field(
                                    'metadata_file',  # ← Changed from 'metadata' to 'metadata_file'
                                    metadata_content,
                                    filename=metadata_filename,
                                    content_type='application/json'
                                )
                                metadata_sent = True
                                logger.debug(f"[FreeCAD] Metadata included | file={metadata_filename} | size={len(metadata_content)}B")
                        except Exception as e:
                            logger.warning(f"[FreeCAD] Failed to read metadata file: {e}")
                    else:
                        logger.warning(f"[FreeCAD] Metadata file not found: {metadata_path}")

                logger.info(f"[FreeCAD] Sending request | user_id={user_id} | file={filename} | metadata={'yes' if metadata_sent else 'no'} | priority={priority}")

                # ============================================================
                # STEP 3: SEND REQUEST TO SERVER
                # ============================================================
                async with self.session.post(url, data=data) as response:
                        response_text = await response.text()
                        logger.debug(f"[FreeCAD] Response received | status={response.status} | size={len(response_text)}B")

                        # Handle different HTTP status codes
                        if response.status == 503:
                            raise FreeCADServerNotAvailableError("FreeCAD server is temporarily unavailable (503)")
                        elif response.status >= 500:
                            raise FreeCADProcessingError(f"FreeCAD server internal error (status: {response.status})")
                        elif response.status == 404:
                            raise FreeCADConnectionError(f"FreeCAD server endpoint not found (404): {url}")
                        elif response.status >= 400:
                            raise FreeCADProcessingError(f"FreeCAD server request error (status: {response.status})")

                        response.raise_for_status()

                        # ============================================================
                        # STEP 4: PARSE AND VALIDATE SERVER RESPONSE
                        # ============================================================
                        import json as json_module
                        try:
                            job_ack = json_module.loads(response_text)
                            logger.debug(f"[FreeCAD] Response parsed | status={job_ack.get('status')} | user_id={job_ack.get('user_id')}")
                        except json_module.JSONDecodeError as e:
                            error_msg = f"Failed to parse server response as JSON: {response_text[:500]}"
                            logger.error(f"[FreeCAD] ❌ {error_msg}")
                            raise FreeCADProcessingError(error_msg)

                        # ============================================================
                        # STEP 5: VERIFY RESPONSE CONTAINS REQUIRED FIELDS
                        # ============================================================



                        # Check user_id
                        if not job_ack.get('user_id'):
                            error_msg = f"Server response missing user_id: {job_ack}"
                            logger.error(f"[FreeCAD] ❌ {error_msg}")
                            raise FreeCADProcessingError(error_msg)

                        # Verify user_id matches what we sent
                        if job_ack.get('user_id') != user_id:
                            error_msg = f"Server returned different user_id. Expected: {user_id}, Got: {job_ack.get('user_id')}"
                            logger.error(f"[FreeCAD] ❌ {error_msg}")
                            raise FreeCADProcessingError(error_msg)

                        # Check status must be 'queued'
                        response_status = job_ack.get('status')
                        if response_status != 'queued':
                            error_msg = f"Server returned invalid status. Expected: 'queued', Got: '{response_status}'. Response: {job_ack}"
                            logger.error(f"[FreeCAD] ❌ {error_msg}")
                            raise FreeCADProcessingError(error_msg)

                        # Check MQTT must be published successfully
                        mqtt_published = job_ack.get('mqtt_published', False)
                        if not mqtt_published:
                            error_msg = f"Server did not publish MQTT message. Job may not have been queued. Response: {job_ack}"
                            logger.error(f"[FreeCAD] ❌ {error_msg}")
                            raise FreeCADProcessingError(error_msg)

                        # ============================================================
                        # STEP 6: VALIDATE FILES RECEIVED BY SERVER
                        # ============================================================
                        files_received = job_ack.get('files_received', {})
                        
                        # Check if Python script was received
                        python_script_info = files_received.get('python_script')
                        if not python_script_info:
                            logger.warning(f"[FreeCAD] ⚠️ Server response missing 'python_script' info")
                        else:
                            script_filename = python_script_info.get('filename', 'unknown')
                            script_size = python_script_info.get('size_bytes', 0)
                            logger.debug(f"[FreeCAD] Server received script | file={script_filename} | size={script_size}B")
                        
                        # Check if metadata was received (if we sent it)
                        metadata_info = files_received.get('metadata')
                        if metadata_sent:
                            # We sent metadata, verify server received it
                            if not metadata_info:
                                logger.error(
                                    f"[FreeCAD] ❌ METADATA VERIFICATION FAILED | "
                                    f"We sent metadata but server did not acknowledge receiving it | "
                                    f"files_received={files_received}"
                                )
                                raise FreeCADProcessingError(
                                    "Server did not receive metadata file. "
                                    "This may cause threaded holes to not be detected correctly."
                                )
                            else:
                                # Server received metadata - log details
                                metadata_filename = metadata_info.get('filename', 'unknown')
                                metadata_size = metadata_info.get('size_bytes', 0)
                                threaded_count = metadata_info.get('threaded_holes_count', 0)
                                logger.debug(f"[FreeCAD] Server received metadata | file={metadata_filename} | threaded_holes={threaded_count}")
                        else:
                            # We didn't send metadata
                            if metadata_info:
                                logger.warning(
                                    f"[FreeCAD] ⚠️ Server reports receiving metadata but we didn't send any | "
                                    f"metadata_info={metadata_info}"
                                )
                            else:
                                logger.debug(f"[FreeCAD] No metadata sent or received (as expected)")

                        # ============================================================
                        # STEP 7: LOG SUCCESS AND RETURN
                        # ============================================================
                        logger.info(
                            f"[FreeCAD] ✅ Job submitted successfully | "
                            f"user_id={user_id} | "
                            f"status={job_ack.get('status')} | "
                            f"mqtt={job_ack.get('mqtt_published', False)} | "
                            f"metadata_received={'yes' if metadata_info else 'no'}"
                        )
                        return job_ack

            except aiohttp.ClientConnectorError as e:
                logger.error(f"[FreeCAD] ❌ Connection failed | url={self.base_url} | error={e}")
                raise FreeCADConnectionError(f"Unable to connect to FreeCAD server at {self.base_url}: {e}")
            except aiohttp.ClientResponseError as e:
                logger.error(f"[FreeCAD] ❌ HTTP error | status={e.status} | error={e}")
                raise FreeCADProcessingError(f"HTTP error from FreeCAD server: {e}")
            except (FreeCADConnectionError, FreeCADProcessingError, FreeCADServerNotAvailableError):
                # Re-raise our custom exceptions
                raise
            except Exception as e:
                logger.error(f"[FreeCAD] ❌ Unexpected error | error={e}")
                raise FreeCADProcessingError(f"Unexpected error during FreeCAD generation: {e}")

    async def get_execution_status_async(self, user_id: str) -> Dict[str, Any]:
        """
        Async get detailed execution status and progress from FreeCAD server

        Args:
            user_id: User ID to get specific job status

        Returns:
            Dictionary containing detailed execution status
        """
        if not user_id:
            raise FreeCADProcessingError("user_id is required for execution status check")

        try:
            url = f"{self.base_url}/freecad/status/{user_id}"
            logger.debug(f"Getting execution status for user {user_id} (async)")

            async with self.session.get(url) as response:
                # Handle different HTTP status codes
                if response.status == 404:
                    raise FreeCADProcessingError(f"No execution status found for user {user_id}")
                elif response.status >= 500:
                    raise FreeCADProcessingError(f"FreeCAD server internal error (status: {response.status})")
                elif response.status >= 400:
                    raise FreeCADProcessingError(f"FreeCAD server request error (status: {response.status})")

                response.raise_for_status()

                status_data = await response.json()

                # Normalize the response format
                normalized_status = {
                    'status': status_data.get('status', 'unknown'),
                    'progress': status_data.get('progress', 0),
                    'current_process': status_data.get('current_process', status_data.get('message', 'No process information')),
                    'message': status_data.get('message', ''),
                    'timestamp': status_data.get('timestamp', datetime.now().isoformat()),
                    'user_id': status_data.get('user_id', user_id),
                    'execution_time': status_data.get('execution_time', 0),
                    'error': status_data.get('error'),
                    'data': status_data.get('data'),
                    'server_info': {
                        'server_url': self.base_url,
                        'endpoint_used': url
                    }
                }

                logger.debug(f"Execution status retrieved (async): {normalized_status['status']} - {normalized_status['progress']}%")

                return normalized_status

        except aiohttp.ClientConnectorError as e:
            logger.error(f"Connection to FreeCAD server failed during status check (async): {e}")
            raise FreeCADConnectionError(f"Unable to connect to FreeCAD server for status check: {e}")
        except (FreeCADConnectionError, FreeCADProcessingError):
            raise
        except Exception as e:
            logger.error(f"Unexpected error during status check (async): {e}")
            raise FreeCADProcessingError(f"Unexpected error during status check: {e}")

    async def wait_for_mqtt_completion_async(self, user_id: str, max_duration: int = None,
                                             progress_callback=None) -> Dict[str, Any]:
        """
        Wait for FreeCAD job completion via MQTT (event-driven, no polling)

        **IMPORTANT**: This method ONLY listens to user_id, NOT job_id.
        The MQTT topics are:
        - freecad/progress/{user_id} - for progress updates
        - freecad/status/{user_id} - for completion/failure notifications

        This is the NEW recommended approach - uses MQTT for real-time notifications
        instead of polling the /freecad/status endpoint.

        Args:
            user_id: User ID to monitor (REQUIRED - used for MQTT topic subscription)
            max_duration: Maximum wait time in seconds (default: None - no timeout)
            progress_callback: Optional async callback function(status_dict) for progress updates

        Returns:
            Dictionary containing final execution result and progress history:
            {
                'success': True/False,
                'final_status': {...},  # Final status data from MQTT
                'total_time': 123.45,   # Total execution time in seconds
                'progress_history': [...],  # List of progress updates
                'error': 'error message'  # Only present if failed
            }

        Raises:
            FreeCADProcessingError: If job fails or MQTT connection fails
        """
        import paho.mqtt.client as mqtt
        from src.services.mqtt_config import get_mqtt_config

        start_time = time.time()
        progress_history = []
        job_completed = {'status': None, 'result': None, 'error': None}

        mqtt_config = get_mqtt_config()

        logger.info(f"[MQTT] Starting listener | user_id={user_id} | broker={mqtt_config.broker_host}:{mqtt_config.broker_port}")

        def on_connect(client, userdata, flags, rc):
            """Callback when MQTT client connects to broker"""
            if rc == 0:
                # Subscribe to MQTT topics for this user_id
                progress_topic = f"freecad/progress/{user_id}"
                status_topic = f"freecad/status/{user_id}"

                client.subscribe(progress_topic, qos=mqtt_config.qos_level)
                client.subscribe(status_topic, qos=mqtt_config.qos_level)

                logger.info(f"[MQTT] Connected | user_id={user_id} | qos={mqtt_config.qos_level}")
            else:
                error_msg = f"MQTT connection failed with code: {rc}"
                logger.error(f"[MQTT] ❌ {error_msg}")
                job_completed['error'] = error_msg

        def on_message(client, userdata, msg):
            """Callback when MQTT message is received"""
            try:
                # Validate that message is for the correct user_id
                topic_parts = msg.topic.split('/')
                if len(topic_parts) < 3 or topic_parts[2] != user_id:
                    logger.debug(f"[MQTT] Ignoring message from topic {msg.topic} (expected user_id={user_id})")
                    return

                data = json.loads(msg.payload.decode())
                topic_type = topic_parts[1]  # 'progress' or 'status'

                logger.debug(
                    f"[MQTT] 📨 Message received | "
                    f"topic={msg.topic} | "
                    f"type={topic_type} | "
                    f"user_id={user_id}"
                )

                # ============================================================
                # HANDLE PROGRESS UPDATES
                # ============================================================
                if topic_type == 'progress':
                    progress = data.get('progress', 0)
                    status = data.get('status', 'unknown')
                    message = data.get('message', '')

                    progress_history.append({
                        'timestamp': datetime.now().isoformat(),
                        'elapsed_time': round(time.time() - start_time, 2),
                        'progress': progress,
                        'status': status,
                        'message': message
                    })

                    # Only log progress milestones (0%, 25%, 50%, 75%, 100%) or important status changes
                    if progress in [0, 25, 50, 75, 100] or status in ['running', 'complete', 'failed']:
                        logger.info(f"[MQTT] Progress | user_id={user_id} | {progress}% | {status} | {message[:50]}")
                    else:
                        logger.debug(f"[MQTT] Progress | user_id={user_id} | {progress}% | {status}")

                    # Call progress callback if provided
                    if progress_callback:
                        try:
                            loop = asyncio.get_event_loop()
                            if loop.is_running():
                                asyncio.run_coroutine_threadsafe(
                                    progress_callback({
                                        'progress': progress,
                                        'status': status,
                                        'current_process': message
                                    }),
                                    loop
                                )
                        except Exception as cb_error:
                            logger.debug(f"[MQTT] Progress callback error: {cb_error}")

                # ============================================================
                # HANDLE STATUS UPDATES (COMPLETION/FAILURE)
                # ============================================================
                if topic_type == 'status':
                    status = data.get('status', '')
                    message = data.get('message', '')

                    logger.info(f"[MQTT] Status | user_id={user_id} | {status} | {message[:60]}")

                    # Check for successful completion
                    if status in ['finished', 'completed', 'success', 'complete']:
                        elapsed = round(time.time() - start_time, 2)
                        logger.info(f"[MQTT] Completed | user_id={user_id} | time={elapsed}s")
                        job_completed['status'] = 'completed'
                        job_completed['result'] = data
                        client.disconnect()

                    # Check for failure
                    elif status in ['failed', 'error']:
                        # Extract and LOG detailed error information from new format
                        error_obj = data.get('error', {})
                        details_obj = data.get('details', {})

                        # Normalize error_obj to dict
                        if isinstance(error_obj, str):
                            try:
                                error_obj = json.loads(error_obj)
                            except (json.JSONDecodeError, TypeError):
                                error_obj = {"error_message": error_obj}
                        if not isinstance(error_obj, dict):
                            error_obj = {"error_message": str(error_obj)}
                        
                        # Normalize details_obj to dict
                        if isinstance(details_obj, str):
                            try:
                                details_obj = json.loads(details_obj)
                            except (json.JSONDecodeError, TypeError):
                                details_obj = {}
                        if not isinstance(details_obj, dict):
                            details_obj = {}

                        # Pull useful fields for logging from both error and details
                        specific_exception = (
                            error_obj.get('specific_exception')
                            or error_obj.get('error_message')
                            or details_obj.get('specific_exception')
                        )
                        error_hint = (
                            error_obj.get('error_hint')
                            or details_obj.get('error_hint')
                        )
                        stdout_tail = error_obj.get('stdout_tail') or details_obj.get('stdout_tail')
                        stderr_tail = error_obj.get('stderr_tail') or details_obj.get('stderr_tail')

                        # Log full technical details for developers/admins only
                        try:
                            logger.error(
                                "[MQTT] ❌ Job failed | "
                                "user_id=%s | status=%s | message=%s | "
                                "specific_exception=%s | error_hint=%s",
                                user_id, status, message, specific_exception, error_hint
                            )
                            # Log tails if present
                            if stdout_tail:
                                logger.error(
                                    "[MQTT] stdout_tail (last %d lines):\n%s",
                                    len(stdout_tail) if isinstance(stdout_tail, list) else 0,
                                    "\n".join(stdout_tail) if isinstance(stdout_tail, list) else str(stdout_tail)
                                )
                            if stderr_tail:
                                logger.error(
                                    "[MQTT] stderr_tail (last %d lines):\n%s",
                                    len(stderr_tail) if isinstance(stderr_tail, list) else 0,
                                    "\n".join(stderr_tail) if isinstance(stderr_tail, list) else str(stderr_tail)
                                )
                        except Exception as log_err:
                            logger.debug(f"[MQTT] Error while logging technical details: {log_err}")

                        # Detect if this "failed" is actually a timeout
                        # Server sometimes reports timeouts as status='failed' instead of 'partial_success'
                        all_error_text = f"{message} {specific_exception or ''} {error_hint or ''}".lower()
                        is_timeout = any(kw in all_error_text for kw in [
                            'timeout', 'timed out', 'timed_out', 'time out',
                            'exceeded', 'deadline'
                        ])
                        is_partial = 'partial_success' in all_error_text

                        job_completed['status'] = 'failed'
                        if is_timeout:
                            logger.warning(f"[MQTT] ⏱️ Timeout detected in failed status | user_id={user_id}")
                            job_completed['error'] = f"TIMEOUT:{message}"
                        elif is_partial:
                            logger.warning(f"[MQTT] ⚠️ Partial success detected in failed status | user_id={user_id}")
                            job_completed['error'] = f"PARTIAL_SUCCESS:{message}"
                        else:
                            # Genuine execution failure → prefer specific error over generic message
                            # Priority: specific_exception > error_hint > message > fallback
                            actual_error = specific_exception or error_hint or message or 'EXECUTION_FAILED'
                            job_completed['error'] = actual_error
                        client.disconnect()

                    # Check for partial success / warning
                    elif status in ['partial_success', 'warning']:
                        elapsed = round(time.time() - start_time, 2)
                        # PDF generation is optional: STEP/OBJ export already succeeded by this
                        # point, so a PDF-only failure must not fail the whole job (see 104.6).
                        if 'pdf' in message.lower():
                            logger.warning(
                                f"[MQTT] ⚠️ PDF generation failed (optional, ignored) | "
                                f"user_id={user_id} | status={status} | "
                                f"message={message} | time={elapsed}s"
                            )
                            job_completed['status'] = 'completed'
                            job_completed['result'] = data
                        else:
                            logger.warning(
                                f"[MQTT] ⚠️ Partial success / warning | "
                                f"user_id={user_id} | status={status} | "
                                f"message={message} | time={elapsed}s"
                            )
                            job_completed['status'] = 'failed'
                            job_completed['error'] = f"PARTIAL_SUCCESS:{message}"
                        client.disconnect()

            except Exception as e:
                logger.error(f"[MQTT] ❌ Error processing message | user_id={user_id} | error={e}")

        # ============================================================
        # CREATE AND CONFIGURE MQTT CLIENT
        # ============================================================
        mqtt_client = mqtt.Client(client_id=f"{mqtt_config.client_id}_wait_{user_id}")
        mqtt_client.on_connect = on_connect
        mqtt_client.on_message = on_message

        if mqtt_config.username and mqtt_config.password:
            mqtt_client.username_pw_set(mqtt_config.username, mqtt_config.password)
            logger.debug(f"[MQTT] Authentication configured for user {user_id}")

        try:
            # ============================================================
            # CONNECT TO MQTT BROKER
            # ============================================================
            logger.debug(f"[MQTT] Connecting | broker={mqtt_config.broker_host}:{mqtt_config.broker_port} | user_id={user_id}")
            mqtt_client.connect(mqtt_config.broker_host, mqtt_config.broker_port, keepalive=60)

            # Start MQTT loop in background
            mqtt_client.loop_start()
            logger.debug(f"[MQTT] Loop started | user_id={user_id}")

            # ============================================================
            # WAIT FOR COMPLETION OR TIMEOUT
            # ============================================================
            logger.info(
                f"[MQTT] ⏳ Waiting for job completion | "
                f"user_id={user_id} | "
                f"max_duration={'unlimited' if max_duration is None else f'{max_duration}s'}"
            )

            # Fallback polling interval: check HTTP status every N seconds
            fallback_poll_interval = 15  # seconds
            last_fallback_check = 0

            while job_completed['status'] is None and job_completed['error'] is None:
                elapsed = time.time() - start_time

                # ============================================================
                # FALLBACK: Periodically check HTTP status endpoint
                # Prevents indefinite freezing if MQTT message is missed
                # (race condition, broker issue, or topic mismatch)
                # ============================================================
                if elapsed - last_fallback_check >= fallback_poll_interval:
                    last_fallback_check = elapsed
                    try:
                        logger.debug(f"[MQTT] Fallback HTTP check | user_id={user_id} | elapsed={elapsed:.0f}s")
                        fallback_status = await self.get_execution_status_async(user_id)
                        fb_status = fallback_status.get('status', 'unknown')
                        fb_message = fallback_status.get('message', '')

                        if fb_status in ['failed', 'error']:
                            logger.warning(
                                f"[MQTT] ⚠️ Fallback detected failure via HTTP | "
                                f"user_id={user_id} | status={fb_status} | message={fb_message[:100]}"
                            )
                            
                            # Try to extract specific FreeCAD error from error/data fields
                            fb_error_obj = fallback_status.get('error') or {}
                            fb_data_obj = fallback_status.get('data') or {}
                            
                            # Normalize to dict
                            if isinstance(fb_error_obj, str):
                                try:
                                    fb_error_obj = json.loads(fb_error_obj)
                                except (json.JSONDecodeError, TypeError):
                                    fb_error_obj = {}
                            if not isinstance(fb_error_obj, dict):
                                fb_error_obj = {}
                            if not isinstance(fb_data_obj, dict):
                                fb_data_obj = {}
                            
                            # Extract actual FreeCAD error from various possible fields
                            specific_error = (
                                fb_error_obj.get('specific_exception')
                                or fb_error_obj.get('error_hint')
                                or fb_error_obj.get('error_message')
                                or fb_data_obj.get('specific_exception')
                                or fb_data_obj.get('error_hint')
                            )
                            
                            if specific_error:
                                logger.info(f"[MQTT] Fallback extracted specific error: {specific_error[:100]}")
                            
                            # Build detailed error message for auto-retry
                            detailed_error = specific_error or fb_message or 'EXECUTION_FAILED'
                            
                            # Check if it's a timeout or partial success
                            all_fb_text = f"{fb_message} {detailed_error}".lower()
                            is_fb_timeout = any(kw in all_fb_text for kw in [
                                'timeout', 'timed out'
                            ])
                            is_fb_partial = 'partial_success' in all_fb_text
                            # PDF generation is optional: a partial_success/warning caused only by
                            # a failed PDF export must not fail the whole job (see 104.6).
                            is_fb_pdf_only = is_fb_partial and 'pdf' in all_fb_text

                            if is_fb_pdf_only:
                                logger.warning(
                                    f"[MQTT] ⚠️ Fallback: PDF generation failed (optional, ignored) | "
                                    f"user_id={user_id} | message={fb_message[:100]}"
                                )
                                job_completed['status'] = 'completed'
                                job_completed['result'] = fallback_status
                                try:
                                    mqtt_client.disconnect()
                                except:
                                    pass
                                break

                            job_completed['status'] = 'failed'
                            if is_fb_timeout:
                                job_completed['error'] = f"TIMEOUT:{fb_message}"
                            elif is_fb_partial:
                                job_completed['error'] = f"PARTIAL_SUCCESS:{fb_message}"
                            else:
                                job_completed['error'] = detailed_error
                            try:
                                mqtt_client.disconnect()
                            except:
                                pass
                            break

                        elif fb_status in ['completed', 'success', 'finished', 'complete']:
                            logger.warning(
                                f"[MQTT] ⚠️ Fallback detected completion via HTTP | "
                                f"user_id={user_id} | status={fb_status}"
                            )
                            job_completed['status'] = 'completed'
                            job_completed['result'] = fallback_status
                            try:
                                mqtt_client.disconnect()
                            except:
                                pass
                            break

                    except Exception as fb_error:
                        logger.debug(f"[MQTT] Fallback check failed (will retry): {fb_error}")

                await asyncio.sleep(0.5)  # Check every 500ms

            # ============================================================
            # CLEANUP AND DISCONNECT
            # ============================================================
            mqtt_client.loop_stop()
            mqtt_client.disconnect()
            logger.info(f"[MQTT] 🔌 Disconnected from broker | user_id={user_id}")

            # ============================================================
            # CHECK RESULT AND RETURN
            # ============================================================
            if job_completed['error']:
                logger.error(
                    f"[MQTT] ❌ Job failed | "
                    f"user_id={user_id} | "
                    f"error={job_completed['error']}"
                )
                raise FreeCADProcessingError(job_completed['error'])

            if job_completed['status'] == 'completed':
                total_time = time.time() - start_time
                logger.info(f"[MQTT] Completed | user_id={user_id} | time={total_time:.2f}s | updates={len(progress_history)}")
                return {
                    'success': True,
                    'final_status': job_completed['result'],
                    'total_time': round(total_time, 2),
                    'progress_history': progress_history
                }
            else:
                total_time = time.time() - start_time
                logger.warning(f"[MQTT] Unknown status | user_id={user_id} | status={job_completed.get('status')} | time={total_time:.2f}s")
                return {
                    'success': False,
                    'final_status': job_completed.get('result', {}),
                    'total_time': round(total_time, 2),
                    'progress_history': progress_history,
                    'error': job_completed.get('error', 'Unknown error')
                }

        except Exception as e:
            # ============================================================
            # EXCEPTION CLEANUP
            # ============================================================
            try:
                mqtt_client.loop_stop()
                mqtt_client.disconnect()
                logger.info(f"[MQTT] 🔌 Disconnected (exception cleanup) | user_id={user_id}")
            except Exception as cleanup_error:
                logger.warning(f"[MQTT] ⚠️ Cleanup error | user_id={user_id} | error={cleanup_error}")
            raise

    async def monitor_execution_progress_async(self, user_id: str, poll_interval: int = 2,
                                               max_duration: int = None,
                                               progress_callback=None) -> Dict[str, Any]:
        """
        DEPRECATED: Use wait_for_mqtt_completion_async() instead

        Async monitor FreeCAD execution progress with real-time updates via POLLING

        This method polls the /freecad/status endpoint which is the OLD approach.
        The NEW approach is to use wait_for_mqtt_completion_async() which uses MQTT
        for real-time event-driven notifications.

        Args:
            user_id: User ID to monitor
            poll_interval: Seconds between status checks (default: 2)
            max_duration: Maximum monitoring duration in seconds (default: None - no timeout)
            progress_callback: Optional async callback function(status_dict) for progress updates

        Returns:
            Dictionary containing final execution result and progress history
        """
        logger.warning(
            f"[DEPRECATED] monitor_execution_progress_async() is deprecated. "
            f"Use wait_for_mqtt_completion_async() for better performance."
        )
        start_time = time.time()
        progress_history = []
        last_progress = -1

        logger.info(f"Starting async execution monitoring for user {user_id}")

        try:
            while True:
                current_time = time.time()
                elapsed_time = current_time - start_time

                # No timeout check - monitor indefinitely

                # Get current status
                try:
                    status = await self.get_execution_status_async(user_id)
                    current_progress = status.get('progress', 0)
                    current_status = status.get('status', 'unknown')
                    current_process = status.get('current_process', '')

                    # Log progress changes
                    if current_progress != last_progress:
                        logger.info(f"Progress update for user {user_id}: {current_progress}% - {current_process}")
                        last_progress = current_progress

                    # Call progress callback if provided
                    if progress_callback:
                        await progress_callback(status)

                    # Add to history
                    progress_history.append({
                        'timestamp': datetime.now().isoformat(),
                        'elapsed_time': round(elapsed_time, 2),
                        'progress': current_progress,
                        'status': current_status,
                        'process': current_process
                    })

                    # Check if execution is complete
                    if current_status in ['completed', 'success', 'finished']:
                        logger.info(f"Execution completed for user {user_id} after {elapsed_time:.2f}s")
                        return {
                            'success': True,
                            'final_status': status,
                            'total_time': round(elapsed_time, 2),
                            'progress_history': progress_history
                        }

                    # Check if execution failed
                    if current_status in ['error', 'failed', 'cancelled']:
                        logger.error(f"Execution failed for user {user_id}: {status.get('message', 'Unknown error')}")
                        return {
                            'success': False,
                            'final_status': status,
                            'total_time': round(elapsed_time, 2),
                            'progress_history': progress_history,
                            'error': status.get('message', 'Execution failed')
                        }

                except FreeCADConnectionError as e:
                    logger.warning(f"Temporary error during monitoring: {e}")
                    if elapsed_time > 30:
                        raise

                # Wait before next poll
                await asyncio.sleep(poll_interval)

        except asyncio.CancelledError:
            logger.info(f"Monitoring cancelled for {user_id}")
            return {
                'success': False,
                'final_status': {'status': 'cancelled', 'message': 'Monitoring cancelled'},
                'total_time': round(time.time() - start_time, 2),
                'progress_history': progress_history
            }
        except Exception as e:
            logger.error(f"Error during execution monitoring (async): {e}")
            return {
                'success': False,
                'final_status': {'status': 'error', 'message': str(e)},
                'total_time': round(time.time() - start_time, 2),
                'progress_history': progress_history,
                'error': str(e)
            }

    async def get_job_result_async(self, user_id: str, auto_download: bool = True, raw: bool = False, expect_obj: bool = True) -> Dict[str, Any]:
        """
        Async get job result for a user

        Args:
            user_id: User ID to get results for
            auto_download: If True, server will copy files to outputs/code/cad_outputs_generated/
                          and return download links. If False, only return file information.

        Returns:
            Dictionary containing job results with file information.
            If auto_download=True, includes local_path for each file.

        Raises:
            FreeCADConnectionError: When unable to connect to server
            FreeCADTimeoutError: When request times out
            FreeCADProcessingError: When server encounters processing errors (including 404)
        """
        try:
            url = f"{self.base_url}/freecad/result/{user_id}"
            # Convert boolean to string for query parameter
            params = {'auto_download': str(auto_download).lower()}

            logger.info(
                f"[RESULT] 📥 Requesting job result | "
                f"user_id={user_id} | "
                f"url={url} | "
                f"auto_download={auto_download}"
            )

            async with self.session.get(url, params=params) as response:
                response_text = await response.text()

                logger.debug(f"[RESULT] Response | user_id={user_id} | status={response.status} | size={len(response_text)}B")

                # Handle different HTTP status codes
                if response.status == 404:
                    # Log the full response for debugging
                    logger.error(
                        f"[RESULT] ❌ 404 Not Found | "
                        f"user_id={user_id} | "
                        f"url={url} | "
                        f"response={response_text[:500]}"
                    )
                    raise FreeCADProcessingError(f"Job result not found for user {user_id}")
                elif response.status >= 500:
                    logger.error(
                        f"[RESULT] ❌ Server error | "
                        f"user_id={user_id} | "
                        f"status={response.status} | "
                        f"response={response_text[:500]}"
                    )
                    raise FreeCADProcessingError(f"FreeCAD server internal error (status: {response.status})")
                elif response.status >= 400:
                    logger.error(
                        f"[RESULT] ❌ Client error | "
                        f"user_id={user_id} | "
                        f"status={response.status} | "
                        f"response={response_text[:500]}"
                    )
                    raise FreeCADProcessingError(f"FreeCAD server request error (status: {response.status})")

                response.raise_for_status()

                # Parse JSON response
                import json as json_module
                try:
                    result_data = json_module.loads(response_text)
                except json_module.JSONDecodeError as e:
                    logger.error(
                        f"[RESULT] ❌ Failed to parse JSON | "
                        f"user_id={user_id} | "
                        f"error={e} | "
                        f"response={response_text[:500]}"
                    )
                    raise FreeCADProcessingError(f"Failed to parse server response: {e}")

                # Log result summary
                if auto_download:
                    files = result_data.get('files', [])
                    logger.info(f"[RESULT] Retrieved | user_id={user_id} | files={len(files)}")
                    if raw:
                        # Return the raw file listing without triggering download/validation
                        return result_data
                    # Process the result and download files if available
                    return await self._handle_json_response_async(result_data, user_id, expect_obj=expect_obj)
                else:
                    logger.debug(f"[RESULT] Info retrieved | user_id={user_id} | no_download")
                    return result_data

        except aiohttp.ClientConnectorError as e:
            logger.error(
                f"[RESULT] ❌ Connection failed | "
                f"user_id={user_id} | "
                f"error={e}"
            )
            raise FreeCADConnectionError(f"Unable to connect to FreeCAD server for result retrieval: {e}")
        except (FreeCADConnectionError, FreeCADProcessingError):
            raise
        except Exception as e:
            logger.error(
                f"[RESULT] ❌ Unexpected error | "
                f"user_id={user_id} | "
                f"error={e}"
            )
            raise FreeCADProcessingError(f"Unexpected error during result retrieval: {e}")

    async def _download_file_with_retry(
        self,
        download_url: str,
        local_path: Path,
        file_type: str,
        filename: str,
        user_id: str = None,
        max_retries: int = 3
    ) -> str:
        """
        Download file with retry logic, Content-Length validation, and integrity checks

        Args:
            download_url: URL to download file from
            local_path: Local path to save file to
            file_type: Type of file (step, obj, json, pdf)
            filename: Original filename
            user_id: User ID for logging context
            max_retries: Maximum number of retry attempts

        Returns:
            String path to downloaded file

        Raises:
            Exception: If download fails after all retries or file validation fails
        """
        last_error = None
        user_context = f"user_id={user_id} | " if user_id else ""

        # Only log start on first attempt, failures logged at end
        start_time = time.time()

        for attempt in range(max_retries):
            try:
                async with self.session.get(download_url) as response:
                    response.raise_for_status()

                    # Get expected file size from Content-Length header
                    expected_size = response.headers.get('Content-Length')
                    if expected_size:
                        expected_size = int(expected_size)

                    # Log for JSON files (important for debugging timeout issues)
                    if file_type == 'json' and expected_size:
                        size_mb = expected_size / (1024 * 1024)
                        logger.info(f"[DOWNLOAD] {user_context}Starting {file_type.upper()} download | size={size_mb:.1f}MB | timeout=300s")

                    # Download file in chunks
                    downloaded_size = 0
                    with open(local_path, 'wb') as f:
                        async for chunk in response.content.iter_chunked(8192):
                            f.write(chunk)
                            downloaded_size += len(chunk)

                    # Validate file size matches Content-Length
                    if expected_size and downloaded_size != expected_size:
                        error_msg = f"File size mismatch: expected {expected_size:,} bytes, got {downloaded_size:,} bytes"
                        # Delete incomplete file
                        if os.path.exists(local_path):
                            os.remove(local_path)
                        raise Exception(error_msg)

                    # Verify file exists and has content
                    if not os.path.exists(local_path):
                        raise Exception(f"Downloaded file does not exist: {local_path}")

                    actual_file_size = os.path.getsize(local_path)
                    if actual_file_size == 0:
                        raise Exception(f"Downloaded file is empty: {local_path}")

                    # Success - no logging here, will be logged in summary
                    return str(local_path)

            except Exception as e:
                last_error = e
                error_str = str(e).lower()

                # Delete partial file if it exists
                if os.path.exists(local_path):
                    try:
                        os.remove(local_path)
                    except:
                        pass

                # Log timeout/large file issues
                is_timeout = any(kw in error_str for kw in ['timeout', 'timed out', 'read timed out', 'connection timeout'])
                if is_timeout and file_type == 'json':
                    logger.warning(f"[DOWNLOAD] {user_context}⏱️ JSON download timeout (attempt {attempt+1}/{max_retries}) | May be too large or network issue")

                # If not the last attempt, wait before retrying (exponential backoff)
                if attempt < max_retries - 1:
                    wait_time = 2 ** attempt  # 1s, 2s, 4s
                    await asyncio.sleep(wait_time)
                else:
                    # Final attempt failed - log error with user context
                    error_msg = f"Failed to download {file_type.upper()} '{filename}' after {max_retries} attempts"
                    if is_timeout:
                        error_msg += f" [TIMEOUT: {error_str[:100]}]"
                    else:
                        error_msg += f": {last_error}"
                    logger.error(f"[DOWNLOAD] {user_context}❌ {error_msg}")
                    raise Exception(error_msg)

        # Should never reach here, but just in case
        raise Exception(f"Failed to download {file_type.upper()} file after {max_retries} attempts")

    async def _handle_json_response_async(self, json_response: Dict[str, Any], user_id: str, expect_obj: bool = True) -> Dict[str, Any]:
        """
        Async handle JSON response from server containing file information

        This method now includes:
        - Comprehensive logging of server response
        - Retry logic with exponential backoff for each file download
        - Content-Length validation to detect truncated downloads
        - File integrity checks (existence, non-zero size)
        - Validation that required CAD files (step, obj) are downloaded. JSON/PDF are optional
        - Immediate error raising if any file fails after retries

        Args:
            json_response: JSON response from server
            user_id: User ID for file management

        Returns:
            Dictionary containing processing result with required CAD files

        Raises:
            Exception: If any file download fails or validation fails
        """
        try:
            # Check if response indicates success
            status = json_response.get('status', '')
            if status != 'success':
                error_msg = f'Server returned non-success status: {status}'
                logger.error(f"[FILE_DOWNLOAD] user_id={user_id} | ❌ {error_msg}")
                return {
                    'success': False,
                    'message': error_msg,
                    'response': json_response
                }

            # Check if response contains files
            files_info = json_response.get('files', [])

            logger.info(f"[FILE_DOWNLOAD] Response | user_id={user_id} | status={json_response.get('status')} | files={len(files_info)}")

            if not files_info:
                error_msg = 'Job completed successfully but no files were generated'
                logger.warning(f"[FILE_DOWNLOAD] user_id={user_id} | ⚠️ {error_msg}")
                return {
                    'success': True,
                    'message': error_msg,
                    'response': json_response
                }

            # Log file list (compact)
            file_types = [f.get('type', '?') for f in files_info]
            logger.debug(f"[FILE_DOWNLOAD] Files | user_id={user_id} | types={','.join(file_types)}")

            # Create output directory
            output_dir = Path("outputs/code/cad_outputs_generated")
            output_dir.mkdir(parents=True, exist_ok=True)

            downloaded_files = {}
            file_sizes = {}
            total_size = 0
            download_list = []  # Collect file info for single log message
            prepared_files = []  # Store prepared file data for download

            # Prepare all files and collect info for logging
            for file_info in files_info:
                filename = file_info.get('filename')
                download_url = file_info.get('download_url', '').strip()
                file_type = file_info.get('type', '').lower()

                # If no download_url but has filename, construct it from user_id and filename
                if not download_url and filename:
                    download_url = f"{self.base_url}/freecad/download/{user_id}/{filename}"

                if not filename or not download_url:
                    error_msg = f"File info missing filename or download_url: {file_info}"
                    logger.error(f"[FILE_DOWNLOAD] user_id={user_id} | ❌ {error_msg}")
                    raise Exception(error_msg)

                # Fix localhost URL issue
                if 'localhost' in download_url:
                    import re
                    if download_url.startswith('http://localhost') or download_url.startswith('https://localhost'):
                        download_url = re.sub(r'https?://localhost:\d+', self.base_url, download_url)
                    else:
                        download_url = download_url.replace('localhost', self.base_url.replace('http://', '').replace('https://', ''))

                # Collect file info for logging (include full URL)
                download_list.append({
                    'type': file_type.upper(),
                    'filename': filename,
                    'url': download_url
                })

                # Store prepared data for download
                prepared_files.append({
                    'filename': filename,
                    'download_url': download_url,
                    'file_type': file_type
                })

            # Log all files to download in one message with URLs
            if download_list:
                file_list = ", ".join([f"{f['type']}({f['filename']}): {f['url']}" for f in download_list])
                logger.info(f"[FILE_DOWNLOAD] Downloading {len(download_list)} files | user_id={user_id} | {file_list}")

            # Download each file using retry logic
            for file_data in prepared_files:
                filename = file_data['filename']
                download_url = file_data['download_url']
                file_type = file_data['file_type']

                # Download file with retry logic
                local_path = output_dir / filename

                try:
                    # Use the new retry helper method
                    downloaded_path = await self._download_file_with_retry(
                        download_url=download_url,
                        local_path=local_path,
                        file_type=file_type,
                        filename=filename,
                        user_id=user_id,
                        max_retries=3
                    )

                    downloaded_files[file_type] = downloaded_path
                    file_size = os.path.getsize(downloaded_path) if os.path.exists(downloaded_path) else 0
                    file_sizes[file_type] = file_size
                    total_size += file_size

                except Exception as e:
                    # Critical files (STEP, OBJ) must succeed
                    if file_type in ['step', 'obj']:
                        logger.error(f"[FILE_DOWNLOAD] user_id={user_id} | Failed to download critical file {file_type.upper()}: {e}")
                        raise
                    # Optional files (JSON, PDF) - log and continue
                    else:
                        logger.warning(f"[FILE_DOWNLOAD] user_id={user_id} | Failed to download optional file {file_type.upper()} (will continue): {e}")

            # JSON can take longer to generate on the server when it's large. If it wasn't
            # in the initial file listing, poll the result endpoint a few times before
            # giving up, instead of failing immediately.
            if 'json' not in downloaded_files:
                max_json_checks = 3
                for attempt in range(1, max_json_checks + 1):
                    logger.info(f"[FILE_DOWNLOAD] user_id={user_id} | JSON not ready yet, re-checking ({attempt}/{max_json_checks}) in 5s...")
                    await asyncio.sleep(5)
                    try:
                        retry_result = await self.get_job_result_async(user_id, auto_download=True, raw=True, expect_obj=expect_obj)
                        json_file_info = next(
                            (f for f in retry_result.get('files', []) if f.get('type', '').lower() == 'json'),
                            None
                        )
                        if not json_file_info:
                            continue

                        filename = json_file_info.get('filename')
                        download_url = json_file_info.get('download_url', '').strip()
                        if not download_url and filename:
                            download_url = f"{self.base_url}/freecad/download/{user_id}/{filename}"
                        if 'localhost' in download_url:
                            import re
                            if download_url.startswith('http://localhost') or download_url.startswith('https://localhost'):
                                download_url = re.sub(r'https?://localhost:\d+', self.base_url, download_url)
                            else:
                                download_url = download_url.replace('localhost', self.base_url.replace('http://', '').replace('https://', ''))

                        downloaded_path = await self._download_file_with_retry(
                            download_url=download_url,
                            local_path=output_dir / filename,
                            file_type='json',
                            filename=filename,
                            user_id=user_id,
                            max_retries=3
                        )
                        downloaded_files['json'] = downloaded_path
                        file_size = os.path.getsize(downloaded_path) if os.path.exists(downloaded_path) else 0
                        file_sizes['json'] = file_size
                        total_size += file_size
                        logger.info(f"[FILE_DOWNLOAD] user_id={user_id} | ✅ JSON became available on check {attempt}/{max_json_checks}")
                        break
                    except Exception as e:
                        logger.warning(f"[FILE_DOWNLOAD] user_id={user_id} | JSON check {attempt}/{max_json_checks} failed: {e}")

            # Validate required FreeCAD output files. STEP and JSON are always
            # mandatory. OBJ is mandatory too UNLESS the caller told us this job
            # is a Perforated Sheet (expect_obj=False) -- Perforated Sheet jobs
            # intentionally skip OBJ generation (see templates.py). Every OTHER
            # shape type keeps the old hard requirement, so a real OBJ-generation
            # bug for a non-perforated shape still fails the job loudly instead
            # of silently succeeding without it.
            required_file_types = {'step', 'json'} | ({'obj'} if expect_obj else set())
            optional_file_types = {'pdf'} | (set() if expect_obj else {'obj'})
            downloaded_types = set(downloaded_files.keys())
            missing_required = required_file_types - downloaded_types
            missing_optional = optional_file_types - downloaded_types

            if missing_required:
                error_msg = f"Missing required files: {', '.join(sorted(missing_required))}. Expected {'+'.join(sorted(t.upper() for t in required_file_types))} but got {list(downloaded_types)}"
                logger.error(f"[FILE_DOWNLOAD] user_id={user_id} | ❌ VALIDATION FAILED: {error_msg}")
                raise Exception(error_msg)

            # Log warning if optional PDF/OBJ are missing, but do not fail the export
            if missing_optional:
                logger.warning(f"[FILE_DOWNLOAD] user_id={user_id} | ⚠️ Optional files missing: {', '.join(sorted(missing_optional))} | Proceeding with {', '.join(sorted(downloaded_types))}")

            # All validations passed - Single consolidated log entry
            file_details = ", ".join([f"{ft.upper()}({file_sizes.get(ft, 0):,}B)" for ft in sorted(downloaded_files.keys())])
            logger.info(f"[FILE_DOWNLOAD] Completed | user_id={user_id} | files={len(downloaded_files)}/{len(files_info)} | total={total_size:,}B | {file_details}")

            return {
                'success': True,
                'message': f'Successfully downloaded and validated required CAD files',
                'files': downloaded_files,
                'response': json_response
            }

        except Exception as e:
            error_msg = f"Error handling JSON response: {e}"
            logger.error(f"[FILE_DOWNLOAD] user_id={user_id} | ❌ {error_msg}")

            # Re-raise the exception to propagate it up
            raise Exception(error_msg)


def create_freecad_client(use_async: bool = True, **kwargs) -> AsyncFreeCADClient:
    """
    Factory function to create FreeCAD client (async only)

    Args:
        use_async: Always True (kept for backward compatibility, but sync client is removed)
        **kwargs: Additional arguments passed to client constructor

    Returns:
        AsyncFreeCADClient instance
    """
    # Always return async client (sync client has been removed)
    return AsyncFreeCADClient(**kwargs)
