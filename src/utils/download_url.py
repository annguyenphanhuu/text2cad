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
from pathlib import Path

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


def build_download_url(file_path, base_url: str = None) -> str:
    """
    Return the public /download/ URL for an exported file.

    The path is preserved as-is relative to the project root, because
    GET /download/{file_path} (routes/cad.py) resolves it straight against
    Path.cwd() with no directory remapping — so the URL has to carry the real
    on-disk path. The previous production/main.py copy of this logic instead
    rebuilt the URL as outputs/<format>/<date>/<filename>, guessing <format>
    from the file extension whenever the path held no recognisable directory;
    that produced outputs/step/... for STEP files, which actually live in
    outputs/cad/, and a 404 on download.

    Args:
        file_path: Absolute or project-relative path to the exported file.
        base_url:  Override for the public base URL; defaults to resolve_base_url().
    """
    if not file_path:
        return ""

    file_path_str = str(file_path).replace('\\', '/')

    # Already a full URL (production stores some export links pre-resolved).
    if file_path_str.startswith(('http://', 'https://')):
        return file_path_str

    if base_url is None:
        base_url = resolve_base_url()

    project_root_str = str(Path.cwd()).replace('\\', '/')
    if project_root_str in file_path_str:
        relative_path = file_path_str.split(project_root_str, 1)[1]
    else:
        relative_path = file_path_str

    relative_path = relative_path.lstrip('/')
    return f"{base_url}/download/{relative_path}"
