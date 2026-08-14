import os
import json
import re
import csv
import functools
import logging
from typing import List, Dict, Any
from pathlib import Path
from langchain_core.documents import Document

from .models import AnalysisAndParameterCheckOutput, ShapeRequirement, DesignRequirements
from src.utils import path_manager

logger = logging.getLogger(__name__)

_FALLBACK_QUESTIONS = {
    "json_parse": "I encountered an error processing your request. Could you please rephrase your design requirements more clearly?",
    "general": "Something went wrong while processing your request. Please try describing your design in a different way.",
}


def _create_fallback_response(error_type: str = "json_parse") -> AnalysisAndParameterCheckOutput:
    """Create a fallback response when parsing fails."""

    question = _FALLBACK_QUESTIONS.get(error_type, _FALLBACK_QUESTIONS["json_parse"])

    description = "System Error - Invalid Response Format" if error_type == "json_parse" else "System Error - Processing Failed"

    return AnalysisAndParameterCheckOutput(
        description=description,
        complexity_level=1,
        missing_info=True,
        questions=[question],
    )


def detect_detailed_explanation_request(user_text: str) -> bool:
    """Detect if user explicitly requested detailed explanation.
    
    CRITICAL: Step-by-step requests ("show me the steps", "step by step", etc.)
    are NOT detailed explanation requests — they are CAD build plan requests.
    This function returns False for step-by-step intents to avoid conflicting
    with the unified chain's step_by_step_requested detection.
    """
    user_lower = user_text.lower()

    # ── Step 1: NEGATION GATE — step-by-step intent overrides everything ──
    # If user clearly wants to SKIP steps, also not a detailed explanation request.
    step_negation_keywords = [
        "skip the steps", "skip the build plan", "let's skip the steps",
    ]
    for kw in step_negation_keywords:
        if kw in user_lower:
            return False

    # ── Step 2: STEP-BY-STEP POSITIVE GATE — if step plan requested, not info ──
    step_positive_keywords = [
        "show me the steps", "give me the steps", "step by step", "step-by-step",
        "what are the steps", "walk me through the steps",
    ]
    for kw in step_positive_keywords:
        if kw in user_lower:
            return False  # Step plan intent, NOT a detailed info request

    # ── Step 3: Standard detailed explanation keywords ──
    detailed_keywords = [
        "why", "explain", "detail", "what does", "what is",
        "clarify", "elaborate", "more info", "tell me more", "be specific",
        "list", "available", "options", "choices",
        "what are", "which", "can you provide", "provide me", "tell me about",
    ]
    return any(keyword in user_lower for keyword in detailed_keywords)


