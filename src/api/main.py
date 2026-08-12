"""
Main API Application
Contains all endpoints including demo endpoints at root.
"""

import os
import sys
import logging
import platform
import mimetypes
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Optional, List

from fastapi import FastAPI, Request, HTTPException, UploadFile, File, Form, Depends
from fastapi.security import HTTPBearer
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from typing import Optional # Added import
from fastapi import Depends # Added import
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from fastapi.openapi.utils import get_openapi
from fastapi.openapi.models import Example

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("tolery-main")


class _SuppressMonitorAccessLogFilter(logging.Filter):
    """Hide noisy monitor polling requests from uvicorn access logs."""

    _SUPPRESSED_PATHS = (
        "/api/monitor/overview",
        "/api/monitor/ram",
        "/api/monitor/performance",
        "/api/monitor/sizing",
    )

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        return not any(path in message for path in self._SUPPRESSED_PATHS)


if os.getenv("SUPPRESS_MONITOR_ACCESS_LOGS", "true").lower() == "true":
    logging.getLogger("uvicorn.access").addFilter(_SuppressMonitorAccessLogFilter())

# ============================================================================
# BASE_URL CONFIGURATION
# ============================================================================

# Get domain and port from environment
DOMAIN = os.getenv("DOMAIN", "http://localhost")
PORT = int(os.getenv("UVICORN_PORT", 8124))

# Only include port in BASE_URL if DOMAIN is localhost
if DOMAIN == "http://localhost" or DOMAIN == "localhost":
    BASE_URL = f"{DOMAIN}:{PORT}"
else:
    BASE_URL = DOMAIN


logger.info(f"Configured BASE_URL: {BASE_URL}")

# Add project root to Python path for imports
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

# Import processors and dependencies
try:
    from ..utils.pdf_handler import PDFProcessor
    from ..utils.image_handler import ImageProcessor
    from ..core.chatbot import text_to_cad_agent
    from ..database.database import init_db, get_db
    from ..crud import chat_processing as crud
    from ..models.sessions import Session as SessionModel, ChatHistory
    from ..middleware.auth import auth_middleware, verify_token_dependency
    from ..utils.runtime_monitor import runtime_monitor
    from ..utils.sizing_advisor import sizing_advisor
    from ..utils.context_manager import get_user_id
except ImportError:
    from src.utils.pdf_handler import PDFProcessor
    from src.utils.image_handler import ImageProcessor
    from src.core.chatbot import text_to_cad_agent
    from src.database.database import init_db, get_db
    from src.crud import chat_processing as crud
    from src.models.sessions import Session as SessionModel, ChatHistory
    from src.middleware.auth import auth_middleware, verify_token_dependency
    from src.utils.runtime_monitor import runtime_monitor
    from src.utils.sizing_advisor import sizing_advisor
    from src.utils.context_manager import get_user_id

# Initialize database
init_db()

# Initialize shared processors used by health and upload endpoints.
# Constructors tolerate missing OPENAI_API_KEY by setting client=None.
pdf_processor = PDFProcessor(cad_agent=text_to_cad_agent)
image_processor = ImageProcessor(cad_agent=text_to_cad_agent)

# Security scheme for token authentication
security = HTTPBearer()

# Create FastAPI app with security configuration
app = FastAPI(
    title="Tolery CAD Generation API",
    description="Main application with all endpoints including demo endpoints at root.",
    version="2.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    swagger_ui_parameters={
        "persistAuthorization": True,
        "displayRequestDuration": True,
        "filter": True,
        "tryItOutEnabled": True
    }
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://preprodv4.tolery.io",
        "https://origin-preprod-v4.test",
    ],
    allow_credentials=True,
    allow_methods=["POST", "OPTIONS"],
    allow_headers=[
        "Content-Type",
        "Accept",
        "X-CSRF-TOKEN",
    ],
    expose_headers=[
        "Content-Type",
    ],
)

# Add authentication middleware
app.middleware("http")(auth_middleware)


@app.on_event("startup")
async def start_runtime_monitor_sampler():
    async def _sample_loop():
        while True:
            runtime_monitor.sample_ram()
            await asyncio.sleep(2)

    app.state.runtime_monitor_task = asyncio.create_task(_sample_loop())


@app.on_event("shutdown")
async def stop_runtime_monitor_sampler():
    task = getattr(app.state, "runtime_monitor_task", None)
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

# Static files and templates setup
templates_dir = project_root / 'templates'
static_dir = project_root / 'static'

try:
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    templates = Jinja2Templates(directory=str(templates_dir))
except Exception as e:
    logger.warning(f"Could not mount static files or templates: {e}")
    templates = None

# Database dependency is now imported from database.database module

# ============================================================================
# REQUEST/RESPONSE MODELS
# ============================================================================

try:
    from ..schemas.sessions import ChatRequest
except ImportError:
    from src.schemas.sessions import ChatRequest


class PDFProcessingRequest(BaseModel):
    """Request model for PDF processing endpoint."""
    user_input: str = Field("", description="Optional text prompt to guide PDF processing", example="Analyze this technical drawing and generate CAD code")
    session_id: Optional[str] = Field(None, description="Optional session ID for conversation continuity", example="session_abc123_456789")

    class Config:
        schema_extra = {
            "example": {
                "user_input": "Analyze this technical drawing and generate CAD code for the part shown",
                "session_id": "session_abc123_456789"
            }
        }

class ImageProcessingRequest(BaseModel):
    """Request model for image processing endpoint."""
    user_input: str = Field("", description="Optional text prompt to guide image processing", example="Generate CAD code from this technical drawing")
    session_id: Optional[str] = Field(None, description="Optional session ID for conversation continuity", example="session_xyz789_123456")

    class Config:
        schema_extra = {
            "example": {
                "user_input": "Generate CAD code from this technical drawing or sketch",
                "session_id": "session_xyz789_123456"
            }
        }

class PDFProcessingResponse(BaseModel):
    """Response model for PDF processing."""
    success: bool
    response: str
    session_id: Optional[str] = None
    obj_export: Optional[str] = None
    step_export: Optional[str] = None
    json_export: Optional[str] = None

class ImageProcessingResponse(BaseModel):
    """Response model for image processing"""
    success: bool
    response: str
    session_id: Optional[str] = None
    obj_export: Optional[str] = None
    step_export: Optional[str] = None
    json_export: Optional[str] = None



class UpdateCodeRequest(BaseModel):
    """Request model for the /api/update-latest-code endpoint."""
    code: str
    session_id: Optional[str] = None

class ChatMessage(BaseModel):
    """Chat message model."""
    timestamp: str
    role: str
    content: str

class ChatHistoryResponse(BaseModel):
    """Chat history response model."""
    session_id: str
    messages: List[ChatMessage]
    total_messages: int

# ============================================================================
# ROOT ENDPOINTS
# ============================================================================

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    if templates:
        return templates.TemplateResponse("index.html", {"request": request})
    else:
        return HTMLResponse("""
        <html>
            <head><title>Tolery CAD API</title></head>
            <body>
                <h1>Tolery CAD Generation API</h1>
                <p>API Documentation:</p>
                <ul>
                    <li><a href="/docs">API Documentation</a></li>
                    <li><a href="/api-test/docs">API-TEST Documentation</a></li>
                    <li><a href="/api-production/docs">API-PRODUCTION Documentation</a></li>
                </ul>
            </body>
        </html>
        """)


