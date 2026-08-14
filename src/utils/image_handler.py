# -*- coding: utf-8 -*-
"""
Image Handler Module

This module provides functionality to process image files using OpenAI Vision API.
Supports various image formats and integrates with the CAD generation system.
"""

import os
import base64
import tempfile
import logging
from pathlib import Path
from typing import Tuple, Optional, Any
from dotenv import load_dotenv
from openai import AsyncOpenAI  # Changed from OpenAI to AsyncOpenAI for non-blocking I/O
from sqlalchemy.orm import Session # Added import
import uuid # Added for session_id generation
import random # Added for session_id generation

logger = logging.getLogger(__name__)

try:
    from src.core.text_to_cad_agent import TextToCADAgent
    from src.crud.sessions import add_chat_history_entry # create_session and SessionModel removed from here
    from src.schemas.sessions import ChatRequest as ApiChatRequest # For chat_request_obj
    # SessionModel removed from here
except ImportError:
    logger.warning("TextToCADAgent, add_chat_history_entry, or ApiChatRequest not available for import in image_handler.py") # Adjusted warning
    TextToCADAgent = None
    add_chat_history_entry = None
    # create_session and SessionModel no longer set to None here as they are imported locally
    ApiChatRequest = None

class ImageProcessor:
    """
    Handles processing of image files using OpenAI's Vision API.
    
    This class provides functionality to:
    1. Process various image formats (JPG, JPEG, PNG, GIF, BMP, TIFF, WEBP)
    2. Convert images to base64 for API transmission
    3. Analyze images with optional custom prompts
    4. Integrate with CAD generation system
    """
    
    SUPPORTED_FORMATS = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.webp'}
    MAX_FILE_SIZE = 20 * 1024 * 1024  # 20MB
    
    def __init__(self, api_key: str = None, cad_agent: Optional[Any] = None, cost_tracker=None):
        """
        Initialize the image processor with optional API key and CAD agent.
        
        Args:
            api_key: Optional OpenAI API key. If not provided, will look for 
                    OPENAI_API_KEY environment variable.
            cad_agent: Optional instance of TextToCADAgent for CAD generation.
            cost_tracker: Optional CostTracker instance to track OpenAI costs
        """
        load_dotenv()
        
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.cost_tracker = cost_tracker

        if self.api_key:
            try:
                self.client = AsyncOpenAI(api_key=self.api_key)  # Changed to AsyncOpenAI for non-blocking I/O
                logger.info("AsyncOpenAI client initialized successfully for image processing")
            except Exception as e:
                logger.error(f"Failed to initialize AsyncOpenAI client: {e}")
                self.client = None
        else:
            logger.warning("No OpenAI API key provided for image processing")
            self.client = None

        self.cad_agent = cad_agent
        if self.cad_agent and TextToCADAgent and isinstance(self.cad_agent, TextToCADAgent):
            logger.info("TextToCADAgent instance provided to ImageProcessor.")
        elif cad_agent:
            logger.warning("A cad_agent was provided, but TextToCADAgent class was not imported or type mismatch.")
        else:
            logger.info("TextToCADAgent instance not provided to ImageProcessor. Will only perform image analysis.")

    def _generate_session_id(self) -> str:
        """Generates a unique session ID."""
        rand_digits = random.randint(100000, 999999)
        rand_uuid_hex = uuid.uuid4().hex[:6]
        return f"session_{rand_uuid_hex}_{rand_digits}"

    def _validate_image_file(self, file_path: str) -> Tuple[bool, str]:
        """
        Validate image file format and size.
        
        Args:
            file_path: Path to the image file
            
        Returns:
            Tuple of (is_valid: bool, error_message: str)
        """
        if not os.path.exists(file_path):
            return False, f"File not found: {file_path}"
        
        file_path_obj = Path(file_path)
        file_extension = file_path_obj.suffix.lower()
        
        if file_extension not in self.SUPPORTED_FORMATS:
            return False, f"Unsupported image format: {file_extension}. Supported formats: {', '.join(self.SUPPORTED_FORMATS)}"
        
        file_size = os.path.getsize(file_path)
        if file_size > self.MAX_FILE_SIZE:
            return False, f"File size ({file_size} bytes) exceeds maximum allowed size ({self.MAX_FILE_SIZE} bytes)"
        
        return True, ""
    
    def _encode_image_to_base64(self, file_path: str) -> str:
        """
        Encode image file to base64 string.
        
        Args:
            file_path: Path to the image file
            
        Returns:
            Base64 encoded string of the image
        """
        with open(file_path, "rb") as image_file:
            return base64.b64encode(image_file.read()).decode('utf-8')
    
    async def process_image(self, db: Session, session_id: str, file_path: str, user_input: str = "") -> Tuple[bool, str, Optional[dict]]:
        """
        Process an image file using OpenAI's Vision API, optionally generate CAD, and save to chat history.
        
        Args:
            db: SQLAlchemy database session.
            session_id: The session ID for this interaction.
            file_path: Path to the image file.
            user_input: Optional user input to send along with the default prompt.
            
        Returns:
            Tuple of (success: bool, result_message_for_user: str, agent_result: Optional[dict])
            If successful, result contains the analysis text or CAD generation result.
            If unsuccessful, result contains an error message.
        """
        if not self.client:
            return False, "OpenAI client not initialized. Check API key.", None
        
        try:
            logger.info(f"Processing image: {file_path}")
            
            # Validate image file
            is_valid, error_message = self._validate_image_file(file_path)
            if not is_valid:
                return False, error_message, None

            # Ensure session exists in the database - import create_session and SessionModel locally
            try:
                from src.crud.sessions import create_session
                from src.models.sessions import Session as SessionModel
                if db and session_id: # create_session and SessionModel are now imported locally
                    try:
                        # Use filename as session name, limit length
                        session_name = Path(file_path).name[:50]
                        existing_session = db.query(SessionModel).filter(SessionModel.session_id == session_id).first()
                        if not existing_session:
                            create_session(db, session_id, session_name)
                            logger.info(f"Created session {session_id} ('{session_name}') in database from image_handler.")
                    except Exception as e_session:
                        logger.error(f"Failed to ensure session {session_id} exists in database: {e_session}")
                        # Optionally, decide if this is a critical failure, for now, we'll log and continue
            except ImportError as e_import_local:
                logger.error(f"Failed to import create_session or SessionModel locally in process_image: {e_import_local}")
                # This is a critical failure for session creation, might need to return error or handle differently
            
            # Encode image to base64
            base64_image = self._encode_image_to_base64(file_path)
            
            # Prepare prompt (shared with pdf_handler — see utils/media_prompts.py)
            from src.utils.media_prompts import build_media_classification_prompt
            prompt = build_media_classification_prompt("image", user_input)

            logger.info(f"Using prompt for image analysis: {prompt}")

            image_analysis_text = ""
            cad_agent_output_dict = None
            final_user_message = ""
            
            # Create chat completion with the image
            try:
                completion = await self.client.chat.completions.create(  # Added await for async operation
                    model="gpt-5-mini",  # Changed from gpt-4.1 for cost optimization
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": f"data:image/jpeg;base64,{base64_image}"
                                    }
                                },
                                {
                                    "type": "text",
                                    "text": prompt
                                }
                            ]
                        }
                    ],
                    max_completion_tokens=500
                )
                
                # Track cost if cost_tracker is available
                if self.cost_tracker:
                    try:
                        usage = completion.usage
                        self.cost_tracker.add_direct_api_cost(
                            chain_name="image_processing",
                            prompt_tokens=usage.prompt_tokens,
                            completion_tokens=usage.completion_tokens,
                            model_name="gpt-5-mini"
                        )
                    except Exception as e:
                        logger.warning(f"Failed to track image processing cost: {e}")
                
                image_analysis_text = completion.choices[0].message.content
                logger.info(f"Successfully processed image. Response length: {len(image_analysis_text)}")
                
                # Determine the appropriate message for CAD agent based on user input
                if user_input and user_input.strip():
                    # User provided text: use both analysis and user input
                    user_message_for_db = f"Based on image analysis: {image_analysis_text}. User request: {user_input}"
                    cad_input_text = user_message_for_db
                else:
                    # File-only: use just the analysis for DB, but pass analysis directly to CAD agent
                    user_message_for_db = f"Image analysis: {image_analysis_text}"
                    cad_input_text = image_analysis_text

                # Try to generate CAD if agent is available and analysis is valid
                # Skip only if analysis contains clear error indicators or is too short
                skip_cad_generation = (
                    not image_analysis_text or
                    len(image_analysis_text.strip()) < 10 or
                    "error" in image_analysis_text.lower() or
                    "cannot" in image_analysis_text.lower() or
                    "unable" in image_analysis_text.lower()
                )

                if self.cad_agent and image_analysis_text and not skip_cad_generation:
                    try:
                        logger.info(f"Attempting to generate CAD from image analysis result for session {session_id}")
                        cad_agent_output_dict = await self.cad_agent.process_request(
                            user_text=cad_input_text, # Pass the appropriate text based on scenario
                            is_edit_request=False,
                            request_origin='image_processing',
                            session_id=session_id # Pass session_id to agent
                        )
                        
                        if cad_agent_output_dict.get("code"):
                            logger.info(f"Successfully generated CAD code from image analysis for session {session_id}")
                            final_user_message = f"Image Analysis: {image_analysis_text}\n\nCAD Generation: {cad_agent_output_dict.get('message', 'Code generated successfully.')}"
                        else:
                            logger.info(f"CAD generation failed or returned questions for session {session_id}, returning image analysis only")
                            final_user_message = f"Image Analysis: {image_analysis_text}"
                            if cad_agent_output_dict.get("message"):
                                final_user_message += f"\n\nCAD Generation: {cad_agent_output_dict['message']}"
                            # If cad_agent_output_dict is None or no message, final_user_message remains just image_analysis_text + CAD gen message
                    except Exception as e:
                        logger.warning(f"CAD generation failed for image analysis in session {session_id}: {e}")
                        final_user_message = f"Image Analysis: {image_analysis_text}\n\nNote: CAD generation encountered an issue: {str(e)}"
                        # Construct a simple agent_result for db logging
                        cad_agent_output_dict = {"error": f"CAD generation encountered an issue: {str(e)}", "message": final_user_message}
                else: # No CAD agent or analysis not suitable for CAD
                    final_user_message = image_analysis_text
                    # Construct a simple agent_result for db logging if CAD was not attempted
                    cad_agent_output_dict = {"message": image_analysis_text} # No error, just the analysis
                
                # Save to chat history
                if add_chat_history_entry and db and session_id:
                    try:
                        # Create a minimal ChatRequest-like object for add_chat_history_entry
                        chat_request_details = ApiChatRequest(
                            message=user_message_for_db, # This is what the agent processed
                            image_path=file_path, # Store the path of the processed image
                            session_id=session_id
                            # Other fields will use defaults from ApiChatRequest or be None
                        ) if ApiChatRequest else None

                        add_chat_history_entry(
                            db=db,
                            session_id=session_id,
                            user_message=user_message_for_db, # The input to the agent/analysis
                            agent_result=cad_agent_output_dict, # Result from CAD agent or constructed
                            chat_request_obj=chat_request_details
                        )
                        logger.info(f"Saved image analysis/CAD attempt to chat history for session {session_id}")
                    except Exception as db_e:
                        logger.error(f"Failed to save image analysis to chat history for session {session_id}: {db_e}")
                        # Continue execution even if database save fails

                return True, final_user_message, cad_agent_output_dict
                
            except Exception as e:
                logger.error(f"Error during image analysis for session {session_id}: {e}")
                # Try to save error to chat history, but don't fail if this fails too
                if add_chat_history_entry and db and session_id:
                    try:
                        add_chat_history_entry(db, session_id, f"Image analysis attempt for {file_path}", {"error": str(e), "message": f"Error processing image content: {str(e)}"})
                    except Exception as db_e:
                        logger.error(f"Failed to save error to chat history for session {session_id}: {db_e}")
                return False, f"Error processing image content: {str(e)}", None
            
        except Exception as e:
            logger.exception(f"Error processing image for session {session_id}: {e}")
            # Try to save error to chat history, but don't fail if this fails too
            if add_chat_history_entry and db and session_id:
                try:
                    add_chat_history_entry(db, session_id, f"Image processing attempt for {file_path}", {"error": str(e), "message": f"Error processing image: {str(e)}"})
                except Exception as db_e:
                    logger.error(f"Failed to save error to chat history for session {session_id}: {db_e}")
            return False, f"Error processing image: {str(e)}", None
    
    async def process_uploaded_file(self, db: Session, uploaded_file, user_input: str = "", session_id: Optional[str] = None) -> Tuple[bool, str, Optional[str], Optional[dict]]:
        """
        Process an uploaded image file from FastAPI's UploadFile and save to chat history.
        
        Args:
            db: SQLAlchemy database session.
            uploaded_file: The UploadFile object from FastAPI.
            user_input: Optional user input to send along with the default prompt.
            session_id: Optional session ID. If not provided, will generate a new one.
            
        Returns:
            Tuple of (success: bool, result_message_for_user: str, session_id: Optional[str], agent_result: Optional[dict])
        """
        # Use provided session_id or generate a new one
        if not session_id:
            session_id = self._generate_session_id()
            logger.info(f"Generated new session ID {session_id} for uploaded file {uploaded_file.filename}")
        else:
            logger.info(f"Using provided session ID {session_id} for uploaded file {uploaded_file.filename}")

        if not self.client:
            return False, "OpenAI client not initialized. Check API key.", session_id, None

        try:
            # Create a temporary file to store the uploaded content
            temp_dir = Path("temp_uploads")
            temp_dir.mkdir(exist_ok=True)
            
            # Create a secure filename
            filename = uploaded_file.filename.replace(" ", "_").replace("/", "_")
            temp_file_path = temp_dir / filename
            
            # Save the uploaded file to disk
            content = await uploaded_file.read()  # Added await for async file read
            
            # Validate file size
            if len(content) > self.MAX_FILE_SIZE:
                return False, f"File size ({len(content)} bytes) exceeds maximum allowed size ({self.MAX_FILE_SIZE} bytes)", session_id, None
            
            with open(temp_file_path, "wb") as buffer:
                buffer.write(content)
            
            logger.info(f"Saved uploaded image file to: {temp_file_path}")
            
            # Process the file, passing db and session_id
            success, result, agent_result = await self.process_image(db, session_id, str(temp_file_path), user_input)
            
            # Clean up
            try:
                os.unlink(temp_file_path)
                logger.info(f"Deleted temporary image file: {temp_file_path}")
            except Exception as e:
                logger.warning(f"Failed to delete temporary image file: {e}")
                
            # Add step-viewer integration if CAD generation was successful and all required files are created
            if agent_result and agent_result.get("code"):
                try:
                    # Check for all required files (PDF output removed from FreeCAD server)
                    step_path = agent_result.get("step_file_path") or agent_result.get("step_path")
                    obj_path = agent_result.get("obj_file_path") or agent_result.get("obj_path")

                    if step_path and obj_path:
                        logger.info(f"All required files created successfully for session {session_id}:")
                        logger.info(f"  - STEP: {step_path}")
                        logger.info(f"  - OBJ: {obj_path}")

                        # Prepare STEP viewer URL for frontend integration
                        # Convert absolute path to relative path for API endpoint
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

                        logger.info(f"STEP Viewer integration ready for session {session_id}: {step_viewer_url}")
                    else:
                        logger.info(f"Not all required files were created for session {session_id}, skipping STEP viewer integration")
                        missing_files = []
                        if not step_path: missing_files.append("STEP")
                        if not obj_path: missing_files.append("OBJ")
                        logger.info(f"Missing files: {', '.join(missing_files)}")
                        agent_result["step_viewer_ready"] = False

                except Exception as e:
                    logger.error(f"Error in STEP viewer integration for session {session_id}: {e}")
                    # Don't fail the entire process if STEP viewer integration fails
                    if agent_result:
                        agent_result["step_viewer_ready"] = False
            else:
                if agent_result:
                    agent_result["step_viewer_ready"] = False

            return success, result, session_id, agent_result
            
        except Exception as e:
            logger.exception(f"Error processing uploaded image file: {e}")
            return False, f"Error processing uploaded image file: {str(e)}", session_id, None
