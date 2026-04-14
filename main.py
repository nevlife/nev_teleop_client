#!/usr/bin/env python3
import argparse
import sys
import asyncio
import json
import logging
import signal
import threading

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from nev_teleop_client.config import load_config
from nev_teleop_client.state import StationState
from nev_teleop_client.client import StationClient
from nev_teleop_client.controller import create_controller
from nev_teleop_client.send_loop import run_send_loop
from nev_teleop_client.gui.main_window import MainWindow

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s  %(levelname)-7s  %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("main")


def main():
    parser = argparse.ArgumentParser(description="NEV Teleop Client")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--server-locator", default=None)
    args = parser.parse_args()

    cfg = load_config(args.config, {"server_zenoh_locator": args.server_locator})
    locator = cfg.get("server_zenoh_locator", "")

    vehicle_id = cfg.get("vehicle_id", 0)

    state = StationState()
    client = StationClient(vehicle_id=vehicle_id)
    client.start(locator)

    loop = asyncio.new_event_loop()
    async_stop_event = asyncio.Event()
    controller = create_controller(state, cfg)
    controller.setup(client, loop)

    done_event = threading.Event()

    async def async_run():
        logger.info(f'Station started -> server: {locator or "auto-discovery"}')
        try:
            await run_send_loop(client, state, cfg, stop_event=async_stop_event)
        finally:
            done_event.set()

    send_thread = threading.Thread(
        target=loop.run_until_complete, args=(async_run(),), daemon=True
    )
    send_thread.start()

    ctrl_thread = threading.Thread(target=controller.start, daemon=True)
    ctrl_thread.start()

    app = QApplication(sys.argv)
    window = MainWindow(client._session, cfg, client=client, state=state)
    window.start()
    window.show()

    signal.signal(signal.SIGINT, lambda *_: app.quit())

    tick = QTimer()
    tick.timeout.connect(lambda: None)
    tick.start(200)

    logger.info("Qt application started")

    app.exec()

    logger.info("Shutting down...")
    window.stop()
    controller.stop()
    loop.call_soon_threadsafe(async_stop_event.set)
    done_event.wait(timeout=2.0)
    loop.call_soon_threadsafe(loop.stop)
    client.stop()
    logger.info("Shutdown complete")
    import os

    os._exit(0)


if __name__ == "__main__":
    main()
