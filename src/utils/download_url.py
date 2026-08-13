"""
Shared base-URL resolution for download links.

Four call sites used to compute this identically (api/test/main.py,
api/production/main.py twice, crud/chat_processing.py); they now all call
resolve_base_url().

Note on the PORT variable: this helper reads PORT, which is what those four call
sites have always read. src/api/main.py and src/crud/sessions.py instead read
UVICORN_PORT for the same purpose, so they are deliberately NOT routed through
here — collapsing the two would silently change which port ends up in download
links on any deployment that sets only one of them.
"""
import os

from dotenv import load_dotenv


def resolve_base_url() -> str:
    """
    Return the public base URL for download links.

    The port is only appended when DOMAIN is localhost; a real domain is assumed
    to be fronted by a reverse proxy on the default port.
    """
    load_dotenv()

    DOMAIN = os.getenv("DOMAIN", "http://localhost")
    PORT = os.getenv("PORT", "8124")

    if DOMAIN == "http://localhost" or DOMAIN == "localhost":
        return f"{DOMAIN}:{PORT}"
    return DOMAIN
