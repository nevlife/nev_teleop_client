import math
import logging
from typing import Optional

from ..state import StationState
from .base import Controller

logger = logging.getLogger(__name__)

try:
    import pygame

    _HAS_PYGAME = True
except ImportError:
    _HAS_PYGAME = False
    logger.warning("pygame not installed — joystick disabled")


class JoystickController(Controller):

    def __init__(self, state: StationState, cfg: dict):
        super().__init__(state)
        self.axis_speed = cfg.get("axis_speed", 1)
        self.axis_steer = cfg.get("axis_steer", 3)
        self.btn_estop = cfg.get("btn_estop", 4)
        self.max_speed = cfg.get("max_speed", 1.0)
        self.max_steer = math.radians(cfg.get("max_steer_deg", 27.0))
        self.deadzone = cfg.get("deadzone", 0.05)
        self.invert_speed = cfg.get("invert_speed", True)

        self._joystick: Optional[object] = None
        self._use_estop_btn = False

    def name(self) -> str:
        return "joystick"

    def start(self):
        if not _HAS_PYGAME:
            logger.warning(
                "pygame not available — joystick disabled, waiting for shutdown"
            )
            self._stop_event.clear()
            while not self._stop_event.is_set():
                import time

                time.sleep(0.1)
            return
        super().start()

    def _setup(self):
        pygame.init()
        pygame.joystick.init()

    def _teardown(self):
        pygame.quit()

    def poll(self) -> bool:
        for event in pygame.event.get():
            if event.type == pygame.JOYDEVICEADDED:
                if self._joystick is None:
                    self._connect(event.device_index)
            elif event.type == pygame.JOYDEVICEREMOVED:
                if (
                    self._joystick
                    and event.instance_id == self._joystick.get_instance_id()
                ):
                    logger.warning("Joystick disconnected")
                    self._joystick = None
                    self.on_disconnect()
            elif event.type == pygame.JOYBUTTONDOWN:
                if self._use_estop_btn and event.button == self.btn_estop:
                    self._toggle_estop()

        if self._joystick is None:
            return False

        joy = self._joystick

        speed = self._apply_deadzone(joy.get_axis(self.axis_speed))
        if self.invert_speed:
            speed = -speed
        linear_x = speed * self.max_speed

        steer = self._apply_deadzone(joy.get_axis(self.axis_steer))
        steer_angle = -steer * self.max_steer

        self.state.update_control(linear_x, steer_angle)

        return True

    def on_disconnect(self):
        super().on_disconnect()
        self._joystick = None

    def _connect(self, device_index: int):
        joy = pygame.joystick.Joystick(device_index)
        self._joystick = joy
        logger.info(f"Joystick connected: {joy.get_name()}")

        num_axes = joy.get_numaxes()
        num_buttons = joy.get_numbuttons()

        if self.axis_speed >= num_axes:
            logger.error(
                f"axis_speed={self.axis_speed} out of range ({num_axes} axes) — clamped to 0"
            )
            self.axis_speed = 0
        if self.axis_steer >= num_axes:
            logger.error(
                f"axis_steer={self.axis_steer} out of range ({num_axes} axes) — clamped to 0"
            )
            self.axis_steer = 0

        self._use_estop_btn = self.btn_estop < num_buttons
        if not self._use_estop_btn:
            logger.warning(
                f"btn_estop={self.btn_estop} out of range ({num_buttons} buttons) — disabled"
            )

    def _apply_deadzone(self, value: float) -> float:
        if abs(value) < self.deadzone:
            return 0.0
        sign = 1 if value > 0 else -1
        return sign * (abs(value) - self.deadzone) / (1.0 - self.deadzone)

    def _toggle_estop(self):
        if self._client is None:
            return
        new_val = self.state.toggle_estop()
        self._client.send_estop(new_val)
        logger.info(f"Joystick e-stop → {new_val}")
