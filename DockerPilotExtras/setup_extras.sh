#!/bin/bash
# DockerPilot Extras - one-time setup (venv + Python deps).
# Node.js/npm must be installed separately (see below).
set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
VENV_DIR="$SCRIPT_DIR/.venv"

echo "============================================================"
echo "DockerPilot Extras - Setup"
echo "============================================================"
echo ""

# Python venv (avoids PEP 668 externally-managed-environment on Ubuntu/Debian 24.04+)
if [ ! -d "$VENV_DIR" ]; then
    echo "Creating virtual environment..."
    python3 -m venv "$VENV_DIR" || { echo "❌ Failed. Install: sudo apt install python3-venv python3-full (Debian/Ubuntu)"; exit 1; }
fi
if [ ! -f "$VENV_DIR/bin/pip" ]; then
    echo "Ensuring pip in venv..."
    "$VENV_DIR/bin/python" -m ensurepip --upgrade 2>/dev/null || true
fi
echo "Installing Python dependencies (Flask, etc.)..."
"$VENV_DIR/bin/pip" install -q -r requirements.txt
echo "✅ Python dependencies installed in .venv"
echo ""

# Node.js check
if command -v node >/dev/null 2>&1 && command -v npm >/dev/null 2>&1; then
    echo "✅ Node.js: $(node --version)  npm: $(npm --version)"
    if [ ! -d "frontend/node_modules" ]; then
        echo "Installing frontend dependencies..."
        (cd frontend && npm install)
        echo "✅ Frontend dependencies installed"
    else
        echo "✅ Frontend node_modules already present"
    fi
else
    echo "⚠️  Node.js/npm not found. Install them to run the frontend:"
    echo "   Debian/Ubuntu: sudo apt install nodejs npm"
    echo "   Or: https://nodejs.org/"
    echo "   Then run: cd frontend && npm install"
fi
echo ""
echo "To start Extras (backend + frontend):"
echo "   $VENV_DIR/bin/python loader.py"
echo "Or activate venv first:"
echo "   source $VENV_DIR/bin/activate && python loader.py"
echo ""
