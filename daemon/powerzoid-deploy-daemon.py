#!/usr/bin/env python3
"""
PowerZoid Deploy — Daemon de monitoreo de despliegues Vercel
Puerto HTTP: 6792

Consulta la API de Vercel para el proyecto asociado al token configurado
y expone el estado del último despliegue en localhost.
Lee config de ~/.config/powerzoid-deploy/{token,team}
"""

import gzip
import json
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
import urllib.request
import urllib.error

# ─────────────────────────────────────────────
# Rutas y parámetros
# ─────────────────────────────────────────────
HOME        = Path.home()
CONFIG_DIR  = HOME / ".config/powerzoid-deploy"
TOKEN_FILE  = CONFIG_DIR / "token"
TEAM_FILE   = CONFIG_DIR / "team"
BILLING_DAY_FILE = CONFIG_DIR / "billing-day"
CREDIT_FILE      = CONFIG_DIR / "included-credit"
DEFAULT_INCLUDED_CREDIT = 20.0   # crédito mensual incluido en el plan Pro (USD)

PORT               = 6792
POLL_IDLE_SECONDS  = 8
POLL_BUILD_SECONDS = 3
API_BASE           = "https://api.vercel.com"
POLL_BILLING_SECONDS = 30 * 60   # los cargos de Vercel tienen granularidad diaria
BILLING_LOOKBACK_DAYS = 35       # ventana suficiente para cubrir un ciclo completo

BUILDING_STATES = {"BUILDING", "QUEUED", "INITIALIZING"}

READY_STATE_INFO = {
    "READY":        {"state": "ready",    "icon": "✅", "label": "Ready"},
    "ERROR":        {"state": "error",    "icon": "❌", "label": "Error"},
    "CANCELED":     {"state": "canceled", "icon": "⊘",  "label": "Canceled"},
    "BUILDING":     {"state": "building", "icon": "⚙",  "label": "Building"},
    "QUEUED":       {"state": "building", "icon": "⚙",  "label": "Queued"},
    "INITIALIZING": {"state": "building", "icon": "⚙",  "label": "Initializing"},
}

NOTIFY_TITLES = {
    "building_start": "🚀 Deploy iniciado",
    "ready":           "✅ Deploy exitoso",
    "error":           "❌ Deploy fallido",
    "canceled":        "⊘ Deploy cancelado",
}

# ─────────────────────────────────────────────
# Estado global
# ─────────────────────────────────────────────
_status: dict = {
    "state":         "unknown",   # unknown | building | ready | error | canceled
    "icon":          "",
    "label":         "",
    "project":       None,
    "time_ago":      "",
    "commit_message": "",
    "branch":        "",
    "inspector_url": "",
    "uid":           None,
    "api_error":     "",
    "billing": {
        "amount":        None,   # cargos on-demand del ciclo actual (USD)
        "used":          None,   # consumo del ciclo a precio efectivo (incluye lo cubierto por el crédito)
        "included":      DEFAULT_INCLUDED_CREDIT,
        "currency":      "USD",
        "period_start":  "",     # YYYY-MM-DD
        "updated":       "",     # HH:MM de la última consulta exitosa
        "error":         "",
    },
}
_lock       = threading.Lock()
_prev_uid: str | None = None
_detail_cache: dict = {}   # uid → readyState visto la última vez


# ─────────────────────────────────────────────
# Utilidades
# ─────────────────────────────────────────────

def log(msg: str) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def read_file(path: Path) -> str:
    try:
        return path.read_text().strip()
    except Exception:
        return ""


def notify(kind: str, project: str, body: str = "") -> None:
    title = NOTIFY_TITLES.get(kind, kind)
    summary = f"{title} · {project}"
    try:
        subprocess.run(
            ["notify-send", "-a", "PowerZoid Deploy", summary, body],
            timeout=5, capture_output=True,
        )
    except Exception as e:
        log(f"  [notify] Error enviando notificación: {e}")


def human_time_ago(ts_ms: int) -> str:
    delta = time.time() - (ts_ms / 1000)
    if delta < 60:
        return "ahora"
    if delta < 3600:
        return f"{int(delta // 60)}m"
    if delta < 86400:
        return f"{int(delta // 3600)}h"
    return f"{int(delta // 86400)}d"


# ─────────────────────────────────────────────
# API Vercel
# ─────────────────────────────────────────────

