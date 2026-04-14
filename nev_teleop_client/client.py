import json
import logging
import threading
import time

import zenoh

logger = logging.getLogger(__name__)

_TOPIC_QOS_SUFFIX = {
    "teleop": dict(
        reliability=zenoh.Reliability.BEST_EFFORT,
        congestion_control=zenoh.CongestionControl.DROP,
        priority=zenoh.Priority.INTERACTIVE_HIGH,
    ),
    "estop": dict(
        reliability=zenoh.Reliability.RELIABLE,
        congestion_control=zenoh.CongestionControl.BLOCK,
        priority=zenoh.Priority.REAL_TIME,
    ),
    "cmd_mode": dict(
        reliability=zenoh.Reliability.RELIABLE,
        congestion_control=zenoh.CongestionControl.BLOCK,
        priority=zenoh.Priority.INTERACTIVE_HIGH,
    ),
    "controller_heartbeat": dict(
        reliability=zenoh.Reliability.BEST_EFFORT,
        congestion_control=zenoh.CongestionControl.DROP,
        priority=zenoh.Priority.BACKGROUND,
    ),
    "station_ping": dict(
        reliability=zenoh.Reliability.BEST_EFFORT,
        congestion_control=zenoh.CongestionControl.DROP,
        priority=zenoh.Priority.DATA_LOW,
    ),
    "bot_ping": dict(
        reliability=zenoh.Reliability.BEST_EFFORT,
        congestion_control=zenoh.CongestionControl.DROP,
        priority=zenoh.Priority.DATA_LOW,
    ),
}


class StationClient:

    def __init__(self, vehicle_id: int = 0):
        self._vehicle_id = vehicle_id
        self._session = None
        self._pubs: dict = {}
        self._subs: list = []
        self._lock = threading.Lock()
        self._rtt_client_server_ms: float = 0.0
        self._rtt_client_bot_ms: float = 0.0
        self._last_station_pong: float = 0.0
        self._last_bot_pong: float = 0.0

    @property
    def vehicle_id(self) -> int:
        return self._vehicle_id

    def start(self, locator: str = "") -> None:
        conf = zenoh.Config()
        if locator:
            conf.insert_json5("connect/endpoints", json.dumps([locator]))
        self._session = zenoh.open(conf)

        vid = self._vehicle_id
        try:
            for suffix, qos in _TOPIC_QOS_SUFFIX.items():
                key = f"nev/station/{vid}/{suffix}"
                self._pubs[key] = self._session.declare_publisher(key, **qos)
        except Exception:
            self.stop()
            raise

        self._subs = [
            self._session.declare_subscriber(
                f"nev/gcs/{vid}/station_pong", self._on_station_pong
            ),
            self._session.declare_subscriber(
                f"nev/gcs/{vid}/bot_pong", self._on_bot_pong
            ),
        ]

        logger.info(
            f'StationClient started (vehicle_id={vid}) -> {locator or "auto-discovery"}'
        )

    def stop(self) -> None:
        for sub in self._subs:
            try:
                sub.undeclare()
            except Exception:
                pass
        self._subs.clear()
        for key, pub in self._pubs.items():
            try:
                pub.undeclare()
            except Exception as e:
                logger.warning(f"Error undeclaring publisher [{key}]: {e}")
        try:
            if self._session:
                self._session.close()
        finally:
            self._pubs.clear()
            self._session = None

    def _pub_key(self, suffix: str) -> str:
        return f"nev/station/{self._vehicle_id}/{suffix}"

    def _publish(self, suffix: str, data: dict) -> None:
        key = self._pub_key(suffix)
        try:
            self._pubs[key].put(json.dumps(data))
        except Exception as e:
            logger.warning(f"zenoh put [{key}]: {e}")

    def send_teleop(self, linear_x: float, steer_angle: float):
        self._publish(
            "teleop",
            {
                "linear_x": round(linear_x, 3),
                "steer_angle": round(steer_angle, 4),
            },
        )

    def send_estop(self, activate: bool):
        self._publish("estop", {"active": activate})
        logger.info(f"E-stop -> {activate}")

    def send_cmd_mode(self, mode: int):
        self._publish("cmd_mode", {"mode": mode})
        logger.info(f"Cmd mode -> {mode}")

    def send_station_ping(self):
        self._publish("station_ping", {"ts": time.time()})

    def send_bot_ping(self):
        self._publish("bot_ping", {"ts": time.time()})

    def send_controller_heartbeat(self, connected: bool):
        self._publish("controller_heartbeat", {"connected": connected})

    def _on_station_pong(self, sample):
        try:
            data = json.loads(bytes(sample.payload))
            ts = data.get("ts")
            if ts is None:
                return
            rtt_ms = (time.time() - ts) * 1000.0
            if rtt_ms < 0:
                return
            with self._lock:
                self._rtt_client_server_ms = round(rtt_ms, 1)
                self._last_station_pong = time.monotonic()
        except Exception as e:
            logger.warning(f"station pong parse error: {e}")

    def _on_bot_pong(self, sample):
        try:
            data = json.loads(bytes(sample.payload))
            ts = data.get("ts")
            if ts is None:
                return
            rtt_ms = (time.time() - ts) * 1000.0
            if rtt_ms < 0:
                return
            with self._lock:
                self._rtt_client_bot_ms = round(rtt_ms, 1)
                self._last_bot_pong = time.monotonic()
        except Exception as e:
            logger.warning(f"bot pong parse error: {e}")

    @property
    def rtt_client_server_ms(self) -> float:
        with self._lock:
            if (
                self._last_station_pong > 0
                and (time.monotonic() - self._last_station_pong) > 3.0
            ):
                self._rtt_client_server_ms = 0.0
            return self._rtt_client_server_ms

    @property
    def rtt_client_bot_ms(self) -> float:
        with self._lock:
            if (
                self._last_bot_pong > 0
                and (time.monotonic() - self._last_bot_pong) > 3.0
            ):
                self._rtt_client_bot_ms = 0.0
            return self._rtt_client_bot_ms
