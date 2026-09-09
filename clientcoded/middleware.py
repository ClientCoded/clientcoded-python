from .context import extract_test_context, clear_test_context


class ClientCodedMiddleware:
    """ASGI middleware for FastAPI/Starlette."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = dict(scope.get("headers", []))
            str_headers = {}
            for k, v in headers.items():
                try:
                    str_headers[k.decode()] = v.decode()
                except (AttributeError, UnicodeDecodeError):
                    str_headers[str(k)] = str(v)
            extract_test_context(str_headers)
            await self.app(scope, receive, send)
            clear_test_context()
        else:
            await self.app(scope, receive, send)


def flask_before_request():
    """Register with app.before_request(flask_before_request)"""
    try:
        from flask import request
        extract_test_context(dict(request.headers))
    except ImportError:
        pass


def flask_after_request(response):
    """Register with app.after_request(flask_after_request)"""
    clear_test_context()
    return response
