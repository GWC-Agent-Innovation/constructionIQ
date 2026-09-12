"""Stream Ollama Cloud responses with cancellable, bounded request lifetimes."""
from __future__ import annotations

import asyncio
import json
import os
import re
import time

import httpx
import ollama


class CloudStreamError(RuntimeError):
    """Provider error delivered after the HTTP response has already started."""

    def __init__(self, detail: str):
        super().__init__(f"Ollama Cloud stream failed: {detail}")
        # Account/configuration errors will not improve on an immediate retry.
        self.retryable = not any(term in detail.lower() for term in (
            "unauthorized", "forbidden", "api key", "authentication", "quota",
            "rate limit", "too many requests", "usage limit", "not found",
            "not supported", "does not support", "unsupported", "invalid",
            "context length", "context window", "too long", "billing",
        ))


def _error_detail(value, api_key: str) -> str:
    """Surface only a bounded error field, with credentials and controls removed."""
    if isinstance(value, dict):
        value = value.get("message")
    if not isinstance(value, str) or not value.strip():
        return "The provider supplied no error details."
    value = value.replace(api_key, "[redacted]")
    value = re.sub(r"(?i)bearer\s+[^\s,;]+", "Bearer [redacted]", value)
    value = re.sub(r"[\x00-\x1f\x7f]", " ", value)
    return " ".join(value.split())[:400]


async def _stream_chat(payload: dict) -> dict:
    api_key = os.environ.get("OLLAMA_API_KEY")
    if not api_key:
        raise RuntimeError("OLLAMA_API_KEY is not set (see .env.example)")
    idle_timeout = float(os.environ.get("OLLAMA_READ_TIMEOUT_SECONDS", "45"))
    # One retry for transient server errors shares the caller's deadline.
    async with httpx.AsyncClient(
        base_url="https://ollama.com", headers={"Authorization": f"Bearer {api_key}"},
        timeout=httpx.Timeout(idle_timeout, connect=10),
    ) as client:
        for attempt in range(2):
            try:
                async with client.stream("POST", "/api/chat", json=payload) as response:
                    if response.is_error:
                        await response.aread()
                        # Read only the provider's error field, never its full body.
                        try:
                            body = response.json()
                            detail = _error_detail(body.get("error") if isinstance(body, dict) else None, api_key)
                        except ValueError:
                            detail = "The provider supplied no error details."
                        raise ollama.ResponseError(
                            f"Ollama Cloud returned HTTP {response.status_code}: {detail}", response.status_code)
                    message = {"role": "assistant", "content": "", "thinking": "", "tool_calls": []}
                    started = last_progress = time.monotonic()
                    async for line in response.aiter_lines():
                        if not line.strip():
                            continue
                        chunk = json.loads(line)
                        if chunk.get("error"):
                            raise CloudStreamError(_error_detail(chunk["error"], api_key))
                        part = chunk.get("message", {})
                        for field in ("content", "thinking"):
                            message[field] += part.get(field) or ""
                        message["tool_calls"].extend(part.get("tool_calls") or [])
                        now = time.monotonic()
                        if now - last_progress >= 15:
                            print(f"    [llm] receiving response ({now - started:.0f}s)", flush=True)
                            last_progress = now
                        if chunk.get("done"):
                            if chunk.get("done_reason") == "length":
                                raise RuntimeError("The model reached its output limit before completing the plan.")
                            return {**chunk, "message": message}
                    raise RuntimeError("Ollama Cloud closed the response before it completed.")
            except (ollama.ResponseError, CloudStreamError, httpx.ConnectTimeout, httpx.ConnectError) as exc:
                if isinstance(exc, CloudStreamError):
                    retryable = exc.retryable
                elif isinstance(exc, ollama.ResponseError):
                    retryable = exc.status_code >= 500
                else:
                    # Failing to even open the connection (vs. a read timeout
                    # after the model started responding) is as transient as a
                    # 5xx - worth the same single retry, not a wasted request.
                    retryable = True
                if not retryable or attempt == 1:
                    raise
                print(f"    [llm] {exc}; retrying once within the remaining request budget", flush=True)
                await asyncio.sleep(1)


async def _bounded_chat(payload: dict, timeout_seconds: float) -> dict:
    try:
        return await asyncio.wait_for(_stream_chat(payload), timeout=timeout_seconds)
    except TimeoutError as exc:
        raise TimeoutError(f"Ollama Cloud exceeded the {timeout_seconds:.0f}s request budget. Please retry shortly.") from exc
    except httpx.TimeoutException as exc:
        raise TimeoutError("Ollama Cloud stopped responding within the connection/read timeout. Please retry shortly.") from exc
    except httpx.TransportError as exc:
        raise RuntimeError("Could not reach Ollama Cloud. Check the network connection and retry.") from exc


def chat(messages: list[dict], tools: list[dict] | None = None, model: str | None = None,
         options: dict | None = None, *, timeout_seconds: float | None = None,
         think: str | None = None):
    # Resolve at call time: backend imports can precede dotenv loading.
    model = (model or os.environ.get("OLLAMA_MODEL", "gpt-oss:120b")).removesuffix("-cloud")
    payload = {
        "model": model, "messages": messages, "stream": True,
        "options": {"num_ctx": int(os.environ.get("OLLAMA_NUM_CTX", "8192")),
                    "num_predict": int(os.environ.get("OLLAMA_NUM_PREDICT", "8192")),
                    **(options or {})},
    }
    if tools:
        payload["tools"] = tools
    if model.startswith("gpt-oss"):
        payload["think"] = think or os.environ.get("OLLAMA_THINK", "low")
    budget = float(os.environ.get("OLLAMA_REQUEST_TIMEOUT_SECONDS", "90"))
    if timeout_seconds is not None:
        budget = min(budget, timeout_seconds)
    if budget <= 0:
        raise TimeoutError("The planning time budget has expired.")
    started = time.monotonic()
    print(f"    [llm] {model}; request budget {budget:.0f}s", flush=True)
    try:
        # Synchronous callers run on the CLI or FastAPI's worker thread.
        return asyncio.run(_bounded_chat(payload, budget))
    finally:
        print(f"    [llm] request finished after {time.monotonic() - started:.1f}s", flush=True)
