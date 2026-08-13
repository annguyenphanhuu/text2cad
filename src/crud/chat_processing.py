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
import json
import pprint
import time
import traceback
from datetime import datetime

import asyncio # Added for async operations
from ..models.sessions import Session as SessionModel, ChatHistory
from ..schemas.sessions import ChatRequest, ChatResponse
from ..core.text_to_cad_agent import TextToCADAgent
from .sessions import create_session, get_session_by_id, update_session, get_latest_code, add_chat_history_entry
from ..utils.web_search_handler import WebSearchProcessor
from ..utils.language_utils import detect_language, get_success_message, get_error_message, get_session_language
from ..database.db_retry import retry_db_operation, DatabaseRetryError, is_connection_error

logger = logging.getLogger(__name__)

# Web search processor will be initialized per-request with cost_tracker
# web_search_processor = WebSearchProcessor()

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
    
    logger.info(f"[FLOW] Starting chat request handling - Origin: {request_origin} | user_id={user_id}")
    logger.info(f"[FLOW] Request details: message='{chat_req.message[:100]}...', session_id={chat_req.session_id}, is_edit={chat_req.is_edit_request}")
    
    print("\n" + "="*80)
    print(f"[DEBUG] CHATBOT REQUEST INFO (Origin: {request_origin})")
    print("-"*80)
    print(f"User ID: {user_id}")
    print(f"Message: {chat_req.message}")
    print(f"Session ID: {chat_req.session_id}")
    print(f"Is Edit Request: {chat_req.is_edit_request}")

    if hasattr(chat_req, 'image_path') and chat_req.image_path:
        print(f"Image Path: {chat_req.image_path}")
        logger.info(f"[FLOW] Image path provided: {chat_req.image_path}")
    if hasattr(chat_req, 'part_file_name') and chat_req.part_file_name:
        print(f"Part File Name: {chat_req.part_file_name}")
        logger.info(f"[FLOW] Part file name: {chat_req.part_file_name}")
    if hasattr(chat_req, 'export_format') and chat_req.export_format:
        print(f"Export Format: {chat_req.export_format}")
        logger.info(f"[FLOW] Export format: {chat_req.export_format}")
    if hasattr(chat_req, 'material_choice') and chat_req.material_choice:
        print(f"Material Choice: {chat_req.material_choice}")
        logger.info(f"[FLOW] Material choice: {chat_req.material_choice}")
    if hasattr(chat_req, 'selected_feature_uuid') and chat_req.selected_feature_uuid:
        print(f"Selected Feature UUID: {chat_req.selected_feature_uuid}")
        logger.info(f"[FLOW] Selected feature UUID: {chat_req.selected_feature_uuid}")

    try:
        logger.info(f"[SESSION] Resolving session ID for request | user_id={user_id}")
        
        # Wrap database operations with retry logic
        @retry_db_operation(max_retries=3, delay=1.0)
        def _resolve_session_with_retry():
            return _resolve_session_id(db, chat_req)
        
        session_id = _resolve_session_with_retry()
        
        # Store session_id in context for logging
        set_session_id(session_id)
        
        logger.info(f"[SESSION] Resolved session ID: {session_id} | user_id={user_id}")
        
        @retry_db_operation(max_retries=3, delay=1.0)
        def _get_latest_code_with_retry():
            return get_latest_code(db, session_id)
        
        latest_code = _get_latest_code_with_retry()
        if latest_code:
            print(f"Latest Code Available: Yes (Length: {len(latest_code)} characters)")
            logger.info(f"[SESSION] Found latest code in DB - Length: {len(latest_code)} characters")
        else:
            print(f"Latest Code Available: No")
            logger.info(f"[SESSION] No latest code found in database")

        try:
            state = agent._get_session_state(session_id)
            if 'latest_requirements' in state and state['latest_requirements']:
                print("-"*80)
                print("Latest RAG Context:")
                try:
                    print(json.dumps(state['latest_requirements'].dict(), indent=2))
                    logger.info(f"[AGENT_STATE] Retrieved RAG context for session {session_id}")
                except:
                    print(pprint.pformat(state['latest_requirements'], indent=2))
                    logger.info(f"[AGENT_STATE] Retrieved RAG context (pformat) for session {session_id}")
            else:
                logger.info(f"[AGENT_STATE] No latest requirements found in agent state for session {session_id}")
        except Exception as e:
            print(f"Error accessing agent state: {str(e)}")
            logger.warning(f"[AGENT_STATE] Error accessing agent state for session {session_id}: {str(e)}")

        print("="*80 + "\n")

        logger.info(f"[SESSION] Ensuring session exists in database")
        session = get_session_by_id(db, session_id)
        if not session:
            session_name = chat_req.message[:50].strip() if chat_req.message else "User Session"
            session = create_session(db, session_id, session_name)
            logger.info(f"[SESSION] Created new session: {session_id} with name: {session_name}")
        else:
            logger.info(f"[SESSION] Using existing session: {session_id}")

        chat_req.session_id = session_id

        is_edit_request = chat_req.is_edit_request
        logger.info(f"[EDIT_MODE] Is edit request: {is_edit_request}")

        if is_edit_request:
            logger.info(f"[EDIT_MODE] Processing edit request - retrieving latest code")
            db_latest_code = get_latest_code(db, session_id)
            if db_latest_code:
                logger.info(f"[EDIT_MODE] Retrieved latest code from database - Length: {len(db_latest_code)} characters")
                agent._update_session_state(session_id, latest_code=db_latest_code)
                logger.info(f"[EDIT_MODE] Synced database latest_code to agent session state for {session_id}")
            else:
                logger.warning(f"[EDIT_MODE] Edit mode requested but no latest code found in database for session {session_id}")

        logger.info(f"[WEB_SEARCH] Checking for web search requirements")
        processed_message = chat_req.message
        web_metadata = {}

        # Create web search processor with cost_tracker from agent
        cost_tracker = agent._get_cost_tracker(session_id)
        web_search_processor = WebSearchProcessor(cost_tracker=cost_tracker)

        # Use await for async web search methods
        if web_search_processor and await web_search_processor.is_web_search_request(chat_req.message):
            logger.info(f"[WEB_SEARCH] Web search request detected for session {session_id}")

            # Use await for async method - returns 3 values: (has_urls, enhanced_text, metadata)
            has_urls, enhanced_text, metadata = await web_search_processor.process_text_with_urls(chat_req.message)

            if has_urls:
                logger.info(f"[WEB_SEARCH] Processed {metadata['successful_extractions']}/{metadata['urls_found']} URLs successfully")
                processed_message = enhanced_text
                web_metadata = metadata
            else:
                logger.info(f"[WEB_SEARCH] No valid URLs found for web search in session {session_id}")
        else:
            logger.info(f"[WEB_SEARCH] No web search requirements detected for session {session_id}")

        # 🆕 FACE PROCESSING: Detect and enhance face selection
        face_metadata = {}
        
        if hasattr(agent, 'face_processor'):
            try:
                # Parse face selection from message
                face_data = agent.face_processor.parse_face_selection(processed_message)
                
                if face_data:
                    logger.info(f"[FACE] Detected - ID:{face_data['face_id']} Type:{face_data['shape_type']} Session:{session_id}")
                    
                    # Enhance message with face context
                    enhanced_message = agent.face_processor.enhance_user_request(processed_message)
                    processed_message = enhanced_message
                    
                    # Store face metadata for later use
                    face_metadata = {
                        'face_detected': True,
                        'face_id': face_data['face_id'],
                        'shape_type': face_data['shape_type'],
                        'spatial_context': face_data['spatial_context'],
                        'bbox': face_data['bbox'],
                        'geometry': face_data['geometry'],
                        'context': face_data['context']
                    }
                    
                    logger.debug(f"[FACE] Enhanced message: {len(processed_message)} chars")
                else:
                    logger.debug(f"[FACE] No selection detected")
                    face_metadata = {'face_detected': False}
            except Exception as e:
                logger.warning(f"[FACE] Error: {e}")
                face_metadata = {'face_detected': False, 'error': str(e)}
        else:
            logger.debug(f"[FACE] Processor not available")
            face_metadata = {'face_detected': False, 'error': 'face_processor not available'}

        logger.info(f"[AGENT] Starting agent processing for session {session_id}")
        agent_start_time = time.time()

        agent_result = await agent.process_request(
            user_text=processed_message,
            is_edit_request=is_edit_request,
            session_id=session_id,
            request_origin=request_origin
        )
        
        agent_duration = time.time() - agent_start_time
        logger.info(f"[AGENT] Agent processing completed in {agent_duration:.2f}s for session {session_id}")

        print("\n" + "="*80)
        print(f"[DEBUG] CHATBOT RESPONSE SUMMARY (Origin: {request_origin})")
        print("-"*80)
        if "code" in agent_result and agent_result["code"]:
            print(f"Generated Code: Yes (Length: {len(agent_result['code'])} characters)")
            logger.info(f"[AGENT_RESULT] Code generated - Length: {len(agent_result['code'])} characters")
        else:
            print(f"Generated Code: No")
            logger.info(f"[AGENT_RESULT] No code generated")

        if "message" in agent_result and agent_result["message"]:
            print(f"Response Message: {agent_result['message'][:200]}...")
            logger.info(f"[AGENT_RESULT] Response message present - Length: {len(agent_result['message'])} characters")
        elif "error" in agent_result and agent_result["error"]:
            print(f"Error: {agent_result['error']}")
            logger.error(f"[AGENT_RESULT] Agent returned error: {agent_result['error']}")

        if "obj_path" in agent_result and agent_result["obj_path"]:
            print(f"OBJ Export Path: {agent_result['obj_path']}")
            logger.info(f"[AGENT_RESULT] OBJ export path: {agent_result['obj_path']}")
        if "step_path" in agent_result and agent_result["step_path"]:
            print(f"STEP Export Path: {agent_result['step_path']}")
            logger.info(f"[AGENT_RESULT] STEP export path: {agent_result['step_path']}")
        print("="*80 + "\n")

        if web_metadata:
            agent_result["web_search_metadata"] = web_metadata
            logger.info(f"[WEB_SEARCH] Added web search metadata with {len(web_metadata.get('web_contents', []))} extracted contents")

        if face_metadata and face_metadata.get('face_detected'):
            agent_result["face_metadata"] = face_metadata
            logger.info(f"[FACE] Added metadata - ID:{face_metadata.get('face_id')} Type:{face_metadata.get('shape_type')}")

        logger.info(f"[RESPONSE] Processing agent result and creating response")
        
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
    logger.debug(f"[SESSION_RESOLVE] Starting session ID resolution")
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
        logger.debug(f"[SESSION_RESOLVE] Checking message for existing session ID pattern")
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
    logger.info(f"[RESULT_PROCESS] Processing agent result for session {session_id}")
    
    # Priority order: error > message > code > default
    if agent_result.get("error"):
        chat_response_content = agent_result.get("error")  # Remove "Error: " prefix since error message is already user-friendly
        logger.error(f"[RESULT_PROCESS] Using error message as response: {agent_result.get('error')}")
    elif agent_result.get("message") and not agent_result.get("code"):
        chat_response_content = agent_result.get("message")
        logger.info(f"[RESULT_PROCESS] Using agent message as response (no code generated)")
    elif agent_result.get("code"):
        # Use session-cached language (detected from first substantive message by gpt-4.1-nano)
        # Avoids false EN detection when current message is a short word like "ok", "yes"
        session_lang = get_session_language(session_id)
        chat_response_content = get_success_message(session_lang)
        logger.info(f"[RESULT_PROCESS] Code generated successfully, using success message (lang={session_lang})")
    else:
        session_lang = get_session_language(session_id)
        chat_response_content = get_error_message(session_lang, "processing_completed")
        logger.info(f"[RESULT_PROCESS] Using default completion message (lang={session_lang})")

    # Add response to agent_result for database storage
    agent_result["response"] = chat_response_content

    # Handle export paths (summary already logged in text_to_cad_agent.py)
    obj_export_path, step_export_path, json_export_path, pdf_export_path = _handle_export_paths(chat_req, agent_result)

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
            step_export_path,
            json_export_path,
            request_received_at,
            response_generated_at
        )

    _add_to_chat_history_with_retry()

    # Create download URLs (summary already logged above)
    obj_url = _create_download_url(obj_export_path) if obj_export_path else None
    step_url = _create_download_url(step_export_path) if step_export_path else None
    json_url = _create_download_url(json_export_path) if json_export_path else None
    # PDF is optional (visualization only) — a missing/failed PDF must never
    # affect the response, so any URL-building error here is swallowed.
    try:
        pdf_url = _create_download_url(pdf_export_path) if pdf_export_path else None
    except Exception as pdf_url_error:
        logger.warning(f"[RESULT_PROCESS] Failed to build PDF download URL (optional, ignored): {pdf_url_error}")
        pdf_url = None

    logger.info(f"[RESULT_PROCESS] Creating final ChatResponse for session {session_id}")

    # Extract web_search_metadata from agent_result if available
    web_search_metadata = agent_result.get("web_search_metadata", None)
    if web_search_metadata:
        logger.info(f"[RESULT_PROCESS] Including web_search_metadata with {len(web_search_metadata.get('web_contents', []))} contents")

    # Use session-cached language (populated by gpt-4.1-nano on first turn)
    detected_lang = get_session_language(session_id)
    logger.info(f"[RESULT_PROCESS] Session language: {detected_lang}")

    return ChatResponse(
        chat_response=chat_response_content,
        session_id=session_id,
        obj_export=obj_url,
        step_export=step_url,
        json_export=json_url,
        technical_drawing_export=pdf_url,
        tessellated_export=None,
        attribute_and_transientid_map=None,
        manufacturing_errors=[],
        web_search_metadata=web_search_metadata,
        detected_language=detected_lang
    )


