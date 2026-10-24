# ClientCoded Python SDK v0.5.0
# Uses OpenInference (open standard) for tracing
# pip install clientcoded
#
# 0.5.0
#   * Traces go to https://api.clientcoded.com/traces by default (was the
#     n8n AP-35 webhook). CLIENTCODED_TRACE_URL still overrides it.
#   * Merges 0.4.0 (test-run tagging via x-clientcoded-context, middleware)
#     with the GitHub improvements (tool.name, session id, retrieved docs).
#   * Long inputs/outputs that get cut to 2,000 characters now say how long
#     they were and, for lists/results, how many items they held, e.g.
#     "... (truncated: 21,840 characters, 128 rows)".
#   * If ClientCoded rejects traces (bad API key, wrong agent, rate limit),
#     the SDK logs one warning per reason instead of failing silently.
#     It still never raises into the customer's agent.

import os
import time
import uuid
import logging
import threading
import functools
import requests

try:
    from opentelemetry import trace as otel_trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult
    HAS_OTEL = True
except ImportError:
    HAS_OTEL = False

CLIENTCODED_API = os.environ.get(
    "CLIENTCODED_TRACE_URL",
    "https://api.clientcoded.com/traces"
)

_log = logging.getLogger("clientcoded")
_warned = set()

_config = {
    'agent_id': None,
    'api_key': None,
}

import contextvars
from .context import get_test_context

_conversation_id = contextvars.ContextVar('conversation_id', default=None)
_turn = contextvars.ContextVar('turn', default=None)


# ============================================================
# ClientCoded Span Exporter
# Converts OpenTelemetry/OpenInference spans to ClientCoded
# trace format and sends them to the ClientCoded trace endpoint
# ============================================================

class ClientCodedExporter(SpanExporter if HAS_OTEL else object):
    """Exports OpenTelemetry/OpenInference spans to ClientCoded."""

    def __init__(self, agent_id=None, api_key=None, endpoint=None):
        self.agent_id = agent_id or _config.get('agent_id')
        self.api_key = api_key or _config.get('api_key')
        self.endpoint = endpoint or CLIENTCODED_API

    def export(self, spans):
        for span in spans:
            try:
                attrs = dict(span.attributes) if span.attributes else {}

                # Span kind
                span_kind = attrs.get('openinference.span.kind', 'UNKNOWN')

                # Tool name (prefer tool.name, fall back to span name)
                tool_name = attrs.get('tool.name', span.name or 'unknown')
                if span_kind and span_kind != 'UNKNOWN':
                    # Same naming as 0.4.0 ("LLM.Messages", "TOOL.run_sql")
                    # so new traces match the ones already stored.
                    tool_name = span_kind.upper() + '.' + tool_name

                # Input (prefer structured, fall back to raw)
                input_data = (
                    attrs.get('input.value') or
                    attrs.get('llm.input_messages') or
                    None
                )

                # Output (prefer structured, fall back to raw)
                output_data = (
                    attrs.get('output.value') or
                    attrs.get('llm.output_messages') or
                    None
                )

                # Token counts
                input_tokens = attrs.get('llm.token_count.prompt', None)
                output_tokens = attrs.get('llm.token_count.completion', None)

                # Model
                model = attrs.get('llm.model_name', None)

                # Retrieved documents (for RAG agents)
                retrieved_docs = attrs.get('retrieval.documents', None)

                # Session ID
                session_id = attrs.get('session.id', None)

                # Error
                error = None
                if span.status and span.status.status_code == otel_trace.StatusCode.ERROR:
                    error = span.status.description or 'Unknown error'
                for event in span.events or []:
                    if event.name == 'exception':
                        error = dict(event.attributes).get('exception.message', error)

                # Latency
                latency_ms = None
                if span.start_time and span.end_time:
                    latency_ms = int((span.end_time - span.start_time) / 1_000_000)

                # Parent
                parent_trace_id = None
                if span.parent and span.parent.span_id:
                    parent_trace_id = format(span.parent.span_id, '016x')

                ctx = get_test_context() or {}
                payload = {
                    'agent_id': self.agent_id,
                    'api_key': self.api_key,
                    'test_run_id': ctx.get('test_run_id'),
                    'question_index': ctx.get('question_index'),
                    'trace_id': format(span.context.span_id, '016x'),
                    'parent_trace_id': parent_trace_id,
                    'conversation_id': _conversation_id.get(),
                    'turn': _turn.get(),
                    'tool_name': tool_name,
                    # Usually already shortened (with a size note) by the
                    # decorators; this only cuts auto-instrumented spans.
                    'input': _truncate(str(input_data)) if input_data else None,
                    'output': _truncate(str(output_data)) if output_data else None,
                    'error': error,
                    'latency_ms': latency_ms,
                    'timestamp': time.time(),
                    'extra': {
                        'input_tokens': input_tokens,
                        'output_tokens': output_tokens,
                        'model': model,
                        'span_kind': span_kind,
                        'retrieved_docs': _truncate(str(retrieved_docs)) if retrieved_docs else None,
                        'session_id': session_id,
                        'tool_description': attrs.get('tool.description', None),
                    }
                }

                thread = threading.Thread(
                    target=_post_trace,
                    args=(self.endpoint, payload),
                    daemon=True
                )
                thread.start()

            except Exception:
                pass

        return SpanExportResult.SUCCESS if HAS_OTEL else None

    def shutdown(self):
        pass

    def force_flush(self, timeout_millis=None):
        return True


