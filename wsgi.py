"""Vercel/WSGI entry point for StockBridge."""

from runtime_diagnostics import startup_watch

# Keep the app assignment at module level for Vercel's Flask entrypoint detector.
startup_monitor = startup_watch()
startup_monitor.__enter__()
from app import create_app

app = create_app()
startup_monitor.__exit__(None, None, None)

# Temporary opt-in evidence; protected runtime logs only, no network or SQL.
try:
    import os
    from startup_database_diagnostic import startup_diagnostic
    startup_diagnostic(app.config, os.environ)
except Exception:
    # Optional evidence must never break startup or expose exception details.
    # Do not log here: logging itself may be the failing operation.
    pass