def _clean_json_string(json_str: str) -> str:
    """Cleans a JSON string from an LLM to make it more parsable."""
    try:
        # Remove any leading/trailing whitespace
        json_str = json_str.strip()

        # Remove markdown code blocks if present
        if json_str.startswith('```') and json_str.endswith('```'):
            lines = json_str.split('\n')
            if len(lines) > 2:
                json_str = '\n'.join(lines[1:-1])

        # Fix single quotes to double quotes for keys and string values
        # More comprehensive regex to handle various cases
        json_str = re.sub(r"(\s*[\{\[,]\s*)'([^']*)'(\s*:)", r'\1"\2"\3', json_str)  # Keys
        json_str = re.sub(r"(\s*:\s*)'([^']*)'(\s*[,\}\]])", r'\1"\2"\3', json_str)  # String values after colons
        json_str = re.sub(r"(\[\s*)'([^']*)'", r'\1"\2"', json_str)  # String values at start of arrays
        json_str = re.sub(r"(\s*,\s*)'([^']*)'", r'\1"\2"', json_str)  # String values after commas in arrays

        # Fix boolean values that might be quoted
        json_str = re.sub(r'"(true|false)"', r'\1', json_str, flags=re.IGNORECASE)

        # Fix null values that might be quoted
        json_str = re.sub(r'"null"', r'null', json_str, flags=re.IGNORECASE)

        # Remove trailing commas from objects and arrays
        json_str = re.sub(r",\s*([\}\]])", r"\1", json_str)

        # Fix unescaped quotes in string values - this is the main fix for the error
        # Use a more sophisticated approach to handle quotes in JSON strings

        # Split by lines to process each line individually
        lines = json_str.split('\n')
        fixed_lines = []

        for line in lines:
            # Skip lines that don't contain string values
            if ':' not in line or line.strip() in ['{', '}', '[', ']']:
                fixed_lines.append(line)
                continue

            # Find the colon position
            colon_pos = line.find(':')
            if colon_pos == -1:
                fixed_lines.append(line)
                continue

            key_part = line[:colon_pos + 1]
            value_part = line[colon_pos + 1:].strip()

            # Check if this is a string value that needs fixing
            if value_part.startswith('"') and value_part.rstrip(',').endswith('"'):
                # Extract the string content
                has_comma = value_part.endswith(',')
                if has_comma:
                    string_content = value_part[1:-2]  # Remove quotes and comma
                else:
                    string_content = value_part[1:-1]  # Remove quotes

                # Escape any unescaped quotes in the content
                # First, preserve already escaped quotes
                string_content = string_content.replace('\\"', '___TEMP_ESCAPED___')
                # Then escape unescaped quotes
                string_content = string_content.replace('"', '\\"')
                # Restore the preserved escaped quotes
                string_content = string_content.replace('___TEMP_ESCAPED___', '\\"')

                # Reconstruct the line
                comma_suffix = ',' if has_comma else ''
                fixed_line = f'{key_part} "{string_content}"{comma_suffix}'
                fixed_lines.append(fixed_line)
            else:
                fixed_lines.append(line)

        json_str = '\n'.join(fixed_lines)

        # Fix common escape sequence issues (but preserve our new escaping)
        json_str = json_str.replace("\\'", "'")

        # Remove any non-JSON content before the first { or [
        first_brace = json_str.find('{')
        first_bracket = json_str.find('[')
        if first_brace != -1 and (first_bracket == -1 or first_brace < first_bracket):
            json_str = json_str[first_brace:]
        elif first_bracket != -1:
            json_str = json_str[first_bracket:]

        # Remove any non-JSON content after the last } or ]
        last_brace = json_str.rfind('}')
        last_bracket = json_str.rfind(']')
        if last_brace != -1 and (last_bracket == -1 or last_brace > last_bracket):
            json_str = json_str[:last_brace + 1]
        elif last_bracket != -1:
            json_str = json_str[:last_bracket + 1]

    except Exception as e:
        logger.warning(f"Error in JSON cleaning: {e}")
        # Return original string if cleaning fails
        pass

    return json_str

def parse_unified_analysis(json_str) -> AnalysisAndParameterCheckOutput:
    """Convert JSON string or dict to AnalysisAndParameterCheckOutput Pydantic model"""
    original_json_str = json_str  # Keep original for logging

    try:
        # Handle case where input is already a dict
        if isinstance(json_str, dict):
            logger.debug(f"Input is already a dict, using directly")
            parsed_data = json_str
        else:
            # Log the raw response for debugging
            logger.debug(f"Raw LLM response (first 500 chars): {json_str[:500]}")

            # Extract JSON from markdown if needed
            if "```json" in json_str:
                match = re.search(r'```json\s*(.*?)\s*```', json_str, re.DOTALL)
                if match:
                    json_str = match.group(1)
                    logger.debug("Extracted JSON from markdown code block")
                else:
                    logger.warning("Found ```json marker but could not extract JSON content")

            # Validate and fix JSON before parsing
            validated_json = validate_and_fix_json_response(json_str)
            logger.debug(f"Validated JSON (first 500 chars): {validated_json[:500]}")

            # Try to parse the JSON
            parsed_data = json.loads(validated_json)
            logger.debug("Successfully parsed JSON")

        # Sanitize the data
        # parsed_data = _sanitize_shape_data(parsed_data)
        # logger.debug("Successfully sanitized shape data")

        # Create and return the Pydantic model
        result = AnalysisAndParameterCheckOutput(**parsed_data)
        logger.debug("Successfully created AnalysisAndParameterCheckOutput model")
        return result

    except json.JSONDecodeError as e:
        logger.error(f"JSON parsing failed: {e}")
        logger.error(f"Raw response: {original_json_str}")
        logger.error(f"Validated JSON: {validated_json if 'validated_json' in locals() else 'N/A'}")

        # Try alternative parsing strategies
        try:
            # Strategy 1: Try to find and extract just the JSON object
            json_match = re.search(r'\{.*\}', original_json_str, re.DOTALL)
            if json_match:
                alt_json = _clean_json_string(json_match.group(0))
                data = json.loads(alt_json)
                # data = _sanitize_shape_data(data)
                logger.info("Successfully recovered using alternative JSON extraction")
                return AnalysisAndParameterCheckOutput(**data)
        except Exception as alt_e:
            logger.warning(f"Alternative parsing strategy failed: {alt_e}")

        # Strategy 2: Try more aggressive quote fixing
        try:
            # More aggressive approach to fix quotes
            fixed_json = _fix_json_quotes_aggressive(original_json_str)
            if fixed_json != original_json_str:
                data = json.loads(fixed_json)
                # data = _sanitize_shape_data(data)
                logger.info("Successfully recovered using aggressive quote fixing")
                return AnalysisAndParameterCheckOutput(**data)
        except Exception as alt_e2:
            logger.warning(f"Aggressive quote fixing strategy failed: {alt_e2}")

        return _create_fallback_response("json_parse")


