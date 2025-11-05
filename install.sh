#!/bin/bash

# Docker Pilot - One-Click Installation Script
# Supports Linux and macOS

set -e

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

# Install dependencies
echo ""
echo "📦 Installing dependencies..."
if [ -f "requirements.txt" ]; then
    pip3 install -r requirements.txt
    echo -e "${GREEN}✅ Dependencies installed from requirements.txt${NC}"
else
    echo -e "${RED}❌ requirements.txt not found${NC}"
    exit 1
fi

# Install in development mode
echo ""
echo "📦 Installing Docker Pilot..."
pip3 install -e .

echo ""
echo -e "${GREEN}✅ Installation completed successfully!${NC}"
echo ""

# Verify installation
echo "🔍 Verifying installation..."
if dockerpilot --help &> /dev/null; then
    echo -e "${GREEN}✅ Docker Pilot is ready!${NC}"
else
    echo -e "${YELLOW}⚠️  Installation complete, but command verification failed${NC}"
    echo "   Try running: dockerpilot --help"
fi

echo ""
echo "🚀 Quick Start:"
echo "   dockerpilot                    # Interactive mode"
echo "   dockerpilot --help             # Show help"
echo "   dockerpilot validate           # Check system"
echo ""
echo "📚 Documentation: README.md"
echo ""

# Optional: Install GitPython for Git integration
read -p "Install GitPython for Git integration? (y/N) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    pip3 install GitPython
    echo -e "${GREEN}✅ GitPython installed${NC}"
fi

echo ""
echo "🎉 Setup complete! Happy deploying!"

