import json
import logging
import time

import zenoh

logger = logging.getLogger(__name__)

_TOPIC_QOS = {
    'nev/station/client_heartbeat':     dict(reliability=zenoh.Reliability.BEST_EFFORT,  congestion_control=zenoh.CongestionControl.DROP,  priority=zenoh.Priority.DATA_LOW),
    'nev/station/teleop':               dict(reliability=zenoh.Reliability.BEST_EFFORT,  congestion_control=zenoh.CongestionControl.DROP,  priority=zenoh.Priority.INTERACTIVE_HIGH),
    'nev/station/estop':                dict(reliability=zenoh.Reliability.RELIABLE,     congestion_control=zenoh.CongestionControl.BLOCK, priority=zenoh.Priority.REAL_TIME),
    'nev/station/cmd_mode':             dict(reliability=zenoh.Reliability.RELIABLE,     congestion_control=zenoh.CongestionControl.BLOCK, priority=zenoh.Priority.INTERACTIVE_HIGH),
    'nev/station/controller_heartbeat': dict(reliability=zenoh.Reliability.BEST_EFFORT,  congestion_control=zenoh.CongestionControl.DROP,  priority=zenoh.Priority.BACKGROUND),
}


class StationClient:
    TOPICS = (
        'nev/station/client_heartbeat',
        'nev/station/teleop',
        'nev/station/estop',
        'nev/station/cmd_mode',
        'nev/station/controller_heartbeat',
    )

    def __init__(self):
        self._session = None
        self._pubs: dict = {}

    def start(self, locator: str = '') -> None:
        conf = zenoh.Config()
        if locator:
            conf.insert_json5('connect/endpoints', json.dumps([locator]))
        self._session = zenoh.open(conf)

        for key in self.TOPICS:
            self._pubs[key] = self._session.declare_publisher(key, **_TOPIC_QOS[key])

        logger.info(f'StationClient started → {locator or "auto-discovery"}')

    def stop(self) -> None:
        for pub in self._pubs.values():
            pub.undeclare()
        if self._session:
            self._session.close()

    def _publish(self, key: str, data: dict) -> None:
        try:
            self._pubs[key].put(json.dumps(data))
        except Exception as e:
            logger.warning(f'zenoh put [{key}]: {e}')

    def send_client_heartbeat(self):
        self._publish('nev/station/client_heartbeat', {
            'ts': time.time(),
        })

    def send_teleop(self, linear_x: float, steer_angle: float,
                    raw_speed: float = 0.0, raw_steer: float = 0.0):
        self._publish('nev/station/teleop', {
            'linear_x':    round(linear_x,    3),
            'steer_angle': round(steer_angle, 4),
            'raw_speed':   round(raw_speed,   3),
            'raw_steer':   round(raw_steer,   3),
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

    def send_controller_heartbeat(self, connected: bool):
        self._publish('nev/station/controller_heartbeat', {
            'connected': connected,
        })
