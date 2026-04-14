import threading
from typing import Tuple


class StationState:

    def __init__(self):
        self._lock = threading.Lock()
        self._linear_x: float = 0.0
        self._steer_angle: float = 0.0
        self._estop: bool = False
        self._controller_connected: bool = False

    @property
    def linear_x(self) -> float:
        with self._lock:
            return self._linear_x

    @linear_x.setter
    def linear_x(self, value: float):
        with self._lock:
            self._linear_x = value

    @property
    def steer_angle(self) -> float:
        with self._lock:
            return self._steer_angle

    @steer_angle.setter
    def steer_angle(self, value: float):
        with self._lock:
            self._steer_angle = value

    @property
    def estop(self) -> bool:
        with self._lock:
            return self._estop

    @estop.setter
    def estop(self, value: bool):
        with self._lock:
            self._estop = value

    @property
    def controller_connected(self) -> bool:
        with self._lock:
            return self._controller_connected

    @controller_connected.setter
    def controller_connected(self, value: bool):
        with self._lock:
            self._controller_connected = value

    def update_control(self, linear_x: float, steer_angle: float) -> None:
        with self._lock:
            self._linear_x = linear_x
            self._steer_angle = steer_angle

    def get_control(self) -> Tuple[float, float]:
        with self._lock:
            return (self._linear_x, self._steer_angle)

    def reset_control(self, connected: bool = False) -> None:
        with self._lock:
            self._controller_connected = connected
            self._linear_x = 0.0
            self._steer_angle = 0.0

    def toggle_estop(self) -> bool:
        with self._lock:
            self._estop = not self._estop
            return self._estop
