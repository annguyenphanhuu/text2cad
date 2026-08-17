"""
CRUD operations for session-related models.
"""
from sqlalchemy.orm import Session
from fastapi import HTTPException
from sqlalchemy.sql import func
from typing import Optional
import uuid
import logging
import re

# Set up logger
logger = logging.getLogger(__name__)

try:
    from ..models.sessions import Session as SessionModel, ChatHistory
    from ..database.db_retry import retry_db_operation, DatabaseRetryError, is_connection_error
except ImportError:
    # Fallback for direct imports
    import sys
    import os
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from src.models.sessions import Session as SessionModel, ChatHistory
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
        logger.debug(f"Successfully created session {session_id} in {duration:.3f}s")
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
                if part in ['obj', 'step', 'cad', 'pdf', 'dxf', 'technical_drawings']:
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


    return downloadable_urls


def add_chat_history_entry(
    db: Session,
    session_id: str,
    user_message: str,
    agent_result: dict,
    chat_request_obj=None,
    obj_export_path: Optional[str] = None,
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
    logger.debug(f"[LATEST_CODE] Retrieving latest code for session: {session_id}")
    
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
            logger.debug(f"[LATEST_CODE] Found latest code for session {session_id}: {code_length} characters")
            return latest_chat.lasted_code
        else:
            logger.debug(f"[LATEST_CODE] No latest code found for session: {session_id}")
            return None
            
    except Exception as e:
        logger.error(f"[LATEST_CODE] Error retrieving latest code for session {session_id}: {str(e)}")
        return None
