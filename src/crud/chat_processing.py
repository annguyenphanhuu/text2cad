"""
CRUD operations for chat processing and request handling.
"""
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from fastapi import HTTPException
from typing import Optional
import logging
import uuid
import random
import re
import time
import traceback
from datetime import datetime

from ..models.sessions import Session as SessionModel
from ..schemas.sessions import ChatRequest, ChatResponse
from ..core.text_to_cad_agent import TextToCADAgent
from .sessions import create_session, get_session_by_id, get_latest_code, add_chat_history_entry
from ..utils.web_search_handler import WebSearchProcessor
from ..utils.messages import get_success_message, get_error_message
from ..database.db_retry import retry_db_operation, DatabaseRetryError, is_connection_error
from ..utils.download_url import build_download_url

logger = logging.getLogger(__name__)

# Web search processor will be initialized per-request with cost_tracker
# web_search_processor = WebSearchProcessor()


# ═════════════════════════════════════════════════════════════════════════════
# Shared request pre-processing
#
# handle_chat_request() and generate_cad_realtime_stream() run the same three
# pre-processing steps before handing off to the agent. They were duplicated
# verbatim (differing only in log prefix), so a fix applied to one silently
# missed the other. `tag` keeps the two flows distinguishable in the logs.
# ═════════════════════════════════════════════════════════════════════════════

async def _apply_web_search(agent, session_id: str, message: str, tag: str):
    """
    Expand `message` with content extracted from any URLs it contains.

    Returns (processed_message, web_metadata) — the original message and an
    empty dict when the request needs no web search or holds no valid URLs.
    """
    cost_tracker = agent._get_cost_tracker(session_id)
    web_search_processor = WebSearchProcessor(cost_tracker=cost_tracker)

    if not (web_search_processor and await web_search_processor.is_web_search_request(message)):
        logger.debug(f"[{tag}_WEB] No web search requirements detected for session {session_id}")
        return message, {}

    logger.info(f"[{tag}_WEB] Web search request detected for session {session_id}")
    has_urls, enhanced_text, metadata = await web_search_processor.process_text_with_urls(message)

    if not has_urls:
        logger.info(f"[{tag}_WEB] No valid URLs found for web search in session {session_id}")
        return message, {}

    logger.info(
        f"[{tag}_WEB] Processed {metadata['successful_extractions']}/{metadata['urls_found']} "
        f"URLs successfully"
    )
    return enhanced_text, metadata


def _face_selection_metadata(agent, session_id: str, message: str, tag: str) -> dict:
    """Metadata of the face the user picked in the viewer (for the response), if any.

    The message itself is left untouched: the IR edit flow parses the
    `Face Selection: ...` text again to find the matching face of the part.
    Always carries a 'face_detected' key.
    """
    try:
        face_data = agent.face_processor.parse_face_selection(message)
    except Exception as e:
        logger.warning(f"[{tag}_FACE] Error: {e}")
        return {'face_detected': False, 'error': str(e)}

    if not face_data:
        return {'face_detected': False}

    logger.info(
        f"[{tag}_FACE] Detected - ID:{face_data['face_id']} "
        f"Type:{face_data['shape_type']} Session:{session_id}"
    )
    return {
        'face_detected': True,
        'face_id': face_data['face_id'],
        'shape_type': face_data['shape_type'],
        'spatial_context': face_data['spatial_context'],
        'bbox': face_data['bbox'],
        'geometry': face_data['geometry'],
        'context': face_data['context'],
    }


def _sync_latest_code_from_db(db, agent, session_id: str, tag: str) -> None:
    """
    Copy the session's most recent generated script from the DB into agent state.

    Required before an edit request after an API restart: the in-memory state is
    empty, and the IR edit flow takes the part IR back out of this stub script.
    """
    db_latest_code = get_latest_code(db, session_id)
    if db_latest_code:
        agent._update_session_state(session_id, latest_code=db_latest_code)
        # The [EDIT_GATE] line reports whether edit mode actually engaged.
        logger.debug(
            f"[{tag}_EDIT] Synced latest_code from DB → agent state "
            f"({len(db_latest_code)} chars) | session={session_id}"
        )
    else:
        logger.warning(
            f"[{tag}_EDIT] Edit mode requested but no latest_code in DB "
            f"for session={session_id} — will fall back to generation"
        )


