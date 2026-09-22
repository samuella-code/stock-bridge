"""Vercel/WSGI entry point for StockBridge."""

from app import create_app


app = create_app()
