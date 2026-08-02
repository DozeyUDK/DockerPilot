"""Load and validate Secure Deploy JSON Schemas (stdlib only)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict


_SCHEMA_DIR_CANDIDATES = (
    Path(__file__).resolve().parent / "schemas",
    Path(__file__).resolve().parents[3] / "schemas",
)


class SchemaValidationError(ValueError):
    """Raised when a document fails schema or semantic validation."""

    def __init__(self, message: str, path: str = "$") -> None:
        self.path = path
        super().__init__(f"{path}: {message}")


def _schema_dir() -> Path:
    for candidate in _SCHEMA_DIR_CANDIDATES:
        if candidate.is_dir():
            return candidate
    raise FileNotFoundError("schemas/ directory not found relative to package")


def load_schema(name: str) -> Dict[str, Any]:
    """Load a schema JSON document by filename."""
    path = _schema_dir() / name
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _is_type(value: Any, type_name: str) -> bool:
    if type_name == "object":
        return isinstance(value, dict)
    if type_name == "array":
        return isinstance(value, list)
    if type_name == "string":
        return isinstance(value, str)
    if type_name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if type_name == "number":
        return (isinstance(value, (int, float)) and not isinstance(value, bool))
    if type_name == "boolean":
        return isinstance(value, bool)
    if type_name == "null":
        return value is None
    return False


def _type_matches(value: Any, declared: Any) -> bool:
    if isinstance(declared, list):
        return any(_type_matches(value, item) for item in declared)
    return _is_type(value, declared)


def _validate(instance: Any, schema: Dict[str, Any], path: str) -> None:
    if "const" in schema and instance != schema["const"]:
        raise SchemaValidationError(f"expected const {schema['const']!r}", path)

    if "enum" in schema and instance not in schema["enum"]:
        raise SchemaValidationError(f"value not in enum {schema['enum']!r}", path)

    if "type" in schema and not _type_matches(instance, schema["type"]):
        raise SchemaValidationError(f"expected type {schema['type']!r}", path)

    if "not" in schema:
        try:
            _validate(instance, schema["not"], path)
        except SchemaValidationError:
            pass
        else:
            raise SchemaValidationError("matched forbidden subschema", path)

    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < schema["minLength"]:
            raise SchemaValidationError("string shorter than minLength", path)
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            raise SchemaValidationError("string longer than maxLength", path)
        if "pattern" in schema and re.search(schema["pattern"], instance) is None:
            raise SchemaValidationError("string does not match pattern", path)

    if isinstance(instance, bool):
        pass
    elif isinstance(instance, (int, float)):
        if "minimum" in schema and instance < schema["minimum"]:
            raise SchemaValidationError("below minimum", path)
        if "maximum" in schema and instance > schema["maximum"]:
            raise SchemaValidationError("above maximum", path)

    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            raise SchemaValidationError("array shorter than minItems", path)
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            raise SchemaValidationError("array longer than maxItems", path)
        if schema.get("uniqueItems"):
            serialized = [json.dumps(item, sort_keys=True, default=str) for item in instance]
            if len(serialized) != len(set(serialized)):
                raise SchemaValidationError("array items are not unique", path)
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(instance):
                _validate(item, item_schema, f"{path}[{index}]")
        if "contains" in schema:
            contains = schema["contains"]
            if not any(
                _try_validate(item, contains) for item in instance
            ):
                raise SchemaValidationError("array missing required contains item", path)

    if isinstance(instance, dict):
        props = schema.get("properties") or {}
        required = schema.get("required") or []
        for key in required:
            if key not in instance:
                raise SchemaValidationError(f"missing required property {key!r}", path)
        additional = schema.get("additionalProperties", True)
        for key, value in instance.items():
            child = f"{path}.{key}"
            if key in props:
                _validate(value, props[key], child)
            elif additional is False:
                raise SchemaValidationError(f"additional property not allowed: {key!r}", path)
            elif isinstance(additional, dict):
                _validate(value, additional, child)


def _try_validate(instance: Any, schema: Dict[str, Any]) -> bool:
    try:
        _validate(instance, schema, "$")
        return True
    except SchemaValidationError:
        return False


_FORBIDDEN_SECRET_KEYS = re.compile(
    r"(?i)(password|secret|token|api[_-]?key|private[_-]?key|credential)"
)


def _walk_forbid_secret_values(obj: Any, path: str) -> None:
    if isinstance(obj, dict):
        for key, value in obj.items():
            child = f"{path}.{key}"
            if _FORBIDDEN_SECRET_KEYS.search(str(key)) and key not in {
                "secrets",
                "required_refs",
                "exception_ref",
                "policy_refs",
            }:
                # Keys that look like secret *names* under secrets.refs are OK;
                # values that look like high-entropy secrets are not accepted in Spec.
                if isinstance(value, str) and len(value) >= 8 and key.lower() not in {
                    "name",
                    "provider",
                    "manifest_service",
                    "target",
                    "injection",
                    "environment_key",
                }:
                    raise SchemaValidationError(
                        "plaintext secret-like field is forbidden",
                        child,
                    )
            _walk_forbid_secret_values(value, child)
    elif isinstance(obj, list):
        for index, item in enumerate(obj):
            _walk_forbid_secret_values(item, f"{path}[{index}]")


def _semantic_spec_checks(doc: Dict[str, Any]) -> None:
    runtime = doc.get("runtime") or {}
    cap_drop = [str(item).upper() for item in runtime.get("cap_drop") or []]
    if "ALL" not in cap_drop:
        raise SchemaValidationError("cap_drop must include ALL", "$.runtime.cap_drop")

    for index, cap in enumerate(runtime.get("cap_add") or []):
        if str(cap).strip().upper() == "ALL":
            raise SchemaValidationError("cap_add must not include ALL", f"$.runtime.cap_add[{index}]")

    if runtime.get("read_only") is not True:
        raise SchemaValidationError("read_only must be true", "$.runtime.read_only")
    if runtime.get("no_new_privileges") is not True:
        raise SchemaValidationError(
            "no_new_privileges must be true", "$.runtime.no_new_privileges"
        )

    for key in ("command", "entrypoint"):
        value = runtime.get(key)
        if isinstance(value, str):
            raise SchemaValidationError(
                f"{key} must be an argv array, not a shell string",
                f"$.runtime.{key}",
            )

    image = doc.get("image") or {}
    if image.get("allow_mutable_tag") and not image.get("exception_ref"):
        raise SchemaValidationError(
            "allow_mutable_tag requires exception_ref",
            "$.image.exception_ref",
        )

    network = doc.get("network") or {}
    exposure = network.get("exposure")
    ports = network.get("published_ports") or []
    sources = network.get("allowed_sources") or []
    if exposure in {"lan_allowlist", "zerotier_allowlist"} and ports and not sources:
        raise SchemaValidationError(
            "allowlist exposure with published ports requires allowed_sources",
            "$.network.allowed_sources",
        )
    if exposure == "none" and ports:
        raise SchemaValidationError(
            "exposure none forbids published_ports",
            "$.network.published_ports",
        )
    for index, port in enumerate(ports):
        bind = str(port.get("bind_address") or "")
        if bind in {"0.0.0.0", "::", "*"} and exposure not in {
            "lan_allowlist",
            "zerotier_allowlist",
            "public_via_existing_proxy",
        }:
            raise SchemaValidationError(
                "wildcard bind requires non-localhost exposure profile",
                f"$.network.published_ports[{index}].bind_address",
            )
        if bind == "::" and not any(":" in str(src) or src.lower() == "any" for src in sources):
            if exposure in {"lan_allowlist", "zerotier_allowlist"}:
                raise SchemaValidationError(
                    "IPv6 wildcard bind requires IPv6 allowed_sources policy",
                    f"$.network.published_ports[{index}].bind_address",
                )

    for index, volume in enumerate((doc.get("storage") or {}).get("volumes") or []):
        vtype = volume.get("type")
        source = volume.get("source")
        if vtype == "bind":
            if not volume.get("approved_root_ref"):
                raise SchemaValidationError(
                    "bind volume requires approved_root_ref",
                    f"$.storage.volumes[{index}].approved_root_ref",
                )
            if source in {"/", "/etc", "/var/run", "/var/run/docker.sock"}:
                raise SchemaValidationError(
                    "dangerous bind source rejected",
                    f"$.storage.volumes[{index}].source",
                )
            if isinstance(source, str) and (
                source == "/var/run/docker.sock" or source.endswith("/docker.sock")
            ):
                raise SchemaValidationError(
                    "docker.sock bind rejected",
                    f"$.storage.volumes[{index}].source",
                )

    health = doc.get("health") or {}
    if health.get("required") is not True:
        raise SchemaValidationError("health.required must be true", "$.health.required")
    if not health.get("test"):
        raise SchemaValidationError("health.test is required", "$.health.test")

    for index, exc in enumerate((doc.get("exceptions") or {}).get("policy_refs") or []):
        if not exc.get("expires"):
            raise SchemaValidationError(
                "exception requires expires",
                f"$.exceptions.policy_refs[{index}].expires",
            )

    # Forbidden runtime primitives (closed Spec — these fields must not appear).
    for forbidden in ("privileged", "network_mode", "devices", "group_add", "pid", "ipc"):
        if forbidden in runtime:
            raise SchemaValidationError(
                f"forbidden runtime field {forbidden!r}",
                f"$.runtime.{forbidden}",
            )

    _walk_forbid_secret_values(doc, "$")


def validate_instance(instance: Any, schema: Dict[str, Any]) -> None:
    _validate(instance, schema, "$")


def validate_secure_deployment_spec(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Validate Spec v1 against schema + semantic invariants. Returns doc."""
    if not isinstance(doc, dict):
        raise SchemaValidationError("spec must be an object", "$")
    schema = load_schema("secure-deployment-spec-v1.schema.json")
    validate_instance(doc, schema)
    _semantic_spec_checks(doc)
    return doc


def validate_deployment_plan(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Validate Plan v1 against schema. Returns doc."""
    if not isinstance(doc, dict):
        raise SchemaValidationError("plan must be an object", "$")
    schema = load_schema("deployment-plan-v1.schema.json")
    validate_instance(doc, schema)
    _walk_forbid_secret_values(doc, "$")
    return doc
