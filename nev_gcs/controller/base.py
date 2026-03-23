import time
import threading
import logging
from abc import ABC, abstractmethod
from typing import Optional

from ..state import StationState

logger = logging.getLogger(__name__)


class Controller(ABC):

    def __init__(self, state: StationState):
        self.state = state
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._client = None
        self._loop = None
        self._last_broadcast = 0.0

    def setup(self, client, loop):
        self._client = client
        self._loop = loop

    def start(self):
        self._running = True
        self._thread = threading.Thread(
            target=self._run, name=self.name(), daemon=True,
        )
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)

    @abstractmethod
    def name(self) -> str:
        ...

    @abstractmethod
    def poll(self) -> bool:
        ...

    def on_disconnect(self):
        self.state.controller_connected = False
        self.state.linear_x = 0.0
        self.state.steer_angle = 0.0
        self.state.raw_speed = 0.0
        self.state.raw_steer = 0.0

    def _run(self):
        self._setup()
        while self._running:
            self.state.controller_connected = self.poll()
            self._broadcast_status()
            time.sleep(0.02)  # 50 Hz
        self._teardown()

    def _setup(self):
        pass

    def _teardown(self):
        pass

    def _broadcast_status(self):
        now = time.monotonic()
        if self._client and self._loop and now - self._last_broadcast >= 0.05:
            self._loop.call_soon_threadsafe(
                self._client.send_controller_heartbeat,
                self.state.controller_connected,
            )
            self._last_broadcast = now
