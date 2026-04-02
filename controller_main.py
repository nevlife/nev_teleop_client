#!/usr/bin/env python3
import argparse
import asyncio
import logging
import signal
import threading

from nev_teleop_client.config import load_config
from nev_teleop_client.state import StationState
from nev_teleop_client.client import StationClient
from nev_teleop_client.controller import create_controller
from nev_teleop_client.send_loop import run_send_loop

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s  %(levelname)-7s  %(name)s: %(message)s',
    datefmt='%H:%M:%S',
)
logger = logging.getLogger('controller')


def main():
    parser = argparse.ArgumentParser(description='NEV Controller (headless)')
    parser.add_argument('--config', default='config.yaml')
    parser.add_argument('--server-locator', default=None)
    args = parser.parse_args()

    cfg = load_config(args.config, {'server_zenoh_locator': args.server_locator})
    locator = cfg.get('server_zenoh_locator', '')

    state = StationState()
    client = StationClient()
    client.start(locator)

    loop = asyncio.new_event_loop()
    async_stop_event = asyncio.Event()
    controller = create_controller(state, cfg)
    controller.setup(client, loop)

    done_event = threading.Event()
    stop_flag = threading.Event()

    async def async_run():
        logger.info(f'Controller started → server: {locator or "auto-discovery"}')
        try:
            await run_send_loop(client, state, cfg, stop_event=async_stop_event)
        finally:
            done_event.set()

    send_thread = threading.Thread(
        target=loop.run_until_complete, args=(async_run(),), daemon=True)
    send_thread.start()

    def shutdown(*_):
        logger.info('Shutting down...')
        stop_flag.set()
        controller.stop()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    try:
        controller.start()
    except KeyboardInterrupt:
        pass

    loop.call_soon_threadsafe(async_stop_event.set)
    done_event.wait(timeout=2.0)
    loop.call_soon_threadsafe(loop.stop)
    client.stop()
    logger.info('Shutdown complete')


if __name__ == '__main__':
    main()
