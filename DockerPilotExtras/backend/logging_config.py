"""Structured, low-noise logging for the DockerPilot Extras Flask service."""

from __future__ import annotations

import json
import logging
import os
import re
import stat
import time
import uuid
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .security import redact_sensitive_text


_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
_SAFE_EXTRA_FIELDS = (
    "event",
    "request_id",
    "server_id",
    "operation",
    "error_type",
    "error",
    "occurrences",
    "downtime_seconds",
    "duration_ms",
    "http_method",
    "http_status",
    "endpoint",
)

_STANDARD_LOG_RECORD_FIELDS = frozenset(logging.makeLogRecord({}).__dict__)


class RedactingLogRecordFilter(logging.Filter):
    """Sanitize records before they reach local handlers or propagated root handlers."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = _safe_text(record.getMessage())
        except Exception:
            record.msg = "Log message formatting failed"
        record.args = ()

        if record.exc_info:
            try:
                record.exc_text = _safe_text(logging.Formatter().formatException(record.exc_info))
            except Exception:
                record.exc_text = "<exception formatting failed>"
            # Do not let a parent/root handler reformat the original exception.
            record.exc_info = None
        elif record.exc_text:
            record.exc_text = _safe_text(record.exc_text)

        if record.stack_info:
            record.stack_info = _safe_text(record.stack_info)

        # Extras are visible to host-level formatters even when our JSON
        # formatter deliberately whitelists a smaller set of fields.
        for field, value in tuple(record.__dict__.items()):
            if field not in _STANDARD_LOG_RECORD_FIELDS and isinstance(value, str):
                setattr(record, field, _safe_text(value, limit=1000))
        return True


def _safe_text(value: object, limit: int = 4000) -> str:
    try:
        text = redact_sensitive_text(value)
    except Exception:
        text = "<unavailable>"
    return text[:limit]


def _parse_level(value: object, default: int) -> int:
    if isinstance(value, int):
        return value
    candidate = getattr(logging, str(value or "").strip().upper(), None)
    return candidate if isinstance(candidate, int) else default


def _parse_octal(value: object, default: int) -> int:
    try:
        return int(str(value), 8)
    except (TypeError, ValueError):
        return default


def _validate_log_directory_hierarchy(path: Path, *, allow_missing: bool) -> Path:
    """Reject symlinks and directories writable by untrusted users in the log path."""
    absolute = Path(os.path.abspath(os.fspath(path)))
    if absolute == Path(absolute.anchor):
        raise OSError("The filesystem root cannot be used as the DockerPilot Extras log directory")
    current = Path(absolute.anchor)
    components = absolute.parts[1:]
    effective_uid = os.geteuid() if os.name == "posix" else None

    for component in components:
        current = current / component
        try:
            info = os.lstat(current)
        except FileNotFoundError:
            if allow_missing:
                continue
            raise OSError(f"Log directory component does not exist: {current}")

        if stat.S_ISLNK(info.st_mode):
            raise OSError(f"Refusing symlink in DockerPilot Extras log path: {current}")
        if not stat.S_ISDIR(info.st_mode):
            raise OSError(f"Log path component is not a directory: {current}")

        if effective_uid is not None:
            if info.st_uid not in {0, effective_uid}:
                raise OSError(f"Untrusted owner on DockerPilot Extras log path: {current}")
            writable_by_others = bool(info.st_mode & (stat.S_IWGRP | stat.S_IWOTH))
            trusted_sticky_directory = info.st_uid == 0 and bool(info.st_mode & stat.S_ISVTX)
            if writable_by_others and not trusted_sticky_directory:
                raise OSError(f"Unsafe group/world-writable log path component: {current}")

    return absolute


class JsonLineFormatter(logging.Formatter):
    """Emit bounded JSON objects and redact message/exception text."""

    def format(self, record: logging.LogRecord) -> str:
        try:
            message = record.getMessage()
        except Exception:
            message = "Log message formatting failed"

        payload = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "message": _safe_text(message),
        }

        context_request_id = None
        try:
            from flask import g, has_request_context

            if has_request_context():
                context_request_id = getattr(g, "dockerpilot_request_id", None)
        except (ImportError, RuntimeError):
            context_request_id = None

        for field in _SAFE_EXTRA_FIELDS:
            value = getattr(record, field, None)
            if field == "request_id" and value is None:
                value = context_request_id
            if value is None:
                continue
            if isinstance(value, str):
                payload[field] = _safe_text(value, limit=1000)
            elif isinstance(value, (int, float, bool)):
                payload[field] = value

        if record.exc_info:
            try:
                payload["exception"] = _safe_text(self.formatException(record.exc_info))
            except Exception:
                payload["exception"] = "<exception formatting failed>"
        elif record.exc_text:
            payload["exception"] = _safe_text(record.exc_text)

        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


class SafeConsoleFormatter(logging.Formatter):
    """Redact message, traceback and stack text before writing to the terminal."""

    def format(self, record: logging.LogRecord) -> str:
        safe_record = logging.makeLogRecord(record.__dict__.copy())
        try:
            safe_record.msg = _safe_text(record.getMessage())
        except Exception:
            safe_record.msg = "Log message formatting failed"
        safe_record.args = ()
        if safe_record.exc_info:
            try:
                safe_record.exc_text = _safe_text(self.formatException(safe_record.exc_info))
            except Exception:
                safe_record.exc_text = "<exception formatting failed>"
            safe_record.exc_info = None
        elif safe_record.exc_text:
            safe_record.exc_text = _safe_text(safe_record.exc_text)
        if safe_record.stack_info:
            safe_record.stack_info = _safe_text(safe_record.stack_info)
        return super().format(safe_record)


class _PermissionedRotatingFileHandler(RotatingFileHandler):
    """Keep the active file and newly rotated files at the configured mode."""

    def __init__(self, filename: str, *, file_mode: int, **kwargs):
        self.file_mode = file_mode
        super().__init__(filename, **kwargs)

    def handleError(self, record):
        """Never let logging's default handler print the original secret-bearing record."""
        try:
            os.write(2, b"DockerPilot Extras file logging failed; event dropped\n")
        except OSError:
            pass

    def _open(self):
        # O_NOFOLLOW prevents a pre-created extras.jsonl symlink from redirecting
        # log writes or chmod to an unrelated file on POSIX systems.
        nofollow = getattr(os, "O_NOFOLLOW", 0)
        if nofollow:
            flags = os.O_CREAT | os.O_APPEND | os.O_WRONLY | nofollow
            flags |= getattr(os, "O_NONBLOCK", 0)
            descriptor = os.open(self.baseFilename, flags, self.file_mode)
            try:
                info = os.fstat(descriptor)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise OSError("Refusing a non-regular or hard-linked DockerPilot Extras log file")
                if os.name == "posix" and info.st_uid != os.geteuid():
                    raise OSError("DockerPilot Extras log file is not owned by the service user")
                os.fchmod(descriptor, self.file_mode)
                return os.fdopen(descriptor, self.mode, encoding=self.encoding)
            except Exception:
                os.close(descriptor)
                raise

        if Path(self.baseFilename).is_symlink():
            raise OSError("Refusing to open a symlink as the DockerPilot Extras log file")
        stream = super()._open()
        try:
            os.chmod(self.baseFilename, self.file_mode)
        except OSError:
            pass
        return stream