@app.get("/monitor", response_class=HTMLResponse)
async def monitor_page(request: Request):
    """Runtime monitoring dashboard."""
    if templates:
        return templates.TemplateResponse("monitor.html", {"request": request})
    return HTMLResponse("""
    <html>
        <head><title>Tolery Monitor</title></head>
        <body>
            <h1>Tolery Runtime Monitor</h1>
            <p>Templates are not available.</p>
        </body>
    </html>
    """)


@app.get("/api/health")
async def health_check():
    return {
        "status": "ok",
        "timestamp": datetime.now().isoformat(),
        "version": "2.0.0",
        "environment": {
            "python_version": platform.python_version(),
            "system": platform.system(),
            "processor": platform.processor(),
            "pdf_processor_available": getattr(pdf_processor, "client", None) is not None,
            "image_processor_available": getattr(image_processor, "client", None) is not None
        }
    }


@app.get("/api/monitor/overview")
async def monitor_overview():
    """Runtime monitor overview for active users, sessions, cost, and RAM peak."""
    return runtime_monitor.get_overview()


@app.get("/api/monitor/ram")
async def monitor_ram(limit: int = 300):
    """RAM samples for realtime charts."""
    limit = max(1, min(limit, 7200))
    return runtime_monitor.get_ram_series(limit=limit)


@app.get("/api/monitor/requests")
async def monitor_requests(limit: int = 100):
    """Active and recent CAD stream requests."""
    limit = max(1, min(limit, 500))
    return runtime_monitor.get_requests(limit=limit)


@app.get("/api/monitor/sessions")
async def monitor_sessions(limit: int = 100):
    """Session-level accumulated request cost for this API process."""
    limit = max(1, min(limit, 500))
    return {"sessions": runtime_monitor.get_sessions(limit=limit)}


@app.get("/api/monitor/performance")
async def monitor_performance():
    """Latency percentiles (P50/P90/P95/P99), throughput (req/min), CPU stats, and per-step timing."""
    return runtime_monitor.get_performance_summary()


@app.get("/api/monitor/sizing")
async def monitor_sizing(ram_safety_factor: float = 1.6, cpu_target: float = 70.0):
    """Server sizing recommendation based on observed metrics."""
    perf = runtime_monitor.get_performance_summary()
    advice = sizing_advisor.recommend(
        perf,
        safety={
            "ram_safety_factor": max(1.0, min(3.0, ram_safety_factor)),
            "cpu_utilization_target_pct": max(10.0, min(100.0, cpu_target)),
        },
    )
    return advice


@app.get("/api/monitor/export")
async def monitor_export():
    """Export full monitoring data as JSON for offline analysis."""
    import json as _json
    data = runtime_monitor.get_export_data()
    payload = _json.dumps(data, ensure_ascii=False, indent=2)
    from fastapi.responses import Response
    return Response(
        content=payload,
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=tolery-monitor-export.json"},
    )


# ============================================================================
# CHAT AND PROCESSING ENDPOINTS
# ============================================================================

@app.post("/api/chat-to-cad", summary="Process a chat message",
         description="Takes a text message, processes it, and returns generated code")
async def chat(
    request_data: ChatRequest,
    db: Session = Depends(get_db)
):
    """Process a chat message and generate CAD code using singleton agent."""
    # Generate user_id from session_id or create temporary one
    user_id = request_data.session_id or f"temp_user_{hash(request_data.message) % 10000}"

    try:
        logger.info(f"[MAIN-CHAT] Processing request for user {user_id}")

        # Use the initialized text_to_cad_agent instance
        agent = text_to_cad_agent

        # Process request using agent
        response = await agent.process_request(
            user_text=request_data.message,
            session_id=user_id,
            is_edit_request=False,
            request_origin='api'
        )

        # Return the response in expected format
        return {
            "response": response.get('response', ''),
            "session_id": user_id,
            "obj_export": response.get('obj_export') or response.get('obj_path'),
            "step_export": response.get('step_export') or response.get('step_path'),
            "json_export": response.get('json_path') or response.get('json_export'),
            "success": True
        }

    except Exception as e:
        logger.error(f"[MAIN-CHAT] Error processing request for user {user_id}: {str(e)}")
        # Fallback to legacy method for backward compatibility
        try:
            from src.schemas.sessions import ChatRequest as ApiChatRequest
            api_request = ApiChatRequest(
                message=request_data.message,
                session_id=request_data.session_id,
                image_path="",
                part_file_name="part_file_name",
                export_format="obj",
                material_choice="STEEL",
                selected_feature_uuid="",
                is_edit_request=request_data.is_edit_request
            )

            # Process using legacy method
            result = await crud.handle_chat_request(db, api_request, text_to_cad_agent, request_origin='web')
            
            # Check for errors
            if isinstance(result, dict) and result.get("error"):
                logger.error(f"Error processing chat request: {result['error']}")
                raise HTTPException(status_code=500, detail=result["error"])

            # Return the result dictionary
            return result
            
        except Exception as fallback_error:
            logger.exception(f"Both pooled and legacy methods failed: {str(fallback_error)}")
            raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(fallback_error)}")
    
    finally:
        # No need to release agent with singleton pattern
        logger.info(f"[MAIN-CHAT] Request completed for user {user_id}")

# ============================================================================
# SIMPLIFIED PARAMETER DEFINITIONS
# ============================================================================

