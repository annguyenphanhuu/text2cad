"""
Log Viewer — two distinct search modes

  Mode A  search=<keyword>     Full-file keyword scan + N context lines around each hit.
  Mode B  session_id=<id>      Full session trail: anchors (lines with the session prefix)
                                + shared-module lines between anchors (cost_tracker, reranker …)
                                + up to N lines before/after the session boundary.
  Default (no filter)          Tail: last `lines` lines.

session_id takes priority when both params are supplied.
"""

import os
import re
import logging
from typing import Optional, List

from fastapi import Query, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_ANSI_RE      = re.compile(rb"\x1b\[[0-9;]*m|\033\[[0-9;]*m")   # bytes-mode
_SESSION_MARK = b"[session_"                                       # any session prefix

_LOG_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "..", "..", "logs", "application.log",
)
_LOG_PATH = os.path.normpath(_LOG_PATH)

_LOG_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "logs")
)

# Extensions considered as log files
_LOG_EXTENSIONS = {".log", ".txt"}


# ---------------------------------------------------------------------------
# Response model
# ---------------------------------------------------------------------------
class LogResponse(BaseModel):
    log_file_path:  str
    total_lines:    int
    matched_lines:  int  = Field(..., description="Anchor hits (session mode) or keyword hits")
    returned_lines: int
    lines:          list[str]
    error:          Optional[str] = None


class LogFileInfo(BaseModel):
    filename:   str
    size_bytes: int
    size_human: str


class LogFilesResponse(BaseModel):
    log_dir:    str
    files:      List[LogFileInfo]
    total_files: int


def _human_size(n: int) -> str:
    """Convert byte count to a human-readable string."""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _decode(raw: bytes) -> str:
    """Strip ANSI codes, decode UTF-8, strip trailing newline."""
    return _ANSI_RE.sub(b"", raw).decode("utf-8", errors="replace").rstrip("\r\n")


def _read_lines() -> list[bytes]:
    """Read entire log file as a list of raw byte lines."""
    with open(_LOG_PATH, "rb") as f:
        data = f.read()
    lines = data.split(b"\n")
    if lines and lines[-1] == b"":
        lines.pop()
    return lines


def _collect(raw_lines: list[bytes], indices: set[int]) -> list[str]:
    """Decode and return lines at the given indices, in file order."""
    return [_decode(raw_lines[i]) for i in sorted(indices)]


# ---------------------------------------------------------------------------
# Mode A — Keyword search
# ---------------------------------------------------------------------------
def _keyword_search(raw_lines: list[bytes], keyword: str, context: int) -> tuple[int, list[str]]:
    """
    Scan entire file for `keyword` (case-insensitive).
    Return (match_count, result_lines) where result_lines contains each match
    plus `context` surrounding lines.
    """
    kw      = keyword.lower().encode("utf-8")
    n       = len(raw_lines)
    anchors = [i for i, line in enumerate(raw_lines) if kw in line.lower()]

    if not anchors:
        return 0, []

    included: set[int] = set()
    for idx in anchors:
        included.update(range(max(0, idx - context), min(n, idx + context + 1)))

    return len(anchors), _collect(raw_lines, included)


# ---------------------------------------------------------------------------
# Mode B — Session tracking
# ---------------------------------------------------------------------------
_CLS_NONE   = 0   # no session prefix
_CLS_ANCHOR = 1   # contains our session_id
_CLS_OTHER  = 2   # contains a DIFFERENT session prefix


def _classify(raw_lines: list[bytes], session_bytes: bytes) -> list[int]:
    """Classify each line as ANCHOR / OTHER_SESSION / NONE."""
    result = []
    for line in raw_lines:
        if session_bytes in line:
            result.append(_CLS_ANCHOR)
        elif _SESSION_MARK in line:
            result.append(_CLS_OTHER)
        else:
            result.append(_CLS_NONE)
    return result


