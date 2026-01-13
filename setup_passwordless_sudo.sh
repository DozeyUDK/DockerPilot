#!/bin/bash
# Setup passwordless sudo for DockerPilot backup operations

set -e

echo "============================================================"
echo "DockerPilot - Passwordless Sudo Setup"
echo "============================================================"
echo ""
echo "This script will configure passwordless sudo for backup operations."
echo "This will allow DockerPilot to backup Docker volumes without asking for a password."
echo ""
echo "WARNING: You will be prompted for sudo password NOW (one time only)."
echo ""
read -p "Continue? (y/n): " -n 1 -r
echo ""

if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Cancelled."
    exit 0
fi

# Get current user
CURRENT_USER=$(whoami)
SUDO_FILE="/etc/sudoers.d/dockerpilot"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo ""
echo "📝 Creating sudoers file for user: $CURRENT_USER"
echo "📁 File: $SUDO_FILE"
echo ""

# Create sudoers file
sudo tee "$SUDO_FILE" > /dev/null <<EOF
# DockerPilot - Passwordless sudo for Docker backup operations
# Created: $(date)
# User: $CURRENT_USER

# Backup Docker volumes - tar
$CURRENT_USER ALL=(ALL) NOPASSWD: /bin/tar -czf $PROJECT_DIR/backup_* *
$CURRENT_USER ALL=(ALL) NOPASSWD: /bin/tar -czf * -C /var/lib/docker/volumes/* *

# Ownership fix for backup files
$CURRENT_USER ALL=(ALL) NOPASSWD: /bin/chown $CURRENT_USER\\:$CURRENT_USER $PROJECT_DIR/backup_*/*

# Docker operations (used by pilot.py for backup volumes)
$CURRENT_USER ALL=(ALL) NOPASSWD: /usr/bin/docker run --rm -v * alpine\\:latest *
EOF

# Set correct permissions
sudo chmod 440 "$SUDO_FILE"
sudo chown root:root "$SUDO_FILE"

echo "✅ Sudoers file created"
echo ""

# Verify syntax
echo "🔍 Verifying syntax..."
if sudo visudo -c -f "$SUDO_FILE" 2>&1 | grep -q "parsed OK"; then
    echo "✅ Syntax is valid"
else
    echo "❌ Syntax error in sudoers file!"
    sudo rm "$SUDO_FILE"
    exit 1
fi

echo ""
echo "🧪 Testing passwordless sudo..."
if sudo -n tar --version > /dev/null 2>&1; then
    echo "✅ Passwordless sudo is working!"
else
    echo "⚠️  May require re-login"
fi

echo ""
echo "============================================================"
echo "✅ SETUP COMPLETED SUCCESSFULLY!"
echo "============================================================"
echo ""
echo "Passwordless sudo configured for:"
echo "  • tar (backup Docker volumes)"
echo "  • chown (fix ownership backup files)"
echo "  • docker run (volume backup containers)"
echo ""
echo "DockerPilot can now backup without asking for a password! 🎉"
echo ""
echo "📝 To view the configuration:"
echo "   sudo cat $SUDO_FILE"
echo ""
echo "🔄 If it still asks for a password, log out and log back in."
echo ""

