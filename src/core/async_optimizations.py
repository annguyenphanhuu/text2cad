#!/usr/bin/env python3
"""
Async Optimizations Module

Only `async_timer` survives here. The module used to also carry AsyncOptimizer,
AsyncFileOperations, AsyncProcessingPipeline, AsyncLLMProcessor and
AsyncRAGProcessor; none of them was ever called (AsyncLLMProcessor was
constructed once in text_to_cad_agent and then never used, so the rate limiting
it advertised never actually ran). They were removed rather than left in place
implying a behaviour the pipeline does not have.
"""

import logging
import time
from functools import wraps

logger = logging.getLogger(__name__)


def async_timer(func_name: str = None):
    """Decorator to time async functions"""
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            name = func_name or func.__name__
            start_time = time.time()
            try:
                result = await func(*args, **kwargs)
                duration = time.time() - start_time
                logger.info(f"[ASYNC_TIMER] {name} completed in {duration:.2f}s")
                return result
            except Exception as e:
                duration = time.time() - start_time
                logger.error(f"[ASYNC_TIMER] {name} failed after {duration:.2f}s: {e}")
                raise
        return wrapper
    return decorator
