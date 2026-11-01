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
        chunks: list[bytes] = []
        total_bytes = 0
        read_chunk = getattr(resp.content, "readany", None)
        if not callable(read_chunk):
            read_chunk = getattr(resp.content, "read", None)

        while True:
            chunk = await read_chunk() if callable(read_chunk) else b""
            if not chunk:
                break
            total_bytes += len(chunk)
            if total_bytes > max_bytes:
                raise ResponseTooLargeError(
                    f"Upstream response body exceeded maximum allowed size ({max_bytes} bytes)"
                )
            chunks.append(chunk)

        raw = b"".join(chunks)
        charset = getattr(resp, "charset", None) or "utf-8"
        return raw.decode(charset, errors="replace")

    # Fallback for unit-test mocks or custom response wrappers
    if hasattr(resp, "text") and callable(resp.text):
        res = resp.text()
        text: Any = await res if inspect.isawaitable(res) else res
        if not isinstance(text, str):
            return "" if hasattr(text, "_mock_return_value") else str(text)
        if len(text.encode("utf-8", errors="replace")) > max_bytes:
            raise ResponseTooLargeError(
                f"Upstream response text exceeded maximum allowed size ({max_bytes} bytes)"
            )
        return text

    content = getattr(resp, "content", None)
    if content is not None:
        read_chunk = getattr(content, "readany", None)
        if not callable(read_chunk):
            read_chunk = getattr(content, "read", None)
        if callable(read_chunk):
            chunks: list[bytes] = []
            total_bytes = 0
            while True:
                res = read_chunk()
                chunk_b = await res if inspect.isawaitable(res) else res
                if not isinstance(chunk_b, (bytes, bytearray)):
                    break
                if not chunk_b:
                    break
                total_bytes += len(chunk_b)
                if total_bytes > max_bytes:
                    raise ResponseTooLargeError(
                        f"Upstream response body exceeded maximum allowed size ({max_bytes} bytes)"
                    )
                chunks.append(chunk_b)
            raw_b = b"".join(chunks)
            charset = getattr(resp, "charset", None) or "utf-8"
            return raw_b.decode(charset, errors="replace")

    raise TypeError(f"Unsupported response type for read_bounded_text: {type(resp)}")