@app.post("/api/process-pdf", response_model=PDFProcessingResponse, tags=["pdf"])
async def process_pdf(
    file: UploadFile = File(..., description="PDF file to process"),
    user_input: str = Form("", description="Optional text prompt to guide processing"),
    session_id: Optional[str] = Form(None, description="Optional session ID for conversation continuity"),
    db: Session = Depends(get_db)
):
    """Process a PDF file with OpenAI using singleton agent for multi-user support."""
    try:
        # Process the PDF analysis first (this doesn't need agent pool)
        success, analysis_result, final_session_id, agent_result = await pdf_processor.process_uploaded_file(db, file, user_input, session_id)

        # Use the final session_id (either provided or generated)
        session_id = final_session_id
        user_id = session_id or f"temp_user_{hash(user_input) % 10000}"

        if not success:
            logger.error(f"PDF processing failed: {analysis_result}")
            return PDFProcessingResponse(
                success=False,
                response=analysis_result,
                session_id=session_id or "unknown"
            )

        # If agent_result already contains CAD generation OR questions, return it directly
        if agent_result and (agent_result.get("code") or agent_result.get("message")):
            logger.info(f"[PROCESS-PDF] CAD/questions already processed by PDFProcessor for session {session_id}")

            # DEBUG: Log agent_result to see what fields are available
            logger.info(f"[PROCESS-PDF] agent_result keys: {list(agent_result.keys())}")
            logger.info(f"[PROCESS-PDF] File paths in agent_result:")
            logger.info(f"  - obj_export: {agent_result.get('obj_export')}")
            logger.info(f"  - obj_path: {agent_result.get('obj_path')}")
            logger.info(f"  - json_export: {agent_result.get('json_export')}")
            logger.info(f"  - json_path: {agent_result.get('json_path')}")
            logger.info(f"  - step_export: {agent_result.get('step_export')}")
            logger.info(f"  - step_path: {agent_result.get('step_path')}")

            return PDFProcessingResponse(
                success=True,
                response=analysis_result,
                session_id=session_id,
                obj_export=agent_result.get("obj_export") or agent_result.get("obj_path"),
                step_export=agent_result.get("step_export") or agent_result.get("step_path"),
                json_export=agent_result.get("json_path") or agent_result.get("json_export")
            )

        # If CAD generation is needed, use Agent Pool Manager
        agent = None
        try:
            # Create message for CAD generation based on PDF analysis
            chat_message = f"Based on PDF analysis: {analysis_result}. {user_input if user_input else ''}"

            logger.info(f"[PROCESS-PDF] Processing request for user {user_id}")

            # Use the initialized text_to_cad_agent instance
            agent = text_to_cad_agent

            # Process request using agent
            response = await agent.process_request(
                user_text=chat_message,
                session_id=user_id,
                is_edit_request=False,
                request_origin='api'
            )

            return PDFProcessingResponse(
                success=True,
                response=response.get('message', ''),
                session_id=session_id,
                obj_export=response.get('obj_export') or response.get('obj_path'),
                step_export=response.get('step_export') or response.get('step_path'),
                json_export=response.get('json_path') or response.get('json_export')
            )

        except Exception as pool_error:
            logger.error(f"[PROCESS-PDF] Pool manager failed for user {user_id}: {str(pool_error)}")

            # Fallback to legacy method for backward compatibility
            try:
                # Process the PDF using the processor's async method which handles session properly
                success, message, final_session_id, agent_result = await pdf_processor.process_uploaded_file(db, file, user_input, session_id)

                # Extract export files from agent_result if available
                obj_export = None
                step_export = None
                json_export = None
                if agent_result:
                    obj_export = agent_result.get("obj_export") or agent_result.get("obj_path")
                    step_export = agent_result.get("step_export") or agent_result.get("step_path")
                    json_export = agent_result.get("json_path") or agent_result.get("json_export")

                return PDFProcessingResponse(
                    success=success,
                    response=message,
                    session_id=final_session_id,
                    obj_export=obj_export,
                    step_export=step_export,
                    json_export=json_export
                )
            except Exception as fallback_error:
                logger.exception(f"Both pooled and legacy methods failed: {str(fallback_error)}")
                return PDFProcessingResponse(
                    success=False,
                    response=f"Error processing PDF: {str(fallback_error)}",
                    session_id=session_id
                )

        finally:
            # No need to release agent with singleton pattern
            logger.info(f"[PROCESS-PDF] Request completed for user {user_id}")

    except Exception as e:
        logger.exception(f"Error processing PDF: {str(e)}")
        return PDFProcessingResponse(success=False, response=f"Error processing PDF: {str(e)}", session_id=session_id)

@app.post("/api/process-image", response_model=ImageProcessingResponse, tags=["image"])
async def process_image(
    file: UploadFile = File(..., description="Image file to process (PNG, JPG, JPEG, GIF, BMP, TIFF)"),
    user_input: str = Form("", description="Optional text prompt to guide processing"),
    session_id: Optional[str] = Form(None, description="Optional session ID for conversation continuity"),
    db: Session = Depends(get_db)
):
    """Process an image file with OpenAI Vision using singleton agent for multi-user support."""
    try:
        # Process the image analysis first (this doesn't need agent pool)
        success, analysis_result, final_session_id, agent_result = await image_processor.process_uploaded_file(db, file, user_input, session_id)

        # Use the final session_id (either provided or generated)
        session_id = final_session_id
        user_id = session_id or f"temp_user_{hash(user_input) % 10000}"

        # If agent_result already contains CAD generation OR questions, return it directly
        if agent_result and (agent_result.get("code") or agent_result.get("message")):
            logger.info(f"[PROCESS-IMAGE] CAD/questions already processed by ImageProcessor for session {session_id}")
            return ImageProcessingResponse(
                success=True,
                response=analysis_result,
                session_id=session_id,
                obj_export=agent_result.get("obj_export") or agent_result.get("obj_path"),
                step_export=agent_result.get("step_export") or agent_result.get("step_path"),
                json_export=agent_result.get("json_path") or agent_result.get("json_export")
            )

        if not success:
            logger.error(f"Image processing failed: {analysis_result}")
            return ImageProcessingResponse(
                success=False,
                response=analysis_result,
                session_id=session_id or "unknown"
            )

        # If CAD generation is needed, use Agent Pool Manager
        agent = None
        try:
            # Create message for CAD generation based on image analysis
            chat_message = f"Based on image analysis: {analysis_result}. {user_input if user_input else ''}"

            logger.info(f"[PROCESS-IMAGE] Processing request for user {user_id}")

            # Use the initialized text_to_cad_agent instance
            agent = text_to_cad_agent

            # Process request using agent
            response = await agent.process_request(
                user_text=chat_message,
                session_id=user_id,
                is_edit_request=False,
                request_origin='api'
            )

            return ImageProcessingResponse(
                success=True,
                response=response.get('message', ''),
                session_id=session_id,
                obj_export=response.get('obj_export') or response.get('obj_path'),
                step_export=response.get('step_export') or response.get('step_path'),
                json_export=response.get('json_path') or response.get('json_export')
            )

        except Exception as pool_error:
            logger.error(f"[PROCESS-IMAGE] Pool manager failed for user {user_id}: {str(pool_error)}")

            # Fallback to legacy method for backward compatibility
            try:
                # Process the image using the processor's async method if available, or create temp file with session
                if hasattr(image_processor, 'process_uploaded_file'):
                    success, message, final_session_id, agent_result = await image_processor.process_uploaded_file(db, file, user_input, session_id)
                else:
                    # Fallback: create temp file and use session_id properly
                    temp_dir = Path("temp_uploads")
                    temp_dir.mkdir(exist_ok=True)

                    file_path = temp_dir / file.filename
                    with open(file_path, "wb") as f:
                        content = await file.read()
                        f.write(content)

                    # Generate session_id if not provided
                    if not session_id:
                        import random
                        import uuid
                        rand_digits = random.randint(100000, 999999)
                        rand_uuid_hex = uuid.uuid4().hex[:6]
                        session_id = f"session_{rand_uuid_hex}_{rand_digits}"

                    # Process the image with proper session_id
                    success, message = image_processor.process_image(db, session_id, str(file_path), user_input)
                    final_session_id = session_id
                    agent_result = None

                    # Clean up
                    if file_path.exists():
                        file_path.unlink()

                # Extract export files from agent_result if available
                obj_export = None
                step_export = None
                json_export = None
                if agent_result:
                    obj_export = agent_result.get("obj_export") or agent_result.get("obj_path")
                    step_export = agent_result.get("step_export") or agent_result.get("step_path")
                    json_export = agent_result.get("json_path") or agent_result.get("json_export")

                return ImageProcessingResponse(
                    success=success,
                    response=message,
                    session_id=final_session_id,
                    obj_export=obj_export,
                    step_export=step_export,
                    json_export=json_export
                )
            except Exception as fallback_error:
                logger.exception(f"Both pooled and legacy methods failed: {str(fallback_error)}")
                return ImageProcessingResponse(
                    success=False,
                    response=f"Error processing image: {str(fallback_error)}",
                    session_id=session_id
                )

        finally:
            # No need to release agent with singleton pattern
            logger.info(f"[PROCESS-IMAGE] Request completed for user {user_id}")

    except Exception as e:
        logger.exception(f"Error processing image: {str(e)}")
        return ImageProcessingResponse(success=False, response=f"Error processing image: {str(e)}", session_id=session_id)