def _handle_export_paths(chat_req: ChatRequest, agent_result: dict) -> tuple:
    """
    Handle export file paths based on export format.
    """
    export_format = chat_req.export_format
    obj_path = agent_result.get("obj_path")
    step_path = agent_result.get("step_path")
    json_path = agent_result.get("json_path")
    # PDF is optional (visualization only) — not affected by export_format filtering below.
    pdf_path = agent_result.get("pdf_path")

    logger.info(f"[EXPORT_PATHS] Processing export format: {export_format}")
    logger.info(f"[EXPORT_PATHS] Available paths - OBJ: {obj_path}, STEP: {step_path}, JSON: {json_path}")

    # Only search for recent files if code was actually generated AND no error occurred.
    # This prevents serving old files from a previous session when FreeCAD execution fails.
    code_generated = agent_result.get("code") is not None
    has_error = bool(agent_result.get("error"))

    if not obj_path and not step_path and not json_path and code_generated and not has_error:
        logger.info(f"[EXPORT_PATHS] No paths in agent result but code was generated, searching for recent files")
        try:
            from src.utils.file_finder import find_step_file, find_obj_files
            import time

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
        logger.info(f"[EXPORT_PATHS] No paths in agent result and no code generated, skipping file search")

    if export_format is None or export_format == "":
        logger.info(f"[EXPORT_PATHS] No specific format requested, returning both paths")
        return obj_path, step_path, json_path, pdf_path
    elif export_format.lower() == "obj":
        logger.info(f"[EXPORT_PATHS] OBJ format requested, returning OBJ path only")
        return obj_path, None, json_path, pdf_path
    elif export_format.lower() == "step":
        logger.info(f"[EXPORT_PATHS] STEP format requested, returning STEP path only")
        return None, step_path, json_path, pdf_path
    else:
        logger.warning(f"[EXPORT_PATHS] Unknown export format '{export_format}', returning both paths")
        return obj_path, step_path, json_path, pdf_path


