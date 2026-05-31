"""CORS configuration."""

from fastapi.middleware.cors import CORSMiddleware
from src.config.settings import get_settings


def setup_cors(app):
    settings = get_settings()
    origins = getattr(settings, 'cors_origins', '*')
    if isinstance(origins, str):
        origins = [o.strip() for o in origins.split(',')]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