@app.post("/api/process-multi-file", response_model=dict, tags=["files"])
async def process_multi_file(
    pdf_file: Optional[UploadFile] = File(None),
    image_file: Optional[UploadFile] = File(None),
    user_input: str = Form(""),
    session_id: Optional[str] = Form(None),
    db: Session = Depends(get_db)
):
    """Process multiple files (PDF and/or image) together with session context using singleton agent."""
    if not pdf_file and not image_file:
        raise HTTPException(status_code=400, detail="At least one file (PDF or image) must be provided")

    # Generate session_id if not provided
    if not session_id:
        import random
        import uuid
        rand_digits = random.randint(100000, 999999)
        rand_uuid_hex = uuid.uuid4().hex[:6]
        session_id = f"session_{rand_uuid_hex}_{rand_digits}"

    user_id = session_id or f"temp_user_{hash(user_input) % 10000}"
    results = {"session_id": session_id}

    # Collect analysis results
    pdf_analysis = None
    image_analysis = None

    # Process PDF if provided
    if pdf_file:
        logger.info(f"Processing PDF in multi-file request: {pdf_file.filename}")
        try:
            success, message, final_session_id, agent_result = await pdf_processor.process_uploaded_file(db, pdf_file, user_input, session_id)
            results["pdf"] = {"success": success, "message": message, "session_id": final_session_id}
            # Update session_id to the one used by PDF processor
            session_id = final_session_id
            if success:
                pdf_analysis = message
        except Exception as e:
            logger.error(f"Error processing PDF in multi-file request: {e}")
            results["pdf"] = {"success": False, "message": f"Error processing PDF: {str(e)}", "session_id": session_id}

    # Process image if provided
    if image_file:
        logger.info(f"Processing image in multi-file request: {image_file.filename}")
        try:
            success, message, final_session_id, agent_result = await image_processor.process_uploaded_file(db, image_file, user_input, session_id)
            results["image"] = {"success": success, "message": message, "session_id": final_session_id}
            if agent_result:
                results["image"]["json_viewer_url"] = agent_result.get("json_viewer_url")
                results["image"]["json_viewer_ready"] = agent_result.get("json_viewer_ready")
            if success:
                image_analysis = message
        except Exception as e:
            logger.error(f"Error processing image in multi-file request: {e}")
            results["image"] = {"success": False, "message": f"Error processing image: {str(e)}", "session_id": session_id}

    # Combine results if both files were processed
    if pdf_file and image_file:
        pdf_success = results.get("pdf", {}).get("success", False)
        image_success = results.get("image", {}).get("success", False)

        if pdf_success and image_success:
            combined_message = f"PDF Analysis: {results['pdf']['message']}\n\nImage Analysis: {results['image']['message']}"
            results["combined"] = {"success": True, "message": combined_message}

            # Use singleton agent for CAD generation from combined analysis
            try:
                logger.info(f"[PROCESS-MULTI-FILE] Processing request for user {user_id}")

                # Use the initialized text_to_cad_agent instance
                agent = text_to_cad_agent

                # Create combined message for CAD generation
                chat_message = f"Based on combined analysis:\n{combined_message}\n\n{user_input if user_input else ''}"

                # Process request using singleton agent
                response = await agent.process_request(
                    user_text=chat_message,
                    session_id=user_id,
                    is_edit_request=False,
                    request_origin='api'
                )

                # Add CAD generation results to combined
                results["combined"]["cad_response"] = response.get('response', '')
                results["combined"]["obj_export"] = response.get('obj_export')
                results["combined"]["step_export"] = response.get('step_export')

            except Exception as processing_error:
                logger.error(f"[PROCESS-MULTI-FILE] Processing failed for user {user_id}: {str(processing_error)}")
                results["combined"]["cad_error"] = str(processing_error)

            finally:
                # No need to release agent with singleton pattern
                logger.info(f"[PROCESS-MULTI-FILE] Request completed for user {user_id}")
        else:
            results["combined"] = {"success": False, "message": "One or more files failed to process"}

    # If only one file was processed successfully, use Agent Pool Manager for CAD generation
    elif (pdf_file and results.get("pdf", {}).get("success")) or (image_file and results.get("image", {}).get("success")):
        agent = None
        try:
            analysis_text = pdf_analysis or image_analysis
            chat_message = f"Based on analysis: {analysis_text}. {user_input if user_input else ''}"

            logger.info(f"[PROCESS-MULTI-FILE] Processing request for user {user_id}")

            # Use the initialized text_to_cad_agent instance
            agent = text_to_cad_agent

            # Process request using agent
            response = await agent.process_request(
                user_text=chat_message,
                session_id=user_id,
                is_edit_request=False,
                request_origin='api'
            )

            # Add CAD generation results
            file_type = "pdf" if pdf_file else "image"
            results[file_type]["cad_response"] = response.get('response', '')
            results[file_type]["obj_export"] = response.get('obj_export')
            results[file_type]["step_export"] = response.get('step_export')

        except Exception as processing_error:
            logger.error(f"[PROCESS-MULTI-FILE] Processing failed for user {user_id}: {str(processing_error)}")
            file_type = "pdf" if pdf_file else "image"
            results[file_type]["cad_error"] = str(processing_error)

        finally:
            # No need to release agent with singleton pattern
            logger.info(f"[PROCESS-MULTI-FILE] Request completed for user {user_id}")

    return results

@app.post("/api/refresh_chat")
async def refresh_chat_session():
    """Refresh the chat session."""
    try:
        from src.core.chatbot import clear_chat_history
        clear_chat_history()
        return {"success": True, "message": "Chat history cleared"}
    except Exception as e:
        logger.error(f"Error refreshing chat session: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/generate-cad-stream")
