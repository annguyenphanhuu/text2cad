"""
Authentication middleware for Tolery API
Provides token-based authentication for API endpoints
"""

import os
import logging
from fastapi import Request, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

# Configure logging
logger = logging.getLogger("tolery-auth")

# Get JWT token from environment variable with fallback
API_TOKEN = os.getenv("API_SECRET_TOKEN", "yJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ1c2VyX2lkIjoxMjMsInVzZXJuYW1lIjoiaHV5Iiwicm9sZSI6ImFkbWluIn0.E_YGrdhOQcuUcQ6Ugn_wUOhoMxu-bD2Ajol5YvhJit8")
VALID_TOKEN = API_TOKEN  # Alias for backward compatibility

# FastAPI Security scheme for Swagger UI
security = HTTPBearer()

def verify_token(token: str) -> bool:
    """Simple token verification"""
    return token == API_TOKEN


class TokenAuthMiddleware(BaseHTTPMiddleware):
    """
    Token-based authentication middleware for FastAPI

    Checks for valid JWT token in request headers:
    - Authorization: Bearer <JWT_TOKEN>

    Excludes certain public endpoints from authentication if needed.
    """

    def __init__(self, app, exclude_paths=None):
        super().__init__(app)
        # Paths that don't require authentication (if any)
        self.exclude_paths = exclude_paths or []

    async def dispatch(self, request: Request, call_next):
        """
        Process each request and check for valid authentication token
        """
        # Skip authentication for excluded paths
        if request.url.path in self.exclude_paths:
            return await call_next(request)

        # Skip authentication for OPTIONS requests (CORS preflight)
        if request.method == "OPTIONS":
            return await call_next(request)

        # Check for token in Authorization header (Bearer token format only)
        token = None
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.split(" ")[1]

        # Validate token
        if not token:
            logger.warning(f"Missing token for {request.method} {request.url.path}")
            return JSONResponse(
                status_code=401,
                content={
                    "error": "Authentication required",
                    "message": "JWT token is required. Include token in 'Authorization: Bearer <token>' header",
                    "required_token_format": "JWT token"
                }
            )

        if token != API_TOKEN:
            logger.warning(f"Invalid token attempt for {request.method} {request.url.path}: {token[:10]}...")
            return JSONResponse(
                status_code=403,
                content={
                    "error": "Invalid token",
                    "message": "The provided JWT token is invalid",
                    "required_token_format": "JWT token"
                }
            )

        # Token is valid, proceed with request
        logger.info(f"Authenticated request: {request.method} {request.url.path}")
        return await call_next(request)


async def verify_token_dependency(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """
    FastAPI dependency to verify JWT token

    Args:
        credentials: HTTP Bearer credentials from request

    Returns:
        str: The validated token

    Raises:
        HTTPException: If token is invalid
    """
    if credentials.credentials != API_TOKEN:
        logger.warning(f"Invalid token attempt: {credentials.credentials[:10]}...")
        raise HTTPException(
            status_code=403,
            detail={
                "error": "Invalid token",
                "message": "The provided JWT token is invalid",
                "required_token_format": "JWT token"
            }
        )

    logger.info("Token validated successfully")
    return credentials.credentials


def get_token_info():
    """
    Get information about the JWT token for documentation/testing purposes
    """
    return {
        "token": API_TOKEN,
        "format": "JWT token",
        "usage": {
            "header": f"Authorization: Bearer {API_TOKEN}"
        },
        "example_curl": f"curl -H 'Authorization: Bearer {API_TOKEN}' http://localhost:8124/api-production/sessions"
    }


def verify_token_simple(token: str) -> bool:
    """
    Simple token verification function

    Args:
        token: The token to verify

    Returns:
        bool: True if token is valid, False otherwise
    """
    return token == API_TOKEN


async def auth_middleware(request: Request, call_next):
    """
    Middleware to protect API endpoints and extract user_id.

    - Allows access to the root UI, static files, and docs without a token.
    - Requires a valid 'Bearer' token for all other requests under '/api/'.
    - Extracts user_id from X-User-ID header and stores in context for logging.
    """
    from ..utils.context_manager import set_user_id, clear_context
    
    # Clear context at the start of each request
    clear_context()
    
    # Extract user_id from headers if present
    user_id = request.headers.get("X-User-ID")
    if user_id:
        set_user_id(user_id)
        logger.debug(f"[AUTH] Extracted user_id from header: {user_id}")
    
    public_paths = [
        "/", "/docs", "/redoc", "/openapi.json", "/api/health",
        "/api-test/docs", "/api-test/redoc", "/api-test/openapi.json",
        "/api-production/docs", "/api-production/redoc", "/api-production/openapi.json"
    ]

    # Allow access to viewer endpoints without a token
    if (request.url.path.startswith("/api/3d-viewer/") or
        request.url.path.startswith("/api/json-viewer/") or
        request.url.path.startswith("/api/pdf-viewer/")):
        response = await call_next(request)
        return response
    
    # Allow access to public paths and static files
    if request.url.path in public_paths or request.url.path.startswith("/static"):
        response = await call_next(request)
        return response

    # For API paths (except health check), require authentication
    if (request.url.path.startswith("/api/") or request.url.path.startswith("/api-test/")) and request.url.path != "/api/health":
        token = None

        # Get token from Authorization header (Bearer token format only)
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.split(" ")[1]

        # Special case: browser-native flows that cannot easily attach custom headers.
        # - EventSource uses query auth for SSE.
        # - monitor/export is opened as a download window from the monitor UI.
        query_token_paths = {"/api/generate-cad-stream", "/api/monitor/export"}
        if not token and request.url.path in query_token_paths:
            token = request.query_params.get("token")

        # If no token found, return 401
        if not token:
            error_message = "Authorization token is missing. Use Bearer token in 'Authorization: Bearer <token>' header."
            if request.url.path in query_token_paths:
                error_message += " For supported browser endpoints, you can also use '?token=<token>' query parameter."
            return JSONResponse(
                status_code=401,
                content={"detail": error_message}
            )

        # Verify the token
        if not verify_token(token):
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid or expired token for API endpoint."}
            )

    # Proceed with the request
    response = await call_next(request)
    return response

