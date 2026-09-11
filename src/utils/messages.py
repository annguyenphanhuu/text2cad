"""
User-facing message catalog.

The product is English-only: there is no language detection, no session
language cache, and no translation layer. Every string returned here is
English, and callers pass no language argument.
"""

# ═════════════════════════════════════════════════════════════════════════════
# Message catalog — single source of truth for all user-facing strings
# ═════════════════════════════════════════════════════════════════════════════

_MESSAGES: dict[str, str] = {
    "success": "Your part generation successful.",
    "error_general": "An error occurred while processing your request.",
    "error_agent_not_initialized": (
        "Text-to-CAD agent not initialized properly. Check API keys and connections."
    ),
    "error_processing_completed": "Processing completed.",
}


def _get_message(key: str) -> str:
    """Core message lookup. Returns an empty string for unknown keys."""
    return _MESSAGES.get(key, "")


# ═════════════════════════════════════════════════════════════════════════════
# Public message API
# ═════════════════════════════════════════════════════════════════════════════

def get_success_message() -> str:
    """Return the part-generation success message."""
    return _get_message("success")


def get_error_message(error_type: str = "general") -> str:
    """
    Return the error message for the given error type.

    Args:
        error_type: One of "general", "agent_not_initialized", "processing_completed".
                    Unknown types fall back to the general error message.
    """
    return _get_message(f"error_{error_type}") or _get_message("error_general")
