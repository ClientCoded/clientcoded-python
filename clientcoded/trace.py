# ClientCoded Python SDK v0.2.0
# pip install clientcoded

import requests
import functools
import time
import uuid
import threading
import os
import contextvars

CLIENTCODED_API = os.environ.get(
    "CLIENTCODED_TRACE_URL",
    "https://clientcoded.app.n8n.cloud/webhook/ap-35-trace-ingest"
)

_config = {
    'agent_id': None,
    'api_key': None,
}

_conversation_id = contextvars.ContextVar('conversation_id', default=None)
_turn = contextvars.ContextVar('turn', default=None)
_parent_trace_id = contextvars.ContextVar('parent_trace_id', default=None)


def configure(agent_id, api_key, trace_url=None):
    """
    Configure the SDK globally.

    Args:
        agent_id: Your agent's ID from ClientCoded dashboard
        api_key: Your API key from ClientCoded dashboard
        trace_url: Optional custom trace endpoint URL
    """
    _config['agent_id'] = agent_id
    _config['api_key'] = api_key
    if trace_url:
        global CLIENTCODED_API
        CLIENTCODED_API = trace_url


def set_conversation_id(conversation_id):
    """Call this at the start of each conversation."""
    _conversation_id.set(conversation_id)
    _turn.set(0)


def set_turn(turn_number):
    """Call this at the start of each turn."""
    _turn.set(turn_number)


def increment_turn():
    """Increment the turn counter by 1."""
    current = _turn.get() or 0
    _turn.set(current + 1)


def trace(func=None, *, name=None):
    """
    Decorator that traces a function call with parent-child nesting.

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
            parent_id = _parent_trace_id.get()

            # Set this trace as the parent for any nested calls
            token = _parent_trace_id.set(trace_id)

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
                # Restore parent context
                _parent_trace_id.reset(token)

                _send_trace(
                    trace_id=trace_id,
                    parent_trace_id=parent_id,
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
    Decorator that traces an async function call with parent-child nesting.

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
            parent_id = _parent_trace_id.get()

            token = _parent_trace_id.set(trace_id)

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
                _parent_trace_id.reset(token)

                _send_trace(
                    trace_id=trace_id,
                    parent_trace_id=parent_id,
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
    """
    _send_trace(
        trace_id=str(uuid.uuid4()),
        parent_trace_id=_parent_trace_id.get(),
        tool_name=tool_name,
        args=(),
        kwargs=input_data or {},
        result=output_data,
        error=error,
        latency_ms=latency_ms
    )


# ============================================================
# Auto-Instrumentation
# ============================================================

_instrumented = set()


def auto_instrument(frameworks=None):
    """
    Automatically instrument supported frameworks.

    Usage:
        import clientcoded
        clientcoded.configure(agent_id="xxx", api_key="xxx")
        clientcoded.auto_instrument(frameworks=["langchain", "openai"])

    Supported frameworks:
        - "langchain": Traces all LangChain tool calls, LLM calls, and chain runs
        - "openai": Traces all OpenAI API calls
        - "anthropic": Traces all Anthropic API calls

    If frameworks is None, attempts to instrument all available frameworks.
    """
    if frameworks is None:
        frameworks = ["langchain", "openai", "anthropic"]

    for framework in frameworks:
        if framework in _instrumented:
            continue

        if framework == "langchain":
            _instrument_langchain()
        elif framework == "openai":
            _instrument_openai()
        elif framework == "anthropic":
            _instrument_anthropic()

        _instrumented.add(framework)


def _instrument_langchain():
    """Auto-instrument LangChain tool calls, LLM calls, and chain runs."""
    try:
        # Instrument BaseTool.invoke
        from langchain_core.tools import BaseTool
        _original_tool_invoke = BaseTool.invoke

        def _traced_tool_invoke(self, input, config=None, **kwargs):
            trace_id = str(uuid.uuid4())
            parent_id = _parent_trace_id.get()
            token = _parent_trace_id.set(trace_id)
            start = time.time()
            error = None
            result = None
            try:
                result = _original_tool_invoke(self, input, config, **kwargs)
                return result
            except Exception as e:
                error = str(e)
                raise
            finally:
                _parent_trace_id.reset(token)
                _send_trace(
                    trace_id=trace_id,
                    parent_trace_id=parent_id,
                    tool_name="langchain.tool." + (self.name or self.__class__.__name__),
                    args=(input,),
                    kwargs=kwargs,
                    result=result,
                    error=error,
                    latency_ms=int((time.time() - start) * 1000)
                )

        BaseTool.invoke = _traced_tool_invoke

        # Instrument BaseTool.ainvoke
        _original_tool_ainvoke = BaseTool.ainvoke

        async def _traced_tool_ainvoke(self, input, config=None, **kwargs):
            trace_id = str(uuid.uuid4())
            parent_id = _parent_trace_id.get()
            token = _parent_trace_id.set(trace_id)
            start = time.time()
            error = None
            result = None
            try:
                result = await _original_tool_ainvoke(self, input, config, **kwargs)
                return result
            except Exception as e:
                error = str(e)
                raise
            finally:
                _parent_trace_id.reset(token)
                _send_trace(
                    trace_id=trace_id,
                    parent_trace_id=parent_id,
                    tool_name="langchain.tool." + (self.name or self.__class__.__name__),
                    args=(input,),
                    kwargs=kwargs,
                    result=result,
                    error=error,
                    latency_ms=int((time.time() - start) * 1000)
                )

        BaseTool.ainvoke = _traced_tool_ainvoke

        # Instrument BaseChatModel.invoke
        try:
            from langchain_core.language_models.chat_models import BaseChatModel
            _original_llm_invoke = BaseChatModel.invoke

            def _traced_llm_invoke(self, input, config=None, **kwargs):
                trace_id = str(uuid.uuid4())
                parent_id = _parent_trace_id.get()
                token = _parent_trace_id.set(trace_id)
                start = time.time()
                error = None
                result = None
                try:
                    result = _original_llm_invoke(self, input, config, **kwargs)
                    return result
                except Exception as e:
                    error = str(e)
                    raise
                finally:
                    _parent_trace_id.reset(token)
                    _send_trace(
                        trace_id=trace_id,
                        parent_trace_id=parent_id,
                        tool_name="langchain.llm." + self.__class__.__name__,
                        args=(),
                        kwargs={"input_preview": str(input)[:500]},
                        result=str(result)[:500] if result else None,
                        error=error,
                        latency_ms=int((time.time() - start) * 1000)
                    )

            BaseChatModel.invoke = _traced_llm_invoke
        except ImportError:
            pass

    except ImportError:
        pass


