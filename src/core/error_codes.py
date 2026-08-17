"""
Error codes reported when a CAD request fails.

Every failure that reaches the user is rendered as ``"<code>: <message>"`` —
e.g. ``"101.1: The CAD engine took too long ..."`` — never as a bare code. The
code is what support greps for in the logs; the message is what the user can
act on. `str(CadError)` produces exactly that string, and it is what
chat_processing puts in the chat response.

Families
--------
101.x   The job ran out of time.
102.x   Communication with the FreeCAD server: transport, submission handshake,
        progress channel, file download. Nothing was built.
104.x   The FreeCAD server ran the script, but the CAD result failed or is
        incomplete.
105.x   This API's own side: script generation/editing and local file handling.

(103.x is deliberately free — no failure in the pipeline needs it today.)

Who assigns which code
----------------------
Each code is assigned by the side that actually saw the failure, and nobody
re-derives it downstream:

* **101.1 and 104.x — the FreeCAD server.** It watched the script run, so it
  names the failure itself and sends it in its outcome envelope
  (``tolery-freecad/job_contract.py``). ``freecad_remote_client.read_job_verdict``
  reads that code as given.
* **101.2 and 102.x — this API's transport layer.** Only the client can observe
  that the server is unreachable, slow, or answering nonsense, so every raise
  site in ``freecad_remote_client`` attaches the matching ``code``.
* **104.4 and 105.x — this API.** Its own code generation, session and file
  handling, plus the case where a result payload contradicts itself.

Callers must never re-derive a code by searching an error message for keywords:
a message is free text, an empty ``str(TimeoutError())`` matches no keyword at
all, and every wrap of an exception loses more of it. The one keyword-based
fallback that remains (``_classify_job_failure``) exists solely for a FreeCAD
server older than the outcome contract, which sends prose and no code.
"""

from typing import Optional

# ═══════════════════════════════════════════════════════════════════════════
# 101.x — Timeout
# ═══════════════════════════════════════════════════════════════════════════
_TIMEOUT = {
    # The FreeCAD server killed the job: freecadcmd exceeded the worker's
    # execution ceiling (worker.py, process.wait(timeout=900)).
    "101.1": (
        "The CAD engine took too long to build this model and the job was "
        "stopped. Please simplify the part (fewer holes, patterns or bends) "
        "and try again."
    ),
    # Our own HTTP call to the server timed out (connect / read / total).
    # The job may or may not still be running on the server.
    "101.2": (
        "The CAD server did not answer in time. Please try again in a few "
        "moments."
    ),
}

# ═══════════════════════════════════════════════════════════════════════════
# 102.x — Communication with the FreeCAD server (nothing was built)
# ═══════════════════════════════════════════════════════════════════════════
_COMMUNICATION = {
    # Connection refused / DNS / 503 / health check failed / HTTP 5xx.
    "102.1": (
        "The CAD server is unreachable right now. Please try again in a few "
        "moments."
    ),
    # The server answered the submission with an HTTP error (4xx), or the
    # upload itself failed.
    "102.2": (
        "The CAD server refused the job. Please try again; if this keeps "
        "happening, contact support."
    ),
    # Answer received but unusable: not JSON, missing fields, or the metadata
    # file we uploaded was not acknowledged.
    "102.3": (
        "The CAD server sent back an unexpected answer and the job could not "
        "be confirmed. Please try again."
    ),
    # The server acknowledged a different user_id than the one submitted —
    # results would belong to another session, so the job is abandoned.
    "102.4": (
        "The CAD server registered the job under a different session. Please "
        "try again."
    ),
    # Submission accepted but the job never entered the queue.
    "102.5": (
        "The CAD server did not queue the job. Please try again in a few "
        "moments."
    ),
    # Queued, but the server published no MQTT notification, so completion
    # can never be observed.
    "102.6": (
        "The CAD server queued the job but never announced it, so its "
        "progress cannot be followed. Please try again."
    ),
    # The job is unknown to the server after having been submitted.
    "102.7": "The job was lost by the CAD server before it finished. Please try again.",
    # The job finished but its files could not be fetched.
    "102.8": (
        "The generated files could not be downloaded from the CAD server. "
        "Please try again."
    ),
    # The MQTT broker itself is down or refused the connection.
    "102.9": (
        "The progress channel is unavailable, so the job cannot be followed. "
        "Please try again in a few moments."
    ),
}

