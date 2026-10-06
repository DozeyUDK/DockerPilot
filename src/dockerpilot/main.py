import argparse
import sys

from . import __version__
from .cli.parser import build_cli_parser
from .pilot import DockerPilotEnhanced, LogLevel


def _render_permission_error(exc: PermissionError) -> str:
    """Render expected filesystem permission failures without a Python traceback."""
    path = exc.filename or exc.filename2 or "<unknown>"
    reason = exc.strerror or str(exc) or "Permission denied"
    errno_detail = f" (errno {exc.errno})" if exc.errno is not None else ""
    return "\n".join(
        [
            "DockerPilot could not access a required path.",
            f"  Path: {path}",
            f"  Reason: {reason}{errno_detail}",
            "  Hint: check the owner/group and read/write permissions for the current user.",
        ]
    )


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv

    if any(arg in ("-h", "--help") for arg in argv):
        parser = build_cli_parser()
        parser.parse_args(argv)
        return

    bootstrap_parser = argparse.ArgumentParser(add_help=False)
    bootstrap_parser.add_argument('--version', action='version', version=f'DockerPilot {__version__}')
    bootstrap_parser.add_argument('--config', '-c', type=str, default=None)
    bootstrap_parser.add_argument('--log-level', '-l', choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'], default='INFO')
    known_args, _ = bootstrap_parser.parse_known_args(argv)

    try:
        log_level_enum = LogLevel[known_args.log_level]
    except Exception:
        log_level_enum = LogLevel.INFO

    try:
        pilot = DockerPilotEnhanced(config_file=known_args.config, log_level=log_level_enum)
        pilot.run_cli()
    except PermissionError as exc:
        print(_render_permission_error(exc), file=sys.stderr)
        return 1

if __name__ == "__main__":
    main()
