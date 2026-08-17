# -*- coding: utf-8 -*-
import os
import logging
import tempfile
from pathlib import Path
from typing import Tuple, Optional, Any
import asyncio

from openai import AsyncOpenAI  # Changed from OpenAI to AsyncOpenAI
from dotenv import load_dotenv
from sqlalchemy.orm import Session
import uuid
import random

# Import configuration
from src.config.pdf_config import (
    OPENAI_MODEL, OPENAI_MAX_TOKENS, OPENAI_TEMPERATURE,
    CAD_MODEL, CAD_MAX_TOKENS, CAD_TEMPERATURE,
    ERROR_MESSAGES, SUCCESS_MESSAGES,
    get_temp_dir, validate_file_size, validate_file_extension,
    get_error_message, get_success_message,
    LOG_FORMAT, LOG_LEVEL
)

# Import required modules
from src.core.text_to_cad_agent import TextToCADAgent
from src.crud.sessions import add_chat_history_entry, create_session
from src.schemas.sessions import ChatRequest as ApiChatRequest
from src.models.sessions import Session as SessionModel

# Configure logging
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format=LOG_FORMAT
)
logger = logging.getLogger("pdf-handler")

class PDFProcessor:
    """
    Handles processing of PDF files using OpenAI's API.

    This class provides functionality to:
    1. Upload PDF files to OpenAI API
    2. Process PDFs with optional custom prompts
    3. Generate CAD instructions from PDF analysis
    4. Save results to database with chat history
    """

    def __init__(self, api_key: str = None, cad_agent: Optional[TextToCADAgent] = None, cost_tracker=None):
        """
        Initialize the PDF processor with optional API key and CAD agent.

        Args:
            api_key: Optional OpenAI API key. If not provided, will look for
                    OPENAI_API_KEY environment variable.
            cad_agent: Optional instance of TextToCADAgent for CAD generation.
            cost_tracker: Optional CostTracker instance to track OpenAI costs
        """
        # Load environment variables from .env file if present
        load_dotenv()

        # Use provided API key or get from environment
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.cost_tracker = cost_tracker

        # Initialize AsyncOpenAI client for non-blocking I/O
        if self.api_key:
            try:
                self.client = AsyncOpenAI(api_key=self.api_key)  # Changed to AsyncOpenAI
                logger.debug(get_success_message("openai_initialized"))
            except Exception as e:
                logger.error(get_error_message("openai_error", error=str(e)))
                self.client = None
        else:
            logger.warning(get_error_message("no_api_key"))
            self.client = None

        self.cad_agent = cad_agent
        if self.cad_agent and isinstance(self.cad_agent, TextToCADAgent):
            logger.debug("TextToCADAgent instance provided to PDFProcessor.")
        elif cad_agent:
            logger.warning("A cad_agent was provided, but type mismatch. CAD integration might not work.")
        else:
            logger.info("TextToCADAgent instance not provided to PDFProcessor. Will only perform PDF analysis.")

    def _generate_session_id(self) -> str:
        """Generates a unique session ID."""
        rand_digits = random.randint(100000, 999999)
        rand_uuid_hex = uuid.uuid4().hex[:6]
        return f"session_{rand_uuid_hex}_{rand_digits}"

    def _validate_file(self, file_path: str) -> Tuple[bool, str]:
        """
        Validate PDF file before processing.
        
        Args:
            file_path: Path to the PDF file
            
        Returns:
            Tuple of (is_valid: bool, error_message: str)
        """
        if not os.path.exists(file_path):
            return False, get_error_message("file_not_found", filename=file_path)
        
        if not validate_file_extension(file_path):
            return False, get_error_message("invalid_extension", extensions=", ".join([".pdf"]))
        
        try:
            file_size = os.path.getsize(file_path)
            if not validate_file_size(file_size):
                return False, get_error_message("file_too_large", max_size=50)
        except OSError as e:
            return False, f"Cannot access file: {str(e)}"
        
        return True, ""

    async def _upload_file_to_openai(self, file_path: str) -> Tuple[bool, Optional[str], str]:
        """
        Upload file to OpenAI API asynchronously.

        Args:
            file_path: Path to the PDF file

        Returns:
            Tuple of (success: bool, file_id: Optional[str], message: str)
        """
        try:
            with open(file_path, "rb") as file_to_upload:
                upload_response = await self.client.files.create(  # Added await
                    file=file_to_upload,
                    purpose="user_data"
                )
            file_id = upload_response.id
            logger.info(f"File uploaded successfully. File ID: {file_id}")
            return True, file_id, get_success_message("file_uploaded")
        except Exception as e:
            error_msg = get_error_message("openai_error", error=str(e))
            logger.error(error_msg)
            return False, None, error_msg

    async def _analyze_pdf_content(self, file_id: str, prompt: str) -> Tuple[bool, str]:
        """
        Analyze PDF content using OpenAI API asynchronously.

        Args:
            file_id: OpenAI file ID
            prompt: Analysis prompt

        Returns:
            Tuple of (success: bool, analysis_result: str)
        """
        try:
            completion = await self.client.chat.completions.create(  # Added await
                model=OPENAI_MODEL,
                max_completion_tokens=OPENAI_MAX_TOKENS,
                # temperature removed - gpt-5-mini only supports default (1)
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "file", "file": {"file_id": file_id}},
                            {"type": "text", "text": prompt}
                        ]
                    }
                ]
            )
            
            # Track cost if cost_tracker is available
            if self.cost_tracker:
                try:
                    usage = completion.usage
                    self.cost_tracker.add_direct_api_cost(
                        chain_name="pdf_processing",
                        prompt_tokens=usage.prompt_tokens,
                        completion_tokens=usage.completion_tokens,
                        model_name=OPENAI_MODEL
                    )
                except Exception as e:
                    logger.warning(f"Failed to track PDF processing cost: {e}")
            
            analysis_result = completion.choices[0].message.content
            logger.info(f"Successfully processed PDF. Response length: {len(analysis_result)}")
            return True, analysis_result
        except Exception as e:
            error_msg = get_error_message("processing_failed", error=str(e))
            logger.error(error_msg)
            return False, error_msg

    def _should_skip_cad_generation(self, analysis_text: str) -> bool:
        """
        Determine if CAD generation should be skipped based on analysis quality.
        
        Args:
            analysis_text: PDF analysis result
            
        Returns:
            bool: True if CAD generation should be skipped
        """
        if not analysis_text or len(analysis_text.strip()) < 10:
            return True
        
        skip_keywords = ["error", "cannot", "unable", "failed", "not found"]
        return any(keyword in analysis_text.lower() for keyword in skip_keywords)

    async def _generate_cad_instructions(self, session_id: str, cad_input_text: str) -> Optional[dict]:
        """
        Generate CAD instructions using TextToCADAgent.
        
        Args:
            session_id: Session ID for tracking
            cad_input_text: Input text for CAD generation
            
        Returns:
            Optional[dict]: CAD generation result or None if failed
        """
        if not self.cad_agent:
            return None
        
        try:
            logger.info(f"Attempting to generate CAD from PDF analysis result for session {session_id}")
            cad_result = await self.cad_agent.process_request(
                user_text=cad_input_text,
                is_edit_request=False,
                request_origin='pdf_processing',
                session_id=session_id
            )
            
            if cad_result and cad_result.get("code"):
                logger.info(f"Successfully generated CAD code from PDF analysis for session {session_id}")
            else:
                logger.info(f"CAD generation failed or returned questions for session {session_id}")
            
            return cad_result
        except Exception as e:
            error_msg = get_error_message("cad_generation_failed", error=str(e))
            logger.warning(error_msg)
            return {"error": error_msg, "message": f"CAD generation encountered an issue: {str(e)}"}

    def _ensure_session_exists(self, db: Session, session_id: str, file_path: str) -> None:
        """
        Ensure session exists in database.
        
        Args:
            db: Database session
            session_id: Session ID
            file_path: File path for session name
        """
        try:
            if db and session_id:
                session_name = Path(file_path).name[:50]
                existing_session = db.query(SessionModel).filter(SessionModel.session_id == session_id).first()
                if not existing_session:
                    create_session(db, session_id, session_name)
                    logger.info(f"Created session {session_id} ('{session_name}') in database from pdf_handler.")
        except Exception as e:
            logger.error(get_error_message("database_error", error=str(e)))

    def _save_to_chat_history(self, db: Session, session_id: str, user_message: str, agent_result: dict) -> None:
        """
        Save processing result to chat history.
        
        Args:
            db: Database session
            session_id: Session ID
            user_message: User message for history
            agent_result: Agent processing result
        """
        try:
            if add_chat_history_entry and db and session_id:
                chat_request_details = ApiChatRequest(message=user_message, session_id=session_id) if ApiChatRequest else None
                add_chat_history_entry(
                    db=db, 
                    session_id=session_id, 
                    user_message=user_message, 
                    agent_result=agent_result, 
                    chat_request_obj=chat_request_details
                )
                logger.info(f"Saved PDF analysis/CAD attempt to chat history for session {session_id}")
        except Exception as e:
            logger.error(get_error_message("database_error", error=str(e)))

    def _trigger_step_viewer_integration(self, agent_result: dict) -> None:
        """
        Trigger STEP viewer integration when all required files are successfully created.

        Args:
            agent_result: Agent processing result containing file paths
        """
        try:
            # Check if all required files are present (PDF output removed from FreeCAD server)
            step_path = agent_result.get("step_path")
            obj_path = agent_result.get("obj_path")

            if step_path and obj_path:
                logger.info(f"All required files created successfully:")
                logger.info(f"  - STEP: {step_path}")
                logger.info(f"  - OBJ: {obj_path}")

                # Prepare STEP viewer URL for frontend integration
                # Convert absolute path to relative path for API endpoint
                from pathlib import Path
                project_root = Path.cwd()
                try:
                    step_relative_path = Path(step_path).relative_to(project_root)
                    step_viewer_url = f"/api/step-viewer/{step_relative_path}"
                except ValueError as e:
                    logger.warning(f"Could not create relative path for STEP viewer: {e}")
                    # Fallback: use filename only
                    step_viewer_url = f"/api/step-viewer/outputs/step/{Path(step_path).name}"

                # Add STEP viewer URL to agent result for frontend consumption
                agent_result["step_viewer_url"] = step_viewer_url
                agent_result["step_viewer_ready"] = True

                logger.info(f"STEP Viewer integration ready: {step_viewer_url}")
            else:
                logger.info("Not all required files were created, skipping STEP viewer integration")
                missing_files = []
                if not step_path: missing_files.append("STEP")
                if not obj_path: missing_files.append("OBJ")
                logger.info(f"Missing files: {', '.join(missing_files)}")

        except Exception as e:
            logger.error(f"Error in STEP viewer integration: {e}")
            # Don't fail the entire process if STEP viewer integration fails

    async def _cleanup_openai_file(self, file_id: str) -> None:
        """
        Clean up uploaded file from OpenAI asynchronously.

        Args:
            file_id: OpenAI file ID to delete
        """
        if file_id:
            try:
                await self.client.files.delete(file_id)  # Added await
                logger.info(f"Deleted file from OpenAI: {file_id}")
            except Exception as e:
                logger.warning(f"Failed to delete file from OpenAI: {e}")

    def _prepare_prompt(self, user_input: str = "") -> str:
        """
        Prepare analysis prompt with optional user input.
        
        Args:
            user_input: Optional user input to include
            
        Returns:
            str: Complete prompt for analysis
        """
        # Shared with image_handler — see utils/media_prompts.py
        from src.utils.media_prompts import build_media_classification_prompt
        return build_media_classification_prompt("PDF", user_input)

    async def process_pdf(self, db: Session, session_id: str, file_path: str, user_input: str = "") -> Tuple[bool, str, Optional[dict]]:
        """
        Process a PDF file using OpenAI's API, optionally generate CAD, and save to chat history.

        Args:
            db: SQLAlchemy database session.
            session_id: The session ID for this interaction.
            file_path: Path to the PDF file.
            user_input: Optional user input to send along with the default prompt.

        Returns:
            Tuple of (success: bool, result_message_for_user: str, agent_result: Optional[dict])
        """
        if not self.client:
            return False, get_error_message("no_api_key"), None

        # Initialize variables
        pdf_analysis_text = ""
        cad_agent_output_dict = None
        final_user_message = ""
        file_id = None

        try:
            logger.info(f"Processing PDF: {file_path} for session {session_id}")

            # Ensure session exists in database
            self._ensure_session_exists(db, session_id, file_path)

            # Validate file
            is_valid, validation_error = self._validate_file(file_path)
            if not is_valid:
                return False, validation_error, None

            # Prepare prompt
            prompt = self._prepare_prompt(user_input)
            logger.info(f"Using prompt: {prompt}")

            # Upload file to OpenAI
            upload_success, file_id, upload_message = await self._upload_file_to_openai(file_path)  # Added await
            if not upload_success:
                return False, upload_message, None

            # Analyze PDF content
            analysis_success, pdf_analysis_text = await self._analyze_pdf_content(file_id, prompt)  # Added await
            if not analysis_success:
                if add_chat_history_entry and db and session_id:
                    add_chat_history_entry(db, session_id, f"PDF analysis attempt for {file_path}", 
                                         {"error": pdf_analysis_text, "message": pdf_analysis_text})
                return False, pdf_analysis_text, None

            # Prepare messages for database and CAD generation
            if user_input and user_input.strip():
                user_message_for_db = f"Based on PDF analysis: {pdf_analysis_text}. User request: {user_input}"
                cad_input_text = user_message_for_db
            else:
                user_message_for_db = f"PDF analysis: {pdf_analysis_text}"
                cad_input_text = pdf_analysis_text

            # Generate CAD instructions if applicable
            if not self._should_skip_cad_generation(pdf_analysis_text):
                cad_agent_output_dict = await self._generate_cad_instructions(session_id, cad_input_text)
                
                if cad_agent_output_dict and cad_agent_output_dict.get("code"):
                    final_user_message = f"PDF Analysis: {pdf_analysis_text}\n\nCAD Generation: {cad_agent_output_dict.get('message', 'Code generated successfully.')}"
                    
                    # Trigger JSON viewer integration after successful CAD generation
                    self._trigger_step_viewer_integration(cad_agent_output_dict)
                else:
                    final_user_message = f"PDF Analysis: {pdf_analysis_text}"
                    if cad_agent_output_dict and cad_agent_output_dict.get("message"):
                        final_user_message += f"\n\nCAD Generation: {cad_agent_output_dict['message']}"
            else:
                final_user_message = pdf_analysis_text
                cad_agent_output_dict = {"message": pdf_analysis_text}

            # Save to chat history
            self._save_to_chat_history(db, session_id, user_message_for_db, cad_agent_output_dict)

            return True, final_user_message, cad_agent_output_dict

        except Exception as e:
            logger.exception(f"Error processing PDF for session {session_id}: {e}")
            error_msg = get_error_message("processing_failed", error=str(e))
            if add_chat_history_entry and db and session_id:
                add_chat_history_entry(db, session_id, f"PDF processing attempt for {file_path}",
                                     {"error": str(e), "message": error_msg})
            return False, error_msg, None
        finally:
            # Clean up uploaded file
            await self._cleanup_openai_file(file_id)  # Added await

    async def process_uploaded_file(self, db: Session, uploaded_file, user_input: str = "", 
                                  session_id: Optional[str] = None) -> Tuple[bool, str, Optional[str], Optional[dict]]:
        """
        Process an uploaded PDF file from FastAPI's UploadFile and save to chat history.

        Args:
            db: SQLAlchemy database session.
            uploaded_file: The UploadFile object from FastAPI.
            user_input: Optional user input to send along with the default prompt.
            session_id: Optional session ID. If not provided, will generate a new one.

        Returns:
            Tuple of (success: bool, result_message_for_user: str, session_id: Optional[str],
                      agent_result: Optional[dict])
        """
        # Use provided session_id or generate a new one
        if not session_id:
            session_id = self._generate_session_id()
            logger.info(f"Generated new session ID {session_id} for uploaded file {uploaded_file.filename}")
        else:
            logger.info(f"Using provided session ID {session_id} for uploaded file {uploaded_file.filename}")

        if not self.client:
            return False, get_error_message("no_api_key"), session_id, None

        temp_file_path_obj = None
        try:
            temp_dir = Path(get_temp_dir())
            filename = uploaded_file.filename.replace(" ", "_").replace("/", "_")
            temp_file_path_obj = temp_dir / filename

            content = await uploaded_file.read()
            with open(temp_file_path_obj, "wb") as buffer:
                buffer.write(content)

            logger.info(f"Saved uploaded file to: {temp_file_path_obj}")

            success, result, agent_result = await self.process_pdf(db, session_id, str(temp_file_path_obj), user_input)
            return success, result, session_id, agent_result

        except Exception as e:
            logger.exception(f"Error processing uploaded file: {e}")
            return False, get_error_message("processing_failed", error=str(e)), session_id, None
        finally:
            if temp_file_path_obj and temp_file_path_obj.exists():
                try:
                    os.unlink(temp_file_path_obj)
                    logger.info(f"Deleted temporary file: {temp_file_path_obj}")
                except Exception as e:
                    logger.warning(f"Failed to delete temporary uploaded file: {e}")

# Usage example:
# processor = PDFProcessor()
# success, response = processor.process_pdf("path/to/your.pdf", "Analyze this document")
# if success:
#     print(f"Response: {response}")
# else:
#     print(f"Error: {response}")
