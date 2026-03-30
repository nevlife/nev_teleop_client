"""
Telemetry side panel — mirrors the original web dashboard cards.

Cards: HUNTER, MUX, NETWORK, TWIST, E-STOP, JOYSTICK, RESOURCES,
       NET INTERFACES, DISK, ALERTS
"""
import json
import logging
import math
import threading

import zenoh
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QScrollArea, QFrame,
)

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────
MODE_NAMES   = {-1: 'IDLE', 0: 'CTRL', 1: 'NAV', 2: 'REMOTE'}
SRC_NAMES    = {-1: 'NONE', 0: 'NAV',  1: 'TELEOP'}
NS_CODES     = {0: 'OK', 1: 'HB-DELAY', 2: 'SOCK-ERR'}
BRIDGE_FLAGS = {0: 'OK', 1: 'SRV-CMD', 2: 'SOCK-ERR', 3: 'HB-TIMEOUT', 4: 'CTRL-TIMEOUT'}
MUX_FLAGS    = {0: 'OK', 1: 'NAV+NO-TELEOP'}

# ── Colors ───────────────────────────────────────────────────────────────────
BG       = '#0d1117'
BG_CARD  = '#161b22'
BORDER   = '#21262d'
TEXT     = '#c9d1d9'
MUTED    = '#8b949e'
GREEN    = '#3fb950'
RED      = '#f85149'
YELLOW   = '#d29922'
BLUE     = '#58a6ff'
FONT     = "Consolas, 'Courier New', monospace"


def _sgn(v):
    return f'{v:+.2f}'


def _fmt_gb(b):
    return f'{b / 1073741824:.1f}'


def _fmt_rate(bps):
    if bps < 1024:
        return f'{bps:.0f}B/s'
    if bps < 1048576:
        return f'{bps / 1024:.1f}K/s'
    return f'{bps / 1048576:.1f}M/s'


def _text_cls(val, warn_at, error_at):
    if val >= error_at:
        return RED
    if val >= warn_at:
        return YELLOW
    return ''


def _dot_html(on, color=GREEN):
    c = color if on else BORDER
    return f'<span style="display:inline-block;width:7px;height:7px;border-radius:50%;background:{c};vertical-align:middle;margin-right:4px;"></span>'


def _kv(key, val, color=''):
    style = f'color:{color};' if color else ''
    return (
        f'<div style="display:flex;justify-content:space-between;align-items:baseline;line-height:1.8;gap:8px;">'
        f'<span style="color:{MUTED};white-space:nowrap;">{key}</span>'
        f'<span style="text-align:right;{style}">{val}</span>'
        f'</div>'
    )


def _bar(pct):
    w = max(0, min(100, pct or 0))
    color = RED if w > 90 else YELLOW if w > 70 else BLUE
    return (
        f'<div style="width:100%;height:3px;background:{BORDER};border-radius:2px;margin:2px 0 6px;overflow:hidden;">'
        f'<div style="height:100%;width:{w}%;background:{color};border-radius:2px;"></div>'
        f'</div>'
    )


