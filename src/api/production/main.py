import os
import logging
import time
from datetime import datetime
from typing import List, Optional, Dict, Any

from fastapi import FastAPI, HTTPException, Depends
from src.api.cors import configure_cors
from fastapi.security import HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import or_, func

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("tolery-api-production")

# Import database and models
try:
    from ..database.database import SessionLocal
    from ..models.sessions import Session as SessionModel, ChatHistory
    from ..crud import chat_processing as crud
    from ..core.chatbot import text_to_cad_agent
except ImportError:
    import sys
    sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    from src.database.database import SessionLocal
    from src.models.sessions import Session as SessionModel, ChatHistory
    from src.crud import chat_processing as crud
    from src.core.chatbot import text_to_cad_agent

# Import routers
# PDF and Image chat routers removed from production API

# Import authentication
try:
    from ..middleware.auth import verify_token_dependency as verify_token, get_token_info
except ImportError:
    from src.middleware.auth import verify_token_dependency as verify_token, get_token_info

# FastAPI app with security configuration
app = FastAPI(
    title="Tolery API-PRODUCTION",
    description="Production API for CAD Generation System - Token Authentication Required",
    version=datetime.now().strftime("v%y.%m.%d"),
    docs_url="/docs",
    redoc_url="/redoc"
)

# CORS middleware (shared policy — see src/api/cors.py)
configure_cors(app)

# Note: Static files and templates are handled by main app, not sub-apps

# Note: Static files and templates are handled by main app, not sub-apps

# Import database dependency from main database module
try:
    from ...database.database import get_db
except ImportError:
    try:
        from src.database.database import get_db
    except ImportError:
        # Fallback: create local get_db function
        def get_db():
            db = SessionLocal()
            try:
                yield db
            finally:
                db.close()

# ============================================================================
# API-PRODUCTION: CORE ENDPOINTS
# ============================================================================

# 0. VERSION ENDPOINT
class VersionResponse(BaseModel):
    version: str = Field(..., description="API version")

@app.get("/version", response_model=VersionResponse, tags=["info"], summary="Get API Version")
async def get_version():
    """Get the current API version."""
    return VersionResponse(version=app.version)

# 0. TOKEN VALIDATION ENDPOINT - HIDDEN
# @app.get("/validate-token", tags=["auth"], summary="Validate API Token")
# async def validate_token(token: str = Depends(verify_token)):
#     """Simple endpoint to validate API token without requiring database access."""
#     return {
#         "status": "success",
#         "message": "Token is valid",
#         "token_format": "tolery + 8 digits"
#     }

# 1. SESSIONS MANAGEMENT
class SessionListResponse(BaseModel):
    sessions: List[Dict[str, Any]] = Field(..., description="List of sessions")

@app.get("/sessions", response_model=SessionListResponse, tags=["session"], summary="List Sessions")
async def get_sessions(token: str = Depends(verify_token), db: Session = Depends(get_db)):
    """Get list of all available sessions with their basic information."""
    try:
        # Optimized query to get all sessions and their latest part_file_name
        # 1. Create a subquery to find the latest chat message with a part_file_name for each session
        latest_chat_subquery = db.query(
            ChatHistory.session_id,
            func.max(ChatHistory.id).label("max_id")
        ).filter(ChatHistory.part_file_name.isnot(None)).group_by(ChatHistory.session_id).subquery()

        # 2. Join SessionModel with ChatHistory on the subquery result to get the part_file_name
        sessions = db.query(
            SessionModel,
            ChatHistory.part_file_name
        ).outerjoin(
            latest_chat_subquery, SessionModel.session_id == latest_chat_subquery.c.session_id
        ).outerjoin(
            ChatHistory, ChatHistory.id == latest_chat_subquery.c.max_id
        ).order_by(SessionModel.created_at.desc()).all()

        # 3. Format the response
        session_list = [
            {
                "session_id": session.session_id,
                "created_at": session.created_at.strftime("%Y-%m-%d %H:%M:%S.%f"),
                "last_modified": session.updated_at.strftime("%Y-%m-%d %H:%M:%S.%f") if session.updated_at else None,
                "part_file_name": part_file_name
            }
            for session, part_file_name in sessions
        ]

        return SessionListResponse(sessions=session_list)

    except Exception as e:
        logger.error(f"Error getting sessions: {e}")
        raise HTTPException(status_code=500, detail=f"Error getting sessions: {str(e)}")

