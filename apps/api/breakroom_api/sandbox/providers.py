"""Bounded text-only provider calls. Keys and endpoints never enter the guest."""
import asyncio
import httpx

from .sources import bounded_json


class ModelError(RuntimeError):
    pass


def generate(provider, model, key, prompt, max_output_tokens, *, transport=None, timeout_seconds=30):
    return asyncio.run(_generate(provider, model, key, prompt, max_output_tokens, transport=transport, timeout_seconds=timeout_seconds))


async def _generate(provider, model, key, prompt, max_output_tokens, *, transport, timeout_seconds):
    if not isinstance(prompt, str) or not 1 <= len(prompt.encode()) <= 16384:
        raise ModelError("Model prompt must contain 1..16384 bytes")
    if type(max_output_tokens) is not int or not 1 <= max_output_tokens <= 2048:
        raise ModelError("Model output limit must be 1..2048 tokens")
    if provider == "openai":
        url = "https://api.openai.com/v1/responses"
        headers = {"Authorization": "Bearer " + key}
        body = {"model": model, "input": prompt, "max_output_tokens": max_output_tokens, "store": False}
    elif provider == "anthropic":
        url = "https://api.anthropic.com/v1/messages"
        headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
        body = {"model": model, "messages": [{"role": "user", "content": prompt}], "max_tokens": max_output_tokens}
    else:
        raise ModelError("Model provider is not approved")
    try:
        async with asyncio.timeout(min(30, max(.01, timeout_seconds))), httpx.AsyncClient(timeout=min(30, max(.01, timeout_seconds)), follow_redirects=False, trust_env=False, transport=transport) as client:
            async with client.stream("POST", url, headers=headers, json=body) as response:
                if response.status_code != 200:
                    raise ModelError("Model provider rejected or could not complete the request")
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 262144:
                        raise ModelError("Model response exceeds its size limit")
                value = bounded_json(bytes(raw), limit=262144)
        if provider == "openai":
            chunks = [part["text"] for item in value["output"] if item.get("type") == "message"
                      for part in item.get("content", []) if part.get("type") == "output_text"]
        else:
            chunks = [item["text"] for item in value["content"] if item.get("type") == "text"]
        answer = "\n".join(chunks)
        if not answer or len(answer.encode()) > 32768:
            raise ModelError("Model returned no bounded text output")
        usage = value.get("usage", {})
        usage = {key: usage.get(key) if type(usage.get(key)) is int and 0 <= usage[key] <= 10000000 else None
                 for key in ("input_tokens", "output_tokens")}
        return {"text": answer, "usage": usage}
    except (TimeoutError, httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise ModelError("Model provider response unavailable or invalid") from exc
