"""ASGI entry point: python -m uvicorn reporisk.api.main:app

Builds the app from environment variables (and .env), creating the account
database on first start. Kept separate so importing create_app has no side effects.
"""

from .app import create_app

app = create_app()
