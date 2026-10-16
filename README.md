# PowerZoid Deploy — GNOME Shell Extension

Monitorea despliegues de **Vercel** en tiempo real desde la barra superior del sistema. Parte de la suite **PowerZoids**.

```
$5,91/20 ▲ biobia-escritorio · ⚙ Building     ← build en curso
$5,91/20 ▲ biobia-escritorio · ✅ 5m           ← deploy exitoso
$5,91/20 ▲ biobia-escritorio · ❌ 2h           ← deploy fallido
```

El monto inicial es el crédito incluido consumido en el ciclo de facturación actual
(`usado/incluido`); si hay cargos on-demand se agregan como `+$x`. La API de Vercel
solo entrega días ya cerrados, así que el consumo de hoy aparece recién al día
siguiente (el menú indica la fecha de corte); por eso puede diferir un poco del dashboard.

```
```

---

## Arquitectura

```
[Vercel API]  ←── HTTPS (8s reposo / 3s building)
                        ↓
          [Python daemon]  localhost:6792/status
                        ↓  (8s reposo / 3s building)
          [GJS Extension]  → Panel GNOME Shell
```

Patrón PowerZoid estándar: el daemon Python maneja toda la lógica (consulta a
Vercel, detección de transiciones de estado, notificaciones de escritorio) y
expone un endpoint JSON en localhost. La extensión GJS solo maneja UI y hace
polling al daemon.

---

## Instalación

### Paso 0 — Crear un token de Vercel

1. Ve a **https://vercel.com/account/tokens**
2. Crea un token nuevo (scope mínimo: `deployments:read`)
3. Copia el token — solo se muestra una vez

### Paso 1 — Instalar

```bash
cd ~/Proyectos/powerzoid-deploy
bash install.sh
```

El instalador pedirá el token de Vercel si no existe
`~/.config/powerzoid-deploy/token`.

### Paso 2 (opcional) — Cuenta de equipo

Si el proyecto pertenece a un equipo de Vercel (no a tu cuenta personal):

```bash
echo "team_xxxxxxxx" > ~/.config/powerzoid-deploy/team
systemctl --user restart powerzoid-deploy.service
```

### Paso 3 — Logout y login

En Wayland, las extensiones nuevas requieren logout para activarse:

```bash
gnome-session-quit --logout
```

### Paso 4 — Verificar instalación

```bash
# Extensión activa
gnome-extensions info powerzoid-deploy@cleal.cl | grep State
# → State: ACTIVE

# Daemon corriendo
systemctl --user status powerzoid-deploy.service

# Daemon respondiendo
curl -s http://localhost:6792/status | python3 -m json.tool
```

---

## Configuración en tiempo de ejecución

| Archivo | Descripción | Requerido |
|---|---|---|
| `~/.config/powerzoid-deploy/token` | Token API de Vercel | **Sí** |
| `~/.config/powerzoid-deploy/team` | Team ID de Vercel | No (cuentas personales) |
| `~/.config/powerzoid-deploy/panel-position` | Posición en el panel (`left`/`center`/`right`) | No (default: `left`) |
| `~/.config/powerzoid-deploy/font-size` | Tamaño de letra del panel en px (`8`–`20`) | No (default: `13`) |
| `~/.config/powerzoid-deploy/billing-day` | Día del mes (`1`–`28`) en que empieza el ciclo de facturación | No (se detecta solo) |
| `~/.config/powerzoid-deploy/included-credit` | Crédito mensual incluido en el plan (USD) | No (default: `20`) |

---

## Menú de la extensión

Clic izquierdo o derecho en el indicador:

```
  Alinear a la izquierda
● Alinear al centro          ← punto indica posición actual
  Alinear a la derecha
──────────────────────
📦  nombre-del-proyecto
✅  Ready
💬  fix: commit message
🌿  main
🕐  hace 5m
💲  Crédito usado: $5.91 / $20.0 USD (desde 19/09) · datos al 14/10
💳  Cargos on-demand: $0.00
──────────────────────
↗  Abrir en Vercel
──────────────────────
Tamaño: 13 px
──────────────────────
A+   Aumentar letra
A−   Reducir letra
──────────────────────
↺    Restablecer
```

Al seleccionar una alineación distinta, la extensión se autorestaura
(`disableExtension`/`enableExtension`) para evitar un bug de rendering de
GNOME Shell 50 al reubicar actores entre boxes del panel.

El tamaño de letra (8–20px, default 13px) se ajusta desde el menú y se
conserva entre reinicios en `~/.config/powerzoid-deploy/font-size`.

---

## Notificaciones de escritorio

- `🚀 Deploy iniciado · {proyecto}` — al detectar nuevo deploy en BUILDING
- `✅ Deploy exitoso · {proyecto}` — al pasar de BUILDING a READY
- `❌ Deploy fallido · {proyecto}` — al pasar de BUILDING a ERROR
- `⊘ Deploy cancelado · {proyecto}` — al pasar de BUILDING a CANCELED

---

## Resolución de problemas

### Extensión en estado INITIALIZED (no ACTIVE) tras login

```bash
gnome-extensions enable powerzoid-deploy@cleal.cl
```

Si sigue en INITIALIZED, cerrar sesión y volver a entrar.

### El daemon no arranca (falta token)

```bash
cat ~/.config/powerzoid-deploy/token   # debe tener el token
systemctl --user restart powerzoid-deploy.service
journalctl --user -u powerzoid-deploy.service -n 20
```

### La extensión muestra "▲ offline"

El daemon no está corriendo o no responde en el puerto 6792.

```bash
systemctl --user status powerzoid-deploy.service
```

### Builds muy rápidos no muestran estado BUILDING

El build completó antes de que el daemon hiciera el siguiente poll (8s).
Es esperado para builds de menos de ~8 segundos. El deploy final (✅/❌)
sí se mostrará correctamente.

### Logs en tiempo real

```bash
journalctl --user -u powerzoid-deploy.service -f
journalctl /usr/bin/gnome-shell -f | grep -i deploy
```

---

## API Vercel utilizada

| Endpoint | Uso |
|---|---|
| `GET /v6/deployments?limit=1` | Detectar UID del deploy más reciente |
| `GET /v13/deployments/{uid}` | Obtener detalles: `inspectorUrl`, `readyState`, commit meta |

Consumo típico en reposo: ~7 llamadas/minuto (muy por debajo del límite
de 100/min para cuentas personales de Vercel).

---

## Desinstalar

```bash
bash uninstall.sh
```

El token, team ID y posición del panel se conservan por seguridad.

---

## Requisitos

- Fedora 44 / GNOME Shell 45–50
- Python 3 (stdlib, sin dependencias externas)
- `notify-send` (libnotify, incluido en Fedora + GNOME por defecto)

---

## Licencia

GPL-2.0
