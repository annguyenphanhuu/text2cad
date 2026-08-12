"""
Pydantic schemas for session-related operations.
"""
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from datetime import datetime


class ChatRequest(BaseModel):
    """
    Schema for chat request.
    """
    message: str
    image_path: Optional[str] = ""
    session_id: Optional[str] = ""
    part_file_name: str = "part_file_name"  # Merged from app/schemas.py - default value
    export_format: Optional[str] = None  # obj, step, dxf - None means export both OBJ and STEP
    material_choice: Optional[str] = "STEEL"  # STEEL, STAINLESS, ALUMINUM - Optional, defaults to STEEL
    selected_feature_uuid: Optional[str] = ""
    is_edit_request: bool = False  # Add field for edit mode


class ChatResponse(BaseModel):
    """
    Schema for chat response.

    The chat_response field contains the chatbot's response message but does not include
    the generated code. It may contain:
    - Success messages when the model is created successfully
    - Error messages if something went wrong
    - Requests for additional information if parameters are missing
    - Other informational messages from the chatbot

    The actual generated code is not included in the response.
    """
    chat_response: str  # Contains the chatbot's response, but not the actual code
    session_id: str
    obj_export: Optional[str] = None
    step_export: Optional[str] = None  # Path to STEP export file
    json_export: Optional[str] = None  # Path to JSON export file
    technical_drawing_export: Optional[str] = None  # PDF export URL — optional, may be None if not generated
    tessellated_export: Optional[Dict[str, Any]] = None
    attribute_and_transientid_map: Optional[Dict[str, Any]] = None
    manufacturing_errors: Optional[List[str]] = None
    web_search_metadata: Optional[Dict[str, Any]] = None  # Web search metadata with extracted URL content
    detected_language: Optional[str] = None  # Detected language of the user's message (e.g. EN, FR)
    total_cost_usd: Optional[float] = None  # Total OpenAI API cost for this request turn (USD)
    cost_breakdown: Optional[Dict[str, Any]] = None  # Per-chain cost breakdown for this request turn


class SessionInfo(BaseModel):
    """
    Schema for detailed session information.
    """
    id: int
    session_id: str
    name: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True # For Pydantic v2, replaces orm_mode