def install_request_logging(app, *, logger=None, slow_request_ms: int | None = None) -> None:
    """Attach request IDs and log only actionable HTTP requests."""

    if app.extensions.get("dockerpilot_extras.request_logging"):
        return

    from flask import g, request

    target_logger = logger or app.logger
    if slow_request_ms is None:
        try:
            slow_request_ms = max(
                1, int(os.environ.get("DOCKERPILOT_EXTRAS_SLOW_REQUEST_MS", "1500"))
            )
        except ValueError:
            slow_request_ms = 1500

    @app.before_request
    def _dockerpilot_request_start():
        supplied = (request.headers.get("X-Request-ID") or "").strip()
        g.dockerpilot_request_id = (
            supplied if _REQUEST_ID_RE.fullmatch(supplied) else uuid.uuid4().hex
        )
        g.dockerpilot_request_started = time.perf_counter()

    @app.after_request
    def _dockerpilot_request_complete(response):
        request_id = getattr(g, "dockerpilot_request_id", uuid.uuid4().hex)
        response.headers["X-Request-ID"] = request_id

        started = getattr(g, "dockerpilot_request_started", None)
        duration_ms = (
            round((time.perf_counter() - started) * 1000, 2)
            if started is not None
            else 0.0
        )
        method = request.method.upper()
        status = int(response.status_code)
        level = None

        # Some polling resources already emit an aggregated domain event. Avoid
        # duplicating that event as one HTTP error per frontend poll.
        if request.environ.get("dockerpilot_extras.suppress_http_log"):
            return response

        if status >= 500:
            level = logging.ERROR
        elif status >= 400:
            level = logging.WARNING
        elif duration_ms >= slow_request_ms:
            level = logging.WARNING
        elif method not in {"GET", "HEAD", "OPTIONS"}:
            level = logging.INFO

        if level is not None:
            target_logger.log(
                level,
                "HTTP request completed",
                extra={
                    "event": "http_request",
                    "request_id": request_id,
                    "http_method": method,
                    "http_status": status,
                    "duration_ms": duration_ms,
                    "endpoint": request.endpoint or "unknown",
                },
            )
        return response

    app.extensions["dockerpilot_extras.request_logging"] = True