# ═══════════════════════════════════════════════════════════════════════════
# 104.x — The server ran the script, but the CAD result failed
# ═══════════════════════════════════════════════════════════════════════════
_EXECUTION = {
    # freecadcmd exited non-zero: the generated script raised.
    "104.1": (
        "The CAD engine could not build this model — the generated script "
        "failed while running. Please rephrase or simplify your request and "
        "try again."
    ),
    # The script tripped an encoding error (charmap / codec / unicode), which
    # is a defect in what was generated, not in what was asked.
    "104.2": (
        "The generated script contains characters the CAD engine cannot read. "
        "Please avoid accents and special characters in names, then try again."
    ),
    # The script ran to completion but a required output is missing. Covers
    # both "no STEP at all" and "STEP but no OBJ" -- the FreeCAD server only
    # enforces STEP, this API also requires OBJ for non-perforated shapes, so
    # the wording must not claim that nothing was produced.
    "104.3": (
        "The CAD engine finished without producing all the required 3D files. "
        "Please rephrase or simplify your request and try again."
    ),
    # The result payload itself reports a non-success job.
    "104.4": "The CAD server reported that the job did not complete. Please try again.",
    # The server crashed while handling the job (worker-level exception).
    "104.5": (
        "The CAD server hit an internal error while building the model. "
        "Please try again; if this keeps happening, contact support."
    ),
    # Partial success: the model exists, an optional output does not. The PDF
    # case is downgraded to success before it gets here (see
    # freecad_remote_client), so this is a genuinely incomplete export.
    "104.6": (
        "The model was built but part of the export is missing. Please try "
        "again if you need the missing file."
    ),
}

# ═══════════════════════════════════════════════════════════════════════════
# 105.x — This API's own side (script generation and local files)
# ═══════════════════════════════════════════════════════════════════════════
_API_SIDE = {
    "105.1": (
        "The CAD script could not be generated for this request. Please "
        "rephrase your request and try again."
    ),
    "105.2": (
        "The session was lost, so the request could not be processed. Please "
        "start a new conversation."
    ),
    "105.3": (
        "The requested change could not be applied to the current model. "
        "Please rephrase your change and try again."
    ),
    "105.4": "The generated script could not be saved. Please try again.",
    "105.5": "The generated CAD files could not be stored. Please try again.",
    "105.6": "An unexpected error occurred while processing the request. Please try again.",
}

ERROR_MESSAGES = {**_TIMEOUT, **_COMMUNICATION, **_EXECUTION, **_API_SIDE}

#: Fallback for a code that is not in the table — a programming mistake, but it
#: must not turn into a KeyError on top of the failure being reported.
UNKNOWN_ERROR_CODE = "105.6"


class CadError(Exception):
    """A failure the user is shown, identified by its code.

    ``str(err)`` is ``"<code>: <message>"``. ``detail`` holds the technical
    cause for the logs and is deliberately kept out of that string, so nothing
    internal leaks into the chat response.
    """

    def __init__(self, code: str, detail: Optional[str] = None):
        self.code = code
        self.message = ERROR_MESSAGES.get(code) or ERROR_MESSAGES[UNKNOWN_ERROR_CODE]
        self.detail = detail
        super().__init__(f"{code}: {self.message}")


def error_code_of(exc: BaseException) -> Optional[str]:
    """Return the error code carried by an exception, or None.

    Covers both CadError and the FreeCAD* transport exceptions, which carry the
    same ``code`` attribute.
    """
    return getattr(exc, "code", None)


def as_user_error(exc: BaseException) -> str:
    """Render any exception as a ``"<code>: <message>"`` user-facing string.

    Anything without a code is an unexpected internal failure and is reported
    as 105.6 — so the user never sees a raw Python message, and the logs still
    have the original.
    """
    code = error_code_of(exc)
    if code and code in ERROR_MESSAGES:
        return f"{code}: {ERROR_MESSAGES[code]}"
    return str(CadError(UNKNOWN_ERROR_CODE))
