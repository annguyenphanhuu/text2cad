"""
OpenAI Cost Tracking Wrapper for LangChain
Provides utilities to wrap LangChain chain invocations with cost tracking
"""
import logging
from typing import Any, Dict, Callable
from langchain_community.callbacks import get_openai_callback
from src.utils.cost_tracker import CostTracker

logger = logging.getLogger(__name__)


async def ainvoke_with_cost_tracking(
    chain_name: str,
    chain_invoke_func: Callable,
    chain_input: Dict[str, Any],
    cost_tracker: CostTracker = None,
    model_name: str = "unknown"
) -> Any:
    """
    Wrap an async chain invocation with OpenAI cost tracking.
    
    Args:
        chain_name: Name of the chain being invoked (e.g., "greeting_classification")
        chain_invoke_func: The async invoke function to call (e.g., chain.ainvoke)
        chain_input: Input dictionary for the chain
        cost_tracker: Optional CostTracker instance to record costs
        model_name: Name of the model being used (e.g., "gpt-4.1-2025-04-14")
        
    Returns:
        The result from the chain invocation
        
    Example:
        result = await ainvoke_with_cost_tracking(
            "greeting_classification",
            self.greeting_classification_chain.ainvoke,
            {"user_text": user_text},
            self.cost_tracker,
            self.model_names['default']
        )
    """
    with get_openai_callback() as cb:
        try:
            # Invoke the chain
            result = await chain_invoke_func(chain_input)
            
            # Track the cost if tracker is provided
            if cost_tracker:
                cost_tracker.add_chain_cost(chain_name, cb, model_name)
            else:
                # Log even without tracker
                logger.info(
                    f"[COST] {chain_name} | Model: {model_name} | "
                    f"${cb.total_cost:.6f} "
                    f"(prompt: {cb.prompt_tokens} tokens, "
                    f"completion: {cb.completion_tokens} tokens, "
                    f"total: {cb.total_tokens} tokens)"
                )
            
            return result
            
        except Exception as e:
            # ── OpenAI error type classification ──────────────────────────────
            # Distinguishes RateLimitError vs Timeout vs Connection issues so we
            # can confirm the root cause of "Erreur de connexion" in production.
            try:
                import openai as _openai
                _e_type = type(e).__name__
                if isinstance(e, _openai.RateLimitError):
                    _retry_after = getattr(getattr(e, 'response', None), 'headers', {})
                    _retry_after = _retry_after.get('retry-after', 'N/A') if _retry_after else 'N/A'
                    logger.error(
                        f"[OPENAI_ERROR] RateLimitError (HTTP 429) | "
                        f"chain={chain_name} | model={model_name} | "
                        f"retry-after={_retry_after}s | session traceable via cost_tracker"
                    )
                elif isinstance(e, _openai.APITimeoutError):
                    logger.error(
                        f"[OPENAI_ERROR] APITimeoutError | "
                        f"chain={chain_name} | model={model_name} | "
                        f"The call exceeded the configured timeout — likely caused SSE silence"
                    )
                elif isinstance(e, _openai.APIConnectionError):
                    logger.error(
                        f"[OPENAI_ERROR] APIConnectionError | "
                        f"chain={chain_name} | model={model_name} | "
                        f"Network/connection issue to OpenAI endpoint"
                    )
                else:
                    logger.error(
                        f"[OPENAI_ERROR] {_e_type} | "
                        f"chain={chain_name} | model={model_name} | detail={str(e)[:200]}"
                    )
            except Exception:
                pass  # Never let classification crash the original error path
            # Log error with cost info (if any tokens were used before error)
            if cb.total_tokens > 0:
                logger.error(
                    f"[COST] {chain_name} FAILED after ${cb.total_cost:.6f} "
                    f"({cb.total_tokens} tokens): {str(e)}"
                )
                if cost_tracker:
                    cost_tracker.add_chain_cost(f"{chain_name}_FAILED", cb, model_name)
            raise


def invoke_with_cost_tracking(
    chain_name: str,
    chain_invoke_func: Callable,
    chain_input: Dict[str, Any],
    cost_tracker: CostTracker = None
) -> Any:
    """
    Wrap a synchronous chain invocation with OpenAI cost tracking.
    
    Args:
        chain_name: Name of the chain being invoked (e.g., "greeting_classification")
        chain_invoke_func: The invoke function to call (e.g., chain.invoke)
        chain_input: Input dictionary for the chain
        cost_tracker: Optional CostTracker instance to record costs
        
    Returns:
        The result from the chain invocation
        
    Example:
        result = invoke_with_cost_tracking(
            "greeting_classification",
            self.greeting_classification_chain.invoke,
            {"user_text": user_text},
            self.cost_tracker
        )
    """
    with get_openai_callback() as cb:
        try:
            # Invoke the chain
            result = chain_invoke_func(chain_input)
            
            # Track the cost if tracker is provided
            if cost_tracker:
                cost_tracker.add_chain_cost(chain_name, cb)
            else:
                # Log even without tracker
                logger.info(
                    f"[COST] {chain_name}: ${cb.total_cost:.6f} "
                    f"(prompt: {cb.prompt_tokens} tokens, "
                    f"completion: {cb.completion_tokens} tokens, "
                    f"total: {cb.total_tokens} tokens)"
                )
            
            return result
            
        except Exception as e:
            # Log error with cost info (if any tokens were used before error)
            if cb.total_tokens > 0:
                logger.error(
                    f"[COST] {chain_name} FAILED after ${cb.total_cost:.6f} "
                    f"({cb.total_tokens} tokens): {str(e)}"
                )
                if cost_tracker:
                    cost_tracker.add_chain_cost(f"{chain_name}_FAILED", cb)
            raise
