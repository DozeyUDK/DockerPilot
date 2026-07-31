"""Deterministic canonical JSON and SHA-256 helpers."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def _canonicalize(obj: Any) -> Any:
    """Return a structure suitable for deterministic JSON encoding.

    - Object keys sorted lexicographically (UTF-8 codepoint order via str sort)
    - Arrays preserve order (semantic argv / port lists)
    - Numbers: JSON numbers as Python int/float; bool kept distinct from int
    - No NaN/Infinity (rejected)
    """
    if obj is None or isinstance(obj, bool):
        return obj
    if isinstance(obj, int) and not isinstance(obj, bool):
        return obj
    if isinstance(obj, float):
        if obj != obj or obj in (float("inf"), float("-inf")):
            raise ValueError("non-finite floats are not allowed in canonical JSON")
        return obj
    if isinstance(obj, str):
        return obj
    if isinstance(obj, dict):
        return {key: _canonicalize(obj[key]) for key in sorted(obj.keys())}
    if isinstance(obj, (list, tuple)):
        return [_canonicalize(item) for item in obj]
    raise TypeError(f"unsupported type for canonical JSON: {type(obj)!r}")


def canonical_json_bytes(obj: Any) -> bytes:
    """UTF-8 JSON with sorted keys, compact separators, no trailing newline."""
    canonical = _canonicalize(obj)
    return json.dumps(
        canonical,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    """Lowercase hex SHA-256 digest."""
    return hashlib.sha256(data).hexdigest()


def sha256_canonical(obj: Any) -> str:
    """SHA-256 of canonical JSON encoding of ``obj``."""
    return sha256_hex(canonical_json_bytes(obj))