def api_get(path: str, token: str, team: str, params: dict | None = None) -> dict:
    params = dict(params or {})
    if team:
        params["teamId"] = team
    url = f"{API_BASE}{path}"
    if params:
        url += "?" + "&".join(f"{k}={v}" for k, v in params.items())
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "User-Agent":    "PowerZoid-Deploy/1.0",
    })
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def fetch_latest_deployment(token: str, team: str) -> dict | None:
    data = api_get("/v6/deployments", token, team, {"limit": 1})
    deployments = data.get("deployments", [])
    if not deployments:
        return None
    uid = deployments[0]["uid"]
    return api_get(f"/v13/deployments/{uid}", token, team)


def extract_meta(detail: dict) -> tuple[str, str]:
    meta = detail.get("meta", {}) or {}
    commit_message = (
        meta.get("githubCommitMessage")
        or meta.get("gitlabCommitMessage")
        or meta.get("bitbucketCommitMessage")
        or ""
    )
    branch = (
        meta.get("githubCommitRef")
        or meta.get("gitlabCommitRef")
        or meta.get("bitbucketCommitRef")
        or ""
    )
    commit_message = commit_message.strip().splitlines()[0] if commit_message.strip() else ""
    return commit_message, branch.strip()


def fetch_billing_rows(token: str, team: str, since: datetime):
    """Itera las filas FOCUS (JSONL) de /v1/billing/charges desde `since` hasta mañana."""
    until = (datetime.now(timezone.utc) + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0)
    params = {
        "from": since.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "to":   until.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
    }
    if team:
        params["teamId"] = team
    url = f"{API_BASE}/v1/billing/charges?" + "&".join(f"{k}={v}" for k, v in params.items())
    req = urllib.request.Request(url, headers={
        "Authorization":   f"Bearer {token}",
        "User-Agent":      "PowerZoid-Deploy/1.0",
        "Accept-Encoding": "gzip",
    })
    with urllib.request.urlopen(req, timeout=60) as resp:
        stream = gzip.GzipFile(fileobj=resp) if resp.headers.get("Content-Encoding") == "gzip" else resp
        for line in stream:
            line = line.strip()
            if line:
                yield json.loads(line)


def configured_period_start(now: datetime) -> str:
    """Inicio del ciclo según ~/.config/powerzoid-deploy/billing-day (día del mes), o ''."""
    try:
        day = int(read_file(BILLING_DAY_FILE))
    except ValueError:
        return ""
    if not 1 <= day <= 28:
        return ""
    start = now.replace(day=day)
    if start.date() > now.date():
        prev_month_end = now.replace(day=1) - timedelta(days=1)
        start = prev_month_end.replace(day=day)
    return start.strftime("%Y-%m-%d")


def poll_billing(token: str, team: str) -> None:
    """Calcula el consumo del ciclo actual y los cargos on-demand (lo que Vercel cobrará aparte del plan).

    EffectiveCost es el consumo a precio real, incluido lo que cubre el crédito
    del plan ('Included Credit' del dashboard).

    BilledCost ya descuenta el crédito incluido del plan; se excluyen las
    licencias de suscripción (el propio plan Pro) para coincidir con el
    'On-Demand Charges' del dashboard. El inicio del ciclo se detecta por la
    fila diaria del plan con ConsumedQuantity=1 (día de renovación), salvo
    que se configure billing-day.
    """
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=BILLING_LOOKBACK_DAYS)
    by_day: dict[str, float] = {}
    used_by_day: dict[str, float] = {}
    renewals: list[str] = []
    currency = "USD"
    try:
        for row in fetch_billing_rows(token, team, since):
            day = (row.get("ChargePeriodStart") or "")[:10]
            if row.get("ServiceCategory") == "Subscription Licenses":
                if (row.get("ConsumedQuantity") or 0) >= 1 and (row.get("EffectiveCost") or 0) > 0:
                    renewals.append(day)
                continue
            by_day[day] = by_day.get(day, 0.0) + float(row.get("BilledCost") or 0)
            used_by_day[day] = used_by_day.get(day, 0.0) + float(row.get("EffectiveCost") or 0)
            currency = row.get("BillingCurrency") or currency
    except urllib.error.HTTPError as e:
        with _lock:
            _status["billing"]["error"] = f"HTTP {e.code}"
        log(f"  [Billing] Error API: HTTP {e.code}")
        return
    except Exception as e:
        with _lock:
            _status["billing"]["error"] = str(e)
        log(f"  [Billing] Error: {e}")
        return

    period_start = configured_period_start(now) or (max(renewals) if renewals else "")
    if not period_start:
        period_start = now.replace(day=1).strftime("%Y-%m-%d")
    amount = sum(v for d, v in by_day.items() if d >= period_start)
    used   = sum(v for d, v in used_by_day.items() if d >= period_start)
    try:
        included = float(read_file(CREDIT_FILE))
    except ValueError:
        included = DEFAULT_INCLUDED_CREDIT

    with _lock:
        _status["billing"] = {
            "amount":       round(amount, 2),
            "used":         round(used, 2),
            "included":     included,
            "currency":     currency,
            "period_start": period_start,
            "updated":      time.strftime("%H:%M"),
            "error":        "",
        }
    log(f"  [Billing] Ciclo desde {period_start}: usado {used:.2f}/{included:.0f}, on-demand {amount:.2f} {currency}")


