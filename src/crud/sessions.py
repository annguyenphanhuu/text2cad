"""
CRUD operations for session-related models.
"""
from sqlalchemy.orm import Session
from fastapi import HTTPException
from sqlalchemy.sql import func
from sqlalchemy import or_
from typing import Optional
import random
import uuid
import logging
import re

# Set up logger
logger = logging.getLogger(__name__)

try:
    from ..models.sessions import Session as SessionModel, ChatHistory
    from ..schemas.sessions import ChatRequest, ChatResponse
    from ..core.text_to_cad_agent import TextToCADAgent
    from ..utils.language_utils import get_success_message, get_session_language
    from ..database.db_retry import retry_db_operation, DatabaseRetryError, is_connection_error
except ImportError:
    # Fallback for direct imports
    import sys
    import os
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from src.models.sessions import Session as SessionModel, ChatHistory
    from src.schemas.sessions import ChatRequest, ChatResponse
    from src.core.text_to_cad_agent import TextToCADAgent
    from src.utils.language_utils import get_success_message, get_session_language
    from src.database.db_retry import retry_db_operation, DatabaseRetryError, is_connection_error


# Get all sessions
def get_all_sessions(db: Session):
    """
    Get all sessions sorted by ID in descending order (newest first).

    Args:
        db (Session): Database session.

    Returns:
        list: List of all sessions sorted by ID in descending order.
    """
    return db.query(SessionModel).order_by(SessionModel.id.desc()).all()


# Create new session
def create_session(db: Session, session_id: str, name: str = "User"):
    """
    Create a new session.

    Args:
        db (Session): Database session.
        session_id (str): Session ID.
        name (str, optional): Session name. Defaults to "User".

    Returns:
        SessionModel: Created session.

    Raises:
        HTTPException: If session ID already exists.
    """
    import time
    start_time = time.time()
    
    logger.debug(f"[SESSION_CREATE] Creating session: {session_id}, name: '{name}'")
    
    try:
        logger.debug(f"[SESSION_CREATE] Checking if session ID already exists: {session_id}")
        
        # Wrap database operations with retry logic
        @retry_db_operation(max_retries=3, delay=1.0)
        def _check_existing_session():
            return db.query(SessionModel).filter(SessionModel.session_id == session_id).first()
        
        existing_session = _check_existing_session()

        if existing_session:
            logger.warning(f"Session ID already exists: {session_id}")
            raise HTTPException(status_code=400, detail="Session ID already exists.")

        logger.debug(f"[SESSION_CREATE] Creating new session object for: {session_id}")
        new_session = SessionModel(session_id=session_id, name=name)
        
        @retry_db_operation(max_retries=3, delay=1.0)
        def _create_session():
            db.add(new_session)
            db.commit()
            db.refresh(new_session)
            return new_session
        
        result = _create_session()
        
        duration = time.time() - start_time
        logger.info(f"Successfully created session {session_id} in {duration:.3f}s")
        return result
        
    except HTTPException:
        logger.error(f"HTTPException for session {session_id}")
        raise
    except DatabaseRetryError as e:
        duration = time.time() - start_time
        logger.error(f"Database retry failed for session {session_id} after {duration:.3f}s: {str(e)}")
        logger.error(f"Original error: {e.original_error}")
        db.rollback()
        raise HTTPException(status_code=503, detail=f"Database connection error: {str(e.original_error)}")
    except Exception as e:
        duration = time.time() - start_time
        
        # Check if it's a connection-related error
        if is_connection_error(e):
            logger.error(f"Database connection error for session {session_id} after {duration:.3f}s: {str(e)}")
            db.rollback()
            raise HTTPException(status_code=503, detail=f"Database connection error: {str(e)}")
        
        logger.error(f"Error creating session {session_id} after {duration:.3f}s: {str(e)}")
        db.rollback()
        raise


# Get specific session
def get_session_by_id(db: Session, session_id: str):
    """
    Get a specific session by ID.

    Args:
        db (Session): Database session.
        session_id (str): Session ID.

    Returns:
        SessionModel: Session with the specified ID.
    """
    logger.debug(f"[SESSION_GET] Retrieving session: {session_id}")
    
    try:
        @retry_db_operation(max_retries=3, delay=1.0)
        def _get_session():
            return db.query(SessionModel).filter(SessionModel.session_id == session_id).first()
        
        session = _get_session()
        
        if session:
            logger.debug(f"[SESSION_GET] Found session: {session_id}, name: '{session.name}'")
        else:
            logger.debug(f"[SESSION_GET] Session not found: {session_id}")
            
        return session
        
    except DatabaseRetryError as e:
        logger.error(f"Database retry failed for session {session_id}: {str(e)}")
        logger.error(f"Original error: {e.original_error}")
        return None
    except Exception as e:
        # Check if it's a connection-related error
        if is_connection_error(e):
            logger.error(f"Database connection error retrieving session {session_id}: {str(e)}")
            return None
        
        logger.error(f"Error retrieving session {session_id}: {str(e)}")
        return None