async def handle_chat_request(
    db: Session,
    chat_req: ChatRequest,
    agent,
    request_origin: str = 'api'
) -> ChatResponse:
    """
    Handle chat request with session management and web search integration.
    """
    from ..utils.context_manager import get_user_id, set_session_id
    
    start_time = time.time()
    request_received_at = datetime.utcnow()
    
    # Get user_id from context (set by auth middleware)
    user_id = get_user_id() or "anonymous"
    
    # The optional attributes below are absent on almost every request, so they
    # are appended to the one request line rather than printed as their own.
    # This whole block used to be duplicated as a 12-line print() banner that
    # said the same thing outside the logging stack.
    extras = ' '.join(
        f"{label}={value}"
        for label, attr in (
            ('image', 'image_path'), ('part_file', 'part_file_name'),
            ('export', 'export_format'), ('material', 'material_choice'),
            ('feature_uuid', 'selected_feature_uuid'),
        )
        for value in [getattr(chat_req, attr, None)] if value
    )
    logger.info(
        f"[FLOW] Chat request ({request_origin}) '{chat_req.message[:60]}' | "
        f"user={user_id} | edit={chat_req.is_edit_request}"
        f"{' | ' + extras if extras else ''}"
    )

    try:
        logger.debug(f"[SESSION] Resolving session ID for request | user_id={user_id}")
        
        # Wrap database operations with retry logic
        @retry_db_operation(max_retries=3, delay=1.0)
        def _resolve_session_with_retry():
            return _resolve_session_id(db, chat_req)
        
        session_id = _resolve_session_with_retry()
        
        # Store session_id in context for logging
        set_session_id(session_id)
        
        logger.debug(f"[SESSION] Resolved session ID: {session_id} | user_id={user_id}")
        
        @retry_db_operation(max_retries=3, delay=1.0)
        def _get_latest_code_with_retry():
            return get_latest_code(db, session_id)
        
        latest_code = _get_latest_code_with_retry()
        logger.debug(
            f"[SESSION] Latest code in DB: "
            f"{f'{len(latest_code)} characters' if latest_code else 'none'}"
        )

        logger.debug("[SESSION] Ensuring session exists in database")
        session = get_session_by_id(db, session_id)
        if not session:
            session_name = chat_req.message[:50].strip() if chat_req.message else "User Session"
            session = create_session(db, session_id, session_name)
            logger.debug(f"[SESSION] Created new session: {session_id} with name: {session_name}")
        else:
            logger.debug(f"[SESSION] Using existing session: {session_id}")

        chat_req.session_id = session_id

        is_edit_request = chat_req.is_edit_request
        logger.debug(f"[EDIT_MODE] Is edit request: {is_edit_request}")

        if is_edit_request:
            _sync_latest_code_from_db(db, agent, session_id, tag="CHAT")

        processed_message, web_metadata = await _apply_web_search(
            agent, session_id, chat_req.message, tag="CHAT"
        )
        face_metadata = _face_selection_metadata(agent, session_id, processed_message, tag="CHAT")

        logger.debug(f"[AGENT] Starting agent processing for session {session_id}")
        agent_start_time = time.time()

        agent_result = await agent.process_request(
            user_text=processed_message,
            is_edit_request=is_edit_request,
            session_id=session_id,
            request_origin=request_origin
        )
        
        agent_duration = time.time() - agent_start_time
        logger.info(f"[AGENT] Agent processing completed in {agent_duration:.2f}s for session {session_id}")

        # One result line, plus the artefact paths on their own lines so a
        # terminal can turn them into Ctrl+Click links.
        if agent_result.get("error"):
            logger.error(f"[AGENT_RESULT] Agent returned error: {agent_result['error']}")
        else:
            logger.info(
                f"[AGENT_RESULT] code={len(agent_result.get('code') or '')} chars | "
                f"message={len(agent_result.get('message') or '')} chars"
            )
        artefacts = [(kind, agent_result.get(f"{kind}_path")) for kind in ("obj", "step")]
        if any(path for _, path in artefacts):
            logger.info("[FILES] Exported\n" + "\n".join(
                f"  {kind:<9} {path}" for kind, path in artefacts if path
            ))

        if web_metadata:
            agent_result["web_search_metadata"] = web_metadata
            logger.info(f"[WEB_SEARCH] Added web search metadata with {len(web_metadata.get('web_contents', []))} extracted contents")

        if face_metadata and face_metadata.get('face_detected'):
            agent_result["face_metadata"] = face_metadata
            logger.info(f"[FACE] Added metadata - ID:{face_metadata.get('face_id')} Type:{face_metadata.get('shape_type')}")

        logger.debug("[RESPONSE] Processing agent result and creating response")
        
        # Wrap database operations with retry logic
        @retry_db_operation(max_retries=3, delay=1.0)
        def _process_agent_result_with_retry():
            response_generated_at = datetime.utcnow()
            return _process_agent_result(
                db,
                chat_req,
                agent_result,
                session_id,
                request_received_at,
                response_generated_at
            )
        
        response = _process_agent_result_with_retry()
        
        total_duration = time.time() - start_time
        logger.info(f"[FLOW] Chat request handling completed in {total_duration:.2f}s for session {session_id}")
        
        return response

    except DatabaseRetryError as e:
        total_duration = time.time() - start_time
        logger.error(f"[DATABASE] Database retry failed after {total_duration:.2f}s: {str(e)}")
        logger.error(f"[DATABASE] Original error: {e.original_error}")
        raise HTTPException(
            status_code=503,
            detail=f"Database connection error after retries: {str(e.original_error)}"
        )
    except Exception as e:
        total_duration = time.time() - start_time
        
        # Check if it's a connection-related error
        if is_connection_error(e):
            logger.error(f"[DATABASE] Connection error in chat request handling after {total_duration:.2f}s: {str(e)}")
            raise HTTPException(
                status_code=503,
                detail=f"Database connection error: {str(e)}"
            )
        
        logger.error(f"[FLOW] Error in chat request handling after {total_duration:.2f}s: {str(e)}")
        logger.error(f"[FLOW] Traceback: {traceback.format_exc()}")
        raise


