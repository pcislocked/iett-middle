"""Shared type-coercion helpers used by ARAC client and router."""

from __future__ import annotations

from typing import Any


def _as_text(value: Any) -> str | None:
    """Stringify *value*, strip whitespace, and return ``None`` for blank strings."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _to_int(value: Any) -> int | None:
    """Coerce *value* to ``int`` via float, returning ``None`` on failure."""
    try:
        if value is None:
            return None
        return int(float(value))
    except (TypeError, ValueError, OverflowError):
        return None


def _to_float(value: Any) -> float | None:
    """Coerce *value* to ``float``, returning ``None`` on failure."""
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _to_bool(value: Any) -> bool | None:
    """Coerce *value* to ``bool``, returning ``None`` for unrecognized input."""
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "y"}:
            return True
        if lowered in {"false", "0", "no", "n"}:
            return False
    return None