def configure_logging(
    app,
    *,
    log_dir: str | Path | None = None,
    file_level: int | str | None = None,
    console_level: int | str | None = None,
    max_bytes: int | None = None,
    backup_count: int | None = None,
    file_mode: int | None = None,
    directory_mode: int | None = None,
    slow_request_ms: int | None = None,
) -> Path | None:
    """Configure JSONL file logs and concise console output.

    File logging defaults to INFO and rotates at 10 MiB with five backups.
    The console defaults to WARNING so polling and successful API requests do
    not flood the terminal. Logging failures never prevent application startup.
    """

    from flask.logging import default_handler

    resolved_dir = Path(
        log_dir
        or os.environ.get(
            "DOCKERPILOT_EXTRAS_LOG_DIR",
            str(Path.home() / ".dockerpilot_extras" / "logs"),
        )
    ).expanduser()
    resolved_file_level = _parse_level(
        file_level if file_level is not None else os.environ.get("DOCKERPILOT_EXTRAS_LOG_LEVEL", "INFO"),
        logging.INFO,
    )
    resolved_console_level = _parse_level(
        console_level if console_level is not None else os.environ.get("DOCKERPILOT_EXTRAS_CONSOLE_LEVEL", "WARNING"),
        logging.WARNING,
    )
    resolved_max_bytes = max_bytes
    if resolved_max_bytes is None:
        try:
            resolved_max_bytes = max(1024, int(os.environ.get("DOCKERPILOT_EXTRAS_LOG_MAX_BYTES", str(10 * 1024 * 1024))))
        except ValueError:
            resolved_max_bytes = 10 * 1024 * 1024
    resolved_backup_count = backup_count
    if resolved_backup_count is None:
        try:
            resolved_backup_count = max(0, int(os.environ.get("DOCKERPILOT_EXTRAS_LOG_BACKUP_COUNT", "5")))
        except ValueError:
            resolved_backup_count = 5
    resolved_file_mode = file_mode if file_mode is not None else _parse_octal(
        os.environ.get("DOCKERPILOT_EXTRAS_LOG_FILE_MODE", "0600"), 0o600
    )
    resolved_directory_mode = directory_mode if directory_mode is not None else _parse_octal(
        os.environ.get("DOCKERPILOT_EXTRAS_LOG_DIR_MODE", "0700"), 0o700
    )

    console_handler = logging.StreamHandler()
    console_handler.setLevel(resolved_console_level)
    console_handler.setFormatter(SafeConsoleFormatter("%(levelname)s %(message)s"))
    console_handler._dockerpilot_extras_owned = True

    file_handler = None
    log_path = None
    try:
        if (
            resolved_directory_mode & 0o700 != 0o700
            or resolved_directory_mode & ~0o2750
            or resolved_file_mode & 0o600 != 0o600
            or resolved_file_mode & ~0o640
        ):
            raise OSError(
                "Unsafe log permissions requested; use directory mode 0700/0750/2750 "
                "and file mode 0600/0640"
            )
        resolved_dir = _validate_log_directory_hierarchy(resolved_dir, allow_missing=True)
        directory_existed = resolved_dir.exists()
        resolved_dir.mkdir(parents=True, exist_ok=True, mode=resolved_directory_mode)
        # Re-check after creation; a path component may have appeared between
        # the initial validation and mkdir. Safe ancestors cannot be swapped by
        # an untrusted local user between this check and file creation.
        resolved_dir = _validate_log_directory_hierarchy(resolved_dir, allow_missing=False)
        if directory_existed:
            current_mode = stat.S_IMODE(os.stat(resolved_dir, follow_symlinks=False).st_mode)
            if current_mode & ~resolved_directory_mode:
                raise OSError(
                    "Existing log directory permissions are broader than configured; "
                    "correct the directory mode explicitly before starting Extras"
                )
        else:
            # Only chmod a directory this process has just created. Never
            # change permissions on an arbitrary pre-existing system directory.
            os.chmod(resolved_dir, resolved_directory_mode)
        log_path = resolved_dir / "extras.jsonl"
        file_handler = _PermissionedRotatingFileHandler(
            str(log_path),
            file_mode=resolved_file_mode,
            maxBytes=resolved_max_bytes,
            backupCount=resolved_backup_count,
            encoding="utf-8",
        )
        file_handler.setLevel(resolved_file_level)
        file_handler.setFormatter(JsonLineFormatter())
        file_handler._dockerpilot_extras_owned = True
    except OSError as exc:
        log_path = None
        if file_handler is not None:
            file_handler.close()
            file_handler = None

    handlers = [console_handler]
    if file_handler is not None:
        handlers.insert(0, file_handler)

    app_logger = app.logger
    werkzeug_logger = logging.getLogger("werkzeug")
    old_owned_handlers = set()
    for target in (app_logger, werkzeug_logger):
        for handler in list(target.handlers):
            if getattr(handler, "_dockerpilot_extras_owned", False):
                target.removeHandler(handler)
                old_owned_handlers.add(handler)
    for handler in old_owned_handlers:
        handler.close()

    if default_handler in app_logger.handlers:
        app_logger.removeHandler(default_handler)

    for target in (app_logger, werkzeug_logger):
        for handler in handlers:
            target.addHandler(handler)

    app_logger.setLevel(min(resolved_file_level, resolved_console_level))
    # Redact the shared record before it reaches either our handlers or any
    # parent/root handler. Propagation is kept for pytest caplog and host agents.
    if not any(getattr(item, "_dockerpilot_extras_redactor", False) for item in app_logger.filters):
        redactor = RedactingLogRecordFilter()
        redactor._dockerpilot_extras_redactor = True
        app_logger.addFilter(redactor)
    app_logger.propagate = True
    werkzeug_logger.setLevel(logging.WARNING)
    werkzeug_logger.propagate = False

    if log_path is None:
        app_logger.warning("Structured file logging unavailable; console logging remains active")

    install_request_logging(
        app,
        logger=app_logger,
        slow_request_ms=slow_request_ms,
    )
    return log_path