def _instrument_openai():
    """Auto-instrument OpenAI API calls."""
    try:
        import openai

        # Instrument chat completions
        if hasattr(openai, 'OpenAI'):
            _original_create = openai.resources.chat.completions.Completions.create

            def _traced_create(self, *args, **kwargs):
                trace_id = str(uuid.uuid4())
                parent_id = _parent_trace_id.get()
                token = _parent_trace_id.set(trace_id)
                start = time.time()
                error = None
                result = None
                try:
                    result = _original_create(self, *args, **kwargs)
                    return result
                except Exception as e:
                    error = str(e)
                    raise
                finally:
                    _parent_trace_id.reset(token)

                    output = None
                    input_tokens = None
                    output_tokens = None
                    if result and hasattr(result, 'choices') and result.choices:
                        output = result.choices[0].message.content[:500] if result.choices[0].message.content else None
                    if result and hasattr(result, 'usage') and result.usage:
                        input_tokens = result.usage.prompt_tokens
                        output_tokens = result.usage.completion_tokens

                    _send_trace(
                        trace_id=trace_id,
                        parent_trace_id=parent_id,
                        tool_name="openai.chat.completions",
                        args=(),
                        kwargs={
                            "model": kwargs.get("model", "unknown"),
                            "messages_count": len(kwargs.get("messages", [])),
                        },
                        result=output,
                        error=error,
                        latency_ms=int((time.time() - start) * 1000),
                        extra={
                            "input_tokens": input_tokens,
                            "output_tokens": output_tokens,
                        }
                    )

            openai.resources.chat.completions.Completions.create = _traced_create

    except ImportError:
        pass


def _instrument_anthropic():
    """Auto-instrument Anthropic API calls."""
    try:
        import anthropic

        if hasattr(anthropic, 'Anthropic'):
            _original_create = anthropic.resources.messages.Messages.create

            def _traced_create(self, *args, **kwargs):
                trace_id = str(uuid.uuid4())
                parent_id = _parent_trace_id.get()
                token = _parent_trace_id.set(trace_id)
                start = time.time()
                error = None
                result = None
                try:
                    result = _original_create(self, *args, **kwargs)
                    return result
                except Exception as e:
                    error = str(e)
                    raise
                finally:
                    _parent_trace_id.reset(token)

                    output = None
                    input_tokens = None
                    output_tokens = None
                    if result and hasattr(result, 'content') and result.content:
                        text_blocks = [b.text for b in result.content if hasattr(b, 'text')]
                        output = ' '.join(text_blocks)[:500]
                    if result and hasattr(result, 'usage'):
                        input_tokens = getattr(result.usage, 'input_tokens', None)
                        output_tokens = getattr(result.usage, 'output_tokens', None)

                    _send_trace(
                        trace_id=trace_id,
                        parent_trace_id=parent_id,
                        tool_name="anthropic.messages",
                        args=(),
                        kwargs={
                            "model": kwargs.get("model", "unknown"),
                            "messages_count": len(kwargs.get("messages", [])),
                        },
                        result=output,
                        error=error,
                        latency_ms=int((time.time() - start) * 1000),
                        extra={
                            "input_tokens": input_tokens,
                            "output_tokens": output_tokens,
                        }
                    )

            anthropic.resources.messages.Messages.create = _traced_create

    except ImportError:
        pass


# ============================================================
# Internal functions
# ============================================================

def _send_trace(trace_id, parent_trace_id, tool_name, args, kwargs, result, error, latency_ms, extra=None):
    """Fire-and-forget trace send. Never breaks the customer's agent."""
    agent_id = _config.get('agent_id')
    api_key = _config.get('api_key')

    if not agent_id or not api_key:
        return

    payload = {
        "agent_id": agent_id,
        "api_key": api_key,
        "trace_id": trace_id,
        "parent_trace_id": parent_trace_id,
        "conversation_id": _conversation_id.get(),
        "turn": _turn.get(),
        "tool_name": tool_name,
        "input": _safe_serialize_input(args, kwargs),
        "output": _safe_serialize_output(result),
        "error": error,
        "latency_ms": latency_ms,
        "timestamp": time.time()
    }

    if extra:
        payload["extra"] = extra

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
