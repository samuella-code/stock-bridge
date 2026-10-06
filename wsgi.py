"""Vercel/WSGI entry point for StockBridge."""

from runtime_diagnostics import startup_watch

with startup_watch():
    from app import create_app
    app = create_app()