def _add_to_chat_history(
    db: Session,
    session_id: str,
    chat_req: ChatRequest,
    agent_result: dict,
    obj_export_path: Optional[str],
    step_export_path: Optional[str],
    json_export_path: Optional[str] = None,
    request_received_at: Optional[datetime] = None,
    response_generated_at: Optional[datetime] = None
):
    """
    Add entry to chat history.
    """
    # logger.info(f"[CHAT_HISTORY] Adding chat history entry for session {session_id}")
    
    # if agent_result.get("error"):
    #     output = f"ERROR_RESPONSE: {agent_result.get('error')}"
    #     logger.error(f"[CHAT_HISTORY] Adding error response to history: {agent_result.get('error')}")
    # elif agent_result.get("code"):
    #     output = f"CODE_GENERATED: {agent_result.get('code')}"
    #     logger.info(f"[CHAT_HISTORY] Adding code generation to history - Length: {len(agent_result.get('code'))} characters")
    # elif agent_result.get("message"):
    #     output = agent_result.get("message")
    #     logger.info(f"[CHAT_HISTORY] Adding message response to history - Length: {len(agent_result.get('message'))} characters")
    # else:
    #     output = "Agent produced an unknown response structure."
    #     logger.warning(f"[CHAT_HISTORY] Adding unknown response structure to history")

    lasted_code = agent_result.get("code") if agent_result.get("code") else None
    export_format = chat_req.export_format or "both"
    
    # logger.info(f"[CHAT_HISTORY] Export format for history: {export_format}")

    try:
        add_chat_history_entry(
            db=db,
            session_id=session_id,
            user_message=chat_req.message,
            agent_result=agent_result,
            chat_request_obj=chat_req,
            obj_export_path=obj_export_path,
            json_export_path=json_export_path,
            created_at=request_received_at,
            response_at=response_generated_at
        )
        # logger.info(f"[CHAT_HISTORY] Successfully added chat history entry for session {session_id}")
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
        
        # logger.error(f"[CHAT_HISTORY] Error adding chat history entry for session {session_id}: {str(e)}")
        logger.error(f"[CHAT_HISTORY] Traceback: {traceback.format_exc()}")
        raise

    # if lasted_code:
    #     logger.info(f"[CHAT_HISTORY] Stored latest code in chat_history for session {session_id} ({len(lasted_code)} characters)")


