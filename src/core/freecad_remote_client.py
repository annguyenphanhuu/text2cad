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
from typing import Dict, Any
from datetime import datetime
from dotenv import load_dotenv

try:
    import requests
except Exception:
    requests = None  # Optional: only used in legacy retry decorator

# Load environment variables
load_dotenv()

logger = logging.getLogger(__name__)

#: Minimum percentage advance between two MQTT progress lines on the console.
PROGRESS_LOG_STEP = 25


def _get_filename(path: str) -> str:
    """Extract filename from full path"""
    return os.path.basename(path) if path else ""


# Custom Exception Classes for FreeCAD Server Communication
class FreeCADServerError(Exception):
    """Base exception for FreeCAD server related errors.

    Every raise site sets `code` to the entry of the error table
    (src/core/error_codes.py) that matches the failure exactly. Callers read
    that attribute instead of searching the message for keywords: the message
    is free text, `str(asyncio.TimeoutError())` is empty and matches nothing,
    and each re-wrap of an exception erodes it further.
    """

    #: Default code for the class, overridden per raise site when a more
    #: precise one applies.
    code = None

    def __init__(self, message: str = "", code: str = None):
        super().__init__(message)
        if code:
            self.code = code

class FreeCADConnectionError(FreeCADServerError):
    """Raised when unable to connect to FreeCAD server"""
    code = "102.1"

class FreeCADTimeoutError(FreeCADServerError):
    """Raised when an HTTP call to the FreeCAD server times out"""
    code = "101.2"

class FreeCADServerNotAvailableError(FreeCADServerError):
    """Raised when FreeCAD server is not available or not responding"""
    code = "102.1"

class FreeCADProcessingError(FreeCADServerError):
    """Raised when FreeCAD server encounters processing errors.

    Deliberately has no default code — every raise site names the one that
    applies, so a processing error is never reported as a generic failure.
    """
    code = None


# ═══════════════════════════════════════════════════════════════════════════
# READING THE SERVER'S VERDICT
#
# The FreeCAD server ends every job with one outcome envelope (its
# job_contract.py) carrying `status`, `code` and an `error` object. It is the
# only side that watched the job fail, so its code is taken as given — the
# client's job is to read it, not to re-derive it.
#
# _classify_job_failure below exists for one case only: a server older than the
# contract, which sends a status and prose but no code.
# ═══════════════════════════════════════════════════════════════════════════

#: Terminal statuses the server can report. 'error'/'finished'/'completed'/
#: 'success' are accepted as synonyms because RQ and older builds use them.
_SUCCESS_STATUSES = frozenset({'complete', 'completed', 'success', 'finished'})
_PARTIAL_STATUSES = frozenset({'partial_success', 'warning'})
_FAILURE_STATUSES = frozenset({'failed', 'error'})

#: Outputs whose absence must NOT fail a job. The PDF is a visualisation: STEP
#: and OBJ have already been exported by the time it is produced, so losing it
#: costs the user a drawing, not the model.
_OPTIONAL_OUTPUTS = frozenset({'pdf'})

#: Fallback only — see the note above. Order matters: specific before generic.
_JOB_FAILURE_SIGNATURES = (
    # "exceeded" alone is NOT a timeout signal: "maximum recursion depth
    # exceeded" is a script failure, while a real timeout says "timed out".
    (("timeout", "timed out", "timed_out", "time out"), "101.1"),
    (("charmap", "codec", "unicodeencode", "unicodedecode"), "104.2"),
    (("missing required outputs", "no step", "no files generated",
      "could not be collected"), "104.3"),
    (("unexpected error", "server failed"), "104.5"),
)


def _classify_job_failure(*texts: str) -> str:
    """Map a failed job's server-side text to an error code.

    Falls back to 104.1 — the script ran and failed — because that is what a
    bare `status=failed` from the worker means when nothing more specific
    matches.
    """
    haystack = " ".join(t for t in texts if t).lower()
    for keywords, code in _JOB_FAILURE_SIGNATURES:
        if any(keyword in haystack for keyword in keywords):
            return code
    return "104.1"


