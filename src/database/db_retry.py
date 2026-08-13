"""
Database retry logic for handling connection failures and transient errors.
"""
import time
import logging
from functools import wraps
from typing import Callable, Any
from sqlalchemy.exc import OperationalError, DisconnectionError, TimeoutError

logger = logging.getLogger(__name__)

class DatabaseRetryError(Exception):
    """Raised when all retry attempts have been exhausted."""
    def __init__(self, message: str, original_error: Exception = None):
        super().__init__(message)
        self.original_error = original_error
        self.message = message

def retry_db_operation(
    max_retries: int = 3,
    delay: float = 1.0,
    backoff_factor: float = 2.0,
    exceptions: tuple = (OperationalError, DisconnectionError, TimeoutError)
):
    """
    Decorator to retry database operations on transient failures.
    
    Args:
        max_retries: Maximum number of retry attempts
        delay: Initial delay between retries in seconds
        backoff_factor: Multiplier for delay after each retry
        exceptions: Tuple of exceptions to catch and retry on
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            last_exception = None
            current_delay = delay
            
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    
                    if attempt == max_retries:
                        logger.error(f"Database operation failed after {max_retries} retries: {str(e)}")
                        raise DatabaseRetryError(
                            f"Operation failed after {max_retries} retries: {str(e)}",
                            original_error=e
                        ) from e
                    
                    logger.warning(f"Database operation failed (attempt {attempt + 1}/{max_retries + 1}): {str(e)}")
                    logger.info(f"Retrying in {current_delay} seconds...")
                    
                    time.sleep(current_delay)
                    current_delay *= backoff_factor
                    
                except Exception as e:
                    # Don't retry on non-transient errors
                    logger.error(f"Non-retryable database error: {str(e)}")
                    raise
            
            # This should never be reached, but just in case
            raise last_exception
        
        return wrapper
    return decorator


def is_connection_error(exception: Exception) -> bool:
    """
    Check if an exception is a connection-related error that should be retried.
    """
    connection_error_messages = [
        "Lost connection to MySQL server",
        "MySQL server has gone away",
        "SSL connection error",
        "Connection timed out",
        "Can't connect to MySQL server",
        "Connection refused"
    ]
    
    error_message = str(exception).lower()
    return any(msg.lower() in error_message for msg in connection_error_messages)
