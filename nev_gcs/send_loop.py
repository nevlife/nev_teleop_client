import asyncio
import time

from .client import StationClient
from .state import StationState


async def run_send_loop(client: StationClient, state: StationState, cfg: dict):
    hb_interval = 1.0 / cfg.get('heartbeat_rate', 5.0)
    tc_interval = 1.0 / cfg.get('teleop_rate',    20.0)

    last_hb = 0.0
    last_tc = 0.0

    while True:
        now = time.monotonic()

        if now - last_hb >= hb_interval:
            client.send_gcs_heartbeat()
            last_hb = now

        if now - last_tc >= tc_interval:
            client.send_teleop(
                state.linear_x, state.steer_angle,
                state.raw_speed, state.raw_steer,
            )
            last_tc = now

        await asyncio.sleep(0.01)