async def generate_cad_stream(
    request: Request,
    message: str,
    is_edit_request: bool = False,
    session_id: Optional[str] = None,
    token: Optional[str] = None,
    material_choice: Optional[str] = "STEEL",
    priority: int = 0
):
    """
    Generate CAD with real-time progress updates via Server-Sent Events.

    Note: This endpoint does NOT use Depends(get_db) to avoid holding database
    connections during long-running streams. Database connections are created
    on-demand within the stream for short-lived operations only.

    Args:
        request: FastAPI request object
        message: User's CAD generation request
        is_edit_request: Whether this is an edit request
        session_id: Optional session ID for continuity
        token: Authentication token (query parameter for EventSource)
        priority: FreeCAD queue priority from 0 to 100. Defaults to 0.

    Returns:
        StreamingResponse: Server-Sent Events stream with progress updates
    """
    import time
    import json
    stream_start_time = time.time()

    if priority < 0 or priority > 100:
        raise HTTPException(status_code=400, detail="priority must be an integer from 0 to 100")

    logger.info(f"CAD generation request: '{message[:50]}...' (edit: {is_edit_request}, session: {session_id}, priority: {priority})")
    logger.debug(f"[SSE_PARAMS] Message length: {len(message)} characters")
    logger.debug(f"[SSE_PARAMS] Is edit request: {is_edit_request}")
    logger.debug(f"[SSE_PARAMS] Session ID provided: {session_id is not None}")

    # Check authentication first and send error via SSE if needed
    auth_header = request.headers.get("Authorization")
    auth_token = None
    if auth_header and auth_header.startswith("Bearer "):
        auth_token = auth_header.split(" ")[1]
    elif token:  # From query parameter
        auth_token = token

    if not auth_token:
        async def auth_error_stream():
            error_data = json.dumps({
                "error": "Authentication token is missing. Please check your API token and try again."
            })
            yield f"data: {error_data}\n\n"
        
        return StreamingResponse(
            auth_error_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET",
                "Access-Control-Allow-Headers": "Cache-Control"
            }
        )

    # Verify the token
    from src.middleware.auth import verify_token
    if not verify_token(auth_token):
        async def auth_invalid_stream():
            error_data = json.dumps({
                "error": "Invalid or expired authentication token. Please check your API token and try again."
            })
            yield f"data: {error_data}\n\n"
        
        return StreamingResponse(
            auth_invalid_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET",
                "Access-Control-Allow-Headers": "Cache-Control"
            }
        )

    if not message:
        logger.warning(f"Empty message in SSE request")
        async def empty_message_stream():
            error_data = json.dumps({
                "error": "Message is required for CAD generation."
            })
            yield f"data: {error_data}\n\n"
        
        return StreamingResponse(
            empty_message_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET",
                "Access-Control-Allow-Headers": "Cache-Control"
            }
        )

    request_id = runtime_monitor.new_request_id()
    user_id = get_user_id() or request.headers.get("X-User-ID") or session_id or request_id
    runtime_monitor.start_request(
        request_id=request_id,
        user_id=user_id,
        session_id=session_id,
        message=message,
        is_edit_request=is_edit_request,
        material_choice=material_choice,
        priority=priority,
    )

    async def event_stream():
        """Generator function for Server-Sent Events with heartbeat to prevent Cloudflare timeout."""
        import asyncio
        import json as _json

        stream_id = f"stream_{int(time.time())}"
        logger.debug(f"[SSE_STREAM] Starting event stream {stream_id}")

        # Sentinel objects for inter-task communication
        _HEARTBEAT = object()
        _DONE = object()

        queue: asyncio.Queue = asyncio.Queue()

        async def _producer():
            """Run the CAD generator and push updates into the queue."""
            try:
                logger.debug(f"[SSE_STREAM] Importing streaming function for stream {stream_id}")
                try:
                    from ..crud.chat_processing import generate_cad_realtime_stream
                except ImportError:
                    from src.crud.chat_processing import generate_cad_realtime_stream

                logger.debug(f"[SSE_STREAM] Beginning real-time CAD generation for stream {stream_id}")
                agent = text_to_cad_agent

                async for update in generate_cad_realtime_stream(
                    message=message,
                    is_edit_request=is_edit_request,
                    session_id=session_id,
                    agent=agent,
                    material_choice=material_choice,
                    priority=priority
                ):
                    await queue.put(update)
            except Exception as exc:
                await queue.put({"error": str(exc)})
            finally:
                await queue.put(_DONE)

        # ── HEARTBEAT_INTERVAL: well within Cloudflare's 80s limit ────────────
        HEARTBEAT_INTERVAL = 15.0

        # Track the monotonic time of the last SSE bytes actually flushed to client.
        # Using time.monotonic() so it is immune to system clock adjustments.
        # This is a mutable container (list) so the nested async closure can write to it.
        _last_sse_sent_at = [time.monotonic()]

        async def _heartbeat():
            """
            Poll every 1 second and inject _HEARTBEAT when no SSE event has been
            sent for HEARTBEAT_INTERVAL seconds.

            Why poll at 1s instead of sleep(15s)?
            - Old approach: sleep(15) from TASK-START, not from LAST-SENT-EVENT.
              Example bug: data sent at T=13s, heartbeat fires T=15s (OK),
              next heartbeat only at T=30s → 17s silence after T=13s data!
            - New approach: wakes every 1s, checks elapsed since last actual
              SSE yield. Fires ping within ~1s of the 15s boundary.
            """
            while True:
                await asyncio.sleep(1.0)
                elapsed = time.monotonic() - _last_sse_sent_at[0]
                if elapsed >= HEARTBEAT_INTERVAL:
                    # Only inject if queue is empty; if real data is queued
                    # the consumer will flush it immediately anyway.
                    if queue.empty():
                        await queue.put(_HEARTBEAT)

        producer_task = asyncio.create_task(_producer())
        heartbeat_task = asyncio.create_task(_heartbeat())

        event_count = 0
        try:
            while True:
                # ── Layer 2 safety net (event-loop starvation guard) ──────────
                # IMPORTANT: Do NOT use asyncio.shield() here.
                #
                # Bug with asyncio.shield(queue.get()):
                #   Each TimeoutError leaves an ORPHANED queue.get() coroutine
                #   still waiting in the background. When _producer finally puts
                #   _DONE into the queue, one of those orphans consumes it—the
                #   main loop never sees _DONE and keeps pinging forever (zombie stream).
                #
                # Safe approach: plain wait_for(queue.get(), timeout).
                #   On cancellation, asyncio.Queue guarantees the item stays in
                #   the queue (getters list is cleaned up atomically), so the next
                #   iteration will consume it correctly.
                try:
                    item = await asyncio.wait_for(
                        queue.get(),
                        timeout=HEARTBEAT_INTERVAL
                    )
                except asyncio.TimeoutError:
                    # Client may have already disconnected—stop the zombie stream.
                    if await request.is_disconnected():
                        logger.info(
                            f"[SSE_STREAM] Client disconnected, stopping stream {stream_id} "
                            f"at {time.time() - stream_start_time:.1f}s"
                        )
                        runtime_monitor.mark_disconnected(request_id)
                        break

                    elapsed = time.monotonic() - _last_sse_sent_at[0]
                    logger.warning(
                        f"[SSE_HEARTBEAT] Stream {stream_id} - queue timeout after "
                        f"{elapsed:.1f}s (event loop starved?), sending forced ping "
                        f"at {time.time() - stream_start_time:.1f}s"
                    )
                    yield ": ping\n\n"
                    _last_sse_sent_at[0] = time.monotonic()
                    continue

                if item is _DONE:
                    # Generation finished cleanly
                    stream_duration = time.time() - stream_start_time
                    logger.info(f"CAD generation completed in {stream_duration:.2f}s ({event_count} events)")
                    runtime_monitor.finish_request(request_id)
                    break

                if item is _HEARTBEAT:
                    # SSE comment — keeps the connection alive, invisible to JS EventSource
                    elapsed = time.monotonic() - _last_sse_sent_at[0]
                    logger.debug(
                        f"[SSE_HEARTBEAT] Stream {stream_id} - sending ping "
                        f"({elapsed:.1f}s since last event) "
                        f"at {time.time() - stream_start_time:.1f}s"
                    )
                    yield ": ping\n\n"
                    _last_sse_sent_at[0] = time.monotonic()
                    continue

                # Real update from the generator
                event_count += 1
                update = item
                if isinstance(update, dict):
                    update["request_id"] = request_id
                    runtime_monitor.record_update(request_id, update)
                logger.debug(f"[SSE_EVENT] Stream {stream_id} - Event #{event_count}: {type(update).__name__}")

                if "step" in update:
                    step_name = update.get("step")
                    progress = update.get("overall_percentage", 0)
                    is_complete = update.get("is_complete", False)
                    if is_complete and step_name in ["analysis", "parameters", "generation_code", "export", "complete"]:
                        logger.info(f"Step completed: {step_name} ({progress}%)")
                    else:
                        logger.debug(f"[SSE_PROGRESS] Stream {stream_id} - Step: {step_name}, Progress: {progress}%, Complete: {is_complete}")
                elif "final_result" in update:
                    logger.info(f"CAD generation completed successfully")
                elif "error" in update:
                    logger.error(f"Error in update: {update.get('error')}")

                data = _json.dumps(update)
                logger.debug(f"[SSE_YIELD] Stream {stream_id} - Yielding data length: {len(data)} characters")
                yield f"data: {data}\n\n"
                # Update timestamp after every real data event
                _last_sse_sent_at[0] = time.monotonic()

        except Exception as e:
            stream_duration = time.time() - stream_start_time
            logger.error(f"Error in CAD generation after {stream_duration:.2f}s: {str(e)}")
            logger.debug(f"[SSE_ERROR] Traceback: {__import__('traceback').format_exc()}")
            runtime_monitor.finish_request(request_id, status="failed", error=str(e))
            error_data = _json.dumps({"error": str(e)})
            yield f"data: {error_data}\n\n"

        finally:
            heartbeat_task.cancel()
            producer_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
            try:
                await producer_task
            except asyncio.CancelledError:
                pass

    logger.debug(f"[SSE_RESPONSE] Creating StreamingResponse for CAD generation")
    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET",
            "Access-Control-Allow-Headers": "Cache-Control"
        }
    )




