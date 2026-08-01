/**
 * PowerZoid Deploy — GNOME Shell Extension
 * UUID: powerzoid-deploy@cleal.cl
 *
 * Clic izquierdo  → menú de estado (proyecto, alineación, abrir en Vercel)
 * Clic derecho    → abre el mismo menú (alineación accesible desde ambos)
 */

import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import Gio from 'gi://Gio';
import St from 'gi://St';
import Clutter from 'gi://Clutter';
import Soup from 'gi://Soup';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';
import { Extension } from 'resource:///org/gnome/shell/extensions/extension.js';

const DAEMON_URL   = 'http://127.0.0.1:6792';
const POLL_IDLE_MS = 8000;
const POLL_BUILD_MS = 3000;
const VALID_ALIGNS = ['left', 'center', 'right'];

const POSITION_PATH = `${GLib.get_home_dir()}/.config/powerzoid-deploy/panel-position`;


// ─────────────────────────────────────────────────────────────────────────────
// Indicador en el panel
// ─────────────────────────────────────────────────────────────────────────────

const DeployIndicator = GObject.registerClass(
class DeployIndicator extends PanelMenu.Button {

    _init(extension, initialAlign = 'left') {
        super._init(0.0, 'PowerZoid Deploy');

        this._ext          = extension;
        this._currentAlign = initialAlign;
        this._session      = new Soup.Session();
        this._session.timeout = 5;
        this._lastState     = 'unknown';

        // ── Panel ────────────────────────────────────────────────────────
        this._label = new St.Label({
            text:    '▲ offline',
            y_align: Clutter.ActorAlign.CENTER,
        });
        const box = new St.BoxLayout({ style_class: 'panel-status-menu-box' });
        box.add_child(this._label);
        this.add_child(box);

        // ── Clic derecho: abrir el mismo menú ────────────────────────────
        this.connect('button-release-event', (_actor, ev) => {
            if (ev.get_button() === 3) {
                if (!this.menu.isOpen)
                    this.menu.open();
                return Clutter.EVENT_STOP;
            }
            return Clutter.EVENT_PROPAGATE;
        });

        // ── Menú ───────────────────────────────────────────────────────
        this._buildMenu();

        this._pollTimer = null;
        this._startPolling();
    }

    // ─────────────────────────────────────────────────────────────────────
    // Construcción del menú
    // ─────────────────────────────────────────────────────────────────────

    _buildMenu() {
        // ── Alineación ─────────────────────────────────────────────────
        this._alignItems = {};
        [
            ['Alinear a la izquierda', 'left'],
            ['Alinear al centro',       'center'],
            ['Alinear a la derecha',    'right'],
        ].forEach(([label, align]) => {
            const item = new PopupMenu.PopupMenuItem(label);
            this._alignItems[align] = item;
            item.connect('activate', () => this._setAlignment(align));
            this.menu.addMenuItem(item);
        });
        this._updateAlignMarks(this._currentAlign);

        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());

        // ── Información del proyecto (dinámica) ───────────────────────
        this._infoSection = new PopupMenu.PopupMenuSection();
        this.menu.addMenuItem(this._infoSection);
        this._rebuildInfo(null);

        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());

        // ── Abrir en Vercel ─────────────────────────────────────────────
        this._openItem = new PopupMenu.PopupMenuItem('↗  Abrir en Vercel');
        this._openItem.connect('activate', () => this._openInVercel());
        this._openItem.setSensitive(false);
        this.menu.addMenuItem(this._openItem);
    }

    // ─────────────────────────────────────────────────────────────────────
    // Alineación
    // ─────────────────────────────────────────────────────────────────────

    _setAlignment(align) {
        if (align === this._currentAlign) return;

        this._ext.savePanelPosition(align);

        // Reinicio completo de la extensión: evita un bug de rendering de
        // GNOME Shell 50 al reubicar actores directamente entre boxes del panel.
        const uuid = this._ext.uuid;
        GLib.idle_add(GLib.PRIORITY_DEFAULT, () => {
            Main.extensionManager.disableExtension(uuid);
            Main.extensionManager.enableExtension(uuid);
            return GLib.SOURCE_REMOVE;
        });
    }

    _updateAlignMarks(activeAlign) {
        Object.entries(this._alignItems).forEach(([align, item]) => {
            item.setOrnament(
                align === activeAlign
                    ? PopupMenu.Ornament.DOT
                    : PopupMenu.Ornament.NONE
            );
        });
    }

    // ─────────────────────────────────────────────────────────────────────
    // Polling al daemon
    // ─────────────────────────────────────────────────────────────────────

    _startPolling() {
        this._fetchStatus();
    }

    _scheduleNext(intervalMs) {
        if (this._pollTimer) {
            GLib.source_remove(this._pollTimer);
            this._pollTimer = null;
        }
        this._pollTimer = GLib.timeout_add(GLib.PRIORITY_DEFAULT, intervalMs, () => {
            this._fetchStatus();
            return GLib.SOURCE_REMOVE;
        });
    }

    _fetchStatus() {
        let msg;
        try { msg = Soup.Message.new('GET', `${DAEMON_URL}/status`); }
        catch (_e) { this._applyDisconnected(); return; }

        this._session.send_and_read_async(msg, GLib.PRIORITY_DEFAULT, null,
            (session, result) => {
                try {
                    const bytes = session.send_and_read_finish(result);
                    const data  = JSON.parse(new TextDecoder().decode(bytes.get_data()));
                    this._applyStatus(data);
                    this._scheduleNext(data.state === 'building' ? POLL_BUILD_MS : POLL_IDLE_MS);
                } catch (_e) {
                    this._applyDisconnected();
                    this._scheduleNext(POLL_IDLE_MS);
                }
            }
        );
    }

    // ─────────────────────────────────────────────────────────────────────
    // Actualización de UI
    // ─────────────────────────────────────────────────────────────────────

    _applyStatus(status) {
        this._lastState = status.state || 'unknown';
        const project = status.project || '?';

        if (status.state === 'building') {
            this._label.set_text(`▲ ${project} · ⚙ Building`);
        } else if (status.state && status.icon) {
            this._label.set_text(`▲ ${project} · ${status.icon} ${status.time_ago}`);
        } else {
            this._label.set_text(`▲ ${project}`);
        }

        this._lastInspectorUrl = status.inspector_url || null;
        this._openItem.setSensitive(!!status.inspector_url);
        this._rebuildInfo(status);
    }

    _applyDisconnected() {
        if (this._lastState === 'disconnected') return;
        this._lastState = 'disconnected';
        this._label.set_text('▲ offline');
        this._openItem.setSensitive(false);
        this._rebuildInfo(null);
    }

    _rebuildInfo(status) {
        this._infoSection.removeAll();

        if (!status || !status.project) {
            this._addInfo('⚠  Sin datos de despliegue', true);
            return;
        }

        this._addInfo(`📦  ${status.project}`);
        this._addInfo(`${status.icon || '❓'}  ${status.label || 'Desconocido'}`);
        if (status.commit_message)
            this._addInfo(`💬  ${status.commit_message}`);
        if (status.branch)
            this._addInfo(`🌿  ${status.branch}`);
        if (status.time_ago)
            this._addInfo(`🕐  hace ${status.time_ago}`);
    }

    _addInfo(text, bold = false) {
        const item = new PopupMenu.PopupMenuItem(text, { reactive: false });
        if (bold) item.label.set_style('font-weight: bold;');
        this._infoSection.addMenuItem(item);
    }

    // ─────────────────────────────────────────────────────────────────────
    // Acciones
    // ─────────────────────────────────────────────────────────────────────

    _openInVercel() {
        if (!this._lastInspectorUrl) return;
        try {
            Gio.AppInfo.launch_default_for_uri(this._lastInspectorUrl, null);
        } catch (_e) {}
    }

    // ─────────────────────────────────────────────────────────────────────
    // Limpieza
    // ─────────────────────────────────────────────────────────────────────

    destroy() {
        if (this._pollTimer) { GLib.source_remove(this._pollTimer); this._pollTimer = null; }
        super.destroy();
    }
});


