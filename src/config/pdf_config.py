"""
PDF Processing Configuration Module

This module contains all configuration constants and settings for PDF processing,
including model names, prompts, file paths, and processing parameters.
"""

import os
from typing import Dict, Any

# OpenAI Model Configuration
OPENAI_MODEL = "gpt-5-mini"  # Changed from gpt-4o-mini for cost optimization
OPENAI_MAX_TOKENS = 4000
OPENAI_TEMPERATURE = 0.7

# CAD Generation Configuration
CAD_MODEL = "gpt-4o-mini"
CAD_MAX_TOKENS = 4000
CAD_TEMPERATURE = 0.7

# File Processing Configuration
TEMP_DIR = "temp"
MAX_FILE_SIZE_MB = 50
ALLOWED_EXTENSIONS = [".pdf"]

# Database Configuration
MAX_RETRIES = 3
RETRY_DELAY = 1.0

# Prompt Templates
PDF_ANALYSIS_PROMPT = """
Analyze the following PDF content and provide a comprehensive summary:

Content: {content}

Please provide:
1. Main topics and themes
2. Key technical specifications or requirements
3. Important details that would be relevant for CAD design
4. Any manufacturing or design constraints mentioned

Format your response in a clear, structured manner.
"""

CAD_GENERATION_PROMPT = """
Based on the following PDF analysis, generate detailed CAD instructions:

Analysis: {analysis}

Please provide:
1. Detailed geometric specifications
2. Material requirements
3. Manufacturing considerations
4. Assembly instructions if applicable
5. Quality control parameters

Format as clear, actionable CAD instructions.
"""

# Error Messages
ERROR_MESSAGES = {
    "file_too_large": "File size exceeds maximum limit of {max_size}MB",
    "invalid_extension": "Invalid file extension. Allowed: {extensions}",
    "processing_failed": "PDF processing failed: {error}",
    "cad_generation_failed": "CAD generation failed: {error}",
    "database_error": "Database operation failed: {error}",
    "openai_error": "OpenAI API error: {error}",
    "file_not_found": "File not found: {filename}",
    "temp_dir_error": "Failed to create temporary directory: {path}"
}

# Success Messages
SUCCESS_MESSAGES = {
    "pdf_processed": "PDF processed successfully",
    "cad_generated": "CAD instructions generated successfully",
    "database_updated": "Database updated successfully",
    "file_uploaded": "File uploaded successfully"
}

# Logging Configuration
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
LOG_LEVEL = "INFO"

def get_temp_dir() -> str:
    """Get the temporary directory path, creating it if it doesn't exist."""
    temp_path = os.path.join(os.getcwd(), TEMP_DIR)
    os.makedirs(temp_path, exist_ok=True)
    return temp_path

def validate_file_size(file_size: int) -> bool:
    """Validate if file size is within allowed limits."""
    max_size_bytes = MAX_FILE_SIZE_MB * 1024 * 1024
    return file_size <= max_size_bytes

def validate_file_extension(filename: str) -> bool:
    """Validate if file extension is allowed."""
    return any(filename.lower().endswith(ext) for ext in ALLOWED_EXTENSIONS)

def get_error_message(error_type: str, **kwargs) -> str:
    """Get formatted error message."""
    if error_type in ERROR_MESSAGES:
        return ERROR_MESSAGES[error_type].format(**kwargs)
    return f"Unknown error: {error_type}"

def get_success_message(message_type: str) -> str:
    """Get success message."""
    return SUCCESS_MESSAGES.get(message_type, f"Operation completed: {message_type}")

