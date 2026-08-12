"""
CRUD operations package for Tolery API.
"""

# Session management operations
from . import sessions
from . import chat_processing

# Backwards compatibility for existing imports
from .sessions import (
    get_all_sessions,
    create_session,
    get_session_by_id,
    delete_session,
    update_session,
    get_chats_by_session_id,
    add_chat_history_entry,
    get_exports_by_session_id
)

from .chat_processing import (
    handle_chat_request
)

__all__ = [
    # Modules
    "sessions",
    "chat_processing",

    # Individual functions for backwards compatibility
    "get_all_sessions",
    "create_session",
    "get_session_by_id",
    "delete_session",
    "update_session",
    "get_chats_by_session_id",
    "add_chat_history_entry",
    "get_exports_by_session_id",
    "handle_chat_request"
]
