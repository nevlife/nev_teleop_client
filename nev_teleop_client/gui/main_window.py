import logging
import time

import zenoh
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QLabel, QPushButton,
)

from .video_grid import VideoGrid
from .telemetry_panel import TelemetryPanel

logger = logging.getLogger(__name__)


def _resolve_vehicle_ids(cfg: dict) -> list[str]:
    """Read vehicles list with single-vehicle_id fallback. Always non-empty."""
    vehicles = cfg.get('vehicles')
    if isinstance(vehicles, list) and vehicles:
        ids = []
        for entry in vehicles:
            if isinstance(entry, dict) and 'id' in entry:
                ids.append(str(entry['id']))
            elif isinstance(entry, (str, int)):
                ids.append(str(entry))
        if ids:
            return ids
    single = cfg.get('vehicle_id')
    if single is not None:
        return [str(single)]
    return ['0']

BG       = '#0d1117'
BG_CARD  = '#161b22'
BORDER   = '#21262d'
BORDER2  = '#30363d'
TEXT     = '#c9d1d9'
MUTED    = '#8b949e'
GREEN    = '#3fb950'
RED      = '#f85149'
YELLOW   = '#d29922'
BLUE     = '#58a6ff'
FONT     = "Consolas, 'Courier New', monospace"


class Badge(QLabel):

    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self._base_text = text
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFixedHeight(20)
        self.setMinimumWidth(36)
        self.set_state('off')

    def set_state(self, state, text=None):
        if text:
            self.setText(text)
        else:
            self.setText(self._base_text)

        colors = {
            'ok':    (GREEN, GREEN),
            'warn':  (YELLOW, YELLOW),
            'error': (RED, RED),
            'off':   (MUTED, BORDER2),
        }
        fg, border = colors.get(state, colors['off'])
        self.setStyleSheet(
            f'padding:1px 8px; border:1px solid {border}; border-radius:3px;'
            f'font-size:11px; color:{fg}; background:transparent;'
        )


