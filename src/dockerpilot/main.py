from dockerpilot.pilot import DockerPilotEnhanced, LogLevel
import argparse

def main():
    bootstrap_parser = argparse.ArgumentParser(add_help=False)
    bootstrap_parser.add_argument('--config', '-c', type=str, default=None)
    bootstrap_parser.add_argument('--log-level', '-l', choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'], default='INFO')
    known_args, _ = bootstrap_parser.parse_known_args()

    try:
        log_level_enum = LogLevel[known_args.log_level]
    except Exception:
        log_level_enum = LogLevel.INFO

    pilot = DockerPilotEnhanced(config_file=known_args.config, log_level=log_level_enum)
    pilot.run_cli()

if __name__ == "__main__":
    main()