# Soft delete session
def soft_delete_session(db: Session, session_id: str):
    """
    Soft delete a session.

    Args:
        db (Session): Database session.
        session_id (str): Session ID.

    Returns:
        SessionModel: Soft-deleted session.
    """
    # For now, just return the session (can be updated to set a status flag if needed)
    return db.query(SessionModel).filter(SessionModel.session_id == session_id).first()


# Hard delete session
def hard_delete_session(db: Session, session_id: str):
    """
    Hard delete a session.

    Args:
        db (Session): Database session.
        session_id (str): Session ID.

    Returns:
        SessionModel: Deleted session.

    Raises:
        HTTPException: If session not found.
    """
    session = db.query(SessionModel).filter(SessionModel.session_id == session_id).first()
    if session:
        db.delete(session)
        db.commit()
        return session
    raise HTTPException(status_code=404, detail="Session not found")


# Delete session (soft or hard)
def delete_session(db: Session, session_id: str, deletion_type: str = "soft"):
    """
    Delete a session (soft or hard).

    Args:
        db (Session): Database session.
        session_id (str): Session ID.
        deletion_type (str, optional): Deletion type ("soft" or "hard"). Defaults to "soft".

    Returns:
        dict: Success message.

    Raises:
        HTTPException: If session not found or invalid deletion type.
    """
    if deletion_type == "soft":
        session = soft_delete_session(db, session_id)
        if session:
            return {"message": f"Session {session_id} has been soft deleted."}
        raise HTTPException(status_code=404, detail="Session not found")

    elif deletion_type == "hard":
        hard_delete_session(db, session_id)
        return {"message": f"Session {session_id} has been permanently deleted."}

    else:
        raise HTTPException(status_code=400, detail="Invalid deletion type. Use 'soft' or 'hard'.")


