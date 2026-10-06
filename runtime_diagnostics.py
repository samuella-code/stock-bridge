"""Bounded slow-request diagnostics: no secrets, query strings or local values."""
import logging
import os
import sys
import threading
import time
import traceback
from contextlib import contextmanager


def _watch(label, delay=8):
    thread_id = threading.get_ident()
    def report():
        frame = sys._current_frames().get(thread_id)
        # Function names/locations only: never source lines, arguments or locals.
        stack = ' > '.join(f'{os.path.basename(row.filename)}:{row.lineno}:{row.name}'
                           for row in traceback.extract_stack(frame)) if frame else 'thread unavailable'
        logging.getLogger('stockbridge.runtime').warning('slow phase=%s stack=%s', label, stack)
    timer = threading.Timer(delay, report)
    timer.daemon = True
    timer.start()
    return timer


@contextmanager
def startup_watch():
    timer = _watch('startup') if os.getenv('VERCEL') else None
    try:
        yield
    finally:
        if timer:
            timer.cancel()


def register_request_diagnostics(app):
    from flask import g, request
    @app.before_request
    def request_started():
        if app.testing or not os.getenv('VERCEL'):
            return
        # A route template contains no reset token/provider code/customer input.
        route = request.url_rule.rule if request.url_rule else 'unmatched'
        g.runtime_started = time.perf_counter()
        g.runtime_watch = _watch(f'request {request.method} {route}')
        app.logger.info('request start method=%s route=%s', request.method, route)

    @app.after_request
    def request_finished(response):
        started = getattr(g, 'runtime_started', None)
        if started is not None:
            app.logger.info('request end status=%s elapsed_ms=%s', response.status_code,
                            round((time.perf_counter() - started) * 1000))
        return response

    @app.teardown_request
    def request_teardown(error):
        timer = getattr(g, 'runtime_watch', None)
        if timer:
            timer.cancel()
