"""Secure Deploy preview errors and stable codes."""

from __future__ import annotations


class SecureDeployError(Exception):
    """Base error with stable machine code."""

    def __init__(self, code: str, message: str, http_status: int = 400):
        self.code = code
        self.message = message
        self.http_status = http_status
        super().__init__(message)


class AuthRequiredError(SecureDeployError):
    def __init__(self, code: str = "secure_deploy_auth_required", message: str = "Secure Deploy requires authentication"):
        super().__init__(code, message, http_status=503)


class UnauthorizedError(SecureDeployError):
    def __init__(self, message: str = "Authentication required"):
        super().__init__("unauthorized", message, http_status=401)


class ForbiddenError(SecureDeployError):
    def __init__(self, code: str = "forbidden", message: str = "Forbidden"):
        super().__init__(code, message, http_status=403)


class PayloadTooLargeError(SecureDeployError):
    def __init__(self):
        super().__init__("payload_too_large", "Request body exceeds 2 MiB limit", http_status=413)


class UnsupportedMediaTypeError(SecureDeployError):
    def __init__(self):
        super().__init__("unsupported_media_type", "Content-Type must be application/json", http_status=415)


class ValidationFailedError(SecureDeployError):
    def __init__(self, message: str, code: str = "validation_failed"):
        super().__init__(code, message, http_status=422)


class ScannerError(SecureDeployError):
    def __init__(self, message: str, code: str = "dozeyguard_error"):
        super().__init__(code, message, http_status=502)


class NotFoundError(SecureDeployError):
    def __init__(self, message: str = "Not found"):
        super().__init__("not_found", message, http_status=404)