# ============================================================
# Public API
# ============================================================

def configure(agent_id=None, api_key=None, trace_url=None):
    """Configure the SDK."""
    _config['agent_id'] = agent_id or os.environ.get('CLIENTCODED_AGENT_ID')
    _config['api_key'] = api_key or os.environ.get('CLIENTCODED_API_KEY')
    if trace_url:
        global CLIENTCODED_API
        CLIENTCODED_API = trace_url


def init(agent_id=None, api_key=None, trace_url=None, frameworks=None):
    """
    One-line setup. Configures SDK and instruments all frameworks.

    Usage:
        import clientcoded
        clientcoded.init(agent_id="xxx", api_key="xxx")

    Or with environment variables:
        export CLIENTCODED_AGENT_ID=xxx
        export CLIENTCODED_API_KEY=xxx

        import clientcoded
        clientcoded.init()
    """
    configure(agent_id, api_key, trace_url)

    if not _config['agent_id'] or not _config['api_key']:
        return

    if HAS_OTEL:
        _setup_otel()

    auto_instrument(frameworks)


def set_conversation_id(conversation_id):
    """Call at the start of each conversation."""
    _conversation_id.set(conversation_id)
    _turn.set(0)


def set_turn(turn_number):
    """Set the current turn number."""
    _turn.set(turn_number)


def increment_turn():
    """Increment turn counter by 1."""
    current = _turn.get() or 0
    _turn.set(current + 1)


def auto_instrument(frameworks=None):
    """
    Auto-instrument supported frameworks using OpenInference.

    Supported (install the corresponding package):
        pip install openinference-instrumentation-langchain
        pip install openinference-instrumentation-openai
        pip install openinference-instrumentation-anthropic
        pip install openinference-instrumentation-llama-index
        pip install openinference-instrumentation-crewai
        pip install openinference-instrumentation-dspy
        pip install openinference-instrumentation-haystack
        pip install openinference-instrumentation-bedrock
        pip install openinference-instrumentation-mistralai
        pip install openinference-instrumentation-autogen
    """
    if not HAS_OTEL:
        return

    _setup_otel()

    instrumentors = {
        'langchain': 'openinference.instrumentation.langchain',
        'openai': 'openinference.instrumentation.openai',
        'anthropic': 'openinference.instrumentation.anthropic',
        'llama_index': 'openinference.instrumentation.llama_index',
        'crewai': 'openinference.instrumentation.crewai',
        'dspy': 'openinference.instrumentation.dspy',
        'haystack': 'openinference.instrumentation.haystack',
        'bedrock': 'openinference.instrumentation.bedrock',
        'mistralai': 'openinference.instrumentation.mistralai',
        'autogen': 'openinference.instrumentation.autogen',
    }

    if frameworks is None:
        frameworks = list(instrumentors.keys())

    for framework in frameworks:
        module_path = instrumentors.get(framework)
        if not module_path:
            continue

        try:
            module = __import__(module_path, fromlist=['Instrumentor'])
            instrumentor_class = None
            for attr_name in dir(module):
                obj = getattr(module, attr_name)
                if (isinstance(obj, type) and
                    attr_name.endswith('Instrumentor') and
                    attr_name != 'Instrumentor'):
                    instrumentor_class = obj
                    break

            if instrumentor_class:
                instrumentor_class().instrument()
        except ImportError:
            pass
        except Exception:
            pass


