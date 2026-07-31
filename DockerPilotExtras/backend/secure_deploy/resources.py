"""Flask-RESTful resources for Secure Deploy preview API."""

from __future__ import annotations

import secrets
from typing import Any, Callable, Dict

from dockerpilot.secure_deploy import redact_for_log

from .errors import SecureDeployError
from .service import SecureDeployService
from .store import new_id


def create_secure_deploy_resources(
    *,
    Resource,
    request,
    service: SecureDeployService,
    require_secure_deploy_access: Callable[[], None],
    get_actor: Callable[[], str],
    max_body_bytes: int = 2 * 1024 * 1024,
):
    def _request_id() -> str:
        return request.headers.get("X-Request-ID") or new_id("req")

    def _parse_json() -> Dict[str, Any]:
        content_type = (request.content_type or "").split(";")[0].strip().lower()
        if content_type != "application/json":
            from .errors import UnsupportedMediaTypeError

            raise UnsupportedMediaTypeError()
        length = request.content_length
        if length is not None and length > max_body_bytes:
            from .errors import PayloadTooLargeError

            raise PayloadTooLargeError()
        raw = request.get_data(cache=True, as_text=False) or b""
        if len(raw) > max_body_bytes:
            from .errors import PayloadTooLargeError

            raise PayloadTooLargeError()
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise SecureDeployError("invalid_json", "JSON object required", 400)
        return data

    def _envelope(payload: Dict[str, Any], status: int = 200):
        # Flask-RESTful expects a dict (not a Response) so it can serialize JSON.
        body = {
            "schema_version": 1,
            "request_id": _request_id(),
            **payload,
        }
        return body, status

    def _handle(exc: Exception):
        if isinstance(exc, SecureDeployError):
            return _envelope(
                {
                    "success": False,
                    "error": {"code": exc.code, "message": exc.message},
                },
                exc.http_status,
            )
        return _envelope(
            {
                "success": False,
                "error": {"code": "internal_error", "message": "internal error"},
            },
            500,
        )

    class SecureDeployDrafts(Resource):
        def post(self):
            try:
                require_secure_deploy_access()
                data = _parse_json()
                spec = data.get("spec")
                if not isinstance(spec, dict):
                    raise SecureDeployError("invalid_json", "spec object required", 400)
                result = service.create_draft(spec, get_actor())
                return _envelope({"success": True, **result}, 201)
            except Exception as exc:  # noqa: BLE001 - mapped below
                return _handle(exc)

    class SecureDeployDraftDetail(Resource):
        def get(self, draft_id: str):
            try:
                require_secure_deploy_access()
                draft = service.get_draft(draft_id)
                return _envelope({"success": True, "draft": redact_for_log(draft)})
            except Exception as exc:  # noqa: BLE001
                return _handle(exc)

    class SecureDeployValidate(Resource):
        def post(self):
            try:
                require_secure_deploy_access()
                data = _parse_json()
                spec = data.get("spec")
                if not isinstance(spec, dict):
                    raise SecureDeployError("invalid_json", "spec object required", 400)
                result = service.validate_spec(spec)
                return _envelope({"success": True, **result})
            except Exception as exc:  # noqa: BLE001
                return _handle(exc)

    class SecureDeployPlan(Resource):
        def post(self):
            try:
                require_secure_deploy_access()
                data = _parse_json()
                spec = data.get("spec")
                if not isinstance(spec, dict):
                    raise SecureDeployError("invalid_json", "spec object required", 400)
                result = service.generate_plan(spec, get_actor(), request_id=_request_id())
                # Never return secret values; redact nested structures.
                safe = redact_for_log(result)
                return _envelope({"success": True, **safe}, 201)
            except Exception as exc:  # noqa: BLE001
                return _handle(exc)

    class SecureDeployPlanDetail(Resource):
        def get(self, plan_id: str):
            try:
                require_secure_deploy_access()
                plan = service.get_plan(plan_id)
                return _envelope({"success": True, "plan": redact_for_log(plan)})
            except Exception as exc:  # noqa: BLE001
                return _handle(exc)

    return (
        SecureDeployDrafts,
        SecureDeployDraftDetail,
        SecureDeployValidate,
        SecureDeployPlan,
        SecureDeployPlanDetail,
    )
