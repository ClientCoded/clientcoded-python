# ClientCoded Python SDK v0.3.0
# Uses OpenInference (open standard) for tracing
# pip install clientcoded

import os
import time
import uuid
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
    "https://clientcoded.app.n8n.cloud/webhook/ap-35-trace-ingest"
)

_config = {
    'agent_id': None,
    'api_key': None,
}

import contextvars
_conversation_id = contextvars.ContextVar('conversation_id', default=None)
_turn = contextvars.ContextVar('turn', default=None)

from .context import get_test_context


# ============================================================
# ClientCoded Span Exporter
# Converts OpenTelemetry spans to ClientCoded trace format
# and sends to AP-35
# ============================================================

class ClientCodedExporter(SpanExporter if HAS_OTEL else object):
    """Exports OpenTelemetry/OpenInference spans to ClientCoded AP-35."""

    def __init__(self, agent_id=None, api_key=None, endpoint=None):
        self.agent_id = agent_id or _config.get('agent_id')
        self.api_key = api_key or _config.get('api_key')
        self.endpoint = endpoint or CLIENTCODED_API

    def export(self, spans):
        for span in spans:
            try:
                attrs = dict(span.attributes) if span.attributes else {}

                # Extract OpenInference semantic attributes
                tool_name = (
                    attrs.get('openinference.span.kind', '') + '.' +
                    (span.name or 'unknown')
                ).strip('.')

                # Extract input/output from OpenInference conventions
                input_data = attrs.get('input.value', attrs.get('llm.input_messages', None))
                output_data = attrs.get('output.value', attrs.get('llm.output_messages', None))

                # Extract token counts
                input_tokens = attrs.get('llm.token_count.prompt', None)
                output_tokens = attrs.get('llm.token_count.completion', None)

                # Extract error
                error = None
                if span.status and span.status.status_code == otel_trace.StatusCode.ERROR:
                    error = span.status.description or 'Unknown error'
                for event in span.events or []:
                    if event.name == 'exception':
                        error = dict(event.attributes).get('exception.message', error)

                # Calculate latency
                latency_ms = None
                if span.start_time and span.end_time:
                    latency_ms = int((span.end_time - span.start_time) / 1_000_000)

                # Build parent trace ID
                parent_trace_id = None
                if span.parent and span.parent.span_id:
                    parent_trace_id = format(span.parent.span_id, '016x')

                payload = {
                    'agent_id': self.agent_id,
                    'api_key': self.api_key,
                    'trace_id': format(span.context.span_id, '016x'),
                    'parent_trace_id': parent_trace_id,
                    'conversation_id': _conversation_id.get(),
                    'turn': _turn.get(),
                    'tool_name': tool_name,
                    'input': _truncate(str(input_data)) if input_data else None,
                    'output': _truncate(str(output_data)) if output_data else None,
                    'error': error,
                    'latency_ms': latency_ms,
                    'timestamp': time.time(),
                    'test_run_id': get_test_context().get('test_run_id'),
                    'question_index': get_test_context().get('question_index'),
                    'extra': {
                        'input_tokens': input_tokens,
                        'output_tokens': output_tokens,
                        'model': attrs.get('llm.model_name', None),
                    }
                }

                # Fire and forget
                thread = threading.Thread(
                    target=_post_trace,
                    args=(self.endpoint, payload),
                    daemon=True
                )
                thread.start()

            except Exception:
                pass  # Never break the customer's agent

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
        return  # Silent fail

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

    Supported frameworks (install the corresponding package):
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
        # Try all available
        frameworks = list(instrumentors.keys())

    for framework in frameworks:
        module_path = instrumentors.get(framework)
        if not module_path:
            continue

        try:
            module = __import__(module_path, fromlist=['Instrumentor'])
            # OpenInference instrumentors follow the naming pattern:
            # LangChainInstrumentor, OpenAIInstrumentor, etc.
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
            pass  # Framework instrumentor not installed
        except Exception:
            pass  # Already instrumented or other error


def trace(func=None, *, name=None):
    """
    Manual decorator for custom functions not covered
    by auto-instrumentation.

    Usage:
        @clientcoded.trace
        def my_custom_tool(query):
            return db.execute(query)

        @clientcoded.trace(name="db_lookup")
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
                    span.set_attribute('input.value', _truncate(str(args) + str(kwargs)))
                    try:
                        result = fn(*args, **kwargs)
                        span.set_attribute('output.value', _truncate(str(result)))
                        return result
                    except Exception as e:
                        span.set_status(otel_trace.StatusCode.ERROR, str(e))
                        raise
            else:
                # Fallback: direct trace without OpenTelemetry
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
                        input_data=str(args),
                        output_data=str(result) if result else None,
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
                    span.set_attribute('input.value', _truncate(str(args) + str(kwargs)))
                    try:
                        result = await fn(*args, **kwargs)
                        span.set_attribute('output.value', _truncate(str(result)))
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
                        input_data=str(args),
                        output_data=str(result) if result else None,
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
                span.set_attribute('input.value', _truncate(str(input_data)))
            if output_data:
                span.set_attribute('output.value', _truncate(str(output_data)))
            if error:
                span.set_status(otel_trace.StatusCode.ERROR, error)
    else:
        _send_direct_trace(
            trace_id=str(uuid.uuid4()),
            tool_name=tool_name,
            input_data=str(input_data) if input_data else None,
            output_data=str(output_data) if output_data else None,
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
        'test_run_id': get_test_context().get('test_run_id'),
        'question_index': get_test_context().get('question_index'),
    }

    thread = threading.Thread(
        target=_post_trace,
        args=(CLIENTCODED_API, payload),
        daemon=True
    )
    thread.start()


def _post_trace(endpoint, payload):
    """Send trace to ClientCoded. Silent failure."""
    try:
        requests.post(endpoint, json=payload, timeout=2)
    except Exception:
        pass


def _truncate(s, max_len=2000):
    if not s:
        return s
    if len(s) > max_len:
        return s[:max_len] + '... (truncated)'
    return s