# Get chat history by session ID (paginated)
def get_chats_by_session_id(db: Session, session_id: str, page: int = 1, limit: int = 10):
    """
    Get chat history by session ID (paginated).

    Args:
        db (Session): Database session.
        session_id (str): Session ID.
        page (int, optional): Page number. Defaults to 1.
        limit (int, optional): Number of items per page. Defaults to 10.

    Returns:
        list: List of chat history entries.
    """
    offset = (page - 1) * limit
    return (
        db.query(ChatHistory)
        .filter(ChatHistory.session_id == session_id)
        .order_by(ChatHistory.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )


# Get exports by session ID
def get_exports_by_session_id(db: Session, session_id: str, export_format: str = None):
    """
    Get exports by session ID and convert local file paths to downloadable URLs.

    Args:
        db (Session): Database session.
        session_id (str): Session ID.
        export_format (str, optional): Export format filter. Defaults to None.

    Returns:
        list: List of downloadable URLs for the exported files.

    Raises:
        HTTPException: If no exports found.
    """
    # Import BASE_URL from main module
    import os
    from pathlib import Path

    # Get BASE_URL from environment or use default
    try:
        from ..api.main import BASE_URL
    except ImportError:
        # Fallback if import fails
        PORT = int(os.getenv("UVICORN_PORT", 8124))
        DOMAIN = os.getenv("DOMAIN", "http://localhost")

        # Only include port in BASE_URL if DOMAIN is localhost
        if DOMAIN == "http://localhost" or DOMAIN == "localhost":
            BASE_URL = f"{DOMAIN}:{PORT}"
        else:
            BASE_URL = DOMAIN

    logger.info(f"Using BASE_URL: {BASE_URL}")

    query = db.query(ChatHistory).filter(ChatHistory.session_id == session_id)

    # If export_format is provided, add filter
    if export_format:
        query = query.filter(ChatHistory.export_format == export_format)

    exports = query.all()

    if not exports:
        raise HTTPException(status_code=404, detail="No exports found for this session")

    # Convert local file paths to downloadable URLs
    downloadable_urls = []
    for export in exports:
        # Helper function to create download URL from file path
        def create_url_from_path(file_path, format_type):
            """Create download URL from file path, preserving original filename and date"""
            if not file_path:
                return None
            
            # Check if the path is already a URL
            if file_path.startswith(('http://', 'https://')):
                return file_path
            
            # Clean path
            clean_path = file_path.replace('\\', '/')
            
            # If the path contains the project root directory, extract only the part after it
            project_root = Path.cwd()
            project_root_str = str(project_root).replace('\\', '/')
            
            if project_root_str in clean_path:
                # Extract the part after the project root
                relative_path = clean_path.split(project_root_str, 1)[1].lstrip('\\/')
            else:
                relative_path = clean_path.lstrip('\\/')
            
            # Extract filename and date directory from path
            filename = os.path.basename(relative_path)
            parts = relative_path.split('/')
            
            # Determine format directory and date
            date_dir = "latest"
            format_dir = format_type
            
            # Try to find date directory in path (format: outputs/format/YYYY-MM-DD/filename)
            for i, part in enumerate(parts):
                if part in ['obj', 'step', 'cad', 'json', 'pdf', 'dxf', 'technical_drawings']:
                    format_dir = part
                    if i+1 < len(parts) and re.match(r'\d{4}-\d{2}-\d{2}', parts[i+1]):
                        date_dir = parts[i+1]
                        break
            
            # Construct URL
            url = f"{BASE_URL}/download/outputs/{format_dir}/{date_dir}/{filename}"
            return url
        
        # Process OBJ exports
        if export.obj_export:
            url = create_url_from_path(export.obj_export, "obj")
            if url:
                downloadable_urls.append({"format": "obj", "url": url})
                logger.info(f"OBJ Export URL: {url}")

        # Process STEP exports
        if export.step_export:
            url = create_url_from_path(export.step_export, "step")
            if url:
                downloadable_urls.append({"format": "step", "url": url})
                logger.info(f"STEP Export URL: {url}")

        # Process JSON exports
        if hasattr(export, 'json_export') and export.json_export:
            # Auto-detect json type from path (json or json_latest)
            json_type = "json_latest" if "json_latest" in export.json_export else "json"
            url = create_url_from_path(export.json_export, json_type)
            if url:
                downloadable_urls.append({"format": "json", "url": url})
                logger.info(f"JSON Export URL: {url}")


    return downloadable_urls


# Get tessellation by session ID
def get_tessellation_by_session_id(db: Session, session_id: str):
    """
    Get tessellation by session ID.

    Args:
        db (Session): Database session.
        session_id (str): Session ID.

    Returns:
        dict: Tessellation data.

    Raises:
        HTTPException: If no tessellation found.
    """
    # This is a placeholder - implement actual tessellation retrieval logic
    return {"vertices": [0, 1, 2], "faces": [0, 1, 2]}


# Handle chat request
def handle_chat_request(db: Session, chat_req: ChatRequest, agent: TextToCADAgent, request_origin: str = 'api'):
    """
    Handle chat request.

    Args:
        db (Session): Database session.
        chat_req (ChatRequest): Chat request.
        agent (TextToCADAgent): The TextToCAD agent instance.
        request_origin (str): The origin of the request ('web', 'api', or 'unknown').

    Returns:
        ChatResponse: Chat response.
    """
    # Enhanced session ID continuity management
    session_id = chat_req.session_id
    found_existing_session = False

    # Strategy 1: Check if message mentions an existing session ID
    if not session_id and chat_req.message:
        import re
        session_pattern = re.compile(r'session_[a-f0-9]{6}_\d{6}')
        session_matches = session_pattern.findall(chat_req.message)
        if session_matches:
            potential_session_id = session_matches[0]
            # Verify this session exists in database
            existing_session = db.query(SessionModel).filter(SessionModel.session_id == potential_session_id).first()
            if existing_session:
                session_id = potential_session_id
                chat_req.session_id = session_id
                found_existing_session = True

    # Strategy 2: If first strategy failed, check if this message is a follow-up to a recent question
    if not found_existing_session and not session_id:
        # Get recent chat history entries that asked questions (most recent first)
        # Also include entries from image processing that might need follow-up
        recent_question_sessions = db.query(ChatHistory)\
            .filter(
                or_(
                    ChatHistory.output.like('%Missing parameters:%'),
                    ChatHistory.output.like('%questions:%'),
                    ChatHistory.message.like('%Based on image analysis:%')
                )
            )\
            .order_by(ChatHistory.id.desc())\
            .limit(10)\
            .all()

        for entry in recent_question_sessions:
            # If the current message appears to be answering the questions from this session
            question_output = entry.output

            # Extract parameters from the question
            import re
            params_match = re.search(r'Missing parameters: ([^\]]+)', question_output)
            if params_match:
                param_list = params_match.group(1).split(',')

                # Enhanced parameter matching - check for various patterns
                message_lower = chat_req.message.lower()

                # Check if current message addresses any of these parameters
                for param in param_list:
                    param_clean = param.strip().lower()

                    # Direct parameter name match
                    if param_clean in message_lower:
                        session_id = entry.session_id
                        chat_req.session_id = session_id
                        found_existing_session = True
                        break

                    # Enhanced matching for common dimension patterns
                    if any(keyword in param_clean for keyword in ['length', 'width', 'dimension']):
                        # Look for dimension patterns like "1000x500", "1000 x 500", "length: 1000", etc.
                        dimension_patterns = [
                            r'\d+\s*x\s*\d+',  # 1000x500, 1000 x 500
                            r'\d+\s*\*\s*\d+',  # 1000*500, 1000 * 500
                            r'length[:\s]\s*\d+',  # length: 1000, length 1000
                            r'width[:\s]\s*\d+',   # width: 500, width 500
                            r'size[:\s]\s*\d+',    # size: 1000, size 1000
                            r'\d+\s*mm',           # 1000mm
                            r'\d+\s*cm',           # 100cm
                            r'\d+\s*m(?:\s|$)',    # 1m (with space or end of string)
                        ]

                        if any(re.search(pattern, message_lower) for pattern in dimension_patterns):
                            session_id = entry.session_id
                            chat_req.session_id = session_id
                            found_existing_session = True
                            logger.info(f"Matched follow-up message '{chat_req.message}' to existing session {session_id} based on dimension pattern")
                            break

                    # Enhanced matching for thickness parameters
                    if any(keyword in param_clean for keyword in ['thickness', 'height', 'depth']):
                        thickness_patterns = [
                            r'\d+\.?\d*\s*mm',     # 1.5mm, 2mm
                            r'\d+\.?\d*\s*cm',     # 1.5cm
                            r'thick[:\s]\s*\d+',   # thick: 1.5, thick 1.5
                            r'thickness[:\s]\s*\d+', # thickness: 1.5
                            r'\d+\.?\d*$',         # Just a number like "1.5"
                        ]

                        if any(re.search(pattern, message_lower) for pattern in thickness_patterns):
                            session_id = entry.session_id
                            chat_req.session_id = session_id
                            found_existing_session = True
                            logger.info(f"Matched follow-up message '{chat_req.message}' to existing session {session_id} based on thickness pattern")
                            break

                    # Enhanced matching for hole-related parameters
                    if any(keyword in param_clean for keyword in ['hole', 'perforation', 'pattern']):
                        hole_patterns = [
                            r'hole[:\s]\s*\w+',     # hole: round, hole square
                            r'pattern[:\s]\s*\w+',  # pattern: square, pattern round
                            r'perforation[:\s]\s*\w+', # perforation: round
                            r'(round|square|circular|rectangular)', # shape words
                            r'\d+\s*mm\s*(hole|diameter)', # 5mm hole, 10mm diameter
                        ]

                        if any(re.search(pattern, message_lower) for pattern in hole_patterns):
                            session_id = entry.session_id
                            chat_req.session_id = session_id
                            found_existing_session = True
                            logger.info(f"Matched follow-up message '{chat_req.message}' to existing session {session_id} based on hole pattern")
                            break

                if found_existing_session:
                    break

            # Additional heuristic: If message is very short and contains only numbers/dimensions,
            # it's likely a follow-up to a recent question
            if not found_existing_session and len(chat_req.message.strip()) < 50:
                # Check if message looks like dimensions or simple parameters
                simple_answer_patterns = [
                    r'^\d+\s*x\s*\d+$',        # Just "1000x500"
                    r'^\d+\.?\d*$',             # Just a number "1.5"
                    r'^\d+\s*mm$',              # Just "5mm"
                    r'^(round|square|circular|rectangular)$', # Just shape words
                    r'^\w{1,20}$',              # Single short word
                    r'.*pattern.*',             # Contains "pattern"
                    r'.*straight.*',            # Contains "straight"
                ]

                if any(re.search(pattern, chat_req.message.strip(), re.IGNORECASE) for pattern in simple_answer_patterns):
                    # This looks like a simple answer, link to most recent question
                    session_id = entry.session_id
                    chat_req.session_id = session_id
                    found_existing_session = True
                    logger.info(f"Matched simple answer '{chat_req.message}' to most recent question session {session_id}")
                    break

        # Strategy 3: If still no match, try to match with the most recent session that has missing info
        if not found_existing_session and not session_id:
            # Get the most recent session that has missing parameters or questions
            # Prioritize sessions from the last 10 minutes
            from datetime import datetime, timedelta

            # First try to find very recent sessions (last 10 minutes)
            # Prioritize image analysis sessions first
            ten_minutes_ago = datetime.now() - timedelta(minutes=10)

            # Priority 1: Recent image analysis sessions
            recent_session = db.query(ChatHistory)\
                .filter(ChatHistory.message.like('%Based on image analysis:%'))\
                .filter(ChatHistory.created_at >= ten_minutes_ago)\
                .order_by(ChatHistory.id.desc())\
                .first()

            # Priority 2: Recent sessions with missing parameters
            if not recent_session:
                recent_session = db.query(ChatHistory)\
                    .filter(
                        or_(
                            ChatHistory.output.like('%Missing parameters:%'),
                            ChatHistory.output.like('%questions:%')
                        )
                    )\
                    .filter(ChatHistory.created_at >= ten_minutes_ago)\
                    .order_by(ChatHistory.id.desc())\
                    .first()

            # If no very recent session, fall back to any recent session
            # Priority 3: Any recent image analysis session
            if not recent_session:
                recent_session = db.query(ChatHistory)\
                    .filter(ChatHistory.message.like('%Based on image analysis:%'))\
                    .order_by(ChatHistory.id.desc())\
                    .first()

            # Priority 4: Any recent session with missing parameters
            if not recent_session:
                recent_session = db.query(ChatHistory)\
                    .filter(
                        or_(
                            ChatHistory.output.like('%Missing parameters:%'),
                            ChatHistory.output.like('%questions:%')
                        )
                    )\
                    .order_by(ChatHistory.id.desc())\
                    .first()

            if recent_session:
                session_id = recent_session.session_id
                chat_req.session_id = session_id
                found_existing_session = True
                logger.info(f"Matched follow-up message '{chat_req.message}' to most recent session with questions: {session_id}")

    # If session_id is None or an empty string (from Pydantic default), create a new one.
    if not session_id or session_id == "": # Explicitly check for empty string
        rand_digits = random.randint(100000, 999999)
        rand_uuid = uuid.uuid4().hex[:6]
        new_generated_id = f"session_{rand_uuid}_{rand_digits}"
        session_id = new_generated_id # Update local session_id
        chat_req.session_id = new_generated_id # IMPORTANT: Update the DTO's session_id as well

    # Find session if exists, create if not
    session_db_model = db.query(SessionModel).filter(SessionModel.session_id == session_id).first()
    if not session_db_model:
        # Use a more descriptive name if possible, or default
        session_name = chat_req.message[:50].strip() if chat_req.message else "User Session"
        session_db_model = SessionModel(session_id=session_id, name=session_name)
        db.add(session_db_model)
        db.commit()
        db.refresh(session_db_model)

    # Process the request using the TextToCADAgent
    # Assuming is_edit_request is False for this endpoint, or get it from chat_req if available
    # Get material_choice from request, default to "STEEL" if not provided
    material_choice = getattr(chat_req, 'material_choice', None) or "STEEL"
    agent_result = agent.process_request(
        user_text=chat_req.message,
        is_edit_request=False, # Assuming new chat requests are not edits by default via this CRUD
        request_origin=request_origin,
        session_id=session_id, # Ensure the local, authoritative session_id is passed to the agent
        material_choice=material_choice  # Pass material choice to agent for template substitution
    )

    # Process the request:
    # 1. If parameters are missing: chat_response will be a message, obj_export will be empty
    # 2. If parameters are sufficient: chat_response will be code, obj_export will be the obj file path

    # Check if additional information is required
    missing_parameters = False
    chat_response_content = None
    obj_export_path = None

    # Enhanced response handling with detailed logging
    logger.info(f"[RESPONSE_DEBUG] Processing agent_result for session {session_id}")
    logger.info(f"[RESPONSE_DEBUG] agent_result keys: {list(agent_result.keys())}")
    logger.info(f"[RESPONSE_DEBUG] has 'message': {bool(agent_result.get('message'))}")
    logger.info(f"[RESPONSE_DEBUG] has 'questions': {bool(agent_result.get('questions'))}")
    logger.info(f"[RESPONSE_DEBUG] has 'code': {bool(agent_result.get('code'))}")
    logger.info(f"[RESPONSE_DEBUG] has 'error': {bool(agent_result.get('error'))}")

    # Priority 1: If there is a "message" field and no "code" field, then additional parameters are required
    if agent_result.get("message") and not agent_result.get("code"):
        missing_parameters = True
        chat_response_content = agent_result.get("message")
        logger.info(f"[RESPONSE_DEBUG] Using 'message' field: {chat_response_content[:100]}...")
    
    # Priority 2: If there is a "questions" array and no "code" field (fallback for missing message)
    elif agent_result.get("questions") and not agent_result.get("code"):
        missing_parameters = True
        questions_list = agent_result.get("questions")
        if isinstance(questions_list, list):
            chat_response_content = "\n".join(questions_list)
            logger.info(f"[RESPONSE_DEBUG] Converted {len(questions_list)} questions to message")
        else:
            chat_response_content = str(questions_list)
            logger.info(f"[RESPONSE_DEBUG] Converted non-list questions to string")
        logger.info(f"[RESPONSE_DEBUG] Questions content: {chat_response_content[:100]}...")
    
    # Priority 3: If there is an error, treat it as missing parameters
    elif agent_result.get("error"):
        missing_parameters = True
        chat_response_content = agent_result.get("error")
        logger.info(f"[RESPONSE_DEBUG] Using error message: {chat_response_content}")
    
    # Priority 4: Case where parameters are sufficient and code is available
    elif agent_result.get("code"):
        missing_parameters = False
        # Create success response without including the code
        # Use session-cached language (detected from first substantive message by gpt-4.1-nano)
        session_lang = get_session_language(session_id)
        chat_response_content = get_success_message(session_lang)
        # Save code separately for use in the database
        code_content = agent_result.get("code")
        obj_export_path = agent_result.get("obj_path")
        logger.info(f"[RESPONSE_DEBUG] Code generated successfully, using success message")
    
    # Priority 5: Fallback - no recognizable response format
    else:
        missing_parameters = True
        chat_response_content = "I processed your request but couldn't generate a clear response. Please try rephrasing your request."
        logger.warning(f"[RESPONSE_DEBUG] No recognizable response format in agent_result")

    # Default values for other fields
    tessellated_data = None
    attr_trans_map = None
    mfg_errors = []

    # Get export paths from agent_result based on export_format
    obj_export_path = None
    step_export_path = None

    # Logic for export format:
    # - None or empty: Export both OBJ and STEP
    # - "obj": Export only OBJ
    # - "step": Export only STEP
    export_format = chat_req.export_format

    # Debug logging
    logger.info(f"[DEBUG] export_format received: '{export_format}'")
    logger.info(f"[DEBUG] agent_result keys: {list(agent_result.keys())}")
    logger.info(f"[DEBUG] obj_path from agent: {agent_result.get('obj_path')}")
    logger.info(f"[DEBUG] step_path from agent: {agent_result.get('step_path')}")

    if export_format is None or export_format == "":
        # Export both formats
        obj_export_path = agent_result.get("obj_path")
        step_export_path = agent_result.get("step_path")
        logger.info(f"[SUCCESS] Export format not specified - exporting both OBJ and STEP")
        logger.info(f"   [FILE] OBJ path: {obj_export_path}")
        logger.info(f"   [FILE] STEP path: {step_export_path}")
    elif export_format.lower() == "obj":
        # Export only OBJ
        obj_export_path = agent_result.get("obj_path")
        logger.info(f"[SUCCESS] Export format: OBJ only")
        logger.info(f"   [FILE] OBJ path: {obj_export_path}")
    elif export_format.lower() == "step":
        # Export only STEP
        step_export_path = agent_result.get("step_path")
        logger.info(f"[SUCCESS] Export format: STEP only")
        logger.info(f"   [FILE] STEP path: {step_export_path}")
    else:
        # Default to both if unknown format
        obj_export_path = agent_result.get("obj_path")
        step_export_path = agent_result.get("step_path")
        logger.warning(f"[WARNING] Unknown export format '{export_format}' - defaulting to both OBJ and STEP")
        logger.info(f"   [FILE] OBJ path: {obj_export_path}")
        logger.info(f"   [FILE] STEP path: {step_export_path}")


    # Import BASE_URL from main module
    import os
    from pathlib import Path

    # Get BASE_URL from environment or use default
    try:
        from ..api.main import BASE_URL
    except ImportError:
        # Fallback if import fails
        PORT = int(os.getenv("UVICORN_PORT", 8124))
        DOMAIN = os.getenv("DOMAIN", "http://localhost")

        # Only include port in BASE_URL if DOMAIN is localhost
        if DOMAIN == "http://localhost" or DOMAIN == "localhost":
            BASE_URL = f"{DOMAIN}:{PORT}"
        else:
            BASE_URL = DOMAIN

    # Convert export paths to downloadable URLs based on what was requested
    obj_downloadable_url = None
    step_downloadable_url = None
    technical_drawing_downloadable_url = None

    logger.info(f"[LINK] Creating download URLs...")
    logger.info(f"   [FILE] OBJ path to process: {obj_export_path}")
    logger.info(f"   [FILE] STEP path to process: {step_export_path}")

    # Only create URLs for the formats that were actually exported
    if obj_export_path:
        # Format: http://localhost:8080/download/outputs/obj/YYYY-MM-DD/filename.obj
        obj_filename = os.path.basename(obj_export_path)
        obj_date_dir = obj_export_path.split(os.sep)[-2] if os.sep in obj_export_path else "latest"
        obj_downloadable_url = f"{BASE_URL}/download/outputs/obj/{obj_date_dir}/{obj_filename}"
        logger.info(f"[SUCCESS] Created OBJ downloadable URL: {obj_downloadable_url}")
    else:
        logger.info(f"[ERROR] No OBJ path provided - skipping OBJ URL creation")

    if step_export_path:
        step_filename = os.path.basename(step_export_path)
        step_date_dir = step_export_path.split(os.sep)[-2] if os.sep in step_export_path else "latest"
        step_downloadable_url = f"{BASE_URL}/download/outputs/step/{step_date_dir}/{step_filename}"
        logger.info(f"[SUCCESS] Created STEP downloadable URL: {step_downloadable_url}")
    else:
        logger.info(f"[ERROR] No STEP path provided - skipping STEP URL creation")

    # PDF is optional (visualization only) — a missing or failed PDF must never
    # affect the response or fail the request, so any error here is swallowed.
    try:
        technical_drawing_path = agent_result.get("pdf_path")
        if technical_drawing_path:
            tech_filename = os.path.basename(technical_drawing_path)
            tech_date_dir = technical_drawing_path.split(os.sep)[-2] if os.sep in technical_drawing_path else "latest"
            technical_drawing_downloadable_url = f"{BASE_URL}/download/outputs/pdf/{tech_date_dir}/{tech_filename}"
            logger.info(f"[SUCCESS] Created technical drawing downloadable URL: {technical_drawing_downloadable_url}")
        else:
            logger.info(f"[INFO] No technical drawing (PDF) path provided - skipping (optional)")
    except Exception as pdf_url_error:
        logger.warning(f"[WARNING] Failed to build technical drawing URL (optional, ignored): {pdf_url_error}")
        technical_drawing_downloadable_url = None

    add_chat_history_entry(
        db=db,
        session_id=session_id,
        user_message=chat_req.message,
        agent_result=agent_result,
        chat_request_obj=chat_req,
        obj_export_path=obj_export_path
    )

    # Return the chat response without including the code
    logger.info(f"[RESPONSE] Final response preparation:")
    logger.info(f"   [CHAT] chat_response: {chat_response_content}")
    logger.info(f"   [ID] session_id: {session_id}")
    logger.info(f"   [FILE] obj_export: {obj_downloadable_url}")
    logger.info(f"   [FILE] step_export: {step_downloadable_url}")
    logger.info(f"   [FILE] technical_drawing_export: {technical_drawing_downloadable_url}")

    response = ChatResponse(
        chat_response=chat_response_content,
        session_id=session_id,
        obj_export=obj_downloadable_url,
        step_export=step_downloadable_url,
        technical_drawing_export=technical_drawing_downloadable_url,
        tessellated_export=tessellated_data,
        attribute_and_transientid_map=attr_trans_map,
        manufacturing_errors=mfg_errors
    )

    logger.info(f"[SUCCESS] Response created successfully")
    return response


def add_chat_history_entry(
    db: Session,
    session_id: str,
    user_message: str,
    agent_result: dict,
    chat_request_obj=None,
    obj_export_path: Optional[str] = None,
    json_export_path: Optional[str] = None,
    created_at=None,
    response_at=None
):
    """
    Add a new chat history entry to the database.

    Args:
        db (Session): Database session
        session_id (str): Session ID
        user_message (str): User's message
        agent_result (dict): Agent's result dictionary
        chat_request_obj: Optional chat request object
        obj_export_path (Optional[str]): Path to OBJ export file
    """
    import time
    import traceback
    
    start_time = time.time()
    # logger.info(f"[CHAT_HISTORY_ADD] Adding chat history entry for session: {session_id}")
    # logger.info(f"[CHAT_HISTORY_ADD] User message length: {len(user_message)} characters")
    
    try:
        # logger.info(f"[CHAT_HISTORY_ADD] Processing agent result for session {session_id}")
        
        # Extract information from agent_result
        chat_response = agent_result.get("response", "")
        lasted_code = agent_result.get("code")
        step_path = agent_result.get("step_path")
        
        # logger.info(f"[CHAT_HISTORY_ADD] Agent result summary - Response: {len(chat_response) if chat_response else 0} chars, "
        #            f"Code: {len(lasted_code) if lasted_code else 0} chars, "
        #            f"STEP: {bool(step_path)}")
        
        # Extract additional fields from chat_request_obj if available
        # logger.info(f"[CHAT_HISTORY_ADD] Extracting additional fields from chat request for session {session_id}")
        image_path = getattr(chat_request_obj, 'image_path', None)
        part_file_name = getattr(chat_request_obj, 'part_file_name', None)
        export_format = getattr(chat_request_obj, 'export_format', "both")
        material_choice = getattr(chat_request_obj, 'material_choice', None)
        selected_feature_uuid = getattr(chat_request_obj, 'selected_feature_uuid', None)
        
        # logger.info(f"[CHAT_HISTORY_ADD] Additional fields - Image: {bool(image_path)}, "
        #            f"Part file: {bool(part_file_name)}, Export format: {export_format}, "
        #            f"Material: {bool(material_choice)}, Feature UUID: {bool(selected_feature_uuid)}")

        # Set default values for required fields
        image_path = image_path or None
        part_file_name = part_file_name or "default_part"
        export_format = export_format or "both"
        material_choice = material_choice or "STEEL"
        selected_feature_uuid = selected_feature_uuid or None

        # Get STEP export path from agent_result if available
        step_export_path = agent_result.get("step_path")

        # Get JSON export path from agent_result if available
        if not json_export_path:
            json_export_path = agent_result.get("json_path")

        # Get technical drawing (PDF) export path from agent_result.
        # PDF is optional — missing/failed PDF just leaves this None, no error.
        technical_drawing_export_path = agent_result.get("pdf_path")

        # Extract lasted_code from agent_result if available
        lasted_code = agent_result.get("code") if agent_result.get("code") else None

        # Extract response from agent_result if available
        response = agent_result.get("response") if agent_result.get("response") else None

        logger.info(f"[CHAT_HISTORY_ADD] Creating ChatHistory object for session {session_id}")
        created_timestamp = created_at if created_at is not None else func.current_timestamp()
        response_timestamp = response_at if response_at is not None else func.current_timestamp()

        new_chat_history = ChatHistory(
            session_id=session_id,
            message=user_message,
            response=chat_response,
            created_at=created_timestamp,
            response_at=response_timestamp,
            lasted_code=lasted_code,
            obj_export=obj_export_path,
            step_export=step_path,
            json_export=json_export_path,
            technical_drawing_export=technical_drawing_export_path,
            image_path=image_path,
            part_file_name=part_file_name,
            export_format=export_format,
            material_choice=material_choice,
            selected_feature_uuid=selected_feature_uuid
        )
        
        # logger.info(f"[CHAT_HISTORY_ADD] Adding to database session for {session_id}")
        db.add(new_chat_history)
        
        # logger.info(f"[CHAT_HISTORY_ADD] Committing to database for session {session_id}")
        db.commit()
        db.refresh(new_chat_history)
        
        duration = time.time() - start_time
        # logger.info(f"[CHAT_HISTORY_ADD] Successfully added chat history entry for session {session_id} in {duration:.3f}s")
        # logger.info(f"[CHAT_HISTORY_ADD] New chat history ID: {new_chat_history.id}")
        
        return new_chat_history
        
    except Exception as e:
        duration = time.time() - start_time
        # logger.error(f"[CHAT_HISTORY_ADD] Error adding chat history for session {session_id} after {duration:.3f}s: {str(e)}")
        # logger.error(f"[CHAT_HISTORY_ADD] Traceback: {traceback.format_exc()}")
        db.rollback()
        raise


def update_session(db: Session, session_id: str, **kwargs):
    """
    Update session with provided fields. For lasted_code, updates the latest chat_history entry.

    Args:
        db (Session): Database session.
        session_id (str): Session ID to update.
        **kwargs: Fields to update (e.g., lasted_code, name).

    Returns:
        SessionModel or ChatHistory: Updated session/chat_history or None if not found.
    """
    try:
        # Handle lasted_code separately - store in chat_history
        if 'lasted_code' in kwargs:
            lasted_code = kwargs.pop('lasted_code')

            # Find the latest chat_history entry for this session
            latest_chat = db.query(ChatHistory).filter(
                ChatHistory.session_id == session_id
            ).order_by(ChatHistory.created_at.desc()).first()

            if latest_chat:
                # Update the latest chat_history entry with lasted_code
                latest_chat.lasted_code = lasted_code
                db.commit()
                db.refresh(latest_chat)
                logger.info(f"Updated latest chat_history for session {session_id} with lasted_code ({len(lasted_code)} characters)")
            else:
                logger.warning(f"No chat_history found for session {session_id} to store lasted_code")
                return None

        # Handle other session fields (like name)
        if kwargs:
            session = db.query(SessionModel).filter(SessionModel.session_id == session_id).first()

            if not session:
                logger.warning(f"Session {session_id} not found for update")
                return None

            # Update provided fields
            for field, value in kwargs.items():
                if hasattr(session, field):
                    setattr(session, field, value)
                    logger.info(f"Updated session {session_id} field '{field}'")
                else:
                    logger.warning(f"Field '{field}' not found in Session model")

            # Commit changes
            db.commit()
            db.refresh(session)
            logger.info(f"Successfully updated session {session_id}")
            return session

        # If only lasted_code was updated, return the chat_history entry
        return latest_chat

    except Exception as e:
        db.rollback()
        logger.error(f"Error updating session {session_id}: {e}")
        return None


def get_latest_code(db: Session, session_id: str) -> Optional[str]:
    """
    Get the latest code from chat history for a session.

    Args:
        db (Session): Database session.
        session_id (str): Session ID.

    Returns:
        Optional[str]: Latest code if found, None otherwise.
    """
    logger.info(f"[LATEST_CODE] Retrieving latest code for session: {session_id}")
    
    try:
        latest_chat = (
            db.query(ChatHistory)
            .filter(ChatHistory.session_id == session_id)
            .filter(ChatHistory.lasted_code.isnot(None))
            .order_by(ChatHistory.id.desc())
            .first()
        )
        
        if latest_chat and latest_chat.lasted_code:
            code_length = len(latest_chat.lasted_code)
            logger.info(f"[LATEST_CODE] Found latest code for session {session_id}: {code_length} characters")
            return latest_chat.lasted_code
        else:
            logger.info(f"[LATEST_CODE] No latest code found for session: {session_id}")
            return None
            
    except Exception as e:
        logger.error(f"[LATEST_CODE] Error retrieving latest code for session {session_id}: {str(e)}")
        return None