class MainWindow(QMainWindow):

    def __init__(self, session: zenoh.Session, cfg: dict, client=None):
        super().__init__()
        self._session = session
        self._cfg = cfg
        self._client = client
        self._last_state = {}

        self.setWindowTitle('NEV Teleop Client')
        self.setMinimumSize(1280, 720)
        self.setStyleSheet(
            f'QMainWindow {{ background:{BG}; }}'
            f'QWidget {{ color:{TEXT}; font-family:{FONT}; font-size:12px; }}'
        )

        central = QWidget()
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        topbar = QWidget()
        topbar.setFixedHeight(36)
        topbar.setStyleSheet(f'background:{BG}; border-bottom:1px solid {BORDER};')
        tb_layout = QHBoxLayout(topbar)
        tb_layout.setContentsMargins(12, 0, 12, 0)

        title = QLabel('NEV CLIENT')
        title.setStyleSheet(f'font-size:13px; font-weight:bold; letter-spacing:1px; color:{TEXT};')
        tb_layout.addWidget(title)
        tb_layout.addStretch()

        self._badge_veh = Badge('VEH')
        self._badge_stas = Badge('STAS')
        self._badge_joy = Badge('JOY')
        self._badge_rem = Badge('REM')
        for b in (self._badge_veh, self._badge_stas, self._badge_joy, self._badge_rem):
            tb_layout.addWidget(b)

        self._clock = QLabel('--:--:--')
        self._clock.setStyleSheet(f'font-size:11px; color:{MUTED}; margin-left:8px;')
        tb_layout.addWidget(self._clock)

        main_layout.addWidget(topbar)

        cmdbar = QWidget()
        cmdbar.setFixedHeight(38)
        cmdbar.setStyleSheet(f'background:{BG}; border-bottom:1px solid {BORDER};')
        cb_layout = QHBoxLayout(cmdbar)
        cb_layout.setContentsMargins(12, 0, 12, 0)

        mode_label = QLabel('MODE')
        mode_label.setStyleSheet(f'color:{MUTED}; font-size:11px; margin-right:4px;')
        cb_layout.addWidget(mode_label)

        self._mode_buttons = {}
        for mode_val, mode_name in [(-1, 'IDLE'), (0, 'CTRL'), (1, 'NAV'), (2, 'REMOTE')]:
            btn = QPushButton(mode_name)
            btn.setStyleSheet(self._mode_btn_style(False))
            btn.clicked.connect(lambda checked, m=mode_val: self._on_mode_click(m))
            cb_layout.addWidget(btn)
            self._mode_buttons[mode_val] = btn

        cb_layout.addStretch()

        self._estop_btn = QPushButton('\u25A0 E-STOP')
        self._estop_btn.setStyleSheet(
            f'color:{RED}; border:1px solid {RED}; padding:3px 18px;'
            f'font-size:12px; font-weight:bold; background:transparent; border-radius:3px;'
        )
        self._estop_btn.clicked.connect(self._on_estop_click)
        cb_layout.addWidget(self._estop_btn)

        main_layout.addWidget(cmdbar)

        content = QWidget()
        content_layout = QHBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        # Vehicle list: cfg['vehicles'] preferred, falls back to cfg['vehicle_id'].
        # TODO: dynamic vehicle discovery — follow-up PR.
        self._vehicle_ids = _resolve_vehicle_ids(cfg)
        rtp_mode = bool(cfg.get('rtp_mode', False))

        self.video_grid = VideoGrid(self._vehicle_ids, rtp_mode=rtp_mode)
        content_layout.addWidget(self.video_grid, stretch=1)

        separator = QWidget()
        separator.setFixedWidth(1)
        separator.setStyleSheet(f'background:{BORDER};')
        content_layout.addWidget(separator)

        # Telemetry panel shows a single (selected) vehicle for now.
        # Vehicle selection UI is a follow-up PR.
        self._selected_vehicle_id = self._vehicle_ids[0]
        self.telemetry_panel = TelemetryPanel(vehicle_id=self._selected_vehicle_id)
        content_layout.addWidget(self.telemetry_panel, stretch=0)

        main_layout.addWidget(content, stretch=1)
        self.setCentralWidget(central)

        self._clock_timer = QTimer()
        self._clock_timer.timeout.connect(self._update_clock)
        self._clock_timer.start(1000)

        self._stats_timer = QTimer()
        self._stats_timer.timeout.connect(self._update_stats)
        self._stats_timer.start(1000)

        self.telemetry_panel.telemetry_updated.connect(self._on_telemetry_raw)

    def start(self):
        self.video_grid.start(self._session)
        self.telemetry_panel.start(self._session)
        logger.info('MainWindow started')

    def stop(self):
        self._clock_timer.stop()
        self._stats_timer.stop()
        self.video_grid.stop()
        self.telemetry_panel.stop()
        logger.info('MainWindow stopped')

    def _update_clock(self):
        self._clock.setText(time.strftime('%H:%M:%S'))

    def _update_stats(self):
        # Telemetry panel currently mirrors the selected vehicle's video stats.
        stats = self.video_grid.get_stats(self._selected_vehicle_id)
        self.telemetry_panel.update_video_stats(stats)
        if self._client:
            self.telemetry_panel.update_rtt(self._client.rtt_client_server_ms)

    def _on_telemetry_raw(self, raw: str):
        import json
        s = json.loads(raw)
        self._last_state = s

        robot_age = s.get('robot_age', -1)
        if robot_age < 0:
            self._badge_veh.set_state('off')
        elif robot_age < 2:
            self._badge_veh.set_state('ok')
        else:
            self._badge_veh.set_state('error', f'VEH {robot_age:.0f}s')

        self._badge_stas.set_state('ok' if s.get('station_connected', False) else 'error')
        ctrl = s.get('control', {})
        self._badge_joy.set_state('ok' if ctrl.get('joystick_connected', False) else 'off')
        self._badge_rem.set_state('ok' if s.get('remote_enabled', False) else 'off')

        active_mode = s.get('mux', {}).get('requested_mode', -1)
        station_on = s.get('station_connected', False)
        for mode_val, btn in self._mode_buttons.items():
            is_active = (mode_val == active_mode)
            btn.setStyleSheet(self._mode_btn_style(is_active, not station_on))

        estop_active = ctrl.get('estop', False) or s.get('estop', {}).get('is_estop', False)
        if estop_active:
            self._estop_btn.setText('\u25A0 RELEASE')
            self._estop_btn.setStyleSheet(
                f'color:#fff; border:1px solid {RED}; padding:3px 18px;'
                f'font-size:12px; font-weight:bold; background:{RED}; border-radius:3px;'
            )
        else:
            self._estop_btn.setText('\u25A0 E-STOP')
            self._estop_btn.setStyleSheet(
                f'color:{RED}; border:1px solid {RED}; padding:3px 18px;'
                f'font-size:12px; font-weight:bold; background:transparent; border-radius:3px;'
            )

    def _on_mode_click(self, mode: int):
        if self._client:
            self._client.send_cmd_mode(mode)

    def _on_estop_click(self):
        if self._client:
            ctrl = self._last_state.get('control', {})
            active = not ctrl.get('estop', False)
            self._client.send_estop(active)

    def _mode_btn_style(self, active=False, disabled=False):
        if active:
            return (
                f'background:rgba(88,166,255,0.12); color:#fff; border:1px solid {BLUE};'
                f'font-size:11px; padding:3px 10px; border-radius:3px;'
            )
        opacity = 'opacity:0.4;' if disabled else ''
        return (
            f'background:transparent; color:{MUTED}; border:1px solid {BORDER2};'
            f'font-size:11px; padding:3px 10px; border-radius:3px; {opacity}'
        )

    def closeEvent(self, event):
        self.stop()
        super().closeEvent(event)