def _as_dict(value) -> Dict[str, Any]:
    """Coerce an envelope field to a dict.

    New servers send a real JSON object. Older ones sent the same content
    json.dumps()'d into a string field, so a string is parsed rather than
    rejected — that is the whole reason this used to be lost so easily.
    """
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {"message": value}
        except (json.JSONDecodeError, TypeError):
            return {"message": value}
    return {}


def read_job_verdict(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Interpret one server payload (MQTT message, /status or /result body).

    Returns:
        is_terminal: whether the job has reached a final state at all
        succeeded:   whether the user got a usable model
        code:        error code, or None when the job succeeded
        message:     the server's human-readable line
        detail:      the most specific technical cause available
        error:       the full error object from the server
    """
    status = str(payload.get('status') or '').lower()
    message = payload.get('message') or ''
    error = _as_dict(payload.get('error'))
    details = _as_dict(payload.get('details'))

    is_terminal = status in _SUCCESS_STATUSES | _PARTIAL_STATUSES | _FAILURE_STATUSES
    verdict = {
        'status': status,
        'is_terminal': is_terminal,
        'succeeded': status in _SUCCESS_STATUSES,
        'code': None,
        'message': message,
        'detail': message,
        'error': error,
        'details': details,
    }
    if not is_terminal or verdict['succeeded']:
        return verdict

    # The server names the failure; only fall back to reading its prose when
    # talking to a build that predates the contract.
    code = payload.get('code') or error.get('code')

    specific_exception = error.get('specific_exception') or error.get('error_message')
    error_hint = error.get('error_hint') or details.get('error_hint')

    if status in _PARTIAL_STATUSES:
        # A partial success is only a failure if something REQUIRED is missing.
        # Trusting the declared list beats searching the sentence for "pdf":
        # a message can mention the PDF while the real loss is the STEP.
        missing = [str(kind).lower() for kind in (error.get('missing_types') or [])]
        if missing and set(missing) <= _OPTIONAL_OUTPUTS:
            verdict['succeeded'] = True
            verdict['message'] = message or 'Optional export missing'
            return verdict
        if not missing and 'pdf' in message.lower():
            # Older server: no declared list, only prose to go on.
            verdict['succeeded'] = True
            return verdict
        verdict['code'] = code or "104.6"
        verdict['detail'] = message or 'Partial export'
        return verdict

    verdict['code'] = code or _classify_job_failure(message, specific_exception, error_hint)
    verdict['detail'] = specific_exception or error_hint or message or 'EXECUTION_FAILED'
    return verdict


def _log_job_verdict(log, user_id: str, verdict: Dict[str, Any]) -> None:
    """Log a terminal verdict with everything support needs, once."""
    if verdict['succeeded']:
        if verdict['status'] in _PARTIAL_STATUSES:
            log.warning(
                f"[JOB] ⚠️ Optional export missing (job kept) | user_id={user_id} | "
                f"{verdict['message'][:160]}"
            )
        return

    error = verdict['error']
    log.error(
        "[JOB] ❌ Failed | user_id=%s | code=%s | status=%s | message=%s | "
        "specific_exception=%s | error_hint=%s",
        user_id, verdict['code'], verdict['status'], verdict['message'],
        error.get('specific_exception'), error.get('error_hint'),
    )
    for stream in ('stdout_tail', 'stderr_tail'):
        tail = error.get(stream)
        if isinstance(tail, list) and tail:
            log.error("[JOB] %s (last %d lines):\n%s", stream, len(tail), "\n".join(tail))
        elif tail:
            log.error("[JOB] %s:\n%s", stream, tail)


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
                logger.warning("[FreeCAD] 'server_freecad' env variable not set, using default URL")

        self.base_url = base_url.rstrip('/')
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.session = None  # Will be initialized in __aenter__
        self.semaphore = asyncio.Semaphore(max_concurrent)

        logger.debug(f"[FreeCAD] Client initialized | url={self.base_url} | max_concurrent={max_concurrent}")

    async def __aenter__(self):
        """Async context manager entry"""
        # Set timeout for file downloads (5 minutes for large STEP files)
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
            FreeCADProcessingError: If the script file is unusable (105.4) or the
                server response is invalid / missing required fields (102.3-102.6)
            FreeCADConnectionError: If the server cannot be reached (102.1)
            FreeCADServerNotAvailableError: If the server answers 503 (102.1)
            FreeCADTimeoutError: If the request times out (101.2)
        """
        async with self.semaphore:  # Limit concurrent requests
            try:
                # ============================================================
                # STEP 1: VALIDATE SCRIPT FILE EXISTS
                # ============================================================
                if not os.path.exists(script_path):
                    error_msg = f"Script file not found: {script_path}"
                    logger.error(f"[FreeCAD] ❌ {error_msg}")
                    raise FreeCADProcessingError(error_msg, code="105.4")

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
                    raise FreeCADProcessingError(error_msg, code="105.4")

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

                logger.debug(f"[FreeCAD] Sending request | user_id={user_id} | file={filename} | metadata={'yes' if metadata_sent else 'no'} | priority={priority}")

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
                            raise FreeCADProcessingError(
                                f"FreeCAD server internal error (status: {response.status})", code="102.1"
                            )
                        elif response.status == 404:
                            raise FreeCADConnectionError(f"FreeCAD server endpoint not found (404): {url}")
                        elif response.status >= 400:
                            raise FreeCADProcessingError(
                                f"FreeCAD server request error (status: {response.status})", code="102.2"
                            )

                        response.raise_for_status()

                        # ============================================================
                        # STEP 4: PARSE AND VALIDATE SERVER RESPONSE
                        # ============================================================
                        import json as json_module
                        try:
                            job_ack = json_module.loads(response_text)
                            logger.debug(f"[FreeCAD] Response parsed | status={job_ack.get('status')} | user_id={job_ack.get('user_id')}")
                        except json_module.JSONDecodeError:
                            error_msg = f"Failed to parse server response as JSON: {response_text[:500]}"
                            logger.error(f"[FreeCAD] ❌ {error_msg}")
                            raise FreeCADProcessingError(error_msg, code="102.3")

                        # ============================================================
                        # STEP 5: VERIFY RESPONSE CONTAINS REQUIRED FIELDS
                        # ============================================================



                        # Check user_id
                        if not job_ack.get('user_id'):
                            error_msg = f"Server response missing user_id: {job_ack}"
                            logger.error(f"[FreeCAD] ❌ {error_msg}")
                            raise FreeCADProcessingError(error_msg, code="102.3")

                        # Verify user_id matches what we sent
                        if job_ack.get('user_id') != user_id:
                            error_msg = f"Server returned different user_id. Expected: {user_id}, Got: {job_ack.get('user_id')}"
                            logger.error(f"[FreeCAD] ❌ {error_msg}")
                            raise FreeCADProcessingError(error_msg, code="102.4")

                        # Check status must be 'queued'
                        response_status = job_ack.get('status')
                        if response_status != 'queued':
                            error_msg = f"Server returned invalid status. Expected: 'queued', Got: '{response_status}'. Response: {job_ack}"
                            logger.error(f"[FreeCAD] ❌ {error_msg}")
                            raise FreeCADProcessingError(error_msg, code="102.5")

                        # Check MQTT must be published successfully
                        mqtt_published = job_ack.get('mqtt_published', False)
                        if not mqtt_published:
                            error_msg = f"Server did not publish MQTT message. Job may not have been queued. Response: {job_ack}"
                            logger.error(f"[FreeCAD] ❌ {error_msg}")
                            raise FreeCADProcessingError(error_msg, code="102.6")

                        # ============================================================
                        # STEP 6: VALIDATE FILES RECEIVED BY SERVER
                        # ============================================================
                        files_received = job_ack.get('files_received', {})
                        
                        # Check if Python script was received
                        python_script_info = files_received.get('python_script')
                        if not python_script_info:
                            logger.warning("[FreeCAD] ⚠️ Server response missing 'python_script' info")
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
                                    "This may cause threaded holes to not be detected correctly.",
                                    code="102.3"
                                )
                            else:
                                # Server received metadata - log details
                                metadata_filename = metadata_info.get('filename', 'unknown')
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
                                logger.debug("[FreeCAD] No metadata sent or received (as expected)")

                        # ============================================================
                        # STEP 7: LOG SUCCESS AND RETURN
                        # ============================================================
                        logger.info(
                            f"[FreeCAD] Job queued | {filename} | priority={priority} | "
                            f"user_id={user_id}"
                        )
                        return job_ack

            except FreeCADServerError:
                # Re-raise our custom exceptions first — they already carry the
                # code for the exact failure, and re-wrapping would replace it
                # with a coarser one.
                raise
            except aiohttp.ClientConnectorError as e:
                logger.error(f"[FreeCAD] ❌ Connection failed | url={self.base_url} | error={e}")
                raise FreeCADConnectionError(f"Unable to connect to FreeCAD server at {self.base_url}: {e}")
            except asyncio.TimeoutError:
                # aiohttp raises this for connect/read/total timeouts. str(e) is
                # EMPTY, so any keyword-based classification downstream sees a
                # blank message and mislabels it — the type is the only signal.
                logger.error(f"[FreeCAD] ❌ Request timed out | url={self.base_url}")
                raise FreeCADTimeoutError(f"Timed out submitting job to FreeCAD server at {self.base_url}")
            except aiohttp.ClientResponseError as e:
                logger.error(f"[FreeCAD] ❌ HTTP error | status={e.status} | error={e}")
                raise FreeCADProcessingError(
                    f"HTTP error from FreeCAD server: {e}",
                    code="102.1" if e.status >= 500 else "102.2"
                )
            except Exception as e:
                logger.error(f"[FreeCAD] ❌ Unexpected error | error={e}")
                raise FreeCADProcessingError(
                    f"Unexpected error during FreeCAD generation: {e}", code="102.2"
                )

    async def get_execution_status_async(self, user_id: str) -> Dict[str, Any]:
        """
        Async get detailed execution status and progress from FreeCAD server

        Args:
            user_id: User ID to get specific job status

        Returns:
            Dictionary containing detailed execution status
        """
        if not user_id:
            raise FreeCADProcessingError("user_id is required for execution status check", code="105.2")

        try:
            url = f"{self.base_url}/freecad/status/{user_id}"
            logger.debug(f"Getting execution status for user {user_id} (async)")

            async with self.session.get(url) as response:
                # Handle different HTTP status codes
                if response.status == 404:
                    # The server has no record of this job — it was lost, not
                    # merely slow to answer.
                    raise FreeCADProcessingError(f"No execution status found for user {user_id}", code="102.7")
                elif response.status >= 500:
                    raise FreeCADProcessingError(
                        f"FreeCAD server internal error (status: {response.status})", code="102.1"
                    )
                elif response.status >= 400:
                    raise FreeCADProcessingError(
                        f"FreeCAD server request error (status: {response.status})", code="102.2"
                    )

                response.raise_for_status()

                status_data = await response.json()

                # Normalize the response format. `code`, `error` and `details`
                # are passed through untouched: they are the server's verdict,
                # and read_job_verdict() is the only thing that interprets them.
                normalized_status = {
                    'status': status_data.get('status', 'unknown'),
                    'progress': status_data.get('progress', 0),
                    'current_process': status_data.get('current_process', status_data.get('message', 'No process information')),
                    'message': status_data.get('message', ''),
                    'timestamp': status_data.get('timestamp', datetime.now().isoformat()),
                    'user_id': status_data.get('user_id', user_id),
                    'execution_time': status_data.get('execution_time', 0),
                    'code': status_data.get('code'),
                    'error': status_data.get('error'),
                    'details': status_data.get('details'),
                    'final': bool(status_data.get('final')),
                    'data_source': status_data.get('data_source'),
                    'server_info': {
                        'server_url': self.base_url,
                        'endpoint_used': url
                    }
                }

                logger.debug(f"Execution status retrieved (async): {normalized_status['status']} - {normalized_status['progress']}%")

                return normalized_status

        except FreeCADServerError:
            raise
        except aiohttp.ClientConnectorError as e:
            logger.error(f"Connection to FreeCAD server failed during status check (async): {e}")
            raise FreeCADConnectionError(f"Unable to connect to FreeCAD server for status check: {e}")
        except asyncio.TimeoutError:
            logger.error(f"Status check timed out (async) | user_id={user_id}")
            raise FreeCADTimeoutError(f"Timed out reading job status for user {user_id}")
        except Exception as e:
            logger.error(f"Unexpected error during status check (async): {e}")
            raise FreeCADProcessingError(f"Unexpected error during status check: {e}", code="102.2")

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
        # 'code' is the error-table entry for whatever ended the job; it is set
        # next to 'error' at every failure point so the caller never has to
        # re-derive it from the message text.
        job_completed = {'status': None, 'result': None, 'error': None, 'code': None}
        # Last percentage echoed to the console. The server emits an update every
        # few hundred ms, so this throttles them to roughly one line per quarter
        # of the job — enough to see a long job moving, without a wall of text.
        last_logged_progress = [-PROGRESS_LOG_STEP]

        mqtt_config = get_mqtt_config()

        logger.debug(f"[MQTT] Starting listener | user_id={user_id} | broker={mqtt_config.broker_host}:{mqtt_config.broker_port}")

        def on_connect(client, userdata, flags, rc):
            """Callback when MQTT client connects to broker"""
            if rc == 0:
                # Subscribe to MQTT topics for this user_id
                progress_topic = f"freecad/progress/{user_id}"
                status_topic = f"freecad/status/{user_id}"

                client.subscribe(progress_topic, qos=mqtt_config.qos_level)
                client.subscribe(status_topic, qos=mqtt_config.qos_level)

                logger.debug(f"[MQTT] Connected | user_id={user_id} | qos={mqtt_config.qos_level}")
            else:
                error_msg = f"MQTT connection failed with code: {rc}"
                logger.error(f"[MQTT] ❌ {error_msg}")
                job_completed['error'] = error_msg
                job_completed['code'] = "102.9"

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

                    # The `status in [...]` half of the old condition matched every
                    # update a running job sends, so "only log milestones" logged
                    # all of them. Throttle on the percentage instead; the
                    # terminal status is reported separately below.
                    # 100% is skipped: "[FreeCAD] Job complete in Xs | N files"
                    # follows immediately with the same news plus the timing.
                    if progress >= 100:
                        logger.debug(f"[MQTT] 100% | {message} | user_id={user_id}")
                    elif progress - last_logged_progress[0] >= PROGRESS_LOG_STEP:
                        last_logged_progress[0] = progress
                        logger.info(f"[MQTT] {progress}% | {message[:60]} | user_id={user_id}")
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
                    verdict = read_job_verdict(data)
                    status = verdict['status']

                    if not verdict['is_terminal']:
                        logger.debug(f"[MQTT] Status | user_id={user_id} | {status} | {verdict['message'][:60]}")
                        return

                    elapsed = round(time.time() - start_time, 2)
                    _log_job_verdict(logger, user_id, verdict)

                    if verdict['succeeded']:
                        logger.debug(f"[MQTT] Completed | user_id={user_id} | time={elapsed}s")
                        job_completed['status'] = 'completed'
                        job_completed['result'] = data
                    else:
                        job_completed['status'] = 'failed'
                        job_completed['error'] = verdict['detail']
                        job_completed['code'] = verdict['code']

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
            try:
                mqtt_client.connect(mqtt_config.broker_host, mqtt_config.broker_port, keepalive=60)
            except Exception as connect_error:
                # A broker we cannot even reach is a progress-channel failure
                # (102.9), not a FreeCAD execution failure — without this the
                # socket error fell through to the generic handler and the user
                # was told their model could not be built.
                raise FreeCADProcessingError(
                    f"Unable to connect to MQTT broker "
                    f"{mqtt_config.broker_host}:{mqtt_config.broker_port}: {connect_error}",
                    code="102.9"
                )

            # Start MQTT loop in background
            mqtt_client.loop_start()
            logger.debug(f"[MQTT] Loop started | user_id={user_id}")

            # ============================================================
            # WAIT FOR COMPLETION OR TIMEOUT
            # ============================================================
            logger.debug(
                f"[MQTT] Waiting for job completion | "
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

                        # Same reader as the MQTT path: /freecad/status serves the
                        # same outcome envelope, so both routes reach the same
                        # verdict instead of each guessing in its own way.
                        verdict = read_job_verdict(fallback_status)
                        if verdict['is_terminal']:
                            logger.warning(
                                f"[MQTT] ⚠️ Outcome detected via HTTP fallback | user_id={user_id} | "
                                f"status={verdict['status']} | source={fallback_status.get('data_source')}"
                            )
                            _log_job_verdict(logger, user_id, verdict)

                            if verdict['succeeded']:
                                job_completed['status'] = 'completed'
                                job_completed['result'] = fallback_status
                            else:
                                job_completed['status'] = 'failed'
                                job_completed['error'] = verdict['detail']
                                job_completed['code'] = verdict['code']

                            try:
                                mqtt_client.disconnect()
                            except Exception:
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
            logger.debug(f"[MQTT] 🔌 Disconnected from broker | user_id={user_id}")

            # ============================================================
            # CHECK RESULT AND RETURN
            # ============================================================
            if job_completed['error']:
                logger.error(
                    f"[MQTT] ❌ Job failed | "
                    f"user_id={user_id} | "
                    f"code={job_completed['code']} | "
                    f"error={job_completed['error']}"
                )
                raise FreeCADProcessingError(
                    job_completed['error'],
                    # No code set means the loop ended without any failure
                    # classifying itself — treat it as an execution failure,
                    # the meaning of a bare 'failed' from the worker.
                    code=job_completed['code'] or "104.1"
                )

            if job_completed['status'] == 'completed':
                total_time = time.time() - start_time
                logger.debug(f"[MQTT] Completed | user_id={user_id} | time={total_time:.2f}s | updates={len(progress_history)}")
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
                    'error': job_completed.get('error', 'Unknown error'),
                    'code': job_completed.get('code') or "104.1"
                }

        except Exception:
            # ============================================================
            # EXCEPTION CLEANUP
            # ============================================================
            try:
                mqtt_client.loop_stop()
                mqtt_client.disconnect()
                logger.debug(f"[MQTT] 🔌 Disconnected (exception cleanup) | user_id={user_id}")
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
            "[DEPRECATED] monitor_execution_progress_async() is deprecated. "
            "Use wait_for_mqtt_completion_async() for better performance."
        )
        start_time = time.time()
        progress_history = []
        last_progress = -1

        logger.debug(f"Starting async execution monitoring for user {user_id}")

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
                        logger.debug(f"Progress update for user {user_id}: {current_progress}% - {current_process}")
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
                        logger.debug(f"Execution completed for user {user_id} after {elapsed_time:.2f}s")
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

            logger.debug(
                f"[RESULT] Requesting job result | "
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
                    raise FreeCADProcessingError(f"Job result not found for user {user_id}", code="102.8")
                elif response.status >= 500:
                    logger.error(
                        f"[RESULT] ❌ Server error | "
                        f"user_id={user_id} | "
                        f"status={response.status} | "
                        f"response={response_text[:500]}"
                    )
                    raise FreeCADProcessingError(
                        f"FreeCAD server internal error (status: {response.status})", code="102.1"
                    )
                elif response.status >= 400:
                    logger.error(
                        f"[RESULT] ❌ Client error | "
                        f"user_id={user_id} | "
                        f"status={response.status} | "
                        f"response={response_text[:500]}"
                    )
                    raise FreeCADProcessingError(
                        f"FreeCAD server request error (status: {response.status})", code="102.2"
                    )

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
                    raise FreeCADProcessingError(f"Failed to parse server response: {e}", code="102.3")

                # Log result summary
                if auto_download:
                    files = result_data.get('files', [])
                    logger.debug(f"[RESULT] Retrieved | user_id={user_id} | files={len(files)}")
                    if raw:
                        # Return the raw file listing without triggering download/validation
                        return result_data
                    # Process the result and download files if available
                    return await self._handle_json_response_async(result_data, user_id, expect_obj=expect_obj)
                else:
                    logger.debug(f"[RESULT] Info retrieved | user_id={user_id} | no_download")
                    return result_data

        except FreeCADServerError:
            raise
        except aiohttp.ClientConnectorError as e:
            logger.error(
                f"[RESULT] ❌ Connection failed | "
                f"user_id={user_id} | "
                f"error={e}"
            )
            raise FreeCADConnectionError(f"Unable to connect to FreeCAD server for result retrieval: {e}")
        except asyncio.TimeoutError:
            logger.error(f"[RESULT] ❌ Request timed out | user_id={user_id}")
            raise FreeCADTimeoutError(f"Timed out retrieving job result for user {user_id}")
        except Exception as e:
            logger.error(
                f"[RESULT] ❌ Unexpected error | "
                f"user_id={user_id} | "
                f"error={e}"
            )
            raise FreeCADProcessingError(f"Unexpected error during result retrieval: {e}", code="102.8")

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
            file_type: Type of file (step, obj, pdf)
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

        for attempt in range(max_retries):
            try:
                async with self.session.get(download_url) as response:
                    response.raise_for_status()

                    # Get expected file size from Content-Length header
                    expected_size = response.headers.get('Content-Length')
                    if expected_size:
                        expected_size = int(expected_size)

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

                is_timeout = any(kw in error_str for kw in ['timeout', 'timed out', 'read timed out', 'connection timeout'])

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
                    # The job itself succeeded — only the transfer failed, so
                    # this is 102.8 and never an execution failure.
                    raise FreeCADProcessingError(error_msg, code="102.8")

        # Should never reach here, but just in case
        raise FreeCADProcessingError(
            f"Failed to download {file_type.upper()} file after {max_retries} attempts", code="102.8"
        )

    async def _handle_json_response_async(self, json_response: Dict[str, Any], user_id: str, expect_obj: bool = True) -> Dict[str, Any]:
        """
        Async handle JSON response from server containing file information

        This method now includes:
        - Comprehensive logging of server response
        - Retry logic with exponential backoff for each file download
        - Content-Length validation to detect truncated downloads
        - File integrity checks (existence, non-zero size)
        - Validation that required CAD files (step, obj) are downloaded. PDF is optional
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
            # /freecad/result now reports the job's real outcome, so the verdict
            # is read here exactly as it is on the MQTT path. It used to answer
            # "success" for any user whose output folder existed — a failed job
            # was only discovered further down, by finding no files.
            verdict = read_job_verdict(json_response)
            if verdict['is_terminal'] and not verdict['succeeded']:
                _log_job_verdict(logger, user_id, verdict)
                return {
                    'success': False,
                    'message': verdict['detail'] or f'Server returned status: {status}',
                    'code': verdict['code'] or "104.4",
                    'response': json_response
                }
            if verdict['status'] in _PARTIAL_STATUSES:
                logger.warning(
                    f"[FILE_DOWNLOAD] user_id={user_id} | ⚠️ Optional export missing, continuing | "
                    f"{verdict['message'][:160]}"
                )
            elif not verdict['is_terminal']:
                # 'running' / 'queued': the job has not finished. Retryable, so
                # the caller's download loop waits instead of failing the job.
                raise FreeCADProcessingError(
                    f"Job result not ready yet (status: {status})", code="102.8"
                )

            # Check if response contains files
            files_info = json_response.get('files', [])

            logger.debug(f"[FILE_DOWNLOAD] Response | user_id={user_id} | status={status} | files={len(files_info)}")

            if not files_info:
                # A job that claims success with an empty file list produced
                # nothing usable. Previously returned as success=True, which
                # pushed the failure downstream into a confusing "no STEP file"
                # much later in the export path.
                error_msg = 'Job reported success but no files were generated'
                logger.error(f"[FILE_DOWNLOAD] user_id={user_id} | ❌ {error_msg}")
                return {
                    'success': False,
                    'message': error_msg,
                    'code': "104.3",
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
                    raise FreeCADProcessingError(error_msg, code="102.3")

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

            # The source URLs are all `<base_url>/freecad/download/<user_id>/<name>`
            # — four of them per job, ~400 characters, and none of it survives
            # past the download. The Completed line below reports what arrived.
            if download_list:
                file_list = ", ".join([f"{f['type']}({f['filename']}): {f['url']}" for f in download_list])
                logger.debug(f"[FILE_DOWNLOAD] Downloading {len(download_list)} files | user_id={user_id} | {file_list}")

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
                    # Optional files (PDF) - log and continue
                    else:
                        logger.warning(f"[FILE_DOWNLOAD] user_id={user_id} | Failed to download optional file {file_type.upper()} (will continue): {e}")

            # Validate required FreeCAD output files. STEP is always mandatory.
            # OBJ is mandatory too UNLESS the caller told us this job is a
            # Perforated Sheet (expect_obj=False) -- Perforated Sheet jobs
            # intentionally skip OBJ generation (see templates.py). Every OTHER
            # shape type keeps the old hard requirement, so a real OBJ-generation
            # bug for a non-perforated shape still fails the job loudly instead
            # of silently succeeding without it.
            required_file_types = {'step'} | ({'obj'} if expect_obj else set())
            optional_file_types = {'pdf'} | (set() if expect_obj else {'obj'})
            downloaded_types = set(downloaded_files.keys())
            missing_required = required_file_types - downloaded_types
            missing_optional = optional_file_types - downloaded_types

            if missing_required:
                error_msg = f"Missing required files: {', '.join(sorted(missing_required))}. Expected {'+'.join(sorted(t.upper() for t in required_file_types))} but got {list(downloaded_types)}"
                logger.error(f"[FILE_DOWNLOAD] user_id={user_id} | ❌ VALIDATION FAILED: {error_msg}")
                # The transfer worked; the CAD output the job was supposed to
                # produce is simply not there → 104.3, not a download error.
                raise FreeCADProcessingError(error_msg, code="104.3")

            # Log warning if optional PDF/OBJ are missing, but do not fail the export
            if missing_optional:
                logger.warning(f"[FILE_DOWNLOAD] user_id={user_id} | ⚠️ Optional files missing: {', '.join(sorted(missing_optional))} | Proceeding with {', '.join(sorted(downloaded_types))}")

            # All validations passed - Single consolidated log entry
            file_details = ", ".join([f"{ft.upper()}({file_sizes.get(ft, 0):,}B)" for ft in sorted(downloaded_files.keys())])
            logger.debug(f"[FILE_DOWNLOAD] Completed | user_id={user_id} | files={len(downloaded_files)}/{len(files_info)} | total={total_size:,}B | {file_details}")

            return {
                'success': True,
                'message': 'Successfully downloaded and validated required CAD files',
                'files': downloaded_files,
                'response': json_response
            }

        except FreeCADServerError:
            # Already classified above — re-wrapping it in a plain Exception
            # (as this used to) threw away the code and left every download and
            # validation failure to be reported as a generic execution failure.
            raise
        except Exception as e:
            error_msg = f"Error handling JSON response: {e}"
            logger.error(f"[FILE_DOWNLOAD] user_id={user_id} | ❌ {error_msg}")
            raise FreeCADProcessingError(error_msg, code="102.8")


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
