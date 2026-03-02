#!/usr/bin/env python3
import argparse
import asyncio
import logging
from pathlib import Path

import yaml

from station_state import StationState
from station_client import StationClient, run_send_loop
from joystick import JoystickHandler

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s  %(levelname)-7s  %(name)s: %(message)s',
    datefmt='%H:%M:%S',
)
logger = logging.getLogger('main')


def load_config(path: str, overrides: dict) -> dict:
    cfg = {}
    p = Path(path)
    if p.exists():
        cfg = yaml.safe_load(p.read_text()) or {}
    for k, v in overrides.items():
        if v is not None:
            cfg[k] = v
    return cfg


async def run(cfg: dict):
    locator = cfg.get('server_zenoh_locator', '')

    state  = StationState()
    loop   = asyncio.get_running_loop()
    client = StationClient()
    client.start(locator)

    joystick = JoystickHandler(state, cfg.get('joystick', {}))
    joystick.set_client(client)
    joystick.set_loop(loop)
    joystick.start()

    logger.info(f'Station started → server: {locator or "auto-discovery"}')

    try:
        await run_send_loop(client, state, cfg)
    finally:
        joystick.stop()
        client.stop()
        logger.info('Shutdown complete')


def main():
    parser = argparse.ArgumentParser(description='NEV GCS Station')
    parser.add_argument('--config', default='config.yaml')
    parser.add_argument('--server-locator', default=None)
    args = parser.parse_args()

    cfg = load_config(args.config, {'server_zenoh_locator': args.server_locator})

    try:
        asyncio.run(run(cfg))
    except KeyboardInterrupt:
        logger.info('Stopped by user')

if __name__ == '__main__':
    main()