def _resolve_session_id(db: Session, chat_req: ChatRequest) -> str:
    """
    Resolve session ID - create new if not provided.
    """
    logger.debug("[SESSION_RESOLVE] Starting session ID resolution")
    session_id = chat_req.session_id
    logger.debug(f"[SESSION_RESOLVE] Input session_id: {session_id}")

    if session_id and session_id != "":
        logger.debug(f"[SESSION_RESOLVE] Validating provided session_id: {session_id}")
        existing_session = db.query(SessionModel).filter(
            SessionModel.session_id == session_id
        ).first()
        if existing_session:
            logger.debug(f"[SESSION_RESOLVE] Found existing session: {session_id}")
            return session_id
        else:
            logger.warning(f"Provided session_id {session_id} does not exist, but will use it for continuity")
            return session_id

    if chat_req.message:
        logger.debug("[SESSION_RESOLVE] Checking message for existing session ID pattern")
        session_pattern = re.compile(r'session_[a-f0-9]{6}_\d{6}')
        session_matches = session_pattern.findall(chat_req.message)
        if session_matches:
            potential_session_id = session_matches[0]
            logger.debug(f"[SESSION_RESOLVE] Found potential session ID in message: {potential_session_id}")
            existing_session = db.query(SessionModel).filter(
                SessionModel.session_id == potential_session_id
            ).first()
            if existing_session:
                logger.debug(f"[SESSION_RESOLVE] Validated session ID from message: {potential_session_id}")
                return potential_session_id
            else:
                logger.warning(f"Session ID found in message but not in database: {potential_session_id}")

    new_session_id = _generate_session_id()
    logger.info(f"[SESSION_CREATE] Generated session ID: {new_session_id}")
    return new_session_id


def _generate_session_id() -> str:
    """
    Generate a new unique session ID.
    """
    rand_digits = random.randint(100000, 999999)
    rand_uuid = uuid.uuid4().hex[:6]
    session_id = f"session_{rand_uuid}_{rand_digits}"
    logger.debug(f"[SESSION_GEN] Generated session ID: {session_id}")
    return session_id


