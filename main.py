#!/usr/bin/env python3
import argparse
import asyncio
import logging

from nev_gcs.config import load_config
from nev_gcs.state import StationState
from nev_gcs.client import StationClient
from nev_gcs.controller import create_controller
from nev_gcs.send_loop import run_send_loop

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s  %(levelname)-7s  %(name)s: %(message)s',
    datefmt='%H:%M:%S',
)
logger = logging.getLogger('main')


async def run(cfg: dict):
    locator = cfg.get('server_zenoh_locator', '')

    state  = StationState()
    loop   = asyncio.get_running_loop()
    client = StationClient()
    client.start(locator)

    controller = create_controller(state, cfg)
    controller.setup(client, loop)
    controller.start()

    logger.info(f'Station started → server: {locator or "auto-discovery"}')

    try:
        await run_send_loop(client, state, cfg)
    finally:
        controller.stop()
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