def _session_track(raw_lines: list[bytes], session_id: str, boundary: int) -> tuple[int, list[str]]:
    """
    Return the complete log trail for `session_id`.

    Algorithm:
      1. Find all ANCHOR lines (lines whose text contains session_id).
      2. Between consecutive anchors → include every NONE line
         (shared-module lines: cost_tracker, reranker, etc. that lack a session prefix).
      3. Before the first anchor → expand backward, stop at the first OTHER-session line
         or after `boundary` NONE lines (whichever comes first).
      4. After the last anchor  → same logic forward.

    Returns (anchor_count, result_lines).
    """
    session_bytes = session_id.encode("utf-8")
    classes       = _classify(raw_lines, session_bytes)
    n             = len(raw_lines)

    anchors = [i for i, c in enumerate(classes) if c == _CLS_ANCHOR]
    if not anchors:
        return 0, []

    included: set[int] = set(anchors)

    # ── Between consecutive anchors (bi-directional scan) ───────────────
    # Forward from anchor[k]:  include NONE lines, STOP at first OTHER.
    # Backward from anchor[k+1]: include NONE lines, STOP at first OTHER.
    # Union of both sides → avoids picking up lines that "belong" to another
    # session whose anchor appears between our two consecutive anchors.
    for k in range(len(anchors) - 1):
        start_idx = anchors[k]
        end_idx   = anchors[k + 1]

        for j in range(start_idx + 1, end_idx):      # forward
            if classes[j] == _CLS_OTHER:
                break
            if classes[j] == _CLS_NONE:
                included.add(j)

        for j in range(end_idx - 1, start_idx, -1):  # backward
            if classes[j] == _CLS_OTHER:
                break
            if classes[j] == _CLS_NONE:
                included.add(j)

    # ── Before first anchor (expand backward) ───────────────────────────
    count = 0
    for j in range(anchors[0] - 1, -1, -1):
        if classes[j] == _CLS_OTHER:
            break
        if classes[j] == _CLS_NONE:
            included.add(j)
            count += 1
            if count >= boundary:
                break

    # ── After last anchor (expand forward) ──────────────────────────────
    count = 0
    for j in range(anchors[-1] + 1, n):
        if classes[j] == _CLS_OTHER:
            break
        if classes[j] == _CLS_NONE:
            included.add(j)
            count += 1
            if count >= boundary:
                break

    return len(anchors), _collect(raw_lines, included)


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------
def search_log(
    search:     Optional[str] = None,
    session_id: Optional[str] = None,
    lines:      int           = 10,
) -> dict:
    def _err(msg: str) -> dict:
        return dict(log_file_path=_LOG_PATH, total_lines=0, matched_lines=0,
                    returned_lines=0, lines=[], error=msg)

    if not os.path.exists(_LOG_PATH):
        return _err(f"Log file not found: {_LOG_PATH}")

    try:
        raw   = _read_lines()
        total = len(raw)

        if session_id:                          # Mode B — session tracking
            matched, result = _session_track(raw, session_id, boundary=lines)

        elif search:                            # Mode A — keyword search
            matched, result = _keyword_search(raw, search, context=lines)

        else:                                   # Default — tail
            tail    = raw[-lines:] if lines <= total else raw
            matched = total
            result  = [_decode(l) for l in tail]

        return dict(log_file_path=_LOG_PATH, total_lines=total,
                    matched_lines=matched, returned_lines=len(result),
                    lines=result, error=None)

    except Exception as exc:
        return _err(str(exc))


