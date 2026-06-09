"""Gunicorn configuration.

Render auto-detects Python web services and runs `gunicorn app:app`, whose
DEFAULT worker is a synchronous WSGI worker.  FastAPI is an ASGI app, so the WSGI
worker invokes it with the wrong call signature and every request fails with:

    TypeError: FastAPI.__call__() missing 1 required positional argument: 'send'

Gunicorn automatically loads this file (./gunicorn.conf.py) from the working
directory, so setting the ASGI worker here makes the auto-detected command work
WITHOUT needing a custom start command in the Render dashboard.

WebSockets (/ws) require the ASGI worker too, so this also fixes the live feed.
"""
import os

# ASGI worker — required for FastAPI + WebSockets.
worker_class = "uvicorn.workers.UvicornWorker"

# Render sets WEB_CONCURRENCY (default 1).  Keep a single worker: the live-feed
# hub and EOD scheduler hold in-process state that must not be duplicated.
workers = int(os.getenv("WEB_CONCURRENCY", "1"))

# Long-lived WebSocket connections must not be killed by the default 30s timeout.
timeout = 120
graceful_timeout = 30
keepalive = 5