def _is_session_id_collision(exc: Exception) -> bool:
    """
    Check whether an exception raised by create_session really means
    "this session_id is already taken", as opposed to any other failure
    (database unreachable, FK/NOT NULL violation, …).

    Only a collision is worth retrying with a freshly generated id.
    """
    # create_session turns "Session ID already exists" into HTTPException(400);
    # every other HTTPException it raises (e.g. 503 when the DB is down) is not
    # a collision.
    if isinstance(exc, HTTPException):
        return exc.status_code == 400

    if isinstance(exc, IntegrityError):
        # MySQL 1062 = ER_DUP_ENTRY. Fall back to the message when the driver
        # does not expose the numeric code.
        code = getattr(getattr(exc, "orig", None), "errno", None)
        if code is not None:
            return code == 1062
        return "duplicate entry" in str(exc).lower()

    return False


def _process_agent_result(
    db: Session,
    chat_req: ChatRequest,
    agent_result: dict,
    session_id: str,
    request_received_at: Optional[datetime] = None,
    response_generated_at: Optional[datetime] = None
) -> ChatResponse:
    """
    Process agent result and create chat response.
    """
    logger.debug(f"[RESULT_PROCESS] Processing agent result for session {session_id}")
    
    # Priority order: error > message > code > default
    if agent_result.get("error"):
        chat_response_content = agent_result.get("error")  # Remove "Error: " prefix since error message is already user-friendly
        logger.error(f"[RESULT_PROCESS] Using error message as response: {agent_result.get('error')}")
    elif agent_result.get("message") and not agent_result.get("code"):
        chat_response_content = agent_result.get("message")
        logger.info("[RESULT_PROCESS] Using agent message as response (no code generated)")
    elif agent_result.get("code"):
        chat_response_content = get_success_message()
        logger.info("[RESULT_PROCESS] Code generated successfully, using success message")
    else:
        chat_response_content = get_error_message("processing_completed")
        logger.info("[RESULT_PROCESS] Using default completion message")

    # Add response to agent_result for database storage
    agent_result["response"] = chat_response_content

    # Handle export paths (summary already logged in text_to_cad_agent.py)
    obj_export_path, step_export_path, pdf_export_path = _handle_export_paths(chat_req, agent_result)

    logger.info(f"[HISTORY] Adding entry to chat history for session {session_id}")
    
    # Wrap database operations with retry logic
    @retry_db_operation(max_retries=3, delay=1.0)
    def _add_to_chat_history_with_retry():
        return _add_to_chat_history(
            db,
            session_id,
            chat_req,
            agent_result,
            obj_export_path,
            request_received_at,
            response_generated_at
        )

    _add_to_chat_history_with_retry()

    # Create download URLs (summary already logged above)
    obj_url = build_download_url(obj_export_path) if obj_export_path else None
    step_url = build_download_url(step_export_path) if step_export_path else None
    # PDF is optional (visualization only) — a missing/failed PDF must never
    # affect the response, so any URL-building error here is swallowed.
    try:
        pdf_url = build_download_url(pdf_export_path) if pdf_export_path else None
    except Exception as pdf_url_error:
        logger.warning(f"[RESULT_PROCESS] Failed to build PDF download URL (optional, ignored): {pdf_url_error}")
        pdf_url = None

    logger.debug(f"[RESULT_PROCESS] Creating final ChatResponse for session {session_id}")

    # Extract web_search_metadata from agent_result if available
    web_search_metadata = agent_result.get("web_search_metadata", None)
    if web_search_metadata:
        logger.info(f"[RESULT_PROCESS] Including web_search_metadata with {len(web_search_metadata.get('web_contents', []))} contents")

    return ChatResponse(
        chat_response=chat_response_content,
        session_id=session_id,
        obj_export=obj_url,
        step_export=step_url,
        technical_drawing_export=pdf_url,
        tessellated_export=None,
        attribute_and_transientid_map=None,
        manufacturing_errors=[],
        web_search_metadata=web_search_metadata,
    )


