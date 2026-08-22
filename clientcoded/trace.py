# ClientCoded Python SDK
# pip install clientcoded

import requests
import functools
import time
import uuid
import threading
import os

CLIENTCODED_API = os.environ.get(
    "CLIENTCODED_TRACE_URL",
    "https://clientcoded.app.n8n.cloud/webhook/ap-35-trace-ingest"
)

_context = threading.local()


def configure(agent_id, api_key, trace_url=None):
    """
    Configure the SDK globally.
    
    Args:
        agent_id: Your agent's ID from ClientCoded dashboard
        api_key: Your API key from ClientCoded dashboard
        trace_url: Optional custom trace endpoint URL
    """
    _context.agent_id = agent_id
    _context.api_key = api_key
    if trace_url:
        global CLIENTCODED_API
        CLIENTCODED_API = trace_url


def set_conversation_id(conversation_id):
    """Call this at the start of each conversation."""
    _context.conversation_id = conversation_id
    _context.turn = 0


def set_turn(turn_number):
    """Call this at the start of each turn."""
    _context.turn = turn_number


def increment_turn():
    """Increment the turn counter by 1."""
    current = getattr(_context, 'turn', 0)
    _context.turn = current + 1


def trace(func=None, *, name=None):
    """
    Decorator that traces a function call.
    
    Usage:
        @trace
        def get_invoice(invoice_id):
            return stripe.Invoice.retrieve(invoice_id)
        
        @trace(name="stripe_lookup")
        def get_invoice(invoice_id):
            return stripe.Invoice.retrieve(invoice_id)
    """
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            trace_id = str(uuid.uuid4())
            tool_name = name or fn.__name__
            start = time.time()
            error = None
            result = None
            try:
                result = fn(*args, **kwargs)
                return result
            except Exception as e:
                error = str(e)
                raise
            finally:
                _send_trace(
                    trace_id=trace_id,
                    tool_name=tool_name,
                    args=args,
                    kwargs=kwargs,
                    result=result,
                    error=error,
                    latency_ms=int((time.time() - start) * 1000)
                )
        return wrapper

    if func is not None:
        return decorator(func)
    return decorator


def trace_async(func=None, *, name=None):
    """
    Decorator that traces an async function call.
    
    Usage:
        @trace_async
        async def get_invoice(invoice_id):
            return await stripe.Invoice.aretrieve(invoice_id)
    """
    def decorator(fn):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            trace_id = str(uuid.uuid4())
            tool_name = name or fn.__name__
            start = time.time()
            error = None
            result = None
            try:
                result = await fn(*args, **kwargs)
                return result
            except Exception as e:
                error = str(e)
                raise
            finally:
                _send_trace(
                    trace_id=trace_id,
                    tool_name=tool_name,
                    args=args,
                    kwargs=kwargs,
                    result=result,
                    error=error,
                    latency_ms=int((time.time() - start) * 1000)
                )
        return wrapper

    if func is not None:
        return decorator(func)
    return decorator


def log_trace(tool_name, input_data=None, output_data=None, error=None, latency_ms=None):
    """
    Manual trace logging for cases where decorators don't fit.
    
    Usage:
        start = time.time()
        try:
            result = some_complex_operation()
            log_trace("complex_op", input_data={"key": "val"}, output_data=result)
        except Exception as e:
            log_trace("complex_op", input_data={"key": "val"}, error=str(e))
    """
    _send_trace(
        trace_id=str(uuid.uuid4()),
        tool_name=tool_name,
        args=(),
        kwargs=input_data or {},
        result=output_data,
        error=error,
        latency_ms=latency_ms
    )


def _send_trace(trace_id, tool_name, args, kwargs, result, error, latency_ms):
    """Fire-and-forget trace send. Never breaks the customer's agent."""
    agent_id = getattr(_context, 'agent_id', None)
    api_key = getattr(_context, 'api_key', None)

    if not agent_id or not api_key:
        return

    payload = {
        "agent_id": agent_id,
        "api_key": api_key,
        "trace_id": trace_id,
        "conversation_id": getattr(_context, 'conversation_id', None),
        "turn": getattr(_context, 'turn', None),
        "tool_name": tool_name,
        "input": _safe_serialize_input(args, kwargs),
        "output": _safe_serialize_output(result),
        "error": error,
        "latency_ms": latency_ms,
        "timestamp": time.time()
    }

    # Fire and forget in a background thread
    thread = threading.Thread(target=_post_trace, args=(payload,), daemon=True)
    thread.start()


def _post_trace(payload):
    """Send trace to ClientCoded. Silent failure."""
    try:
        requests.post(
            CLIENTCODED_API,
            json=payload,
            timeout=2
        )
    except Exception:
        pass


def _safe_serialize_input(args, kwargs):
    """Serialize inputs, truncate large values."""
    try:
        serialized = {}
        if args:
            serialized["args"] = [_truncate(str(a)) for a in args]
        if kwargs:
            serialized["kwargs"] = {k: _truncate(str(v)) for k, v in kwargs.items()}
        return serialized
    except Exception:
        return {"error": "unserializable"}


def _safe_serialize_output(result):
    """Serialize output, truncate large responses."""
    if result is None:
        return None
    try:
        return _truncate(str(result))
    except Exception:
        return "unserializable"


def _truncate(s, max_len=2000):
    """Truncate string to max length."""
    if len(s) > max_len:
        return s[:max_len] + "... (truncated)"
    return s