def _create_download_url(file_path: str) -> str:
    """
    Create full download URL with domain for the file path.
    """
    import os
    from pathlib import Path

   

    # Get BASE_URL from environment or use default
    PORT = os.getenv("PORT", "8124")
    DOMAIN = os.getenv("DOMAIN", "http://localhost")

    # Only include port in BASE_URL if DOMAIN is localhost
    if DOMAIN == "http://localhost" or DOMAIN == "localhost":
        BASE_URL = f"{DOMAIN}:{PORT}"
    else:
        BASE_URL = DOMAIN

   

    # Convert to relative path first
    project_root = Path.cwd()
    project_root_str = str(project_root).replace('\\', '/')
    file_path_str = str(file_path).replace('\\', '/')



    if project_root_str in file_path_str:
        # Extract the part after the project root
        relative_path = file_path_str.split(project_root_str, 1)[1].lstrip('\\/')
        # Create full download URL
        # Convert outputs/obj/date/file.obj to /download/outputs/obj/date/file.obj format
        if relative_path.startswith('outputs/obj/'):
            path_parts = relative_path.split('/')
            if len(path_parts) >= 4:  # outputs/obj/date/filename
                date_dir = path_parts[2]
                filename = path_parts[3]
                download_url = f"{BASE_URL}/download/outputs/obj/{date_dir}/{filename}"
            else:
                download_url = f"{BASE_URL}/download/{relative_path}"
        elif relative_path.startswith('outputs/json/') or relative_path.startswith('outputs/json_latest/'):
            path_parts = relative_path.split('/')
            if len(path_parts) >= 4:  # outputs/json/date/filename or outputs/json_latest/date/filename
                json_type = path_parts[1]  # 'json' or 'json_latest'
                date_dir = path_parts[2]
                filename = path_parts[3]
                download_url = f"{BASE_URL}/download/outputs/{json_type}/{date_dir}/{filename}"
            else:
                download_url = f"{BASE_URL}/download/{relative_path}"
        elif relative_path.startswith('outputs/cad/'):
            # Extract filename and date directory for STEP files (stored in cad directory)
            path_parts = relative_path.split('/')
            if len(path_parts) >= 4:  # outputs/cad/date/filename
                date_dir = path_parts[2]
                filename = path_parts[3]
                download_url = f"{BASE_URL}/download/outputs/cad/{date_dir}/{filename}"
            else:
                download_url = f"{BASE_URL}/download/{relative_path}"
        elif relative_path.startswith('outputs/pdf/'):
            path_parts = relative_path.split('/')
            if len(path_parts) >= 4:  # outputs/pdf/date/filename
                date_dir = path_parts[2]
                filename = path_parts[3]
                download_url = f"{BASE_URL}/download/outputs/pdf/{date_dir}/{filename}"
            else:
                download_url = f"{BASE_URL}/download/{relative_path}"
        else:
            download_url = f"{BASE_URL}/download/{relative_path}"

        return download_url
    else:
        # If path doesn't contain project root, treat as filename and try to construct URL
        filename = os.path.basename(file_path)
        if filename.endswith('.obj'):
            # Assume it's in today's outputs/obj directory
            from datetime import datetime
            today = datetime.now().strftime('%Y-%m-%d')
            download_url = f"{BASE_URL}/download/outputs/obj/{today}/{filename}"
        elif filename.endswith('.step'):
            # Assume it's in today's outputs/cad directory
            from datetime import datetime
            today = datetime.now().strftime('%Y-%m-%d')
            download_url = f"{BASE_URL}/download/outputs/cad/{today}/{filename}"
        elif filename.endswith('.pdf') or filename.endswith('.svg'):
            # Assume it's in today's outputs/pdf directory
            from datetime import datetime
            today = datetime.now().strftime('%Y-%m-%d')
            download_url = f"{BASE_URL}/download/outputs/pdf/{today}/{filename}"
        else:
            download_url = f"{BASE_URL}/download/{file_path}"

        return download_url


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
        logger.debug(f"[STREAM] Resolving session ID for streaming request")
        with get_db_session() as db:
            # Distinguish clearly: session_id provided by CLIENT vs generated by SERVER
            client_provided_session_id = session_id  # Save the original value from the client

            resolved_session_id = _resolve_session_id(db, ChatRequest(message=message, session_id=session_id, is_edit_request=is_edit_request))

            # ════════════════════════════════════════════════════════════
            # DEBUG LOG: Show whether session_id was provided by client or generated by server
            # ════════════════════════════════════════════════════════════
            if client_provided_session_id:
                logger.info(
                    f"\n{'='*60}\n"
                    f"[SESSION] CLIENT PROVIDED session_id\n"
                    f"  Input   : {client_provided_session_id}\n"
                    f"  Resolved: {resolved_session_id}\n"
                    f"{'='*60}"
                )
            else:
                logger.info(
                    f"\n{'='*60}\n"
                    f"[SESSION] SERVER GENERATED new session_id\n"
                    f"  Client input : None (not provided)\n"
                    f"  Generated ID : {resolved_session_id}\n"
                    f"{'='*60}"
                )
            # ════════════════════════════════════════════════════════════

            logger.debug(f"[STREAM] Ensuring session exists in database")
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

        # ── Inject session_id into logging context ────────────────────────
        # CompactFileFormatter reads this contextvar on every log record,
        # so ALL downstream modules (cost_tracker, reranker, retriever, etc.)
        # will automatically emit [session_xxx] prefix — no code change needed there.
        from ..utils.context_manager import set_session_id
        set_session_id(resolved_session_id)

        # 🆕 EMIT SESSION_ID IMMEDIATELY - Send to frontend before any processing steps
        logger.info(f"[STREAM_SESSION] Emitting session_id to frontend: {resolved_session_id}")
        
        # detect_language is sync (langdetect) — run in thread pool to avoid blocking event loop
        detected_lang = await asyncio.to_thread(detect_language, message)
        logger.info(f"[STREAM_SESSION] Detected language for session {resolved_session_id}: {detected_lang}")
        
        yield {
            "session_id": resolved_session_id,
            "detected_language": detected_lang,
            "step": "session_initialized",
            "status": f"Session initialized: {resolved_session_id}",
            "message": "Session created",
            "is_complete": True,
            "is_active": False,
            "overall_percentage": 0
        }

        logger.debug(f"[STREAM] Checking for web search requirements")
        processed_message = message
        web_metadata = {}

        # Create web search processor with cost_tracker from agent
        cost_tracker = agent._get_cost_tracker(resolved_session_id)
        web_search_processor = WebSearchProcessor(cost_tracker=cost_tracker)

        # Use await for async web search methods
        if web_search_processor and await web_search_processor.is_web_search_request(message):
            logger.debug(f"[STREAM_WEB] Web search request detected for session {resolved_session_id}")
            # Use await for async method - now returns natural language description
            has_urls, natural_language_text, metadata = await web_search_processor.process_text_with_urls(message)
            if has_urls:
                processed_message = natural_language_text
                web_metadata = metadata
                logger.debug(f"[STREAM_WEB] Natural language text with {metadata['successful_extractions']} URL extractions: {natural_language_text}")
            else:
                logger.debug(f"[STREAM_WEB] No valid URLs found for processing")
        else:
            logger.debug(f"[STREAM_WEB] No web search requirements detected")

        # 🆕 FACE PROCESSING: Detect and enhance face selection
        face_metadata = {}
        
        if hasattr(agent, 'face_processor'):
            try:
                # Parse face selection from message
                face_data = agent.face_processor.parse_face_selection(processed_message)
                
                if face_data:
                    logger.info(f"[STREAM_FACE] Detected - ID:{face_data['face_id']} Type:{face_data['shape_type']} Session:{resolved_session_id}")
                    
                    # Enhance message with face context
                    enhanced_message = agent.face_processor.enhance_user_request(processed_message)
                    processed_message = enhanced_message
                    
                    # Store face metadata for later use
                    face_metadata = {
                        'face_detected': True,
                        'face_id': face_data['face_id'],
                        'shape_type': face_data['shape_type'],
                        'spatial_context': face_data['spatial_context'],
                        'bbox': face_data['bbox'],
                        'geometry': face_data['geometry'],
                        'context': face_data['context']
                    }
                    
                    logger.debug(f"[STREAM_FACE] Enhanced: {len(processed_message)} chars")
                else:
                    logger.debug(f"[STREAM_FACE] No selection detected")
                    face_metadata = {'face_detected': False}
            except Exception as e:
                logger.warning(f"[STREAM_FACE] Error: {e}")
                face_metadata = {'face_detected': False, 'error': str(e)}
        else:
            logger.debug(f"[STREAM_FACE] Processor not available")
            face_metadata = {'face_detected': False, 'error': 'face_processor not available'}

        # ══════════════════════════════════════════════════════════════════════
        # EDIT MODE: sync latest_code from DB into agent state BEFORE streaming.
        # Without this, state['latest_code'] is empty and the edit-mode gate at
        # `process_request_with_progress` (is_edit_request AND state['latest_code'])
        # would fail silently → the request falls through to normal generation
        # flow → the Confirm chain fires even though edit mode is active.
        # ══════════════════════════════════════════════════════════════════════
        if is_edit_request:
            with get_db_session() as _db_edit:
                db_latest_code = get_latest_code(_db_edit, resolved_session_id)
                if db_latest_code:
                    agent._update_session_state(resolved_session_id, latest_code=db_latest_code)
                    logger.info(
                        f"[STREAM_EDIT] Synced latest_code from DB → agent state "
                        f"({len(db_latest_code)} chars) | session={resolved_session_id}"
                    )
                else:
                    logger.warning(
                        f"[STREAM_EDIT] Edit mode requested but no latest_code in DB "
                        f"for session={resolved_session_id} — will fall back to generation"
                    )

        logger.debug(f"[STREAM] Initializing progress tracking")
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
        logger.info(f"[STREAM] FreeCAD priority for streaming: {priority} | session={resolved_session_id}")
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
                    if step_name in ["analysis", "parameters", "generation_code", "export"]:
                        logger.info(f"{step_name.replace('_', ' ').title()} completed")
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

                # Forward additional fields from progress_update (e.g., design_type, export ETA)
                for key in [
                    "design_type",
                    "assembly_warning",
                    "assembly_confirmed",
                    "estimated_time_seconds",
                    "estimated_time_label",
                    "initial_estimated_time_seconds",
                    "initial_estimated_time_label",
                    "estimated_elapsed_seconds",
                    "is_estimate_overrun",
                    "hole_count",
                    "perforation_notation",
                ]:
                    if key in progress_update:
                        response_data[key] = progress_update[key]

                yield response_data

                logger.debug(f"[STREAM_YIELD] Progress update: {step_name} - {status} ({overall_percentage}%)")
                
            elif "final_result" in progress_update:
                logger.debug(f"[STREAM_FINAL] Received final result for session {resolved_session_id}")
                agent_result = progress_update["final_result"]

                if web_metadata:
                    agent_result["web_search_metadata"] = web_metadata
                    logger.info(f"[STREAM_FINAL] ✅ Added web_search_metadata to final result")
                    logger.info(f"[STREAM_FINAL] 📊 Web metadata contains {len(web_metadata.get('web_contents', []))} web contents")
                    for idx, content in enumerate(web_metadata.get('web_contents', [])):
                        logger.info(f"[STREAM_FINAL] 📄 Content {idx+1}: URL={content.get('url', 'N/A')[:80]}...")
                        logger.info(f"[STREAM_FINAL] 📝 Content {idx+1} length: {len(content.get('content', ''))} characters")
                else:
                    logger.debug(f"[STREAM_FINAL] No web metadata to add")
                
                if face_metadata and face_metadata.get('face_detected'):
                    agent_result["face_metadata"] = face_metadata
                    logger.info(f"[STREAM_FINAL] ✅ Face metadata - ID:{face_metadata.get('face_id')} Type:{face_metadata.get('shape_type')}")
                else:
                    logger.debug(f"[STREAM_FINAL] No face metadata")
                
                # ── Collect per-request cost summary ──────────────────────────
                # get_request_summary() is snapshot-based: always returns cost for
                # THIS turn only, never blocked by _logged flag.
                request_cost_summary = {}
                try:
                    tracker = agent._get_cost_tracker(resolved_session_id)
                    request_cost_summary = tracker.get_request_summary()
                    logger.info(
                        f"[STREAM_FINAL] 💰 Request cost: ${request_cost_summary.get('request_total_cost_usd', 0):.4f} | "
                        f"chains={request_cost_summary.get('request_chains', 0)} | "
                        f"tokens={request_cost_summary.get('request_total_tokens', 0):,}"
                    )
                except Exception as _ce:
                    logger.warning(f"[STREAM_FINAL] Could not get request cost: {_ce}")
                
                if not step_progress.get("complete", False):
                    logger.info(f"All processing completed!")
                    yield {
                        "step": "complete",
                        "status": "All processing completed!",
                        "message": "Completed",
                        "icon": "fas fa-check-circle",
                        "is_complete": True,
                        "is_active": False,
                        "overall_percentage": 100,
                    }
                
                logger.debug(f"[STREAM_FINAL] Processing agent result and creating final response")
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
                                detected_language=detected_lang
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
                            detected_language=detected_lang
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
