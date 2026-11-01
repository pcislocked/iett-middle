"""Bounded HTTP response readers to prevent memory exhaustion (OOM DoS)."""

from __future__ import annotations

import inspect
from typing import Any

import aiohttp

# 25 MB ceiling: comfortably fits the largest valid IETT all-fleet (~8 MB)
# and all-stops (~6 MB) SOAP XML responses while blocking rogue/runaway streams.
MAX_UPSTREAM_RESPONSE_BYTES = 25 * 1024 * 1024


class ResponseTooLargeError(ValueError):
    """Raised when an upstream HTTP response exceeds the maximum allowed byte size."""


async def read_bounded_text(
    resp: Any,
    max_bytes: int = MAX_UPSTREAM_RESPONSE_BYTES,
) -> str:
    """Read response text with a strict upper byte limit.

    Compatible with both real ``aiohttp.ClientResponse`` objects (streaming
    via ``resp.content.read``) and lightweight unit-test ``Mock`` objects
    that only mock ``resp.text()``.
    """
    content_length = getattr(resp, "content_length", None)
    if isinstance(content_length, int) and content_length > max_bytes:
        raise ResponseTooLargeError(
            f"Upstream response Content-Length ({content_length} bytes) exceeds limit ({max_bytes} bytes)"
        )

    if isinstance(resp, aiohttp.ClientResponse):
        raw: bytes = await resp.content.read(max_bytes + 1)
        if len(raw) > max_bytes:
            raise ResponseTooLargeError(
                f"Upstream response body exceeded maximum allowed size ({max_bytes} bytes)"
            )
        charset = getattr(resp, "charset", None) or "utf-8"
        return raw.decode(charset, errors="replace")

    # Fallback for unit-test mocks or custom response wrappers
    if hasattr(resp, "text") and callable(resp.text):
        res = resp.text()
        text: str = await res if inspect.isawaitable(res) else res
        if len(text.encode("utf-8", errors="replace")) > max_bytes:
            raise ResponseTooLargeError(
                f"Upstream response text exceeded maximum allowed size ({max_bytes} bytes)"
            )
        return text

    content = getattr(resp, "content", None)
    if content is not None and hasattr(content, "read") and callable(content.read):
        res = content.read(max_bytes + 1)
        raw_b: bytes = await res if inspect.isawaitable(res) else res
        if len(raw_b) > max_bytes:
            raise ResponseTooLargeError(
                f"Upstream response body exceeded maximum allowed size ({max_bytes} bytes)"
            )
        charset = getattr(resp, "charset", None) or "utf-8"
        return raw_b.decode(charset, errors="replace")

    raise TypeError(f"Unsupported response type for read_bounded_text: {type(resp)}")
