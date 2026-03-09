#!/bin/bash

# Docker Pilot - One-Click Installation Script
# Supports Linux and macOS
# Default: installs in a venv (avoids PEP 668 "externally-managed-environment" on Ubuntu/Debian 24.04+)
# Use: ./install.sh --system  for system-wide install (e.g. if you develop the app)

set -e

INSTALL_SYSTEM=false
for arg in "$@"; do
    if [ "$arg" = "--system" ]; then
        INSTALL_SYSTEM=true
        break
    fi
done

echo "🚀 Docker Pilot Installation Script"
echo "===================================="
echo ""

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Check Python version
echo "📋 Checking prerequisites..."
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}❌ Python 3 is not installed. Please install Python 3.9 or higher.${NC}"
    exit 1
fi

PYTHON_MAJOR=$(python3 -c 'import sys; print(sys.version_info[0])')
PYTHON_MINOR=$(python3 -c 'import sys; print(sys.version_info[1])')

if [ "$PYTHON_MAJOR" -lt 3 ] || ([ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -lt 9 ]); then
    echo -e "${RED}❌ Python 3.9 or higher is required. Found: $PYTHON_MAJOR.$PYTHON_MINOR${NC}"
    exit 1
fi

echo -e "${GREEN}✅ Python $PYTHON_MAJOR.$PYTHON_MINOR found${NC}"

# Check Docker
if ! command -v docker &> /dev/null; then
    echo -e "${YELLOW}⚠️  Docker is not installed or not in PATH${NC}"
    echo "   Docker is required for Docker Pilot to work."
    echo "   Please install Docker: https://docs.docker.com/get-docker/"
    read -p "Continue anyway? (y/N) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
else
    echo -e "${GREEN}✅ Docker found${NC}"
fi

# Check if Docker daemon is running
if docker info &> /dev/null; then
    echo -e "${GREEN}✅ Docker daemon is running${NC}"
else
    echo -e "${YELLOW}⚠️  Docker daemon is not running${NC}"
    echo "   Please start Docker and run this script again."
    read -p "Continue anyway? (y/N) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# Get the directory where the script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

if [ "$INSTALL_SYSTEM" = true ]; then
    # System-wide install (for developers or when you explicitly want it)
    echo ""
    echo "📦 Installing Docker Pilot (system-wide, --break-system-packages)..."
    pip3 install -e . --break-system-packages
    DOCKERPILOT_CMD="dockerpilot"
else
    # Default: venv (works on Ubuntu/Debian 24.04+ and other distros with PEP 668)
    VENV_DIR="$SCRIPT_DIR/.venv"
    if [ ! -d "$VENV_DIR" ]; then
        echo ""
        echo "📦 Creating virtual environment..."
        if ! python3 -m venv "$VENV_DIR"; then
            echo -e "${RED}❌ Failed to create venv. Install: sudo apt install python3-venv python3-full (Debian/Ubuntu)${NC}"
            exit 1
        fi
    fi
    # Ensure pip is available (some distros create venv without pip)
    if [ ! -x "$VENV_DIR/bin/pip" ] && [ ! -x "$VENV_DIR/bin/pip3" ]; then
        echo "   Installing pip in venv..."
        "$VENV_DIR/bin/python" -m ensurepip --upgrade
    fi
    echo ""
    echo "📦 Installing Docker Pilot (in venv)..."
    "$VENV_DIR/bin/python" -m pip install -e .
    DOCKERPILOT_BIN="$VENV_DIR/bin/dockerpilot"
    mkdir -p "$HOME/.local/bin"
    ln -sf "$DOCKERPILOT_BIN" "$HOME/.local/bin/dockerpilot"
    DOCKERPILOT_CMD="$HOME/.local/bin/dockerpilot"
    if ! echo ":$PATH:" | grep -q ":${HOME}/.local/bin:"; then
        echo -e "${YELLOW}   Add to your shell (e.g. ~/.bashrc): export PATH=\"\$HOME/.local/bin:\$PATH\"${NC}"
    fi
fi

echo ""
echo -e "${GREEN}✅ Installation completed successfully!${NC}"
echo ""

# Verify installation
echo "🔍 Verifying installation..."
if $DOCKERPILOT_CMD --help &> /dev/null; then
    echo -e "${GREEN}✅ Docker Pilot is ready!${NC}"
else
    echo -e "${YELLOW}⚠️  Try: $DOCKERPILOT_CMD --help${NC}"
fi

echo ""
echo "🚀 Quick Start:"
echo "   $DOCKERPILOT_CMD                    # Interactive mode"
echo "   $DOCKERPILOT_CMD --help             # Show help"
echo "   $DOCKERPILOT_CMD validate           # Check system"
if [ "$INSTALL_SYSTEM" != true ]; then
    echo ""
    echo "   (venv is in: $VENV_DIR)"
fi
echo ""
echo "📚 Documentation: README.md"
echo ""

# Optional: Install GitPython for Git integration
read -p "Install GitPython for Git integration? (y/N) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    if [ "$INSTALL_SYSTEM" = true ]; then
        pip3 install GitPython --break-system-packages
    else
        "$VENV_DIR/bin/python" -m pip install GitPython
    fi
    echo -e "${GREEN}✅ GitPython installed${NC}"
fi

echo ""
echo "🎉 Setup complete! Happy deploying!"