def billing_loop(token: str, team: str) -> None:
    while True:
        try:
            poll_billing(token, team)
        except Exception as e:
            log(f"  [billing_loop] Error inesperado: {e}")
        time.sleep(POLL_BILLING_SECONDS)


# ─────────────────────────────────────────────
# Ciclo de polling
# ─────────────────────────────────────────────

def poll_once(token: str, team: str) -> bool:
    """Devuelve True si el estado actual es 'building' (para el próximo intervalo)."""
    global _prev_uid

    try:
        detail = fetch_latest_deployment(token, team)
    except urllib.error.HTTPError as e:
        with _lock:
            _status["api_error"] = f"HTTP {e.code}"
        log(f"  [Vercel] Error API: HTTP {e.code}")
        return False
    except Exception as e:
        with _lock:
            _status["api_error"] = str(e)
        log(f"  [Vercel] Error de red: {e}")
        return False

    if detail is None:
        with _lock:
            _status.update(state="unknown", project=None, api_error="")
        return False

    uid = detail.get("id") or detail.get("uid")
    if uid is None:
        log(f"  [Vercel] Respuesta sin id/uid: {list(detail.keys())}")
        with _lock:
            _status["api_error"] = "respuesta inesperada de la API"
        return False

    ready_state  = detail.get("readyState", "UNKNOWN")
    project      = detail.get("name", "?")
    inspector    = detail.get("inspectorUrl", "")
    created_ms   = detail.get("ready") or detail.get("createdAt") or 0
    commit_msg, branch = extract_meta(detail)

    info = READY_STATE_INFO.get(ready_state, {"state": "unknown", "icon": "", "label": ready_state})
    is_building = ready_state in BUILDING_STATES

    prev_ready_state = _detail_cache.get(uid)

    # Nuevo deploy detectado en building → notificación de inicio
    if uid != _prev_uid and is_building:
        notify("building_start", project)

    # Transición dentro del mismo deploy: building → resultado final
    if prev_ready_state in BUILDING_STATES and ready_state not in BUILDING_STATES:
        if ready_state == "READY":
            notify("ready", project, commit_msg)
        elif ready_state == "ERROR":
            notify("error", project, commit_msg)
        elif ready_state == "CANCELED":
            notify("canceled", project, commit_msg)

    _detail_cache[uid] = ready_state
    _prev_uid = uid

    with _lock:
        _status.update({
            "state":          info["state"],
            "icon":           info["icon"],
            "label":          info["label"],
            "project":        project,
            "time_ago":       human_time_ago(created_ms) if created_ms else "",
            "commit_message": commit_msg,
            "branch":         branch,
            "inspector_url":  inspector,
            "uid":            uid,
            "api_error":      "",
        })

    return is_building


def poll_loop() -> None:
    token = read_file(TOKEN_FILE)
    team  = read_file(TEAM_FILE)

    if not token:
        log("Token de Vercel no encontrado en ~/.config/powerzoid-deploy/token")
        sys.exit(1)

    log(f"PowerZoid Deploy Daemon v1.0 — puerto {PORT}")
    log(f"  Token: configurado ✓")
    log(f"  Team:  {team or '(cuenta personal)'}")

    threading.Thread(target=billing_loop, args=(token, team), daemon=True).start()

    while True:
        try:
            building = poll_once(token, team)
        except Exception as e:
            log(f"  [poll_loop] Error inesperado: {e}")
            with _lock:
                _status["api_error"] = str(e)
            building = False
        time.sleep(POLL_BUILD_SECONDS if building else POLL_IDLE_SECONDS)


# ─────────────────────────────────────────────
# HTTP Server
# ─────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args) -> None:
        pass  # Silenciar logs HTTP en stdout

    def _json(self, code: int, data: dict) -> None:
        body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/status":
            with _lock:
                data = dict(_status)
                data["billing"] = dict(_status["billing"])
            self._json(200, data)
        elif self.path == "/ping":
            self._json(200, {"ok": True, "port": PORT})
        else:
            self._json(404, {"error": "not found"})


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────

def main() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    t = threading.Thread(target=poll_loop, daemon=True)
    t.start()

    server = HTTPServer(("127.0.0.1", PORT), Handler)
    log(f"HTTP escuchando en localhost:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log("Daemon detenido por usuario")


if __name__ == "__main__":
    main()