def _handle_export_paths(chat_req: ChatRequest, agent_result: dict) -> tuple:
    """
    Handle export file paths based on export format.
    """
    export_format = chat_req.export_format
    obj_path = agent_result.get("obj_path")
    step_path = agent_result.get("step_path")
    # PDF is optional (visualization only) — not affected by export_format filtering below.
    pdf_path = agent_result.get("pdf_path")

    logger.info(f"[EXPORT_PATHS] Processing export format: {export_format}")
    logger.info(f"[EXPORT_PATHS] Available paths - OBJ: {obj_path}, STEP: {step_path}")

    # Only search for recent files if code was actually generated AND no error occurred.
    # This prevents serving old files from a previous session when FreeCAD execution fails.
    code_generated = agent_result.get("code") is not None
    has_error = bool(agent_result.get("error"))

    if not obj_path and not step_path and code_generated and not has_error:
        logger.info("[EXPORT_PATHS] No paths in agent result but code was generated, searching for recent files")
        try:
            from src.utils.file_finder import find_step_file, find_obj_files

            # Look for recent STEP files
            recent_step = find_step_file(time_window=300)  # 5 minutes
            if recent_step:
                step_path = str(recent_step)
                logger.info(f"[EXPORT_PATHS] Found recent STEP file: {step_path}")

            # Look for recent OBJ files
            recent_objs = find_obj_files(time_window=300)  # 5 minutes
            if recent_objs:
                obj_path = str(recent_objs[0])  # Get the most recent
                logger.info(f"[EXPORT_PATHS] Found recent OBJ file: {obj_path}")

        except Exception as e:
            logger.error(f"[EXPORT_PATHS] Error searching for recent files: {e}")
    elif not obj_path and not step_path and not code_generated:
        logger.info("[EXPORT_PATHS] No paths in agent result and no code generated, skipping file search")

    if export_format is None or export_format == "":
        logger.info("[EXPORT_PATHS] No specific format requested, returning both paths")
        return obj_path, step_path, pdf_path
    elif export_format.lower() == "obj":
        logger.info("[EXPORT_PATHS] OBJ format requested, returning OBJ path only")
        return obj_path, None, pdf_path
    elif export_format.lower() == "step":
        logger.info("[EXPORT_PATHS] STEP format requested, returning STEP path only")
        return None, step_path, pdf_path
    else:
        logger.warning(f"[EXPORT_PATHS] Unknown export format '{export_format}', returning both paths")
        return obj_path, step_path, pdf_path


def _add_to_chat_history(
    db: Session,
    session_id: str,
    chat_req: ChatRequest,
    agent_result: dict,
    obj_export_path: Optional[str],
    request_received_at: Optional[datetime] = None,
    response_generated_at: Optional[datetime] = None
):
    """
    Add entry to chat history.
    """
    try:
        add_chat_history_entry(
            db=db,
            session_id=session_id,
            user_message=chat_req.message,
            agent_result=agent_result,
            chat_request_obj=chat_req,
            obj_export_path=obj_export_path,
            created_at=request_received_at,
            response_at=response_generated_at
        )
    except DatabaseRetryError as e:
        logger.error(f"[CHAT_HISTORY] Database retry failed for session {session_id}: {str(e)}")
        logger.error(f"[CHAT_HISTORY] Original error: {e.original_error}")
        # Re-raise to be handled by calling function
        raise
    except Exception as e:
        # Check if it's a connection-related error
        if is_connection_error(e):
            logger.error(f"[CHAT_HISTORY] Database connection error for session {session_id}: {str(e)}")
            raise DatabaseRetryError(f"Connection error in chat history: {str(e)}", e)

        logger.error(f"[CHAT_HISTORY] Traceback: {traceback.format_exc()}")
        raise