# --- 3D Viewer File Serving Endpoint ---

@app.get("/api/3d-viewer/{file_path:path}", tags=["cad"])
async def serve_3d_file(file_path: str, request: Request):
    """
    Serve a file for 3D viewer (without download headers).

    Args:
        file_path (str): Path to the file relative to the project root
        request (Request): The incoming request

    Returns:
        FileResponse: The requested file for viewing

    Raises:
        HTTPException: If the file is not found
    """
    try:
        # Log the request method and path (DEBUG level to avoid spam from browser retries)
        logger.debug(f"3D viewer request: {request.method} {file_path}")

        # Handle HEAD requests - FastAPI normally only captures GET for path operations
        if request.method == "HEAD":
            logger.info("HEAD request detected, will return headers only")

        # Normalize file path - handle various input formats
        if file_path.startswith('/'):
            file_path = file_path[1:]

        # Handle paths with 'download' prefix that might come from _create_download_url
        if file_path.startswith('download/'):
            file_path = file_path[len('download/'):]

        # Common paths that users might try
        known_paths = {
            'outputs': 'outputs',
            'obj': 'outputs/obj'
        }

        # Normalize path to use outputs directory for common shorthands
        for prefix, replacement in known_paths.items():
            if file_path.startswith(f"{prefix}/"):
                file_path = f"{replacement}/{file_path[len(prefix)+1:]}"

        # Construct the full file path
        project_root = Path.cwd()
        full_path = project_root / file_path

        logger.debug(f"Looking for 3D file at: {full_path}")

        # Check if the file exists
        if not full_path.exists():
            # Try to find file in recent outputs as fallback
            if 'obj' in file_path.lower() and '/' in file_path:
                filename = file_path.split('/')[-1]

                # First check cad_outputs_generated directory (for bending/generated code)
                cad_outputs_dir = project_root / 'outputs' / 'code' / 'cad_outputs_generated'
                if cad_outputs_dir.exists():
                    cad_file_path = cad_outputs_dir / filename
                    if cad_file_path.exists():
                        logger.info(f"Found file in cad_outputs_generated: {cad_file_path}")
                        full_path = cad_file_path
                    else:
                        # Also try to find by partial name match for bending files
                        for obj_file in cad_outputs_dir.glob("*.obj"):
                            if filename.lower() in obj_file.name.lower() or obj_file.name.lower() in filename.lower():
                                logger.info(f"Found similar file in cad_outputs_generated: {obj_file}")
                                full_path = obj_file
                                break

                # If not found in cad_outputs_generated, check outputs/obj directory
                recent_dirs = []
                if not full_path.exists():
                    obj_dir = project_root / 'outputs' / 'obj'
                    if not obj_dir.exists():
                        logger.warning(f"outputs/obj directory does not exist, creating it")
                        obj_dir.mkdir(parents=True, exist_ok=True)

                    # Look for recent date directories
                    if obj_dir.exists():
                        recent_dirs = sorted([d for d in obj_dir.iterdir() if d.is_dir()], reverse=True)

                    # If we have recent directories, check them for the file
                    for recent_dir in recent_dirs[:2]:  # Check only the 2 most recent directories
                        possible_path = recent_dir / filename
                        if possible_path.exists():
                            logger.info(f"Found file in recent outputs: {possible_path}")
                            full_path = possible_path
                            break

                # If no recent directory found, create a placeholder one with today's date
                # (only reachable when the file wasn't already found above, so obj_dir exists)
                if not full_path.exists() and not recent_dirs:
                    today = datetime.now().strftime('%Y-%m-%d')
                    today_dir = obj_dir / today
                    today_dir.mkdir(exist_ok=True)
                    logger.info(f"Created today's directory: {today_dir}")

                    # For debugging/testing, create a placeholder file
                    placeholder_path = today_dir / filename
                    if not placeholder_path.exists():
                        try:
                            with open(placeholder_path, 'w') as f:
                                f.write(f"# Placeholder OBJ file\nv 0 0 0\nv 0 0 1\nv 0 1 0\nf 1 2 3\n")
                            logger.info(f"Created placeholder OBJ file: {placeholder_path}")
                            full_path = placeholder_path
                        except Exception as e:
                            logger.error(f"Error creating placeholder file: {e}")

            # If still not found, raise 404
            if not full_path.exists():
                raise HTTPException(status_code=404, detail=f"File not found: {file_path}")

        # Get the filename
        filename = full_path.name

        # Determine the media type
        media_type, _ = mimetypes.guess_type(str(full_path))
        if media_type is None:
            extension = full_path.suffix.lower()
            if extension == '.obj':
                media_type = 'text/plain'  # OBJ files are text-based
            elif extension == '.mtl':
                media_type = 'text/plain'  # Material files are text-based
            else:
                media_type = 'application/octet-stream'

        logger.debug(f"Serving 3D file: {full_path} (media type: {media_type})")

        # Return the file for viewing (not download)
        return FileResponse(
            path=str(full_path),
            media_type=media_type,
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
                "Access-Control-Allow-Headers": "*",
                "Cache-Control": "public, max-age=3600"  # Cache for 1 hour
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error serving 3D file {file_path}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error serving 3D file: {str(e)}")

# Add OPTIONS handler for the 3D viewer endpoint to support CORS preflight requests
@app.options("/api/3d-viewer/{file_path:path}", tags=["cad"])
async def serve_3d_file_options(file_path: str):
    """
    Handle OPTIONS requests for the 3D viewer endpoint.
    """
    return {
        "headers": {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
            "Access-Control-Allow-Headers": "*",
            "Access-Control-Max-Age": "86400",  # 24 hours
        }
    }

# Add HEAD handler for the 3D viewer endpoint
@app.head("/api/3d-viewer/{file_path:path}", tags=["cad"])
async def serve_3d_file_head(file_path: str, request: Request):
    """
    Handle HEAD requests for the 3D viewer endpoint.
    Same as GET but only returns headers.
    """
    return await serve_3d_file(file_path, request)


# --- JSON Viewer File Serving Endpoint ---

@app.get("/api/json-viewer/{file_path:path}", tags=["cad"])
async def serve_json_file(file_path: str, request: Request):
    """
    Serve a JSON file for JSON viewer (without download headers).

    Args:
        file_path (str): Path to the JSON file relative to the project root
        request (Request): The incoming request

    Returns:
        FileResponse: The requested JSON file for viewing

    Raises:
        HTTPException: If the file is not found
    """
    try:
        # Log the request method and path
        logger.debug(f"JSON viewer request: {request.method} {file_path}")

        # Handle HEAD requests
        if request.method == "HEAD":
            logger.info("HEAD request detected, will return headers only")

        # Normalize file path - handle various input formats
        if file_path.startswith('/'):
            file_path = file_path[1:]

        # Handle paths with 'download' prefix that might come from _create_download_url
        if file_path.startswith('download/'):
            file_path = file_path[len('download/'):]

        # Common paths that users might try for JSON files
        known_paths = {
            'outputs': 'outputs',
            'json': 'outputs/json'
        }

        # Normalize path to use outputs directory for common shorthands
        for prefix, replacement in known_paths.items():
            if file_path.startswith(f"{prefix}/"):
                file_path = f"{replacement}/{file_path[len(prefix)+1:]}"

        # Construct the full file path
        project_root = Path.cwd()
        full_path = project_root / file_path

        logger.debug(f"Looking for JSON file at: {full_path}")

        # Check if the file exists
        if not full_path.exists():
            # Try to find file in recent outputs as fallback
            if 'json' in file_path.lower() and '/' in file_path:
                filename = file_path.split('/')[-1]

                # Check outputs/json directory
                json_dir = project_root / 'outputs' / 'json'
                if not json_dir.exists():
                    logger.warning(f"outputs/json directory does not exist, creating it")
                    json_dir.mkdir(parents=True, exist_ok=True)

                # Look for recent date directories
                recent_dirs = []
                if json_dir.exists():
                    recent_dirs = sorted([d for d in json_dir.iterdir() if d.is_dir()], reverse=True)

                # If we have recent directories, check them for the file
                for recent_dir in recent_dirs[:2]:  # Check only the 2 most recent directories
                    possible_path = recent_dir / filename
                    if possible_path.exists():
                        logger.info(f"Found JSON file in recent outputs: {possible_path}")
                        full_path = possible_path
                        break

                # Also check root outputs directory for JSON files
                if not full_path.exists():
                    root_json_path = project_root / 'outputs' / filename
                    if root_json_path.exists():
                        logger.info(f"Found JSON file in root outputs: {root_json_path}")
                        full_path = root_json_path

            # If still not found, raise 404
            if not full_path.exists():
                raise HTTPException(status_code=404, detail=f"JSON file not found: {file_path}")

        # Verify it's actually a JSON file
        if not full_path.suffix.lower() == '.json':
            raise HTTPException(status_code=400, detail=f"File is not a JSON file: {file_path}")

        # Get the filename
        filename = full_path.name

        # Set media type for JSON
        media_type = 'application/json'

        logger.debug(f"Serving JSON file: {full_path} (media type: {media_type})")

        # Return the file for viewing (not download)
        return FileResponse(
            path=str(full_path),
            media_type=media_type,
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
                "Access-Control-Allow-Headers": "*",
                "Cache-Control": "public, max-age=3600"  # Cache for 1 hour
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error serving JSON file {file_path}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error serving JSON file: {str(e)}")

# Add OPTIONS handler for the JSON viewer endpoint to support CORS preflight requests
@app.options("/api/json-viewer/{file_path:path}", tags=["cad"])
async def serve_json_file_options(file_path: str):
    """
    Handle OPTIONS requests for the JSON viewer endpoint.
    """
    return {
        "headers": {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
            "Access-Control-Allow-Headers": "*",
            "Access-Control-Max-Age": "86400",  # 24 hours
        }
    }

# Add HEAD handler for the JSON viewer endpoint
@app.head("/api/json-viewer/{file_path:path}", tags=["cad"])
async def serve_json_file_head(file_path: str, request: Request):
    """
    Handle HEAD requests for the JSON viewer endpoint.
    Same as GET but only returns headers.
    """
    return await serve_json_file(file_path, request)


# --- PDF Viewer File Serving Endpoint ---
# PDF is optional (visualization only) — this endpoint only serves files that
# were already generated; a missing file here has no bearing on STEP/OBJ/JSON.

@app.get("/api/pdf-viewer/{file_path:path}", tags=["cad"])
async def serve_pdf_file(file_path: str, request: Request):
    """
    Serve a PDF file for the PDF viewer (without download headers).

    Args:
        file_path (str): Path to the PDF file relative to the project root
        request (Request): The incoming request

    Returns:
        FileResponse: The requested PDF file for viewing

    Raises:
        HTTPException: If the file is not found
    """
    try:
        logger.debug(f"PDF viewer request: {request.method} {file_path}")

        # Normalize file path - handle various input formats
        if file_path.startswith('/'):
            file_path = file_path[1:]

        # Handle paths with 'download' prefix that might come from _create_download_url
        if file_path.startswith('download/'):
            file_path = file_path[len('download/'):]

        # Common paths that users might try for PDF files
        known_paths = {
            'outputs': 'outputs',
            'pdf': 'outputs/pdf'
        }

        # Normalize path to use outputs directory for common shorthands
        for prefix, replacement in known_paths.items():
            if file_path.startswith(f"{prefix}/"):
                file_path = f"{replacement}/{file_path[len(prefix)+1:]}"

        # Construct the full file path
        project_root = Path.cwd()
        pdf_root = (project_root / 'outputs' / 'pdf').resolve()
        full_path = (project_root / file_path).resolve()

        logger.debug(f"Looking for PDF file at: {full_path}")

        # Security: reject any path that escapes outputs/pdf (path traversal via
        # "..", absolute paths, etc). This endpoint is whitelisted in auth
        # middleware to skip token checks, so containment must be enforced here.
        def _is_within_pdf_root(path: Path) -> bool:
            try:
                path.relative_to(pdf_root)
                return True
            except ValueError:
                return False

        # Check if the file exists (and is inside outputs/pdf)
        if not (full_path.exists() and _is_within_pdf_root(full_path)):
            # Try to find file in recent outputs/pdf date directories as fallback
            filename = os.path.basename(file_path)
            found_fallback = None
            if filename and pdf_root.exists():
                recent_dirs = sorted([d for d in pdf_root.iterdir() if d.is_dir()], reverse=True)
                for recent_dir in recent_dirs[:2]:  # Check only the 2 most recent directories
                    possible_path = (recent_dir / filename).resolve()
                    if possible_path.exists() and _is_within_pdf_root(possible_path):
                        logger.info(f"Found PDF file in recent outputs: {possible_path}")
                        found_fallback = possible_path
                        break

            if found_fallback:
                full_path = found_fallback
            else:
                # If still not found (or path escaped outputs/pdf), raise 404 — PDF is
                # optional so the caller (pdf-viewer.js) is expected to handle this gracefully.
                raise HTTPException(status_code=404, detail=f"PDF file not found: {file_path}")

        # Verify it's actually a PDF file
        if not full_path.suffix.lower() == '.pdf':
            raise HTTPException(status_code=400, detail=f"File is not a PDF file: {file_path}")

        filename = full_path.name
        logger.debug(f"Serving PDF file: {full_path}")

        # Return the file for viewing (not download)
        return FileResponse(
            path=str(full_path),
            media_type='application/pdf',
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
                "Access-Control-Allow-Headers": "*",
                "Cache-Control": "public, max-age=3600",  # Cache for 1 hour
                "Content-Disposition": f"inline; filename={filename}"
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error serving PDF file {file_path}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error serving PDF file: {str(e)}")

# Add OPTIONS handler for the PDF viewer endpoint to support CORS preflight requests
@app.options("/api/pdf-viewer/{file_path:path}", tags=["cad"])
async def serve_pdf_file_options(file_path: str):
    """
    Handle OPTIONS requests for the PDF viewer endpoint.
    """
    return {
        "headers": {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
            "Access-Control-Allow-Headers": "*",
            "Access-Control-Max-Age": "86400",  # 24 hours
        }
    }

# Add HEAD handler for the PDF viewer endpoint
@app.head("/api/pdf-viewer/{file_path:path}", tags=["cad"])
async def serve_pdf_file_head(file_path: str, request: Request):
    """
    Handle HEAD requests for the PDF viewer endpoint.
    Same as GET but only returns headers.
    """
    return await serve_pdf_file(file_path, request)

# ============================================================================
# CHAT HISTORY ENDPOINTS
# ============================================================================

@app.post("/api/update-latest-code")
async def update_latest_code(request_data: UpdateCodeRequest, db: Session = Depends(get_db)):
    """Update the latest code for a session."""
    try:
        # Get the session
        session = db.query(SessionModel).filter(SessionModel.session_id == request_data.session_id).first()
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        # Get the latest chat history entry
        latest_entry = db.query(ChatHistory).filter(
            ChatHistory.session_id == request_data.session_id
        ).order_by(ChatHistory.id.desc()).first()

        if not latest_entry:
            # Create a new chat history entry
            latest_entry = ChatHistory(
                session_id=request_data.session_id,
                message="Code update",
                response="Code updated via API",
                lasted_code=request_data.code
            )
            db.add(latest_entry)
        else:
            # Update the existing entry
            latest_entry.lasted_code = request_data.code

        db.commit()
        return {"success": True, "message": "Code updated successfully"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating code: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error updating code: {str(e)}")

# ============================================================================
# MOUNT API-PRODUCTION and API-TEST
# ============================================================================

# Import and mount API-PRODUCTION
try:
    from .production.main import app as api_production_app
except ImportError:
    from src.api.production.main import app as api_production_app

app.mount("/api-production", api_production_app)

# Import and mount API-TEST
try:
    from .test.main import app as api_test_app
except ImportError:
    from src.api.test.main import app as api_test_app

app.mount("/api-test", api_test_app)

# ============================================================================
# DOWNLOAD ROUTER FOR FILE DOWNLOADS
# ============================================================================

# Import and mount download router for file downloads
try:
    from .routes.cad import download_router
except ImportError:
    from src.api.routes.cad import download_router

app.include_router(download_router)


# Import and mount viewer router for 3D file viewing
try:
    from .routes.cad import viewer_router
except ImportError:
    from src.api.routes.cad import viewer_router

app.include_router(viewer_router, prefix="/api")



app.include_router(viewer_router, prefix="/api")

# Custom OpenAPI schema to add Authorization button
from fastapi.openapi.utils import get_openapi

def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    
    openapi_schema = get_openapi(
        title="Tolery API AI",
        version="2.0.0",
        description="AI-powered API for processing PDFs and images with CAD generation capabilities",
        routes=app.routes,
    )
    
    # Add security components for JWT Bearer token
    openapi_schema["components"] = openapi_schema.get("components", {})
    openapi_schema["components"]["securitySchemes"] = {
        "bearerAuth": {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
            "description": "Enter JWT token for authentication"
        }
    }
    
    # Add global security requirement to show Authorize button
    openapi_schema["security"] = [{"bearerAuth": []}]
    
    # Basic multipart/form-data schema enhancement
    for path, path_item in openapi_schema["paths"].items():
        for method, operation in path_item.items():
            if method.lower() == "post" and path in ["/api/process-pdf", "/api/process-image"]:
                if "requestBody" in operation:
                    content = operation["requestBody"]["content"]
                    if "multipart/form-data" in content:
                        schema = content["multipart/form-data"]["schema"]
                        if "properties" not in schema:
                            schema["properties"] = {}
                        
                        # Simple property definitions
                        schema["properties"]["file"] = {
                            "type": "string",
                            "format": "binary",
                            "description": "File to upload and process"
                        }
                        schema["properties"]["user_input"] = {
                            "type": "string",
                            "description": "Optional text prompt"
                        }
                        schema["properties"]["session_id"] = {
                            "type": "string",
                            "description": "Optional session ID",
                            "nullable": True
                        }
                        schema["required"] = ["file"]
    
    app.openapi_schema = openapi_schema
    return app.openapi_schema

# ============================================================================
# TEST ENDPOINTS - For debugging authentication issues
# ============================================================================

@app.post("/api/test-post-simple", tags=["test"])
async def test_post_simple(message: str):
    """Test POST endpoint without multipart data to compare auth behavior."""
    return {"message": f"Received: {message}", "status": "success"}

@app.post("/api/test-post-multipart", tags=["test"])
async def test_post_multipart(
    file: UploadFile = File(..., description="Test file upload"),
    message: str = Form("", description="Test message")
):
    """Test POST endpoint with multipart data to compare auth behavior."""
    return {
        "filename": file.filename,
        "message": message,
        "status": "success"
    }

app.openapi = custom_openapi
