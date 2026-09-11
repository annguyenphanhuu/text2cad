import json
import re
import logging
import os
from datetime import datetime
from langchain_core.runnables import RunnableLambda
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

logger = logging.getLogger(__name__)

# Template logging paths
LOGS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "logs")

# Ensure logs directory exists
os.makedirs(LOGS_DIR, exist_ok=True)

def log_template_io(log_file, session_id, template_name, inputs, output, error=None):
    """Log template inputs and outputs to dedicated log file"""
    try:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        separator = "=" * 100
        
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(f"\n{separator}\n")
            f.write(f"[{timestamp}] Template: {template_name} | Session: {session_id}\n")
            f.write(f"{separator}\n\n")
            
            # Log inputs - FULL CONTENT, NO TRUNCATION
            f.write("📥 INPUTS:\n")
            f.write("-" * 80 + "\n")
            for key, value in inputs.items():
                f.write(f"{key}:\n{str(value)}\n\n")
            
            # Log output or error - FULL CONTENT, NO TRUNCATION
            if error:
                f.write("❌ ERROR:\n")
                f.write("-" * 80 + "\n")
                f.write(f"{error}\n\n")
            else:
                f.write("📤 OUTPUT:\n")
                f.write("-" * 80 + "\n")
                f.write(f"{str(output)}\n\n")
            
            f.write(f"{separator}\n\n")
    except Exception as e:
        logger.error(f"Failed to log template I/O: {e}")



from .templates import greeting_classification_template


def parse_greeting_classification(raw_output):
    """Parse greeting classification output into a dict, with a safe fallback."""
    try:
        # Extract JSON from the output
        json_match = re.search(r'```json\s*(\{.*?\})\s*```', raw_output, re.DOTALL)
        if json_match:
            json_str = json_match.group(1)
            result = json.loads(json_str)
            return result
        else:
            # Try to parse the entire output as JSON
            result = json.loads(raw_output.strip())
            return result
    except Exception as e:
        logger.error(f"[ERROR] Failed to parse greeting classification: {e}")
        # Default to CAD request if parsing fails
        return {
            "classification": "cad_request",
            "confidence": 0.5,
            "response": ""
        }


# Removed enhanced_greeting_classification function - now using pure AI-based classification





def create_greeting_classification_chain(default_llm):
    """Create the greeting classification chain using pure AI-based classification"""

    # Built once at chain-creation time rather than per invocation.
    greeting_prompt = ChatPromptTemplate.from_template(greeting_classification_template)

    async def ai_only_classification(inputs):
        """
        Pure AI-based classification - no rule-based fallback.

        Must be `async` + `ainvoke`. As a sync function this ran inside
        LangChain's thread executor, which (a) broke the `get_openai_callback`
        contextvar so the call's tokens were logged as 0 and its cost went
        untracked, and (b) blocked a worker thread for the whole round-trip.
        """
        user_text = inputs.get("user_text", "")

        # Only log first 100 chars to avoid cluttering logs with web content
        user_text_preview = user_text[:100] + "..." if len(user_text) > 100 else user_text
        logger.debug(f"[AI_CLASSIFICATION] Using pure AI classification for: '{user_text_preview}'")

        ai_chain = (
            greeting_prompt
            | default_llm
            | StrOutputParser()
            | RunnableLambda(parse_greeting_classification)
        )

        try:
            ai_result = await ai_chain.ainvoke(inputs)
            logger.debug(f"[AI_CLASSIFICATION] Result: {ai_result}")
            return ai_result
        except Exception as e:
            logger.debug(f"[AI_CLASSIFICATION] Error: {e}, using fallback response")
            # Simple fallback if AI completely fails
            return {
                "classification": "cad_request",
                "confidence": 0.5,
                "response": ""
            }

    return RunnableLambda(ai_only_classification)


