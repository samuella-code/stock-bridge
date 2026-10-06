"""Vercel/WSGI entry point for StockBridge."""

from runtime_diagnostics import startup_watch

# Keep the app assignment at module level for Vercel's Flask entrypoint detector.
startup_monitor = startup_watch()
startup_monitor.__enter__()
from app import create_app

app = create_app()
startup_monitor.__exit__(None, None, None)