def _fix_json_quotes_aggressive(json_str: str) -> str:
    """
    More aggressive approach to fix JSON quote issues
    """
    try:
        # Extract JSON content if wrapped in markdown
        if "```json" in json_str:
            match = re.search(r'```json\s*(.*?)\s*```', json_str, re.DOTALL)
            if match:
                json_str = match.group(1)

        # Find the main JSON object
        json_match = re.search(r'\{.*\}', json_str, re.DOTALL)
        if not json_match:
            return json_str

        json_content = json_match.group(0)

        # Split into lines for line-by-line processing
        lines = json_content.split('\n')
        fixed_lines = []

        for line in lines:
            # Skip empty lines and structural lines
            stripped = line.strip()
            if not stripped or stripped in ['{', '}', '[', ']'] or stripped.endswith(','):
                if stripped.endswith(',') and not stripped.endswith('",'):
                    # Check if this is a string value that needs quote fixing
                    if ':' in stripped and not stripped.strip().endswith('",'):
                        # This might be a string value without proper quotes
                        colon_pos = stripped.find(':')
                        key_part = stripped[:colon_pos + 1]
                        value_part = stripped[colon_pos + 1:].strip()

                        if value_part.endswith(','):
                            value_part = value_part[:-1]
                            comma = ','
                        else:
                            comma = ''

                        # If value is not properly quoted, fix it
                        if not (value_part.startswith('"') and value_part.endswith('"')):
                            if not value_part.lower() in ['true', 'false', 'null'] and not value_part.replace('.', '').replace('-', '').isdigit():
                                # Escape internal quotes and wrap in quotes
                                escaped_value = value_part.replace('"', '\\"')
                                value_part = f'"{escaped_value}"'

                        line = f"{key_part} {value_part}{comma}"

                fixed_lines.append(line)
            else:
                fixed_lines.append(line)

        return '\n'.join(fixed_lines)

    except Exception as e:
        logger.warning(f"Error in aggressive quote fixing: {e}")
        return json_str



def validate_and_fix_json_response(response_text: str) -> str:
    """
    Validate and fix common JSON issues in AI responses
    """
    try:
        # Test if it's already valid JSON
        json.loads(response_text)
        return response_text
    except json.JSONDecodeError as e:
        logger.info(f"JSON validation failed, attempting to fix: {e}")

        # Apply cleaning
        cleaned = _clean_json_string(response_text)

        try:
            # Test cleaned version
            json.loads(cleaned)
            logger.info("Successfully fixed JSON using standard cleaning")
            return cleaned
        except json.JSONDecodeError:
            # Try aggressive fixing
            aggressive_fixed = _fix_json_quotes_aggressive(response_text)
            try:
                json.loads(aggressive_fixed)
                logger.info("Successfully fixed JSON using aggressive method")
                return aggressive_fixed
            except json.JSONDecodeError as final_e:
                logger.error(f"Could not fix JSON after all attempts: {final_e}")
                raise final_e


