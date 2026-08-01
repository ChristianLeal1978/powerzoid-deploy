#!/bin/bash
# PowerZoid Deploy — Script de instalación
# Uso: bash install.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
UUID="powerzoid-deploy@cleal.cl"
EXT_DIR="$HOME/.local/share/gnome-shell/extensions/$UUID"
BIN_DIR="$HOME/.local/bin"
SYSTEMD_DIR="$HOME/.config/systemd/user"
CONFIG_DIR="$HOME/.config/powerzoid-deploy"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  PowerZoid Deploy — Instalación"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# [1] Directorios
echo "[1/5] Creando directorios..."
mkdir -p "$BIN_DIR" "$SYSTEMD_DIR" "$CONFIG_DIR"

# [2] Token de Vercel
if [ ! -f "$CONFIG_DIR/token" ]; then
    echo "[2/5] Token de Vercel no encontrado."
    echo "      Genéralo en: https://vercel.com/account/tokens (scope mínimo: deployments:read)"
    read -rsp "      Pega tu token de Vercel: " VERCEL_TOKEN
    echo ""
    printf '%s' "$VERCEL_TOKEN" > "$CONFIG_DIR/token"
    chmod 600 "$CONFIG_DIR/token"
else
    echo "[2/5] Token de Vercel ya configurado."
fi

# [3] Daemon
echo "[3/5] Instalando daemon..."
cp "$SCRIPT_DIR/daemon/powerzoid-deploy-daemon.py" "$BIN_DIR/powerzoid-deploy-daemon.py"
chmod +x "$BIN_DIR/powerzoid-deploy-daemon.py"

# [4] Systemd
echo "[4/5] Configurando systemd..."
cp "$SCRIPT_DIR/systemd/powerzoid-deploy.service" "$SYSTEMD_DIR/"
systemctl --user daemon-reload
systemctl --user enable powerzoid-deploy.service
systemctl --user restart powerzoid-deploy.service

# [5] Extensión GNOME
echo "[5/5] Instalando extensión GNOME Shell..."
mkdir -p "$EXT_DIR"
cp "$SCRIPT_DIR/extension/metadata.json" "$SCRIPT_DIR/extension/extension.js" "$EXT_DIR/"

if gnome-extensions enable "$UUID" 2>/dev/null; then
    echo "  → Extensión activada"
else
    echo "  → La extensión se activará en el próximo inicio de sesión (Wayland)"
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  ✓ Instalación completada"
echo ""
echo "  Próximos pasos:"
echo "  1. Cierra sesión y vuelve a entrar (Wayland):"
echo "     gnome-session-quit --logout"
echo ""
echo "  2. Verifica:"
echo "     gnome-extensions info $UUID | grep State"
echo "     curl -s http://localhost:6792/status | python3 -m json.tool"
echo ""
echo "  Estado del daemon:"
systemctl --user status powerzoid-deploy.service --no-pager -n 5 2>/dev/null || true
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