// ─────────────────────────────────────────────────────────────────────────────
// Extension
// ─────────────────────────────────────────────────────────────────────────────

export default class PowerZoidDeployExtension extends Extension {

    enable() {
        const align = this._loadPanelPosition();
        this._indicator = new DeployIndicator(this, align);
        Main.panel.addToStatusArea('powerzoid-deploy', this._indicator, -1, align);
    }

    disable() {
        this._indicator?.destroy();
        this._indicator = null;
    }

    _loadPanelPosition() {
        try {
            const file = Gio.File.new_for_path(POSITION_PATH);
            const [, bytes] = file.load_contents(null);
            const val = new TextDecoder().decode(bytes).trim();
            return VALID_ALIGNS.includes(val) ? val : 'left';
        } catch (_e) {}
        return 'left';
    }

    savePanelPosition(align) {
        try {
            const dir = Gio.File.new_for_path(`${GLib.get_home_dir()}/.config/powerzoid-deploy`);
            dir.make_directory_with_parents(null);
        } catch (_e) {}
        try {
            const file = Gio.File.new_for_path(POSITION_PATH);
            file.replace_contents(
                new TextEncoder().encode(align),
                null, false,
                Gio.FileCreateFlags.REPLACE_DESTINATION,
                null
            );
        } catch (_e) {}
    }
}
