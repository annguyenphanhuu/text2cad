"""
API routes for CAD-related operations.
"""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
import logging
import mimetypes
from pathlib import Path
from pydantic import BaseModel, Field
from typing import Optional

# Set up logging
logger = logging.getLogger("tolery-api-cad")

# Frontend Chat Request model that matches what JavaScript sends
class FrontendChatRequest(BaseModel):
    """Request model for the frontend chat endpoint."""
    message: str = Field(..., description="The user's message/request")
    is_edit_request: bool = Field(False, description="Whether this is a request to edit existing code")
    session_id: Optional[str] = Field(None, description="Session ID for conversation continuity")

# Frontend Chat Response model that matches what JavaScript expects
class FrontendChatResponse(BaseModel):
    """Response model for the frontend chat endpoint."""
    message: Optional[str] = Field(None, description="Bot response message")
    code: Optional[str] = Field(None, description="Generated code")
    error: Optional[str] = Field(None, description="Error message if any")
    session_id: Optional[str] = Field(None, description="Session ID for conversation continuity")
    obj_export: Optional[str] = Field(None, description="OBJ export file path")
    step_export: Optional[str] = Field(None, description="STEP export file path")

try:
    from ...database.database import get_db
    from ... import crud
    from ...schemas.sessions import ChatRequest, ChatResponse
    from ...core.text_to_cad_agent import TextToCADAgent
    from ...core.chatbot import text_to_cad_agent
except ImportError:
    # Fallback for direct imports
    import sys
    import os
    sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    from src.database.database import get_db
    import src.crud as crud
    from src.schemas.sessions import ChatRequest, ChatResponse
    from src.core.text_to_cad_agent import TextToCADAgent
    from src.core.chatbot import text_to_cad_agent

# Create router
router = APIRouter(
    prefix="/chat_to_cad",
    tags=["cad"],
    responses={404: {"description": "Not found"}},
)

# Create a router for the main chat endpoint that JavaScript expects
chat_router = APIRouter(
    tags=["chat"],
    responses={404: {"description": "Not found"}},
)

# Database dependency is now imported from src.database.database

# Placeholder for TextToCADAgent initialization and retrieval
# Global instances
_text_to_cad_agent: Optional[TextToCADAgent] = None

def get_text_to_cad_agent_instance() -> TextToCADAgent:
    """
    DEPRECATED: Legacy dependency function for backward compatibility.
    Use get_text_to_cad_agent() for new implementations.
    """
    try:
        from src.core.chatbot import text_to_cad_agent
        if text_to_cad_agent is None:
            raise ValueError("TextToCADAgent instance is None. Check that it was properly initialized.")
        return text_to_cad_agent
    except ImportError:
        # Fallback for direct imports
        import sys
        import os
        sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        from src.core.chatbot import text_to_cad_agent
        if text_to_cad_agent is None:
            raise ValueError("TextToCADAgent instance is None. Check that it was properly initialized.")
        return text_to_cad_agent


