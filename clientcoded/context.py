import json
from contextvars import ContextVar

_test_context: ContextVar[dict] = ContextVar('test_context', default={})


def extract_test_context(headers: dict) -> dict:
    """Read x-clientcoded-context from incoming request headers."""
    raw = headers.get('x-clientcoded-context') or headers.get('X-Clientcoded-Context')
    if raw:
        try:
            ctx = json.loads(raw)
            _test_context.set(ctx)
            return ctx
        except (json.JSONDecodeError, TypeError):
            pass
    return {}


def get_test_context() -> dict:
    return _test_context.get()


def clear_test_context():
    _test_context.set({})
