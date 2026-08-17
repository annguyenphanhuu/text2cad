import logging
import os
from dotenv import load_dotenv

# LangChain imports
from langchain_openai import ChatOpenAI

# Load environment variables from config directory
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), '.env'))

from src.core.text_to_cad_agent import TextToCADAgent

logger = logging.getLogger(__name__)

# Initialize LLMs
# Define model choices (should match those in text_to_cad_agent)

# o3-2025-04-16
# gpt-5.4-2026-03-05
# o4-mini-2025-04-16
MODELS = {
    "default": "gpt-5.1-2025-11-13",  # Default model for general tasks
    "advanced": "gpt-5.4-2026-03-05", # Advanced model for complex tasks (OpenAI - upgraded)
    "expert": "gpt-5.4-2026-03-05",   # Expert model for specialized tasks
    "confirm": "gpt-5.4-2026-03-05",  # Model for description_confirm (high accuracy)
}

# Get API keys from environment variables
openai_api_key = os.getenv("OPENAI_API_KEY")

# Initialize models
default_llm = None
advanced_llm = None
expert_llm = None
confirm_llm = None   # gpt-5.4 for description_confirm (high accuracy)

try:
    # Check if API key is available
    if not openai_api_key:
        raise ValueError("OPENAI_API_KEY environment variable is not set. Please check your .env file.")

    # Initialize models with different reasoning efforts for different tasks
    default_llm = ChatOpenAI(
        model=MODELS["default"],
        temperature=0,  
        api_key=openai_api_key
    )

    advanced_llm = ChatOpenAI(
        model=MODELS["advanced"],
        temperature=0,  
        api_key=openai_api_key
    )

    # temperature=0 for the same reason as every other LLM here. expert_llm was
    # the only one left unpinned, and it drives unified_processing,
    # dfm_validation and code_editing — so the pipeline's main classifier ran at
    # the client default. Template I/O logs show the effect: the same user_text
    # returned shape_type "Sheet" on some runs and "unknown"/"L-bracket" on
    # others, which changes the confirm template and the generated geometry.
    expert_llm = ChatOpenAI(
        model=MODELS["expert"],
        temperature=0,
        api_key=openai_api_key
    )

    confirm_llm = ChatOpenAI(
        model=MODELS["confirm"],
        temperature=0,
        api_key=openai_api_key
    )

    # The per-role model ids are reported once by TextToCADAgent's init line.
    logger.debug("Language models initialized")

except ValueError as ve:
    logger.error(f"Configuration error: {ve} — set the required API keys in your .env file.")

except Exception as e:
    logger.error(
        f"Could not initialize the LLM clients: {e} — check your API key and "
        f"network connection.",
        exc_info=True,
    )

# --- New RAG System Setup ---
# RAG is now initialized lazily via RAG Singleton pattern (src/core/rag_singleton.py)
# It will auto-initialize on first use via get_rag_split_context()
# No need for explicit initialization here to avoid duplicate initialization


logger.debug("RAG system will initialize on first use (lazy singleton)")

# Initialize the TextToCADAgent
# Pass the initialized LLMs and the new RAG's retrieve_context function
text_to_cad_agent = TextToCADAgent(
    default_llm=default_llm,
    advanced_llm=advanced_llm,
    expert_llm=expert_llm,
    confirm_llm=confirm_llm,    # gpt-5.4 for description_confirm
)