async def generate_cad_realtime_stream(
    message: str,
    is_edit_request: bool,
    session_id: Optional[str],
    agent: TextToCADAgent,
    material_choice: Optional[str] = "STEEL",
    priority: int = 0
):
    """
    Asynchronously generates CAD and streams real-time progress updates with flexible flow.
    Uses short-lived database connections to prevent pool exhaustion during long-running streams.
    """
    from ..database.database import get_db_session

    stream_start_time = time.time()
    request_received_at = datetime.utcnow()
    logger.debug(f"[STREAM] Starting real-time CAD generation stream for: '{message[:50]}...'")
    logger.debug(f"[STREAM] Stream parameters - edit: {is_edit_request}, session: {session_id}, priority: {priority}")

    try:
        # Use short-lived connection for initial session setup
        logger.debug("[STREAM] Resolving session ID for streaming request")
        with get_db_session() as db:
            # Distinguish clearly: session_id provided by CLIENT vs generated by SERVER
            client_provided_session_id = session_id  # Save the original value from the client

            resolved_session_id = _resolve_session_id(db, ChatRequest(message=message, session_id=session_id, is_edit_request=is_edit_request))

            # ── Inject session_id into logging context ────────────────────
            # Set as early as the id exists: both formatters read this
            # contextvar, so every line from here on carries the session — the
            # per-message `| session=…` trailers become redundant, and the
            # console prints a short tag in its own column instead.
            from ..utils.context_manager import set_session_id
            set_session_id(resolved_session_id)

            # Which side minted the id matters when tracing a lost continuation;
            # everything else about it is already the per-line session tag.
            if client_provided_session_id and client_provided_session_id != resolved_session_id:
                logger.warning(
                    f"[SESSION] Client id '{client_provided_session_id}' resolved to "
                    f"'{resolved_session_id}'"
                )
            elif not client_provided_session_id:
                logger.info("[SESSION] New session (no client id provided)")

            logger.debug("[STREAM] Ensuring session exists in database")
            session_name = message[:50].strip() if message else "User Session"

            # ─────────────────────────────────────────────────────────────────
            # OPTIMISTIC INSERT: attempt INSERT directly instead of SELECT→check→INSERT.
            # More efficient because collisions are extremely rare → saves 1 DB round-trip.
            # ─────────────────────────────────────────────────────────────────
            if client_provided_session_id:
                # Client provided a session_id → continue that session (multi-turn continuation)
                session_obj = get_session_by_id(db, resolved_session_id)
                if not session_obj:
                    # Client-specified session does not exist in DB yet → create it
                    create_session(db, resolved_session_id, session_name)
                    logger.info(f"[SESSION_CREATE] Client-provided session created: {resolved_session_id}")
                else:
                    # Session already exists → continue multi-turn conversation (expected path)
                    logger.debug(f"[STREAM] Continuing existing client session: {resolved_session_id}")
            else:
                # Server generated a new session_id → use OPTIMISTIC INSERT strategy
                # On collision (IntegrityError) → generate a new unique ID and retry
                max_retries = 5
                for attempt in range(max_retries):
                    try:
                        create_session(db, resolved_session_id, session_name)
                        logger.info(f"[SESSION_CREATE] Generated session ID: {resolved_session_id} (attempt {attempt + 1})")
                        break  # Success → exit retry loop
                    except (IntegrityError, HTTPException) as e:
                        db.rollback()
                        # Only a genuine duplicate session_id is worth retrying. Anything else
                        # (DB down, FK/NOT NULL violation, …) would fail identically on every
                        # attempt, so re-raise it with its real cause instead of burning
                        # `max_retries` × connect-timeout and reporting a bogus collision.
                        if not _is_session_id_collision(e):
                            logger.error(
                                f"[SESSION_CREATE] Non-collision failure creating "
                                f"'{resolved_session_id}': {e}"
                            )
                            raise
                        # Collision detected → generate a new session_id and retry
                        old_id = resolved_session_id
                        resolved_session_id = _generate_session_id()
                        logger.warning(
                            f"[SESSION_COLLISION] Collision on '{old_id}' "
                            f"(attempt {attempt + 1}/{max_retries}) → retrying with '{resolved_session_id}'"
                        )
                        if attempt == max_retries - 1:
                            logger.error(f"[SESSION_COLLISION] Failed to create a unique session_id after {max_retries} attempts!")
                            raise RuntimeError(f"Failed to generate unique session_id after {max_retries} attempts")
        # Connection is released here automatically

        # EMIT SESSION_ID IMMEDIATELY - Send to frontend before any processing steps
        logger.debug(f"[STREAM_SESSION] Emitting session_id to frontend: {resolved_session_id}")
        
        yield {
            "session_id": resolved_session_id,
            "step": "session_initialized",
            "status": f"Session initialized: {resolved_session_id}",
            "message": "Session created",
            "is_complete": True,
            "is_active": False,
            "overall_percentage": 0
        }

        processed_message, web_metadata = await _apply_web_search(
            agent, resolved_session_id, message, tag="STREAM"
        )
        face_metadata = _face_selection_metadata(agent, resolved_session_id, processed_message, tag="STREAM")

        # Sync BEFORE streaming starts — see _sync_latest_code_from_db for why.
        if is_edit_request:
            with get_db_session() as _db_edit:
                _sync_latest_code_from_db(_db_edit, agent, resolved_session_id, tag="STREAM")

        logger.debug("[STREAM] Initializing progress tracking")
        step_progress = {
            "analysis": False,
            "parameters": False,
            "generation_code": False,
            "export": False,
            "complete": False
        }
        completed_steps = 0
        total_steps = 5

        logger.debug(f"[STREAM] Starting agent progress streaming for session {resolved_session_id}")
        logger.debug(f"[STREAM] Material choice for streaming: {material_choice}")
        logger.debug(f"[STREAM] FreeCAD priority for streaming: {priority} | session={resolved_session_id}")
        step_count = 0
        async for progress_update in agent.process_request_with_progress(
            user_text=processed_message,
            is_edit_request=is_edit_request,
            session_id=resolved_session_id,
            material_choice=material_choice,
            priority=priority
        ):
            step_count += 1
            logger.debug(f"[STREAM_PROGRESS] Received progress update #{step_count} for session {resolved_session_id}")
            
            if "step" in progress_update:
                step_name = progress_update["step"]
                is_complete = progress_update.get("is_complete", False)
                is_active = progress_update.get("is_active", False)
                status = progress_update.get("status", "Processing...")
                progress = progress_update.get("progress", 0)
                
                logger.debug(f"[STREAM_STEP] Step: {step_name}, Complete: {is_complete}, Active: {is_active}, Progress: {progress}%")
                
                if is_complete and not step_progress.get(step_name, False):
                    step_progress[step_name] = True
                    completed_steps += 1
                    # The SSE layer in api/main.py logs "Step completed: <step> (n%)"
                    # for the same event, with the progress figure attached.
                    logger.debug(f"[STREAM_STEP] Step {step_name} completed, {completed_steps}/{total_steps} steps done")
                
                icon = "fas fa-hourglass-start"
                if is_active:
                    if step_name == "analysis":
                        icon = "fas fa-search fa-spin"
                    elif step_name == "parameters":
                        icon = "fas fa-list-alt fa-spin"
                    elif step_name == "generation_code":
                        icon = "fas fa-code fa-spin"
                    elif step_name == "export":
                        icon = "fas fa-file-export fa-spin"
                    elif step_name == "complete":
                        icon = "fas fa-check-circle fa-spin"
                elif is_complete:
                    if step_name == "analysis":
                        icon = "fas fa-search"
                    elif step_name == "parameters":
                        icon = "fas fa-list-alt"
                    elif step_name == "generation_code":
                        icon = "fas fa-code"
                    elif step_name == "export":
                        icon = "fas fa-file-export"
                    elif step_name == "complete":
                        icon = "fas fa-check-circle"
                
                overall_percentage = min(95, (completed_steps * 100) // total_steps)
                if progress > 0:
                    overall_percentage = progress

                # Build response with standard fields
                response_data = {
                    "step": step_name,
                    "status": status,
                    "message": "Completed" if is_complete else "Processing...",
                    "icon": icon,
                    "is_complete": is_complete,
                    "is_active": is_active,
                    "overall_percentage": overall_percentage,
                }

                if "design_type" in progress_update:
                    response_data["design_type"] = progress_update["design_type"]

                yield response_data

                logger.debug(f"[STREAM_YIELD] Progress update: {step_name} - {status} ({overall_percentage}%)")
                
            elif "final_result" in progress_update:
                logger.debug(f"[STREAM_FINAL] Received final result for session {resolved_session_id}")
                agent_result = progress_update["final_result"]

                if web_metadata:
                    agent_result["web_search_metadata"] = web_metadata
                    logger.info(
                        f"[WEB_SEARCH] Attached {len(web_metadata.get('web_contents', []))} results"
                    )
                    for idx, content in enumerate(web_metadata.get('web_contents', []), 1):
                        logger.debug(
                            f"[WEB_SEARCH] {idx}. {content.get('url', 'N/A')[:80]} "
                            f"({len(content.get('content', ''))} chars)"
                        )
                else:
                    logger.debug("[STREAM_FINAL] No web metadata to add")
                
                if face_metadata and face_metadata.get('face_detected'):
                    agent_result["face_metadata"] = face_metadata
                    logger.info(f"[STREAM_FINAL] ✅ Face metadata - ID:{face_metadata.get('face_id')} Type:{face_metadata.get('shape_type')}")
                else:
                    logger.debug("[STREAM_FINAL] No face metadata")
                
                # ── Collect per-request cost summary ──────────────────────────
                # get_request_summary() is snapshot-based: always returns cost for
                # THIS turn only, never blocked by _logged flag.
                request_cost_summary = {}
                try:
                    tracker = agent._get_cost_tracker(resolved_session_id)
                    request_cost_summary = tracker.get_request_summary()
                    # The same three figures are the TOTAL row of the turn-cost
                    # table that log_request_cost() prints a few lines later.
                    logger.debug(
                        f"[STREAM_FINAL] Request cost: ${request_cost_summary.get('request_total_cost_usd', 0):.4f} | "
                        f"chains={request_cost_summary.get('request_chains', 0)} | "
                        f"tokens={request_cost_summary.get('request_total_tokens', 0):,}"
                    )
                except Exception as _ce:
                    logger.warning(f"[STREAM_FINAL] Could not get request cost: {_ce}")
                
                if not step_progress.get("complete", False):
                    # "Step completed: complete (100%)" covers this for the console.
                    logger.debug("All processing completed")
                    yield {
                        "step": "complete",
                        "status": "All processing completed!",
                        "message": "Completed",
                        "icon": "fas fa-check-circle",
                        "is_complete": True,
                        "is_active": False,
                        "overall_percentage": 100,
                    }
                
                logger.debug("[STREAM_FINAL] Processing agent result and creating final response")
                # Use short-lived connection for final result processing
                with get_db_session() as db:
                    # CRITICAL FIX: Process result if we have code OR no error
                    # This ensures code is saved to database even when export fails
                    has_code = agent_result and agent_result.get("code")
                    has_error = agent_result and agent_result.get("error")
                    
                    if has_code or (agent_result and not has_error):
                        chat_req_obj = ChatRequest(
                            message=processed_message,
                            is_edit_request=is_edit_request,
                            session_id=resolved_session_id
                        )
                        response_generated_at = datetime.utcnow()
                        final_response = _process_agent_result(
                            db,
                            chat_req_obj,
                            agent_result,
                            resolved_session_id,
                            request_received_at,
                            response_generated_at
                        )
                        
                        # If there was an error, update the response to include error message
                        if has_error:
                            logger.warning(f"[STREAM_FINAL] Code saved but export error occurred: {agent_result.get('error')}")
                            # Create response with error message but keep the fact that code was saved
                            final_response = ChatResponse(
                                chat_response=agent_result.get('error'),
                                session_id=resolved_session_id,
                                obj_export=None,
                                step_export=None,
                                tessellated_export=None,
                                attribute_and_transientid_map=None,
                                manufacturing_errors=[],
                            )
                        logger.debug(f"[STREAM_FINAL] Successfully created final response for session {resolved_session_id}")
                    else:
                        error_msg = agent_result.get("error", "Unknown error occurred") if agent_result else "Agent processing failed"
                        logger.error(f"Error in agent result: {error_msg}")
                        final_response = ChatResponse(
                            chat_response=error_msg,
                            session_id=resolved_session_id,
                            obj_export=None,
                            step_export=None,
                            tessellated_export=None,
                            attribute_and_transientid_map=None,
                            manufacturing_errors=[],
                        )
                # Connection is released here automatically

                # ── Attach cost info to final_response ────────────────────────
                if request_cost_summary:
                    final_response.total_cost_usd = request_cost_summary.get("request_total_cost_usd")
                    final_response.cost_breakdown = {
                        "request_chains": request_cost_summary.get("request_chains", 0),
                        "request_total_tokens": request_cost_summary.get("request_total_tokens", 0),
                        "request_prompt_tokens": request_cost_summary.get("request_prompt_tokens", 0),
                        "request_completion_tokens": request_cost_summary.get("request_completion_tokens", 0),
                        "chain_breakdown": request_cost_summary.get("chain_breakdown", []),
                    }

                yield {"final_response": final_response.model_dump()}
                
                stream_duration = time.time() - stream_start_time
                logger.debug(f"[STREAM] Real-time CAD generation completed for session {resolved_session_id} in {stream_duration:.2f}s")

                return

    except Exception as e:
        stream_duration = time.time() - stream_start_time
        logger.error(f"Error in real-time CAD generation after {stream_duration:.2f}s: {str(e)}")
        logger.debug(f"[STREAM] Traceback: {traceback.format_exc()}")
        yield {"error": f"Failed to generate CAD: {str(e)}"}
