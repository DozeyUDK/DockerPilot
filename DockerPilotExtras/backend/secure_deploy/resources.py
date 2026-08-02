"""Flask-RESTful resources for Secure Deploy preview + approval + broker dry-run."""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from dockerpilot.secure_deploy import redact_for_log

from .approval import ApprovalService
from .broker_client import BrokerClient
from .errors import ForbiddenError, SecureDeployError, ValidationFailedError
from .service import SecureDeployService
from .store import new_id


def create_secure_deploy_resources(
    *,
    Resource,
    request,
    service: SecureDeployService,
    approval_service: ApprovalService,
    broker_client: BrokerClient,
    require_secure_deploy_access: Callable[[], None],
    get_actor: Callable[[], str],
    get_session_hash: Callable[[], str],
    verify_step_up_totp: Callable[[str], bool],
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

    def _require_step_up(data: Dict[str, Any]) -> None:
        code = data.get("totp_code")
        # Never log the code; drop from any future use of data.
        data.pop("totp_code", None)
        if not isinstance(code, str) or not code.strip():
            raise ForbiddenError(code="step_up_required", message="step-up TOTP required")
        if not verify_step_up_totp(code.strip()):
            raise ForbiddenError(code="step_up_failed", message="step-up TOTP rejected")

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
            except Exception as exc:  # noqa: BLE001
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

    class SecureDeployPlanApprove(Resource):
        def post(self, plan_id: str):
            try:
                require_secure_deploy_access()
                data = _parse_json()
                _require_step_up(data)
                stored = service.get_plan(plan_id)
                plan = stored.get("plan") or {}
                # Surface hash for operator confirmation (client must echo it).
                expected = data.get("plan_sha256")
                if expected != plan.get("plan_sha256"):
                    raise ValidationFailedError(
                        "plan_sha256 confirmation mismatch — confirm displayed hash",
                        code="plan_hash_confirm",
                    )
                record = approval_service.approve_plan(
                    stored,
                    actor=get_actor(),
                    session_hash=get_session_hash(),
                )
                return _envelope(
                    {
                        "success": True,
                        "approval": redact_for_log(record),
                        "plan_sha256": plan.get("plan_sha256"),
                    },
                    201,
                )
            except Exception as exc:  # noqa: BLE001
                return _handle(exc)

    class SecureDeployApprovalDetail(Resource):
        def get(self, approval_id: str):
            try:
                require_secure_deploy_access()
                record = approval_service.get(approval_id)
                return _envelope({"success": True, "approval": redact_for_log(record)})
            except Exception as exc:  # noqa: BLE001
                return _handle(exc)

    class SecureDeployApprovalRevoke(Resource):
        def post(self, approval_id: str):
            try:
                require_secure_deploy_access()
                data = _parse_json()
                _require_step_up(data)
                record = approval_service.revoke(approval_id, actor=get_actor())
                return _envelope({"success": True, "approval": redact_for_log(record)})
            except Exception as exc:  # noqa: BLE001
                return _handle(exc)

    class SecureDeployBrokerDryRun(Resource):
        def post(self, plan_id: str):
            try:
                require_secure_deploy_access()
                data = _parse_json()
                approval_id = data.get("approval_id")
                if not isinstance(approval_id, str):
                    raise SecureDeployError("invalid_json", "approval_id required", 400)
                admission_bundle_sha256 = data.get("admission_bundle_sha256")
                if not isinstance(admission_bundle_sha256, str):
                    raise SecureDeployError("invalid_json", "admission_bundle_sha256 required", 400)
                stored = service.get_plan(plan_id)
                plan = stored.get("plan") or {}
                approval = approval_service.get(approval_id)
                if approval.get("plan_id") != plan_id:
                    raise ValidationFailedError("approval not bound to plan", code="approval_plan_mismatch")
                if approval.get("status") != "approved":
                    raise ValidationFailedError("approval not active", code="approval_status")
                # Dry-run does not consume approval.
                resp = broker_client.dry_run(plan, approval)
                verification = resp.get("verification") or {}
                summary = {
                    "status": verification.get("status"),
                    "broker_verification_sha256": verification.get("broker_verification_sha256"),
                    "plan_sha256": verification.get("plan_sha256"),
                    "dozeyguard_exit_code": verification.get("dozeyguard_exit_code"),
                    "blocking_findings": verification.get("blocking_findings"),
                    "dry_run": True,
                    "stages": [
                        {"name": s.get("name"), "ok": s.get("ok")}
                        for s in (verification.get("stages") or [])
                    ],
                }
                return _envelope({"success": True, "verification": summary})
            except Exception as exc:  # noqa: BLE001
                return _handle(exc)

    class SecureDeployCanaryAdmit(Resource):
        def post(self, plan_id: str):
            try:
                require_secure_deploy_access()
                data = _parse_json()
                approval_id = data.get("approval_id")
                if not isinstance(approval_id, str):
                    raise SecureDeployError("invalid_json", "approval_id required", 400)
                stored = service.get_plan(plan_id)
                plan = stored.get("plan") or {}
                approval = approval_service.get(approval_id)
                if approval.get("plan_id") != plan_id:
                    raise ValidationFailedError("approval not bound to plan", code="approval_plan_mismatch")
                if approval.get("plan_sha256") != plan.get("plan_sha256"):
                    raise ValidationFailedError("approval hash mismatch", code="approval_plan_mismatch")
                if approval.get("actor") != get_actor():
                    raise ForbiddenError(code="actor_mismatch", message="actor mismatch")
                if approval.get("status") != "approved":
                    raise ValidationFailedError("approval not active", code="approval_status")
                resp = broker_client.admit_canary_execution(
                    plan_id=plan_id,
                    plan_sha256=str(plan.get("plan_sha256") or ""),
                    approval_id=approval_id,
                    admission_bundle_sha256=admission_bundle_sha256,
                )
                return _envelope({"success": True, "canary_admission": resp.get("canary_admission") or {}})
            except Exception as exc:  # noqa: BLE001
                return _handle(exc)

    class SecureDeployCanaryDeploy(Resource):
        def post(self, plan_id: str):
            try:
                require_secure_deploy_access()
                data = _parse_json()
                approval_id = data.get("approval_id")
                if not isinstance(approval_id, str):
                    raise SecureDeployError("invalid_json", "approval_id required", 400)
                stored = service.get_plan(plan_id)
                plan = stored.get("plan") or {}
                approval = approval_service.get(approval_id)
                if approval.get("plan_id") != plan_id:
                    raise ValidationFailedError("approval not bound to plan", code="approval_plan_mismatch")
                if approval.get("plan_sha256") != plan.get("plan_sha256"):
                    raise ValidationFailedError("approval hash mismatch", code="approval_plan_mismatch")
                if approval.get("actor") != get_actor():
                    raise ForbiddenError(code="actor_mismatch", message="actor mismatch")
                resp = broker_client.deploy_canary(
                    plan_id=plan_id,
                    plan_sha256=str(plan.get("plan_sha256") or ""),
                    approval_id=approval_id,
                )
                return _envelope({"success": True, "canary_result": resp.get("canary_result") or {}})
            except Exception as exc:  # noqa: BLE001
                return _handle(exc)

    class SecureDeployCanaryRemove(Resource):
        def post(self, canary_execution_id: str):
            try:
                require_secure_deploy_access()
                _parse_json()
                resp = broker_client.remove_canary(canary_execution_id=canary_execution_id)
                return _envelope({"success": True, "canary_result": resp.get("canary_result") or {}})
            except Exception as exc:  # noqa: BLE001
                return _handle(exc)

    return (
        SecureDeployDrafts,
        SecureDeployDraftDetail,
        SecureDeployValidate,
        SecureDeployPlan,
        SecureDeployPlanDetail,
        SecureDeployPlanApprove,
        SecureDeployApprovalDetail,
        SecureDeployApprovalRevoke,
        SecureDeployBrokerDryRun,
        SecureDeployCanaryAdmit,
        SecureDeployCanaryDeploy,
        SecureDeployCanaryRemove,
    )
