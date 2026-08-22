# ClientCoded Python SDK

Trace your AI agent's tool calls for root cause analysis when failures occur.

## Install

```bash
pip install clientcoded
```

## Quick Start

```python
import clientcoded

# Configure once at startup
clientcoded.configure(
    agent_id="your-agent-id",
    api_key="your-api-key"
)

# Decorate any function your agent calls
@clientcoded.trace
def get_invoice(invoice_id):
    return stripe.Invoice.retrieve(invoice_id)

@clientcoded.trace
def search_knowledge_base(query):
    return pinecone.query(query)

@clientcoded.trace
def update_crm(contact_id, data):
    return hubspot.update_contact(contact_id, data)
```

## Conversation Tracking

```python
# At the start of each conversation
clientcoded.set_conversation_id("conv-123")

# At each turn
clientcoded.set_turn(1)
response = agent.handle_message("What is my invoice total?")

clientcoded.set_turn(2)
response = agent.handle_message("Can you break that down by line item?")
```

## Async Support

```python
@clientcoded.trace_async
async def get_invoice(invoice_id):
    return await stripe.Invoice.aretrieve(invoice_id)
```

## Custom Names

```python
@clientcoded.trace(name="stripe_invoice_lookup")
def get_invoice(invoice_id):
    return stripe.Invoice.retrieve(invoice_id)
```

## Manual Logging

For complex operations where decorators don't fit:

```python
import time

start = time.time()
try:
    result = complex_multi_step_operation()
    clientcoded.log_trace(
        "complex_operation",
        input_data={"step": "final"},
        output_data=result,
        latency_ms=int((time.time() - start) * 1000)
    )
except Exception as e:
    clientcoded.log_trace(
        "complex_operation",
        input_data={"step": "final"},
        error=str(e),
        latency_ms=int((time.time() - start) * 1000)
    )
    raise
```

## LangChain Integration

```python
from langchain.tools import tool
import clientcoded

clientcoded.configure(agent_id="your-agent-id", api_key="your-api-key")

@tool
@clientcoded.trace
def search_database(query: str) -> str:
    """Search the company database."""
    return db.execute(query)
```

## LlamaIndex Integration

```python
from llama_index.core.tools import FunctionTool
import clientcoded

clientcoded.configure(agent_id="your-agent-id", api_key="your-api-key")

@clientcoded.trace
def query_index(question: str) -> str:
    return index.query(question)

tool = FunctionTool.from_defaults(fn=query_index)
```

## What Gets Traced

For each decorated function call, the SDK logs:
- Function name (or custom name)
- Input arguments (truncated to 2000 chars)
- Output (truncated to 2000 chars)
- Error message if the function threw
- Latency in milliseconds
- Conversation ID and turn number (if set)

## What Doesn't Get Traced

- The SDK never captures environment variables
- The SDK never captures file contents
- Large inputs/outputs are truncated, not stored in full
- All trace sends are fire-and-forget with a 2-second timeout

## Safety

- The SDK will **never** break your agent. Every trace send is wrapped in try/catch.
- Trace sends happen in background threads. Zero impact on response latency.
- If the ClientCoded API is down, traces are silently dropped. Your agent continues normally.
- No sensitive data filtering in v0.1. Do not decorate functions that handle passwords, tokens, or PII directly. Wrap them in a function that sanitizes first.

## Configuration

| Environment Variable | Default | Description |
|---|---|---|
| CLIENTCODED_TRACE_URL | https://clientcoded.app.n8n.cloud/webhook/ap-35-trace-ingest | Custom trace endpoint |
