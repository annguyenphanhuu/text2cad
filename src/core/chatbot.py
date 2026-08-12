import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# LangChain imports
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_anthropic import ChatAnthropic
from langchain_community.vectorstores import FAISS

# Load environment variables from config directory
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), '.env'))

from src.core.text_to_cad_agent import TextToCADAgent

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

# Claude model for code generation (temporarily replacing advanced_llm)
CLAUDE_MODEL = "claude-opus-4-6"  # or "claude-3-5-sonnet-20241022", "claude-3-opus-20240229"




# Get API keys from environment variables
openai_api_key = os.getenv("OPENAI_API_KEY")
deepseek_api_key = os.getenv("DEEPSEEK_API_KEY")
anthropic_api_key = os.getenv("ANTHROPIC_API_KEY")

# Initialize models
default_llm = None
advanced_llm = None  # OpenAI advanced (kept as backup)
claude_llm = None    # Claude model - used as advanced_llm for code generation
expert_llm = None
confirm_llm = None   # gpt-5.4 for description_confirm (high accuracy)

try:
    # Check if API key is available
    if not openai_api_key:
        raise ValueError("OPENAI_API_KEY environment variable is not set. Please check your .env file.")

    print("[INIT] Initializing language models...")

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

    # Initialize Claude LLM for code generation (temporarily replacing advanced_llm)
    if not anthropic_api_key:
        print("[WARNING] ANTHROPIC_API_KEY not set. Falling back to OpenAI advanced_llm for code generation.")
        claude_llm = advanced_llm
    else:
        print(f"[INIT] Initializing Claude model for code generation: {CLAUDE_MODEL}")
        claude_llm = ChatAnthropic(
            model=CLAUDE_MODEL,
            temperature=0,
            api_key=anthropic_api_key,
            max_tokens=8192,
        )
        print(f"[SUCCESS] Claude model initialized: {CLAUDE_MODEL}")

    # Test connection with a simple query
    print("[TEST] Testing connection to OpenAI API...")
    test_result = default_llm.invoke("Test connection")
    print("[SUCCESS] Successfully connected to OpenAI API")

except ValueError as ve:
    print(f"[ERROR] Configuration Error: {ve}")
    print("   Please set the required API keys in your .env file.")

except Exception as e:
    print(f"[ERROR] Error initializing or connecting to LLM: {e}")
    print("   Please check your API key and network connection.")

    # Log more detailed error information for debugging
    import traceback
    print(f"   Error details: {traceback.format_exc()}")

# --- New RAG System Setup ---
# RAG is now initialized lazily via RAG Singleton pattern (src/core/rag_singleton.py)
# It will auto-initialize on first use via get_rag_context()
# No need for explicit initialization here to avoid duplicate initialization


print("\n--- RAG System will initialize on first use (lazy loading via singleton) ---")

# Initialize the TextToCADAgent
# Pass the initialized LLMs and the new RAG's retrieve_context function
text_to_cad_agent = TextToCADAgent(
    default_llm=default_llm,
    advanced_llm=advanced_llm,  # Using Claude as advanced_llm for code generation (temporary)
    expert_llm=expert_llm,
    confirm_llm=confirm_llm,    # gpt-5.4 for description_confirm
)