# 2. SESSION INFO
class DocumentInfo(BaseModel):
    document_id: str = Field(..., description="Document ID")
    workspace_id: str = Field(..., description="Workspace ID")
    element_id: str = Field(..., description="Element ID")
    folder_id: str = Field(..., description="Folder ID")

class SessionInfoResponse(BaseModel):
    session_id: str = Field(..., description="Session ID")
    created_at: str = Field(..., description="Creation timestamp")
    last_modified: Optional[str] = Field(None, description="Last update timestamp")
    part_file_name: Optional[str] = Field(None, description="Part file name")
    document_info: Optional[DocumentInfo] = Field(None, description="Document information")

@app.get("/sessions/{session_id}", response_model=SessionInfoResponse, tags=["session"])
async def get_session_info(session_id: str, db: Session = Depends(get_db), token: str = Depends(verify_token)):
    """Get session information."""
    try:
        # Get session
        session = db.query(SessionModel).filter(SessionModel.session_id == session_id).first()
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        # Get latest chat with part_file_name
        latest_chat = db.query(ChatHistory).filter(
            ChatHistory.session_id == session_id,
            ChatHistory.part_file_name.isnot(None)
        ).order_by(ChatHistory.created_at.desc()).first()
        
        part_file_name = latest_chat.part_file_name if latest_chat else None

        # Create document info (placeholder values - in real implementation, this would come from document storage)
        document_info = DocumentInfo(
            document_id="35b94c1786ce15f211fa376a",
            workspace_id="acc3fe5c5bb850f4e0aa6998",
            element_id="c07c00d1d1323b9f49bd3622",
            folder_id="2dd8d7ae982d4163e732db9a"
        )

        return SessionInfoResponse(
            session_id=session.session_id,
            created_at=session.created_at.strftime("%Y-%m-%d %H:%M:%S.%f"),
            last_modified=session.updated_at.strftime("%Y-%m-%d %H:%M:%S.%f") if session.updated_at else None,
            part_file_name=part_file_name,
            document_info=document_info
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting session info: {e}")
        raise HTTPException(status_code=500, detail=f"Error getting session info: {str(e)}")

# Get latest code for a session
@app.get("/sessions/{session_id}/latest-code", tags=["session"])
async def get_latest_code(session_id: str, db: Session = Depends(get_db), token: str = Depends(verify_token)):
    """Get the latest generated code for a specific session."""
    try:
        logger.debug(f"Fetching latest code for session: {session_id}")
        
        # First check if session exists
        session = db.query(SessionModel).filter(SessionModel.session_id == session_id).first()
        if not session:
            logger.error(f"Session not found: {session_id}")
            raise HTTPException(status_code=404, detail="Session not found")

        # Get the latest chat history entry with code
        latest_entry = db.query(ChatHistory).filter(
            ChatHistory.session_id == session_id,
            ChatHistory.lasted_code.isnot(None)  # Check for non-null code
        ).order_by(ChatHistory.created_at.desc()).first()
        
        if not latest_entry or not latest_entry.lasted_code:
            logger.warning(f"No code found for session {session_id}")
            return {
                "error": "No code has been generated in this session yet. Generate code first before using edit mode.",
                "latest_code": None,
                "session_id": session_id
            }
        
        logger.debug(f"Found code for session {session_id}, length: {len(latest_entry.lasted_code)} characters")
        
        
        return {
            "latest_code": latest_entry.lasted_code,
            "session_id": session_id
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting latest code: {e}")
        raise HTTPException(status_code=500, detail=f"Error getting latest code: {str(e)}")

# 3. DELETE SESSION
@app.delete("/sessions/{session_id}", tags=["session"])
async def delete_session(session_id: str, delete_type: str = "soft", db: Session = Depends(get_db), token: str = Depends(verify_token)):
    """Delete a session.
    
    Args:
        session_id: The ID of the session to delete.
        delete_type: Type of deletion: 'soft' (keep in database but clear data) or 'hard' (remove from database).
                    Default is 'soft'.
    """
    try:
        # Import CRUD function
        from src.crud.sessions import delete_session as crud_delete_session
        
        # Check if session exists
        session = db.query(SessionModel).filter(SessionModel.session_id == session_id).first()
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        # Process the delete based on delete_type
        if delete_type not in ["soft", "hard"]:
            raise HTTPException(status_code=400, detail="Invalid delete_type. Use 'soft' or 'hard'.")
            
        if delete_type == "soft":
            # Clear chat history but keep the session
            db.query(ChatHistory).filter(ChatHistory.session_id == session_id).delete()
            db.commit()
            return {"success": True, "message": f"Session {session_id} data cleared (soft delete)"}
        else:  # delete_type == "hard"
            # Delete chat history
            db.query(ChatHistory).filter(ChatHistory.session_id == session_id).delete()
            
            # Delete session
            db.delete(session)
            db.commit()
            return {"success": True, "message": f"Session {session_id} permanently deleted (hard delete)"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting session: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error deleting session: {str(e)}")

# 4. GET EXPORT
class ExportResponseItem(BaseModel):
    session_id: str = Field(..., description="Session ID")
    export_format: str = Field(..., description="Export format (obj or step)")
    export_link: str = Field(..., description="Link to the exported file")
    export_time: str = Field(..., description="Timestamp of the export")

class ExportResponse(BaseModel):
    exports: List[ExportResponseItem] = Field(..., description="List of export files")

@app.get("/api/get-export", response_model=ExportResponse, tags=["session"])
async def get_export(session_id: str, export_format: Optional[str] = None, db: Session = Depends(get_db), token: str = Depends(verify_token)):
    """Get export files for a session.

    If export_format is not provided, returns all export files available (obj, step, dxf).
    If export_format is provided, returns only exports of that format (obj, step, or dxf).

    Supported formats:
    - obj: 3D object file
    - step: STEP CAD file
    - dxf: DXF CAD file
    """
    try:
        from src.utils.download_url import build_download_url, resolve_base_url
        BASE_URL = resolve_base_url()

        # Base query for the session
        query = db.query(ChatHistory).filter(ChatHistory.session_id == session_id)
        
        # Normalize export format to lowercase for consistent comparison
        export_format_lower = export_format.lower() if export_format else None
        
        # Filter by export format if specified
        if export_format:
            
            # Validate export format
            valid_formats = ["obj", "step", "dxf"]
            if export_format_lower not in valid_formats:
                raise HTTPException(
                    status_code=400, 
                    detail=f"Invalid export format. Use one of: {', '.join(valid_formats)}."
                )
            
            # Apply filter based on export format
            if export_format_lower == "obj":
                query = query.filter(ChatHistory.obj_export.isnot(None))
            elif export_format_lower == "step":
                query = query.filter(ChatHistory.step_export.isnot(None))
            elif export_format_lower == "dxf":
                # Only filter by dxf_export if the column exists
                if hasattr(ChatHistory, 'dxf_export'):
                    query = query.filter(ChatHistory.dxf_export.isnot(None))
                else:
                    logger.warning("dxf_export column not found in ChatHistory model")
                    raise HTTPException(status_code=400, detail="DXF export not supported in this version")
        else:
            # If no format specified, get entries with any export (only check columns that exist)
            filters = []
            filters.append(ChatHistory.obj_export.isnot(None))
            filters.append(ChatHistory.step_export.isnot(None))

            # Check if dxf_export attribute exists
            try:
                if hasattr(ChatHistory, 'dxf_export'):
                    filters.append(ChatHistory.dxf_export.isnot(None))
            except Exception:
                # Ignore if dxf_export doesn't exist
                pass
                
            query = query.filter(or_(*filters))
        
        # Get all matching entries, ordered by most recent first
        entries = query.order_by(ChatHistory.created_at.desc()).all()
        
        if not entries:
            if export_format:
                raise HTTPException(status_code=404, detail=f"No {export_format} export found for this session")
            else:
                raise HTTPException(status_code=404, detail="No exports found for this session")
        
        # Prepare the response
        export_items = []
        
        for entry in entries:
            # Check for OBJ export
            if entry.obj_export and (export_format_lower is None or export_format_lower == "obj"):
                export_link = build_download_url(entry.obj_export, BASE_URL)
                
                export_items.append(ExportResponseItem(
                    session_id=session_id,
                    export_format="obj",
                    export_link=export_link,
                    export_time=entry.created_at.strftime("%Y-%m-%d %H:%M:%S.%f")
                ))
            
            # Check for STEP export
            if entry.step_export and (export_format_lower is None or export_format_lower == "step"):
                export_link = build_download_url(entry.step_export, BASE_URL)
                
                export_items.append(ExportResponseItem(
                    session_id=session_id,
                    export_format="step",
                    export_link=export_link,
                    export_time=entry.created_at.strftime("%Y-%m-%d %H:%M:%S.%f")
                ))
                
            # Check for DXF export - carefully check if attribute exists
            try:
                if hasattr(entry, 'dxf_export') and entry.dxf_export:
                    # Only include if no specific format requested, or if dxf was requested
                    if export_format_lower is None or export_format_lower == "dxf":
                        export_link = build_download_url(entry.dxf_export, BASE_URL)
                        export_items.append(ExportResponseItem(
                            session_id=session_id,
                            export_format="dxf",
                            export_link=export_link,
                            export_time=entry.created_at.strftime("%Y-%m-%d %H:%M:%S.%f")
                        ))
            except Exception as e:
                # Just log the error and continue, don't break the entire endpoint
                logger.error(f"Error processing DXF export: {e}")
        
        return ExportResponse(exports=export_items)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting exports: {e}")
        raise HTTPException(status_code=500, detail=f"Error getting exports: {str(e)}")

# 5. CHAT HISTORY (Unified for PDF, Image, Chat)
class ChatHistoryResponse(BaseModel):
    session_id: str = Field(..., description="Session ID")
    messages: List[dict] = Field(..., description="List of chat messages")
    total_messages: int = Field(..., description="Total number of messages")

@app.get("/api/chat-history/{session_id}", response_model=ChatHistoryResponse, tags=["session"])
async def get_chat_history(session_id: str, db: Session = Depends(get_db), token: str = Depends(verify_token)):
    """Retrieve chat history for any session (PDF, Image, or Chat)."""
    logger.info(f"Getting chat history for session: {session_id}")

    try:
        # Query chat history for the session
        chat_entries = db.query(ChatHistory).filter(
            ChatHistory.session_id == session_id
        ).order_by(ChatHistory.created_at.asc()).all()

        messages = []
        for entry in chat_entries:
            # Add user message
            if entry.message:
                messages.append({
                    "timestamp": entry.created_at.strftime("%Y-%m-%d %H:%M:%S.%f"),
                    "role": "human",
                    "content": entry.message
                })

            # Add AI response if available
            if entry.response:
                ai_msg = {
                    "timestamp": (entry.response_at or entry.updated_at or entry.created_at).strftime("%Y-%m-%d %H:%M:%S.%f") if (entry.response_at or entry.updated_at or entry.created_at) else None,
                    "role": "ai",
                    "content": entry.response
                }
                messages.append(ai_msg)
            elif entry.output:
                # Fallback to output if no response
                ai_msg = {
                    "timestamp": (entry.response_at or entry.updated_at or entry.created_at).strftime("%Y-%m-%d %H:%M:%S.%f") if (entry.response_at or entry.updated_at or entry.created_at) else None,
                    "role": "ai",
                    "content": entry.output
                }
                messages.append(ai_msg)

        return ChatHistoryResponse(
            session_id=session_id,
            messages=messages,
            total_messages=len(messages)
        )

    except Exception as e:
        logger.error(f"Error getting chat history: {e}")
        raise HTTPException(status_code=500, detail=f"Error getting chat history: {str(e)}")

# 6. CHAT (Regular text chat)
try:
    from ...schemas.sessions import ChatRequest
except ImportError:
    from src.schemas.sessions import ChatRequest


@app.post("/chat_to_cad", tags=["cad"])
async def chat(request_data: ChatRequest, db: Session = Depends(get_db), token: str = Depends(verify_token)):
    """Process a chat message and generate CAD code with session continuity."""
    user_message = request_data.message
    session_id = request_data.session_id
    
    logger.info(f"Chat request received: '{user_message[:50]}...' (session_id: {session_id})")

    if not user_message:
        logger.warning("Empty message received")
        raise HTTPException(status_code=400, detail="No message provided")

    try:
        # Create a ChatRequest for the API
        from src.schemas.sessions import ChatRequest as ApiChatRequest
        api_request = ApiChatRequest(
            message=user_message,
            session_id=session_id,
            image_path=request_data.image_path,
            part_file_name=request_data.part_file_name,
            export_format=request_data.export_format,
            material_choice=request_data.material_choice,
            selected_feature_uuid=request_data.selected_feature_uuid,
            is_edit_request=request_data.is_edit_request,  # Use the value from request
        )

        # Process using our enhanced session handling in CRUD
        result = await crud.handle_chat_request(db, api_request, text_to_cad_agent, request_origin='web')

        # Check for errors
        if isinstance(result, dict) and result.get("error"):
            logger.error(f"Error processing chat request: {result['error']}")
            raise HTTPException(status_code=500, detail=result["error"])

        # Log success
        if "code" in result and result["code"]:
            logger.info(f"Successfully generated code ({len(result['code'])} characters)")

        # Return the result dictionary
        return result

    except Exception as e:
        logger.exception(f"Unexpected error in chat endpoint: {str(e)}")
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")

# Root endpoint is handled by main app

# PDF and Image Chat routes removed from production API

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8124)
