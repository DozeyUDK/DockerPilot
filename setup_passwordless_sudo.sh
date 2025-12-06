#!/bin/bash
# Setup passwordless sudo for DockerPilot backup operations

set -e

echo "============================================================"
echo "DockerPilot - Passwordless Sudo Setup"
echo "============================================================"
echo ""
echo "Ten skrypt skonfiguruje passwordless sudo dla operacji backup."
echo "To pozwoli DockerPilot robić backup Docker volumes bez pytania o hasło."
echo ""
echo "UWAGA: Będziesz poproszony o hasło sudo TERAZ (jednorazowo)."
echo ""
read -p "Kontynuować? (y/n): " -n 1 -r
echo ""

if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Anulowano."
    exit 0
fi

# Get current user
CURRENT_USER=$(whoami)
SUDO_FILE="/etc/sudoers.d/dockerpilot"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo ""
echo "📝 Tworzenie pliku sudoers dla użytkownika: $CURRENT_USER"
echo "📁 Plik: $SUDO_FILE"
echo ""

# Create sudoers file
sudo tee "$SUDO_FILE" > /dev/null <<EOF
# DockerPilot - Passwordless sudo dla Docker backup operacji
# Utworzony: $(date)
# Użytkownik: $CURRENT_USER

# Backup Docker volumes - tar
$CURRENT_USER ALL=(ALL) NOPASSWD: /bin/tar -czf $PROJECT_DIR/backup_* *
$CURRENT_USER ALL=(ALL) NOPASSWD: /bin/tar -czf * -C /var/lib/docker/volumes/* *

# Ownership fix dla backup files
$CURRENT_USER ALL=(ALL) NOPASSWD: /bin/chown $CURRENT_USER\\:$CURRENT_USER $PROJECT_DIR/backup_*/*

# Docker operations (używane przez pilot.py dla backup volumes)
$CURRENT_USER ALL=(ALL) NOPASSWD: /usr/bin/docker run --rm -v * alpine\\:latest *
EOF

# Set correct permissions
sudo chmod 440 "$SUDO_FILE"
sudo chown root:root "$SUDO_FILE"

echo "✅ Plik sudoers utworzony"
echo ""

# Verify syntax
echo "🔍 Weryfikacja syntax..."
if sudo visudo -c -f "$SUDO_FILE" 2>&1 | grep -q "parsed OK"; then
    echo "✅ Syntax prawidłowy"
else
    echo "❌ Błąd syntax w pliku sudoers!"
    sudo rm "$SUDO_FILE"
    exit 1
fi

echo ""
echo "🧪 Test passwordless sudo..."
if sudo -n tar --version > /dev/null 2>&1; then
    echo "✅ Passwordless sudo działa!"
else
    echo "⚠️  Może wymagać ponownego zalogowania"
fi

echo ""
echo "============================================================"
echo "✅ SETUP ZAKOŃCZONY POMYŚLNIE!"
echo "============================================================"
echo ""
echo "Passwordless sudo skonfigurowane dla:"
echo "  • tar (backup Docker volumes)"
echo "  • chown (fix ownership backup files)"
echo "  • docker run (volume backup containers)"
echo ""
echo "Teraz DockerPilot może robić backup bez pytania o hasło! 🎉"
echo ""
echo "📝 Aby zobaczyć konfigurację:"
echo "   sudo cat $SUDO_FILE"
echo ""
echo "🔄 Jeśli nadal pyta o hasło, wyloguj się i zaloguj ponownie."
echo ""