# ---------------------------------------------------------------------------
# FastAPI endpoint
# ---------------------------------------------------------------------------
def register_log_endpoints(app_instance, verify_token_func):

    @app_instance.get("/logs", response_model=LogResponse, tags=["logs"])
    async def get_application_logs(
        search:     Optional[str] = Query(None, description="Keyword to search (case-insensitive)"),
        session_id: Optional[str] = Query(None, description="Session ID to track (full session trail)"),
        lines:      int           = Query(
            10, ge=0, le=500,
            description=(
                "Keyword mode : context lines around each match. "
                "Session mode : max extra lines before/after the session boundary. "
                "Default: 10"
            ),
        ),
        token: str = Depends(verify_token_func),
    ):
        """
        📋 **application.log** search — two modes:

        - `session_id` → **Session tracking**: returns the complete log trail of a session,
          including shared-module lines (cost_tracker, reranker …) that have no session prefix.
          `lines` caps how many extra lines to include before/after the session boundary.

        - `search` → **Keyword search**: returns every line containing the keyword
          with `lines` context lines before and after each hit.

        `session_id` takes priority when both are provided.
        """
        logger.info(f"Log search | search={search!r} session_id={session_id!r} lines={lines}")
        result = search_log(search=search, session_id=session_id, lines=lines)
        logger.info(f"Done | matched={result['matched_lines']} returned={result['returned_lines']}/{result['total_lines']}")
        return LogResponse(**result)

    # ------------------------------------------------------------------
    # GET /logs/files  — list downloadable log files
    # ------------------------------------------------------------------
    @app_instance.get("/logs/files", response_model=LogFilesResponse, tags=["logs"])
    async def list_log_files(
        token: str = Depends(verify_token_func),
    ):
        """
        📂 **List available log files** in the `logs/` directory.

        Returns every file with a `.log` or `.txt` extension found at the top
        level of the logs folder, along with their sizes.
        Use the `filename` value from the response with the `/logs/download`
        endpoint to fetch the actual file.
        """
        if not os.path.isdir(_LOG_DIR):
            raise HTTPException(status_code=404, detail=f"Log directory not found: {_LOG_DIR}")

        files: List[LogFileInfo] = []
        for entry in sorted(os.scandir(_LOG_DIR), key=lambda e: e.name):
            if entry.is_file() and os.path.splitext(entry.name)[1].lower() in _LOG_EXTENSIONS:
                size = entry.stat().st_size
                files.append(LogFileInfo(
                    filename=entry.name,
                    size_bytes=size,
                    size_human=_human_size(size),
                ))

        logger.info(f"Log files listed: {len(files)} files found")
        return LogFilesResponse(log_dir=_LOG_DIR, files=files, total_files=len(files))

    # ------------------------------------------------------------------
    # GET /logs/download  — download a specific log file
    # ------------------------------------------------------------------
    @app_instance.get("/logs/download", tags=["logs"])
    async def download_log_file(
        filename: str = Query(..., description="Name of the log file to download (e.g. application.log)"),
        token: str = Depends(verify_token_func),
    ):
        """
        ⬇️ **Download a log file** from the `logs/` directory.

        - `filename` — exact filename as returned by `/logs/files`  
          (e.g. `application.log`, `errors.log`).

        The file is streamed back as an attachment so browsers / curl will
        save it directly to disk.

        > ⚠️ Only files inside the `logs/` directory are accessible.
          Path traversal attempts (`../`) are rejected with **400 Bad Request**.
        """
        # Security: reject any path traversal attempt
        safe_name = os.path.basename(filename)
        if safe_name != filename or not safe_name:
            raise HTTPException(
                status_code=400,
                detail="Invalid filename. Path traversal is not allowed.",
            )

        # Only allow known log extensions
        if os.path.splitext(safe_name)[1].lower() not in _LOG_EXTENSIONS:
            raise HTTPException(
                status_code=400,
                detail=f"Only {_LOG_EXTENSIONS} files can be downloaded.",
            )

        file_path = os.path.join(_LOG_DIR, safe_name)
        if not os.path.isfile(file_path):
            raise HTTPException(
                status_code=404,
                detail=f"Log file not found: {safe_name}",
            )

        logger.info(f"Log download requested: {safe_name} ({os.path.getsize(file_path)} bytes)")
        return FileResponse(
            path=file_path,
            media_type="text/plain; charset=utf-8",
            filename=safe_name,
        )