def trace(func=None, *, name=None):
    """
    Manual decorator for custom functions not covered
    by auto-instrumentation.

    Usage:
        @clientcoded.trace
        def my_custom_tool(query):
            return db.execute(query)
    """
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            tool_name = name or fn.__name__

            if HAS_OTEL:
                tracer = otel_trace.get_tracer('clientcoded')
                with tracer.start_as_current_span(tool_name) as span:
                    span.set_attribute('openinference.span.kind', 'TOOL')
                    span.set_attribute('input.value', _truncate_value((args, kwargs), str(args) + str(kwargs)))
                    try:
                        result = fn(*args, **kwargs)
                        span.set_attribute('output.value', _truncate_value(result))
                        return result
                    except Exception as e:
                        span.set_status(otel_trace.StatusCode.ERROR, str(e))
                        raise
            else:
                trace_id = str(uuid.uuid4())
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
                    _send_direct_trace(
                        trace_id=trace_id,
                        tool_name=tool_name,
                        input_data=_truncate_value(args),
                        output_data=_truncate_value(result) if result else None,
                        error=error,
                        latency_ms=int((time.time() - start) * 1000)
                    )
        return wrapper

    if func is not None:
        return decorator(func)
    return decorator


def trace_async(func=None, *, name=None):
    """Async version of @trace."""
    def decorator(fn):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            tool_name = name or fn.__name__

            if HAS_OTEL:
                tracer = otel_trace.get_tracer('clientcoded')
                with tracer.start_as_current_span(tool_name) as span:
                    span.set_attribute('openinference.span.kind', 'TOOL')
                    span.set_attribute('input.value', _truncate_value((args, kwargs), str(args) + str(kwargs)))
                    try:
                        result = await fn(*args, **kwargs)
                        span.set_attribute('output.value', _truncate_value(result))
                        return result
                    except Exception as e:
                        span.set_status(otel_trace.StatusCode.ERROR, str(e))
                        raise
            else:
                trace_id = str(uuid.uuid4())
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
                    _send_direct_trace(
                        trace_id=trace_id,
                        tool_name=tool_name,
                        input_data=_truncate_value(args),
                        output_data=_truncate_value(result) if result else None,
                        error=error,
                        latency_ms=int((time.time() - start) * 1000)
                    )
        return wrapper

    if func is not None:
        return decorator(func)
    return decorator


def log_trace(tool_name, input_data=None, output_data=None, error=None, latency_ms=None):
    """Manual trace logging."""
    if HAS_OTEL:
        tracer = otel_trace.get_tracer('clientcoded')
        with tracer.start_as_current_span(tool_name) as span:
            span.set_attribute('openinference.span.kind', 'TOOL')
            if input_data:
                span.set_attribute('input.value', _truncate_value(input_data))
            if output_data:
                span.set_attribute('output.value', _truncate_value(output_data))
            if error:
                span.set_status(otel_trace.StatusCode.ERROR, error)
    else:
        _send_direct_trace(
            trace_id=str(uuid.uuid4()),
            tool_name=tool_name,
            input_data=_truncate_value(input_data) if input_data else None,
            output_data=_truncate_value(output_data) if output_data else None,
            error=error,
            latency_ms=latency_ms
        )


