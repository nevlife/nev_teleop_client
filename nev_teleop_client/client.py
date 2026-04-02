import json
import logging
import threading
import time

import zenoh

logger = logging.getLogger(__name__)

_TOPIC_QOS = {
    'nev/station/client_heartbeat':     dict(reliability=zenoh.Reliability.BEST_EFFORT,  congestion_control=zenoh.CongestionControl.DROP,  priority=zenoh.Priority.DATA_LOW),
    'nev/station/teleop':               dict(reliability=zenoh.Reliability.BEST_EFFORT,  congestion_control=zenoh.CongestionControl.DROP,  priority=zenoh.Priority.INTERACTIVE_HIGH),
    'nev/station/estop':                dict(reliability=zenoh.Reliability.RELIABLE,     congestion_control=zenoh.CongestionControl.BLOCK, priority=zenoh.Priority.REAL_TIME),
    'nev/station/cmd_mode':             dict(reliability=zenoh.Reliability.RELIABLE,     congestion_control=zenoh.CongestionControl.BLOCK, priority=zenoh.Priority.INTERACTIVE_HIGH),
    'nev/station/controller_heartbeat': dict(reliability=zenoh.Reliability.BEST_EFFORT,  congestion_control=zenoh.CongestionControl.DROP,  priority=zenoh.Priority.BACKGROUND),
    'nev/station/ping':                 dict(reliability=zenoh.Reliability.BEST_EFFORT,  congestion_control=zenoh.CongestionControl.DROP,  priority=zenoh.Priority.DATA_LOW),
}


class StationClient:
    TOPICS = (
        'nev/station/client_heartbeat',
        'nev/station/teleop',
        'nev/station/estop',
        'nev/station/cmd_mode',
        'nev/station/controller_heartbeat',
        'nev/station/ping',
    )

    def __init__(self):
        self._session = None
        self._pubs: dict = {}
        self._subs: list = []
        self._rtt_lock = threading.Lock()
        self._rtt_client_server_ms: float = 0.0
        self._last_pong_time: float = 0.0

    def start(self, locator: str = '') -> None:
        conf = zenoh.Config()
        if locator:
            conf.insert_json5('connect/endpoints', json.dumps([locator]))
        self._session = zenoh.open(conf)

        try:
            for key in self.TOPICS:
                self._pubs[key] = self._session.declare_publisher(key, **_TOPIC_QOS[key])
        except Exception:
            self.stop()
            raise

        self._subs = [
            self._session.declare_subscriber('nev/gcs/station_pong', self._on_pong),
        ]

        logger.info(f'StationClient started → {locator or "auto-discovery"}')

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
                logger.warning(f'Error undeclaring publisher [{key}]: {e}')
        try:
            if self._session:
                self._session.close()
        finally:
            self._pubs.clear()
            self._session = None

    def _publish(self, key: str, data: dict) -> None:
        try:
            self._pubs[key].put(json.dumps(data))
        except Exception as e:
            logger.warning(f'zenoh put [{key}]: {e}')

    def send_client_heartbeat(self):
        self._publish('nev/station/client_heartbeat', {
            'ts': time.time(),
        })

    def send_teleop(self, linear_x: float, steer_angle: float):
        self._publish('nev/station/teleop', {
            'linear_x':    round(linear_x,    3),
            'steer_angle': round(steer_angle, 4),
        })

    def send_estop(self, activate: bool):
        self._publish('nev/station/estop', {
            'active': activate,
        })
        logger.info(f'E-stop → {activate}')

    def send_cmd_mode(self, mode: int):
        self._publish('nev/station/cmd_mode', {
            'mode': mode,
        })
        logger.info(f'Cmd mode → {mode}')

    def send_ping(self):
        self._publish('nev/station/ping', {'ts': time.time()})

    def _on_pong(self, sample):
        try:
            data = json.loads(bytes(sample.payload))
            ts = data.get('ts')
            if ts is None:
                return
            rtt_ms = (time.time() - ts) * 1000.0
            if rtt_ms < 0:
                return
            with self._rtt_lock:
                prev = self._rtt_client_server_ms
                if prev > 0:
                    smoothed = 0.7 * prev + 0.3 * rtt_ms
                else:
                    smoothed = rtt_ms
                self._rtt_client_server_ms = round(smoothed, 1)
                self._last_pong_time = time.monotonic()
        except Exception as e:
            logger.warning(f'pong parse error: {e}')

    @property
    def rtt_client_server_ms(self) -> float:
        with self._rtt_lock:
            if self._last_pong_time > 0 and (time.monotonic() - self._last_pong_time) > 3.0:
                self._rtt_client_server_ms = 0.0
            return self._rtt_client_server_ms

    def send_controller_heartbeat(self, connected: bool):
        self._publish('nev/station/controller_heartbeat', {
            'connected': connected,
        })
