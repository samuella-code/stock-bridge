"""Safe application diagnostics and database-independent HTTP error responses."""
import logging
import os
import re
import sys
import traceback
from uuid import uuid4

from flask import Flask, current_app, g, has_request_context, jsonify, request


def request_id():
    if not has_request_context():
        return 'outside-request'
    if not getattr(g, 'request_id', None):
        g.request_id = uuid4().hex
    return g.request_id


def log_failure(category):
    """Only application-owned categories and exception classes, never messages."""
    category = category if re.fullmatch(r'[a-z_]{1,60}', category) else 'application_failure'
    error = sys.exc_info()[0]
    current_app.logger.error('failure category=%s exception_type=%s', category,
                             error.__name__ if error else 'Unavailable')


class SafeApplicationLogFilter(logging.Filter):
    def filter(self, record):
        # Default traceback formatting includes exception text/SQL parameters.
        # Treat any exception-bearing record as unsafe, including its arguments.
        if record.exc_info:
            error = record.exc_info[0]
            record.msg = 'application_exception exception_type=%s'
            record.args = (error.__name__ if error else 'Unavailable',)
            record.exc_info = None
            record.exc_text = None
        record.stack_info = None
        record.request_id = request_id()
        route = request.url_rule.rule if has_request_context() and request.url_rule else 'unmatched'
        record.msg = record.getMessage() + ' request_id=%s route=%s'
        record.args = (record.request_id, route)
        return True


class SafeFlask(Flask):
    def log_exception(self, exc_info):
        # Flask's default record includes the raw URL path and exception text.
        # Keep the failure visible without reset tokens or database parameters.
        locations = ' > '.join(
            f'{os.path.basename(frame.filename)}:{frame.lineno}:{frame.name}'
            for frame in traceback.extract_tb(exc_info[2])[-8:])
        self.logger.error('unhandled_exception exception_type=%s stack=%s',
                          exc_info[0].__name__, locations)


ERRORS = {
    400: ('Check your request', 'The request could not be processed. Refresh the page and try again.'),
    403: ('Access unavailable', 'You do not have permission to open this page.'),
    404: ('Page not found', 'This page is unavailable or the link is no longer valid.'),
    429: ('Please try again later', 'Too many requests were received. Wait a moment before trying again.'),
    500: ('Something went wrong', 'StockBridge could not complete this request. Please try again later.'),
}


def wants_json():
    return (request.path.startswith(('/api/', '/internal/'))
            or request.endpoint == 'payments.webhook'
            or request.is_json
            or request.accept_mimetypes['application/json'] > request.accept_mimetypes['text/html'])


def register_error_handling(app):
    # Flask app loggers are shared by name across factories; avoid stacking filters.
    for item in list(app.logger.filters):
        if isinstance(item, SafeApplicationLogFilter):
            app.logger.removeFilter(item)
    app.logger.addFilter(SafeApplicationLogFilter())

    @app.before_request
    def identify_request():
        request_id()  # Generated locally; incoming request IDs are never trusted.

    @app.after_request
    def correlate_response(response):
        response.headers['X-Request-ID'] = request_id()
        return response

    def http_error(error):
        status = error.code
        title, message = ERRORS[status]
        current_app.logger.log(logging.ERROR if status >= 500 else logging.WARNING,
                               'http_error status=%s', status)
        # Error rendering must never query subscription/business/notification data.
        response = error.get_response()  # Retain headers such as Retry-After.
        if wants_json():
            response.set_data(jsonify(error={'status': status, 'message': message,
                                           'request_id': request_id()}).get_data())
            response.content_type = 'application/json'
        else:
            # Do not call render_template: Flask-Login's context processor loads
            # the user from SQL even when a template never uses current_user.
            response.set_data(app.jinja_env.get_template('errors/error.html').render(
                status=status, title=title, message=message, request_id=request_id()))
            response.content_type = 'text/html; charset=utf-8'
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Request-ID'] = request_id()
        return response

    for status in ERRORS:
        app.register_error_handler(status, http_error)
