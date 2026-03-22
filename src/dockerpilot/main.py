import argparse
import sys

from . import __version__
from .cli.parser import build_cli_parser
from .pilot import DockerPilotEnhanced, LogLevel


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

    pilot = DockerPilotEnhanced(config_file=known_args.config, log_level=log_level_enum)
    pilot.run_cli()

if __name__ == "__main__":
    main()
