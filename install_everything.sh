#!/bin/bash

# DockerPilot full stack installer wrapper.
# Installs DockerPilot CLI and DockerPilotExtras.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

chmod +x install.sh
exec ./install.sh --extras "$@"