def clean_code(code_str: str) -> str:
    """
    Clean up the code string by removing markdown code blocks.

    Handles multiple cases:
    1. Code wrapped in ```python ... ```
    2. Code with unclosed markdown blocks
    3. Plain code without markdown
    4. Multiple code blocks (extracts first one)

    Args:
        code_str: Raw code string from LLM

    Returns:
        Cleaned code string
    """
    if not code_str:
        logger.warning("[CLEAN_CODE] Received empty code string")
        return ""

    original_length = len(code_str)
    logger.debug(f"[CLEAN_CODE] Processing code string of length {original_length}")

    # Try to extract code from markdown blocks
    if "```python" in code_str:
        logger.debug("[CLEAN_CODE] Found ```python marker")
        # Try to match complete markdown block
        match = re.search(r'```python\s*(.*?)\s*```', code_str, re.DOTALL)
        if match:
            # Successfully found complete block
            code_str = match.group(1)
            logger.info(f"[CLEAN_CODE] Extracted code from complete markdown block (length: {len(code_str)})")
        else:
            # Markdown block not closed properly - extract everything after ```python
            logger.warning("[CLEAN_CODE] Markdown block not closed properly, attempting recovery")
            match = re.search(r'```python\s*(.*)', code_str, re.DOTALL)
            if match:
                code_str = match.group(1)
                # Try to remove trailing ``` if exists
                code_str = re.sub(r'\s*```\s*$', '', code_str)
                logger.info(f"[CLEAN_CODE] Recovered code from unclosed block (length: {len(code_str)})")
            else:
                logger.error("[CLEAN_CODE] Failed to extract code from ```python block")
    elif "```" in code_str:
        logger.debug("[CLEAN_CODE] Found generic ``` marker")
        # Generic code block without python specifier
        match = re.search(r'```\s*(.*?)\s*```', code_str, re.DOTALL)
        if match:
            code_str = match.group(1)
            logger.info(f"[CLEAN_CODE] Extracted code from generic markdown block (length: {len(code_str)})")
        else:
            # Unclosed generic block
            logger.warning("[CLEAN_CODE] Generic markdown block not closed properly, attempting recovery")
            match = re.search(r'```\s*(.*)', code_str, re.DOTALL)
            if match:
                code_str = match.group(1)
                code_str = re.sub(r'\s*```\s*$', '', code_str)
                logger.info(f"[CLEAN_CODE] Recovered code from unclosed generic block (length: {len(code_str)})")
            else:
                logger.error("[CLEAN_CODE] Failed to extract code from generic ``` block")
    else:
        logger.debug("[CLEAN_CODE] No markdown blocks found, treating as plain code")

    cleaned = code_str.strip()

    # CRITICAL SAFEGUARD: Detect if the "code" is actually just a user response (e.g., "6000")
    # This can happen if user responses leak into code generation
    if cleaned and len(cleaned) < 50 and not any(keyword in cleaned.lower() for keyword in ['import', 'def ', 'class ', 'freecad', 'part.', 'app.']):
        # This looks like a simple user response, not actual code
        logger.error(f"[CLEAN_CODE] ⚠️ DETECTED POTENTIAL USER RESPONSE LEAK: '{cleaned}'")
        logger.error(f"[CLEAN_CODE] This appears to be a user response, not Python code. Returning empty string.")
        return ""

    # Additional validation: Check if code contains essential FreeCAD imports
    if cleaned and len(cleaned) > 100:  # Only check if there's substantial content
        has_import = any(keyword in cleaned for keyword in ['import FreeCAD', 'import Part', 'import App', 'from FreeCadUtil'])
        if not has_import:
            logger.warning(f"[CLEAN_CODE] ⚠️ Generated code missing FreeCAD imports. This might indicate a problem.")

    logger.info(f"[CLEAN_CODE] Final cleaned code length: {len(cleaned)} (original: {original_length})")

    return cleaned


def format_retrieved_context(docs: List[Document]) -> str:
    """Formats the retrieved list of documents (from FAISS) into a single string.
    
    Uses script_content from metadata (full code) for generation if available,
    otherwise falls back to page_content for backward compatibility.
    """
    if not docs:
        return "No relevant context found."

    # DEBUG: Log document counts by type
    info_docs = [d for d in docs if isinstance(d, Document) and d.metadata.get('type') == 'info']
    example_docs = [d for d in docs if isinstance(d, Document) and d.metadata.get('type') != 'info']
    logger.info(f"[FORMAT_CONTEXT] Total docs: {len(docs)} | Examples: {len(example_docs)} | Info: {len(info_docs)}")

    context_str = ""
    for i, doc in enumerate(docs):
        if isinstance(doc, Document):
            # Priority: script_content (full code) > page_content (fallback for old index)
            script_content = doc.metadata.get('script_content')
            content = script_content if script_content else doc.page_content
            
            source = doc.metadata.get('source', 'Local Guide')
            shape_type = doc.metadata.get('shape_type')
            doc_type = doc.metadata.get('type', '')
            
            # Enhanced header with shape type for clarity
            header = f"--- Context Source {i+1} ({source})"
            if shape_type:
                header += f" | Shape: {shape_type}"
            if doc_type == 'info':
                header += f" | Type: INFO"
            header += " ---"
            
            context_str += f"{header}\n{content}\n\n"
            
            # DEBUG: Log each info document
            if doc_type == 'info':
                logger.info(f"[FORMAT_CONTEXT] ✅ INFO included: {source} | Content length: {len(content)}")
        else:
            print(f"Warning: Unexpected document type in format_retrieved_context: {type(doc)}")
            context_str += f"--- Context Source {i+1} (Unknown Source) ---\n{str(doc)}\n\n"

    return context_str.strip()
