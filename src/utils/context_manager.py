"""
Context manager for request-scoped user_id and session_id tracking.
Provides a way to pass user_id and session_id through the application without modifying all function signatures.
"""

from contextvars import ContextVar
from typing import Optional

# Context variables for storing user_id and session_id
_user_id_context: ContextVar[Optional[str]] = ContextVar('user_id', default=None)
_session_id_context: ContextVar[Optional[str]] = ContextVar('session_id', default=None)


def set_user_id(user_id: Optional[str]) -> None:
    """Set the user_id for the current context."""
    _user_id_context.set(user_id)


def get_user_id() -> Optional[str]:
    """Get the user_id from the current context."""
    return _user_id_context.get()


def set_session_id(session_id: Optional[str]) -> None:
    """Set the session_id for the current context."""
    _session_id_context.set(session_id)


def get_session_id() -> Optional[str]:
    """Get the session_id from the current context."""
    return _session_id_context.get()


def clear_context() -> None:
    """Clear all context variables."""
    _user_id_context.set(None)
    _session_id_context.set(None)
