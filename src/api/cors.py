"""
Shared CORS policy for the three FastAPI apps (main, api-production, api-test).

All three declared the same allow_origins / allow_methods / allow_headers block
verbatim. Keeping one copy means the policy cannot drift between the public app
and the mounted sub-apps — a drift that would be a security difference, not just
duplication.
"""
from fastapi.middleware.cors import CORSMiddleware

ALLOWED_ORIGINS = [
    "https://preprodv4.tolery.io",
    "https://origin-preprod-v4.test",
]

ALLOWED_METHODS = ["POST", "OPTIONS"]

ALLOWED_HEADERS = [
    "Content-Type",
    "Accept",
    "X-CSRF-TOKEN",
]

EXPOSED_HEADERS = [
    "Content-Type",
]


def configure_cors(app) -> None:
    """Attach the shared CORS middleware to `app`."""
    app.add_middleware(
        CORSMiddleware,
        allow_origins=ALLOWED_ORIGINS,
        allow_credentials=True,
        allow_methods=ALLOWED_METHODS,
        allow_headers=ALLOWED_HEADERS,
        expose_headers=EXPOSED_HEADERS,
    )
