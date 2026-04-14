import asyncio
import time

from .client import StationClient
from .state import StationState


async def run_send_loop(
    client: StationClient,
    state: StationState,
    cfg: dict,
    stop_event: asyncio.Event | None = None,
):
    tc_interval = 1.0 / cfg.get("teleop_rate", 20.0)
    ping_interval = 1.0 / cfg.get("ping_rate", 1.0)

    last_tc = 0.0
    last_ping = 0.0

    if stop_event is None:
        stop_event = asyncio.Event()

    try:
        while not stop_event.is_set():
            now = time.monotonic()

            if now - last_tc >= tc_interval:
                linear_x, steer_angle = state.get_control()
                client.send_teleop(linear_x, steer_angle)
                last_tc = now

            if now - last_ping >= ping_interval:
                client.send_station_ping()
                client.send_bot_ping()
                last_ping = now

            await asyncio.sleep(0.01)
    except asyncio.CancelledError:
        pass
