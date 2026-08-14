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
    # Perforated sheet — user asked for a named/branded hole pattern (a vendor
    # pattern name) that has no equivalent in our supported notation (R/C/LR/LC +
    # T/U/Z — see data/Info/Perforated_Sheet/info.json). Friendly redirect instead
    # of silently ignoring the request or hallucinating a match.
    "perforated_unknown_pattern": (
        "Sorry, I don't recognize this pattern or manufacturer yet 🙂 I can create "
        "round, square, or oblong holes (rounded or rectangular) with a square, "
        "staggered, or custom pitch — just tell me the shape and spacing you'd like "
        "and I'll take care of it!"
    ),
    # Perforated sheet — when hole + % are known but grid family (T/U/Z) is still unknown.
    "perforated_pitch_type_question": (
        "Which grid type do you want? T (staggered 60° / triangular), "
        "U (square / inline), or Z (generic stagger)?"
    ),
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


def get_perforated_unknown_pattern_message() -> str:
    """
    Friendly redirect when the user asks for a named/branded perforation
    pattern that has no equivalent in our supported notation (R/C/LR/LC + T/U/Z).
    """
    return _get_message("perforated_unknown_pattern")


def get_perforated_pitch_type_question() -> str:
    """Ask the user to choose a perforated hole grid type (T / U / Z)."""
    return _get_message("perforated_pitch_type_question")