# ============================================================
# Internal
# ============================================================

_otel_initialized = False


def _setup_otel():
    """Initialize OpenTelemetry with ClientCoded exporter."""
    global _otel_initialized
    if _otel_initialized:
        return

    provider = TracerProvider()
    exporter = ClientCodedExporter(
        agent_id=_config['agent_id'],
        api_key=_config['api_key']
    )
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    otel_trace.set_tracer_provider(provider)
    _otel_initialized = True


def _send_direct_trace(trace_id, tool_name, input_data, output_data, error, latency_ms):
    """Fallback when OpenTelemetry is not installed."""
    if not _config.get('agent_id') or not _config.get('api_key'):
        return

    payload = {
        'agent_id': _config['agent_id'],
        'api_key': _config['api_key'],
        'trace_id': trace_id,
        'parent_trace_id': None,
        'conversation_id': _conversation_id.get(),
        'turn': _turn.get(),
        'tool_name': tool_name,
        'input': _truncate(input_data) if input_data else None,
        'output': _truncate(output_data) if output_data else None,
        'error': error,
        'latency_ms': latency_ms,
        'timestamp': time.time(),
        'test_run_id': (get_test_context() or {}).get('test_run_id'),
        'question_index': (get_test_context() or {}).get('question_index'),
    }

    thread = threading.Thread(
        target=_post_trace,
        args=(CLIENTCODED_API, payload),
        daemon=True
    )
    thread.start()


def _post_trace(endpoint, payload):
    """Send trace to ClientCoded. Never raises into the customer's agent.
    Logs one warning per rejection reason (bad key, wrong agent, rate
    limit) so a misconfiguration is visible instead of silent."""
    try:
        r = requests.post(endpoint, json=payload, timeout=5)
        if r.status_code >= 400:
            try:
                msg = r.json().get('message') or r.text[:200]
            except Exception:
                msg = r.text[:200]
            key = (r.status_code, msg)
            if key not in _warned:
                _warned.add(key)
                _log.warning("ClientCoded rejected a trace (%s): %s", r.status_code, msg)
    except Exception:
        pass


MAX_TRACE_CHARS = 2000


def _describe(obj):
    """Short size summary for lists/results, e.g. '128 rows'."""
    try:
        if isinstance(obj, (list, tuple, set)):
            return "{:,} items".format(len(obj))
        if isinstance(obj, dict):
            if isinstance(obj.get('rows_returned'), int):
                return "{:,} rows".format(obj['rows_returned'])
            for k in ('result', 'results', 'rows', 'data', 'items', 'records', 'documents'):
                v = obj.get(k)
                if isinstance(v, (list, tuple)):
                    return "{:,} {}".format(len(v), 'rows' if k in ('result', 'results', 'rows', 'records') else 'items')
    except Exception:
        pass
    return None


def _truncate_value(obj, text=None, max_len=MAX_TRACE_CHARS):
    """str() an object for a trace, cutting it to max_len and saying how
    big it really was (characters, and items/rows for lists and results)."""
    s = text if text is not None else str(obj)
    if not s or len(s) <= max_len:
        return s
    desc = _describe(obj)
    if desc is None and isinstance(obj, tuple) and obj:
        # (args, kwargs) or args: describe the first list-like argument
        for part in obj:
            desc = _describe(part)
            if desc:
                break
    note = "%s characters" % format(len(s), ',')
    if desc:
        note += ", " + desc
    return s[:max_len] + "... (truncated: " + note + ")"


def _truncate(s, max_len=MAX_TRACE_CHARS):
    if not s:
        return s
    if len(s) > max_len:
        if '... (truncated' in s[max_len - 120:]:
            return s  # already shortened with a size note; keep the note
        return s[:max_len] + "... (truncated: %s characters)" % format(len(s), ',')
    return s