class TelemetryPanel(QWidget):
    """Side panel with telemetry cards, matching the original web dashboard."""

    telemetry_updated = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._sub = None
        self.setFixedWidth(340)

        self.setStyleSheet(f'background:{BG}; color:{TEXT}; font-family:{FONT}; font-size:12px;')

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet(
            f'QScrollArea {{ border: none; background: {BORDER}; }}'
            f'QScrollBar:vertical {{ width:4px; background:transparent; }}'
            f'QScrollBar::handle:vertical {{ background:{BORDER}; border-radius:2px; }}'
        )

        container = QWidget()
        self._layout = QVBoxLayout(container)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(1)

        # Create card labels
        self._cards = {}
        for name in ['HUNTER', 'MUX', 'NETWORK', 'TWIST', 'E-STOP',
                      'JOYSTICK', 'RESOURCES', 'NET INTERFACES', 'DISK', 'ALERTS']:
            card = self._make_card(name)
            self._layout.addWidget(card)
            self._cards[name] = card

        self._layout.addStretch()
        scroll.setWidget(container)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        self.telemetry_updated.connect(self._refresh)

    def _make_card(self, title):
        frame = QFrame()
        frame.setStyleSheet(f'background:{BG_CARD}; padding:8px 10px;')
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(0)

        title_lbl = QLabel(title)
        title_lbl.setStyleSheet(f'font-size:10px;letter-spacing:1.5px;color:{MUTED};margin-bottom:6px;')
        layout.addWidget(title_lbl)

        body = QLabel()
        body.setTextFormat(Qt.TextFormat.RichText)
        body.setWordWrap(True)
        body.setStyleSheet(f'color:{TEXT};')
        body.setObjectName(f'body_{title}')
        layout.addWidget(body)

        return frame

    def _body(self, name) -> QLabel:
        return self._cards[name].findChild(QLabel, f'body_{name}')

    def start(self, session: zenoh.Session):
        self._sub = session.declare_subscriber('nev/gcs/telemetry', self._on_telemetry)
        # 시작 시 테스트 텍스트 표시
        self._body('ALERTS').setText('<span style="color:#3fb950;">Panel OK</span>')
        logger.info('TelemetryPanel started')

    def stop(self):
        if self._sub:
            self._sub.undeclare()
            self._sub = None

    def _on_telemetry(self, sample):
        try:
            raw = bytes(sample.payload)
            self.telemetry_updated.emit(raw.decode())
        except Exception as e:
            logger.warning(f'Telemetry parse error: {e}')

    def update_video_stats(self, stats: dict):
        """Called from main window with video widget stats."""
        # Merged into network card via _refresh; store for next render
        self._video_stats = stats

    def _refresh(self, raw: str):
        try:
            s = json.loads(raw)
            self._render_hunter(s.get('hunter', {}))
            self._render_mux(s.get('mux', {}))
            self._render_network(s.get('network', {}))
            self._render_twist(s.get('twist', {}))
            self._render_estop(s.get('estop', {}), s.get('control', {}))
            self._render_joy(s.get('control', {}), s.get('station_connected', False))
            self._render_resources(s.get('resources', {}), s.get('gpu_list', []))
            self._render_netifaces(s.get('net_interfaces', []), s.get('resources', {}))
            self._render_disk(s.get('disk_partitions', []))
            self._render_alerts(s.get('alerts', []))
        except Exception as e:
            logger.error(f'_refresh error: {e}', exc_info=True)

    def _render_hunter(self, hs):
        steer_deg = hs.get('steering_angle', 0) * 180.0 / math.pi if hs.get('steering_angle', 0) != 0 else 0
        bv = hs.get('battery_voltage', 0)
        bat_cls = _text_cls(bv, 22, 20) if bv < 22 else GREEN
        err = hs.get('error_code', 0)
        err_cls = RED if err != 0 else ''
        err_str = 'NONE' if err == 0 else f'0x{err:X}'

        self._body('HUNTER').setText(
            _kv('vel', f'{_sgn(hs.get("linear_vel", 0))} m/s') +
            _kv('steer', f'{steer_deg:.1f} deg') +
            _kv('state', str(hs.get('robot_state', 0))) +
            _kv('ctrl', str(hs.get('control_mode', 0))) +
            _kv('err', err_str, err_cls) +
            _kv('bat', f'{bv:.2f} V', bat_cls)
        )

    def _render_mux(self, mx):
        mode = mx.get('requested_mode', -1)
        mode_cls = BLUE if mode == 2 else MUTED if mode == -1 else ''
        self._body('MUX').setText(
            _kv('mode', MODE_NAMES.get(mode, str(mode)), mode_cls) +
            _kv('src', SRC_NAMES.get(mx.get('active_source', -1), '?')) +
            _kv('remote', _dot_html(mx.get('remote_enabled', False)) + ('YES' if mx.get('remote_enabled') else 'NO')) +
            _kv('nav', _dot_html(mx.get('nav_active', False)) + ('ON' if mx.get('nav_active') else 'OFF')) +
            _kv('teleop', _dot_html(mx.get('teleop_active', False)) + ('ON' if mx.get('teleop_active') else 'OFF')) +
            _kv('final', _dot_html(mx.get('final_active', False)) + ('ON' if mx.get('final_active') else 'OFF'))
        )

    def _render_network(self, ns):
        connected = ns.get('connected', False)
        st_cls = GREEN if connected else RED
        rtt = ns.get('ht_rtt', 0)
        rtt_cls = _text_cls(rtt, 50, 100)

        enc_d = ns.get('encode_delay', 0)
        net_d = ns.get('video_net_delay', 0)
        tele_d = ns.get('tele_delay_ms', 0)
        total = enc_d + net_d

        sep = f'<div style="line-height:1.8;color:{MUTED};">─────</div>'

        self._body('NETWORK').setText(
            _kv('status', _dot_html(connected, st_cls) + (NS_CODES.get(ns.get('status_code', 2), '?')), st_cls) +
            _kv('video tx', f'{ns.get("bw_video_tx", 0):.2f} Mbps' if ns.get('bw_video_tx', 0) > 0 else '—') +
            _kv('video rx', f'{ns.get("bw_video_rx", 0):.2f} Mbps' if ns.get('bw_video_rx', 0) > 0 else '—') +
            _kv('tele rx', f'{ns.get("bw_telemetry", 0):.2f} Mbps' if ns.get('bw_telemetry', 0) > 0 else '—') +
            sep +
            _kv('enc delay', f'{enc_d:.1f} ms' if enc_d > 0 else '—') +
            _kv('net delay', f'{net_d:.1f} ms' if net_d > 0 else '—') +
            _kv('total delay', f'{total:.1f} ms' if total > 0 else '—') +
            sep +
            _kv('tele delay', f'{tele_d:.1f} ms' if tele_d > 0 else '—') +
            _kv('rtt', f'{rtt:.1f} ms', rtt_cls)
        )

    def _render_twist(self, tv):
        def row(lx, az):
            return f'{_sgn(lx)} m/s / {_sgn(az)} rad/s'
        self._body('TWIST').setText(
            _kv('nav', row(tv.get('nav_lx', 0), tv.get('nav_az', 0))) +
            _kv('teleop', row(tv.get('teleop_lx', 0), tv.get('teleop_az', 0))) +
            _kv('final', row(tv.get('final_lx', 0), tv.get('final_az', 0)))
        )

    def _render_estop(self, es, ctrl):
        active = es.get('is_estop', False) or ctrl.get('estop', False)
        if active:
            self._cards['E-STOP'].setStyleSheet(
                f'background:rgba(248,81,73,0.07);border-left:2px solid {RED};padding:8px 10px;')
            status = f'<span style="color:{RED};">&#9888; E-STOP ACTIVE</span>'
        else:
            self._cards['E-STOP'].setStyleSheet(f'background:{BG_CARD};padding:8px 10px;')
            status = f'<span style="color:{GREEN};">NORMAL</span>'

        bf = es.get('bridge_flag', 0)
        mf = es.get('mux_flag', 0)
        self._body('E-STOP').setText(
            _kv('status', status) +
            _kv('bridge', BRIDGE_FLAGS.get(bf, str(bf)), RED if bf != 0 else '') +
            _kv('mux', MUX_FLAGS.get(mf, str(mf)), YELLOW if mf != 0 else '')
        )

    def _render_joy(self, ctrl, station_connected):
        joy_con = ctrl.get('joystick_connected', False)
        joy_cls = GREEN if joy_con else RED
        sta_cls = GREEN if station_connected else RED

        self._body('JOYSTICK').setText(
            _kv('station',
                _dot_html(station_connected, sta_cls) + ('CONNECTED' if station_connected else 'OFFLINE'),
                sta_cls) +
            _kv('joystick',
                _dot_html(joy_con, joy_cls) + ('CONNECTED' if joy_con else 'DISCONNECTED'),
                joy_cls) +
            _kv('cmd',
                f'{ctrl.get("linear_x", 0):.3f} m/s / '
                f'{ctrl.get("steer_angle_deg", 0):.1f} deg / '
                f'{ctrl.get("angular_z", 0):.4f} rad/s')
        )

    def _render_resources(self, r, gpu_list):
        cpu = r.get('cpu_usage', 0)
        cpu_cls = _text_cls(cpu, 70, 90)
        temp = r.get('cpu_temp', 0)
        temp_cls = _text_cls(temp, 60, 80)
        load = r.get('cpu_load', 0)

        html = _kv('CPU',
            f'<span style="color:{cpu_cls or TEXT};">{cpu:.1f}%</span>'
            f'&ensp;<span style="color:{temp_cls or TEXT};">{temp:.1f}C</span>'
            f'&ensp;load {load:.2f}') + _bar(cpu)

        ram_used = r.get('ram_used', 0)
        ram_total = r.get('ram_total', 0)
        ram_pct = (ram_used / ram_total * 100) if ram_total > 0 else 0
        html += _kv('RAM', f'{ram_used} / {ram_total} MB') + _bar(ram_pct)

        if gpu_list:
            for i, g in enumerate(gpu_list):
                if not g:
                    continue
                usage = g.get('gpu_usage', 0)
                gtemp = g.get('gpu_temp', 0)
                power = g.get('gpu_power', 0)
                mem_u = g.get('gpu_mem_used', 0)
                mem_t = g.get('gpu_mem_total', 0)
                g_cls = _text_cls(usage, 70, 90)
                gt_cls = _text_cls(gtemp, 60, 80)
                html += _kv(f'GPU{i}',
                    f'<span style="color:{g_cls or TEXT};">{usage:.1f}%</span>'
                    f'&ensp;<span style="color:{gt_cls or TEXT};">{gtemp:.0f}C</span>'
                    f'&ensp;{power:.0f}W') + _bar(usage)
                html += _kv('', f'mem {int(mem_u)} / {int(mem_t)} MB')

        self._body('RESOURCES').setText(html)

    def _render_netifaces(self, ifaces, resources):
        if not ifaces:
            self._body('NET INTERFACES').setText(f'<span style="color:{MUTED};">no data</span>')
            return

        html = ''
        total = resources.get('net_total_ifaces', 0)
        active = resources.get('net_active_ifaces', 0)
        if total > 0:
            html += _kv('total', f'{active} up / {total}')

        for iface in ifaces:
            if not iface or not iface.get('name'):
                continue
            is_up = iface.get('is_up', False)
            up_cls = GREEN if is_up else RED
            spd = f'{iface.get("speed_mbps", 0)}M' if iface.get('speed_mbps', 0) > 0 else '—'
            html += _kv(iface['name'],
                _dot_html(is_up, up_cls) +
                f'<span style="color:{up_cls};">{"UP" if is_up else "DOWN"}</span>'
                f'&ensp;{spd}')
            html += _kv('',
                f'&#8595;{_fmt_rate(iface.get("in_bps", 0))}'
                f'&ensp;&#8593;{_fmt_rate(iface.get("out_bps", 0))}')

        self._body('NET INTERFACES').setText(html)

    def _render_disk(self, partitions):
        if not partitions:
            self._body('DISK').setText(f'<span style="color:{MUTED};">no data</span>')
            return
        html = ''
        for p in partitions:
            if not p or not p.get('mountpoint'):
                continue
            pct = p.get('percent', 0)
            cls = _text_cls(pct, 70, 90)
            html += _kv(p['mountpoint'],
                f'{_fmt_gb(p.get("used_bytes", 0))} / {_fmt_gb(p.get("total_bytes", 1))} GB'
                f'  <span style="color:{cls or TEXT};">{pct:.0f}%</span>') + _bar(pct)
        self._body('DISK').setText(html)

    def _render_alerts(self, alerts):
        if not alerts:
            self._body('ALERTS').setText(f'<span style="color:{MUTED};">—</span>')
            return
        html = ''
        for a in alerts:
            lvl = a.get('level', 'ok')
            color = RED if lvl == 'error' else YELLOW if lvl == 'warn' else TEXT
            weight = 'font-weight:bold;' if lvl == 'error' else ''
            html += f'<div style="padding:2px 0;line-height:1.6;color:{color};{weight}">&#9650; {a.get("message", "")}</div>'
        self._body('ALERTS').setText(html)
