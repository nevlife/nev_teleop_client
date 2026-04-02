#!/usr/bin/env python3
import os
import sys

_gst128 = '/opt/gst128/lib/x86_64-linux-gnu'
if os.path.isdir(_gst128) and _gst128 not in os.environ.get('LD_LIBRARY_PATH', ''):
    os.environ['LD_LIBRARY_PATH'] = f'{_gst128}:{os.environ.get("LD_LIBRARY_PATH", "")}'
    os.environ['GST_PLUGIN_PATH'] = f'{_gst128}/gstreamer-1.0'
    os.environ['GST_PLUGIN_SYSTEM_PATH'] = ''
    os.environ['GST_PLUGIN_SCANNER'] = '/opt/gst128/libexec/gstreamer-1.0/gst-plugin-scanner'
    os.execve(sys.executable, [sys.executable] + sys.argv, os.environ)

import argparse
import json
import logging
import signal

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from nev_teleop_client.config import load_config
from nev_teleop_client.gui.main_window import MainWindow

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s  %(levelname)-7s  %(name)s: %(message)s',
    datefmt='%H:%M:%S',
)
logger = logging.getLogger('viewer_h265')


def main():
    parser = argparse.ArgumentParser(description='NEV H.265 Viewer')
    parser.add_argument('--config', default='config.yaml')
    parser.add_argument('--server-locator', default=None)
    args = parser.parse_args()

    cfg = load_config(args.config, {'server_zenoh_locator': args.server_locator})
    locator = cfg.get('server_zenoh_locator', '')

    import zenoh
    conf = zenoh.Config()
    if locator:
        conf.insert_json5('connect/endpoints', json.dumps([locator]))
    session = zenoh.open(conf)
    logger.info(f'Zenoh connected → {locator or "auto-discovery"}')

    app = QApplication(sys.argv)
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    tick = QTimer()
    tick.timeout.connect(lambda: None)
    tick.start(200)

    window = MainWindow(session, cfg, client=None)
    window.start()
    window.show()

    logger.info('H.265 viewer started')
    app.exec()

    window.stop()
    session.close()
    logger.info('Shutdown complete')
    os._exit(0)


if __name__ == '__main__':
    main()