@router.post("/", summary="Chat To Cad", description="Process chat message into CAD actions", response_model=ChatResponse)
async def chat(
    chat_req: ChatRequest,
    db: Session = Depends(get_db)
) -> ChatResponse:
    """
    Process a chat request for CAD operations using singleton agent.

    Args:
        chat_req (ChatRequest): Chat request.
        db (Session): Database session.

    Returns:
        ChatResponse: Chat response.
    """
    # Generate user_id from session_id or create temporary one
    user_id = chat_req.session_id or f"temp_user_{hash(chat_req.message) % 10000}"

    try:
        logger.info(f"[CHAT] Processing request for user {user_id}")

        # Use the initialized text_to_cad_agent instance
        agent = text_to_cad_agent

        # Process request using agent
        response = await agent.process_request(
            user_text=chat_req.message,
            session_id=user_id,
            is_edit_request=False,
            request_origin='api'
        )

        # Convert to ChatResponse format
        chat_response = ChatResponse(
            chat_response=response.get('response', ''),
            session_id=user_id,
            obj_export=response.get('obj_export'),
            step_export=response.get('step_export')
        )

        logger.info(f"[CHAT] Successfully processed request for user {user_id}")
        return chat_response

    except Exception as e:
        logger.error(f"[CHAT] Error processing request for user {user_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
    
    finally:
        # No need to release agent with singleton pattern
        logger.info(f"[CHAT] Request completed for user {user_id}")

@chat_router.post("/chat", summary="Chat endpoint for frontend", description="Process chat message into CAD actions", response_model=FrontendChatResponse)
async def frontend_chat(
    chat_req: FrontendChatRequest,
    db: Session = Depends(get_db)
) -> FrontendChatResponse:
    """
    Process a chat request for CAD operations via the frontend using singleton agent.
    This endpoint matches what the JavaScript frontend expects at /api/chat.

    Args:
        chat_req (FrontendChatRequest): Frontend chat request.
        db (Session): Database session.

    Returns:
        FrontendChatResponse: Frontend-compatible chat response.
    """
    # Generate user_id from session_id or create temporary one
    user_id = chat_req.session_id or f"temp_user_{hash(chat_req.message) % 10000}"

    try:
        logger.info(f"[FRONTEND_CHAT] Processing request for user {user_id}")

        # Use the initialized text_to_cad_agent instance
        agent = text_to_cad_agent

        # Log the request
        logger.info(f"Frontend chat request: '{chat_req.message[:50]}...' (edit: {chat_req.is_edit_request}, session: {chat_req.session_id})")

        # Process request using agent
        response = await agent.process_request(
            user_text=chat_req.message,
            session_id=user_id,
            is_edit_request=chat_req.is_edit_request,
            request_origin='api'
        )

        # Convert response to frontend format
        frontend_response = FrontendChatResponse(
            message=response.get('response', ''),
            code=response.get('code', ''),
            session_id=user_id,
            obj_export=response.get('obj_export'),
            step_export=response.get('step_export')
        )

        logger.info(f"[FRONTEND_CHAT] Successfully processed request for user {user_id}")
        return frontend_response

    except Exception as e:
        logger.error(f"[FRONTEND_CHAT] Error processing request for user {user_id}: {str(e)}")
        
        # Return error response
        if "Pool exhausted" in str(e):
            return FrontendChatResponse(
                error="System is currently busy. Please try again in a moment."
            )
        elif "not initialized" in str(e) or "not ready" in str(e):
            return FrontendChatResponse(
                error="System is starting up. Please wait a moment and try again."
            )
        else:
            return FrontendChatResponse(
                error="An error occurred while processing your request. Please try again."
            )
    
    finally:
        # No need to release agent with singleton pattern
        logger.info(f"[FRONTEND_CHAT] Request completed for user {user_id}")

# Create a separate router for file downloads that will be mounted at the root level
download_router = APIRouter(
    tags=["downloads"],
    responses={404: {"description": "File not found"}},
)

@download_router.get("/download/{file_path:path}", summary="Download File")
def download_file(file_path: str):
    """
    Download a file by its path.

    Args:
        file_path (str): Path to the file relative to the project root

    Returns:
        FileResponse: The requested file for download

    Raises:
        HTTPException: If the file is not found
    """
    try:
        # Log the requested file path for debugging
        logger.info(f"Download requested for file: {file_path}")

        # Print current working directory for debugging
        project_root = Path.cwd()

        # Ensure outputs directory exists
        outputs_dir = project_root / "outputs"
        outputs_dir.mkdir(exist_ok=True)

        # Construct the full file path - ensure it's relative to the project root
        # Remove any leading slashes to ensure it's treated as a relative path
        file_path = file_path.lstrip('\\/')

        # If the path contains the project root directory, extract only the part after it
        project_root_str = str(project_root).replace('\\', '/')
        file_path_str = str(file_path).replace('\\', '/')

        if project_root_str in file_path_str:
            # Extract the part after the project root
            relative_path = file_path_str.split(project_root_str, 1)[1].lstrip('\\/')
            logger.info(f"Extracted relative path: {relative_path}")
            file_path = relative_path

        full_path = project_root / file_path
        logger.info(f"Looking for file at: {full_path}")

        # Check if the file exists
        if not full_path.exists():
            logger.error(f"File not found: {full_path}")
            # Check if the directory exists
            if not full_path.parent.exists():
                logger.error(f"Directory does not exist: {full_path.parent}")
                # Try to create the directory structure
                full_path.parent.mkdir(parents=True, exist_ok=True)
                logger.info(f"Created directory: {full_path.parent}")

            # Try to list files in parent directory if it exists
            if full_path.parent.exists():
                logger.info(f"Files in directory {full_path.parent}:")
                for f in full_path.parent.iterdir():
                    logger.info(f"  - {f.name}")



            raise HTTPException(status_code=404, detail=f"File not found: {file_path}")

        # Get the filename for the download
        filename = full_path.name

        # Determine the media type
        media_type, _ = mimetypes.guess_type(str(full_path))
        if media_type is None:
            # Default media types based on extension
            extension = full_path.suffix.lower()
            if extension == '.obj':
                media_type = 'model/obj'
            elif extension == '.step':
                media_type = 'application/step'
            elif extension == '.dxf':
                media_type = 'application/dxf'
            elif extension == '.pdf':
                media_type = 'application/pdf'
            elif extension == '.svg':
                media_type = 'image/svg+xml'
            else:
                media_type = 'application/octet-stream'

        logger.info(f"Serving file for download: {full_path} (media type: {media_type})")

        # Return the file as a download with appropriate headers
        return FileResponse(
            path=str(full_path),
            filename=filename,
            media_type=media_type,
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
    except HTTPException:
        # Re-raise HTTP exceptions
        raise
    except Exception as e:
        logger.error(f"Error serving file {file_path}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error serving file: {str(e)}")




# Create a separate router for the 3D viewer that will be mounted at /api/3d-viewer
viewer_router = APIRouter(
    prefix="/3d-viewer",
    tags=["viewer"],
    responses={404: {"description": "File not found"}},
)

@viewer_router.get("/{file_path:path}", summary="Serve 3D Model File")
def serve_3d_model(file_path: str):
    """
    Serve a 3D model file (OBJ, STEP, JSON) by its path.

    Args:
        file_path (str): Path to the file relative to the project root

    Returns:
        FileResponse: The requested file

    Raises:
        HTTPException: If the file is not found
    """
    try:
        # DEBUG level to avoid spam from browser retries
        logger.debug(f"3D viewer request: GET {file_path}")
        project_root = Path.cwd()
        full_path = project_root / file_path.lstrip('\\/')

        logger.debug(f"Looking for 3D file at: {full_path}")

        if not full_path.exists():
            logger.error(f"3D file not found: {full_path}")
            raise HTTPException(status_code=404, detail=f"File not found: {file_path}")

        media_type, _ = mimetypes.guess_type(str(full_path))
        if media_type is None:
            extension = full_path.suffix.lower()
            if extension == '.obj':
                media_type = 'application/octet-stream' # Treat as binary
            elif extension == '.step':
                media_type = 'application/step'
            elif extension == '.json':
                media_type = 'application/json'
                logger.info(f"[SUCCESS] Successfully serving JSON file for viewer: {full_path}")
            else:
                media_type = 'application/octet-stream'

        logger.debug(f"Serving 3D file: {full_path} (media type: {media_type})")
        return FileResponse(path=str(full_path), media_type=media_type)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error serving 3D file {file_path}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error serving file: {str(e)}")
