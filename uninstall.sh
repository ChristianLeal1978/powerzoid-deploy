#!/bin/bash
# PowerZoid Deploy — Desinstalación
# Uso: bash uninstall.sh

UUID="powerzoid-deploy@cleal.cl"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  PowerZoid Deploy — Desinstalación"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Desactivar extensión
gnome-extensions disable "$UUID" 2>/dev/null || true

# Detener y deshabilitar servicio
systemctl --user stop powerzoid-deploy.service 2>/dev/null || true
systemctl --user disable powerzoid-deploy.service 2>/dev/null || true
rm -f "$HOME/.config/systemd/user/powerzoid-deploy.service"
systemctl --user daemon-reload

# Eliminar archivos (NO la config)
rm -f "$HOME/.local/bin/powerzoid-deploy-daemon.py"
rm -rf "$HOME/.local/share/gnome-shell/extensions/$UUID"

echo "  ✓ Desinstalado"
echo ""
echo "  Conservados (eliminar manualmente si quieres):"
echo "  - Token:    $HOME/.config/powerzoid-deploy/token"
echo "  - Team ID:  $HOME/.config/powerzoid-deploy/team"
echo "  - Posición: $HOME/.config/powerzoid-deploy/panel-position"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
