"""Length-prefixed Unix-socket framing for broker protocol v1."""

from __future__ import annotations

import json
import re
import struct
from typing import Any, Dict, Tuple

from dockerpilot.secure_deploy.schemas import SchemaValidationError, load_schema

from .errors import ProtocolError

PROTOCOL_VERSION = 1
MAX_FRAME_BYTES = 2 * 1024 * 1024
_LENGTH_STRUCT = struct.Struct("!I")

SUPPORTED_OPERATIONS = frozenset({"ping", "capabilities", "verify_plan", "dry_run"})
FORBIDDEN_OPERATIONS = frozenset(
    {
        "apply",
        "deploy",
        "firewall_apply",
        "materialize_secrets",
        "rollback",
        "exec",
        "command",
        "shell",
    }
)
FORBIDDEN_FIELDS = frozenset({"command", "args", "argv", "path", "working_directory", "environment"})

# Error envelopes may echo a rejected operation name; keep it small and printable.
ERROR_OPERATION_MAX_LEN = 64
_ERROR_OPERATION_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
NEUTRAL_ERROR_OPERATION = "unknown"


def sanitize_error_operation(raw: Any) -> str:
    """Return a safe ``operation`` value for error envelopes.

    Preserves the caller-supplied name when it is a short ASCII identifier.
    Missing/non-string/overlong/binary-ish values become ``unknown`` — never
    fall back to ``ping`` (which falsely implies a ping failure).
    """
    if not isinstance(raw, str):
        return NEUTRAL_ERROR_OPERATION
    if not raw or len(raw) > ERROR_OPERATION_MAX_LEN:
        return NEUTRAL_ERROR_OPERATION
    if not _ERROR_OPERATION_RE.fullmatch(raw):
        return NEUTRAL_ERROR_OPERATION
    return raw

# Error envelopes may echo a rejected operation name; keep it small and printable.
ERROR_OPERATION_MAX_LEN = 64
_ERROR_OPERATION_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
NEUTRAL_ERROR_OPERATION = "unknown"


def encode_frame(obj: Dict[str, Any]) -> bytes:
    raw = json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    if len(raw) > MAX_FRAME_BYTES:
        raise ProtocolError("frame_too_large", "frame exceeds max size")
    return _LENGTH_STRUCT.pack(len(raw)) + raw


def decode_frame(buffer: bytes) -> Tuple[Dict[str, Any], bytes]:
    """Decode one length-prefixed JSON object; return (obj, remaining)."""
    if len(buffer) < 4:
        raise ProtocolError("truncated_frame", "incomplete length prefix")
    (length,) = _LENGTH_STRUCT.unpack_from(buffer, 0)
    if length > MAX_FRAME_BYTES:
        raise ProtocolError("frame_too_large", "declared frame length exceeds max")
    if length < 2:
        raise ProtocolError("invalid_length", "frame length too small")
    if len(buffer) < 4 + length:
        raise ProtocolError("truncated_frame", "incomplete frame body")
    body = buffer[4 : 4 + length]
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProtocolError("invalid_utf8", "frame is not valid UTF-8") from exc
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProtocolError("invalid_json", "frame is not valid JSON") from exc
    if not isinstance(obj, dict):
        raise ProtocolError("invalid_json", "frame JSON must be an object")
    return obj, buffer[4 + length :]


def read_exact(sock, nbytes: int, *, timeout: float | None = None) -> bytes:
    if timeout is not None:
        sock.settimeout(timeout)
    chunks = []
    remaining = nbytes
    while remaining > 0:
        chunk = sock.recv(remaining)
        if not chunk:
            raise ProtocolError("truncated_frame", "socket closed mid-frame")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def recv_message(sock, *, timeout: float = 15.0) -> Dict[str, Any]:
    header = read_exact(sock, 4, timeout=timeout)
    (length,) = _LENGTH_STRUCT.unpack(header)
    if length > MAX_FRAME_BYTES:
        raise ProtocolError("frame_too_large", "declared frame length exceeds max")
    if length < 2:
        raise ProtocolError("invalid_length", "frame length too small")
    body = read_exact(sock, length, timeout=timeout)
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProtocolError("invalid_utf8", "frame is not valid UTF-8") from exc
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProtocolError("invalid_json", "frame is not valid JSON") from exc
    if not isinstance(obj, dict):
        raise ProtocolError("invalid_json", "frame JSON must be an object")
    return obj


def send_message(sock, obj: Dict[str, Any]) -> None:
    sock.sendall(encode_frame(obj))


def validate_request(doc: Dict[str, Any]) -> Dict[str, Any]:
    for field in FORBIDDEN_FIELDS:
        if field in doc:
            raise ProtocolError("forbidden_field", f"field {field} is not allowed")
    op = doc.get("operation")
    if not isinstance(op, str):
        raise ProtocolError("invalid_operation", "operation must be a string")
    if op in FORBIDDEN_OPERATIONS:
        raise ProtocolError("broker_operation_not_supported", f"operation {op} is not supported")
    if doc.get("protocol_version") != PROTOCOL_VERSION:
        raise ProtocolError("unknown_protocol_version", "unsupported protocol_version")
    if op not in SUPPORTED_OPERATIONS:
        raise ProtocolError("broker_operation_not_supported", f"operation {op} is not supported")
    schema = load_schema("secure-deploy-broker-request-v1.schema.json")
    from dockerpilot.secure_deploy.schemas import _validate

    try:
        _validate(doc, schema, "$")
    except SchemaValidationError as exc:
        raise ProtocolError("invalid_request_schema", str(exc)) from exc
    return doc


def validate_response(doc: Dict[str, Any]) -> Dict[str, Any]:
    schema = load_schema("secure-deploy-broker-response-v1.schema.json")
    from dockerpilot.secure_deploy.schemas import _validate

    try:
        _validate(doc, schema, "$")
    except SchemaValidationError as exc:
        raise ProtocolError("invalid_response_schema", str(exc)) from exc
    return doc
