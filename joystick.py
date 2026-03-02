import math
import time
import threading
import logging
from typing import Optional

from station_state import StationState

logger = logging.getLogger(__name__)

try:
    import pygame
    _HAS_PYGAME = True
except ImportError:
    _HAS_PYGAME = False
    logger.warning('pygame not installed — joystick disabled')


class JoystickHandler:
    def __init__(self, state: StationState, cfg: dict):
        self.state          = state
        self.axis_speed     = cfg.get('axis_speed',     1)
        self.axis_steer     = cfg.get('axis_steer',     3)
        self.axis_raw_speed = cfg.get('axis_raw_speed', 4)
        self.axis_raw_steer = cfg.get('axis_raw_steer', 0)
        self.btn_estop      = cfg.get('btn_estop',      4)
        self.max_speed      = cfg.get('max_speed',      1.0)
        self.max_steer      = math.radians(cfg.get('max_steer_deg', 27.0))
        self.deadzone       = cfg.get('deadzone',       0.05)
        self.invert_speed   = cfg.get('invert_speed',   True)

        self._joystick: Optional[object] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._prev_btn_estop = False
        self._client = None
        self._loop = None
        self._use_estop_btn  = False
        self._has_raw_speed  = False
        self._has_raw_steer  = False
        self._last_broadcast = 0.0

    def start(self):
        if not _HAS_PYGAME:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, name='joystick', daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)

    def set_client(self, client):
        self._client = client

    def set_loop(self, loop):
        self._loop = loop

    def _run(self):
        pygame.init()
        pygame.joystick.init()

        while self._running:
            if self._joystick is None:
                self._try_connect()
                if self._joystick is None:
                    time.sleep(1.0)
                continue

            try:
                pygame.event.pump()
            except pygame.error as e:
                logger.warning(f'Joystick disconnected: {e}')
                self._on_disconnect()
                continue

            if pygame.joystick.get_count() == 0:
                logger.warning('Joystick disconnected')
                self._on_disconnect()
                continue

            speed_raw = self._apply_deadzone(self._joystick.get_axis(self.axis_speed))
            if self.invert_speed:
                speed_raw = -speed_raw
            self.state.linear_x = speed_raw * self.max_speed
            print(f'speed_raw={speed_raw:.3f}  linear_x={self.state.linear_x:.4f} m/s')

            steer_raw = self._apply_deadzone(self._joystick.get_axis(self.axis_steer))
            self.state.steer_angle = -steer_raw * self.max_steer
            print(f'steer_raw={steer_raw:.3f}  steer_angle={math.degrees(self.state.steer_angle):.1f}deg')

            if self._has_raw_speed:
                self.state.raw_speed = self._joystick.get_axis(self.axis_raw_speed)
            else:
                self.state.raw_speed = 0.0

            if self._has_raw_steer:
                self.state.raw_steer = self._joystick.get_axis(self.axis_raw_steer)
            else:
                self.state.raw_steer = 0.0

            if self._use_estop_btn:
                btn = bool(self._joystick.get_button(self.btn_estop))
                if btn and not self._prev_btn_estop:
                    self._toggle_estop()
                self._prev_btn_estop = btn

            now = time.monotonic()
            if self._client and now - self._last_broadcast >= 0.05:
                if self._loop:
                    self._loop.call_soon_threadsafe(self._client.send_joystick_connected, self.state.joystick_connected)
                self._last_broadcast = now

            time.sleep(0.02)  # 50 Hz

        pygame.quit()

    def _try_connect(self):
        pygame.joystick.quit()
        pygame.joystick.init()
        if pygame.joystick.get_count() == 0:
            self.state.joystick_connected = False
            return

        joy = pygame.joystick.Joystick(0)
        joy.init()
        self._joystick = joy
        logger.info(f'Joystick connected: {joy.get_name()}')

        self._validate_config(joy)
        self.state.joystick_connected = True

    def _validate_config(self, joy):
        num_axes    = joy.get_numaxes()
        num_buttons = joy.get_numbuttons()

        if self.axis_speed >= num_axes:
            logger.error(
                f'axis_speed={self.axis_speed} out of range '
                f'(joystick has {num_axes} axes) — clamped to 0'
            )
            self.axis_speed = 0

        if self.axis_steer >= num_axes:
            logger.error(
                f'axis_steer={self.axis_steer} out of range '
                f'(joystick has {num_axes} axes) — clamped to 0'
            )
            self.axis_steer = 0

        self._has_raw_speed = self.axis_raw_speed < num_axes
        self._has_raw_steer = self.axis_raw_steer < num_axes
        if not self._has_raw_speed:
            logger.warning(f'axis_raw_speed={self.axis_raw_speed} out of range — raw_speed disabled')
        if not self._has_raw_steer:
            logger.warning(f'axis_raw_steer={self.axis_raw_steer} out of range — raw_steer disabled')

        if self.btn_estop >= num_buttons:
            logger.warning(
                f'btn_estop={self.btn_estop} out of range '
                f'(joystick has {num_buttons} buttons) — e-stop button disabled'
            )
            self._use_estop_btn = False
        else:
            self._use_estop_btn = True

    def _on_disconnect(self):
        self._joystick = None
        self.state.joystick_connected = False
        self.state.linear_x    = 0.0
        self.state.steer_angle = 0.0
        self.state.raw_speed   = 0.0
        self.state.raw_steer   = 0.0

    def _apply_deadzone(self, value: float) -> float:
        if abs(value) < self.deadzone:
            return 0.0
        sign = 1 if value > 0 else -1
        return sign * (abs(value) - self.deadzone) / (1.0 - self.deadzone)

    def _toggle_estop(self):
        if self._client is None:
            return
        new_val = not self.state.estop
        self.state.estop = new_val
        self._client.send_estop(new_val)
        logger.info(f'Joystick e-stop → {new_val}')
