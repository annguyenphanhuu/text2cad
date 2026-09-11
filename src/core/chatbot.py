"""The one TextToCADAgent instance shared by every API app, with its two LLMs."""
import logging
import os

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), '.env'))

from src.core.text_to_cad_agent import TextToCADAgent  # noqa: E402

logger = logging.getLogger(__name__)

MODELS = {
    "llm": "gpt-5.4-2026-03-05",              # IR extraction and patching
    "greeting": "gpt-4.1-nano-2025-04-14",   # greeting / process-question / confirm-reply classification
}

llm = greeting_llm = None
try:
    if not os.getenv("OPENAI_API_KEY"):
        raise ValueError("OPENAI_API_KEY environment variable is not set. Please check your .env file.")
    # temperature=0: the same request must give the same IR and the same classification
    llm = ChatOpenAI(model=MODELS["llm"], temperature=0)
    greeting_llm = ChatOpenAI(model=MODELS["greeting"], temperature=0)
except ValueError as ve:
    logger.error(f"Configuration error: {ve} — set the required API keys in your .env file.")
except Exception as e:
    logger.error(f"Could not initialize the LLM clients: {e} — check your API key and network connection.", exc_info=True)

text_to_cad_agent = TextToCADAgent(llm, greeting_llm)
