#!/usr/bin/env python3
"""H.265 video viewer — GStreamer decode + OpenCV display. No Qt.

Receives H.265 NAL units from Zenoh, decodes via GStreamer (NVIDIA HW),
displays with cv2.imshow(). Shows black screen while waiting for frames.
"""
import os
import sys

# GStreamer 1.28 — must re-exec before any GStreamer libs are loaded
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
import struct
import threading
import time

import cv2
import numpy as np

import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst, GLib

import zenoh

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s  %(levelname)-7s  %(name)s: %(message)s',
    datefmt='%H:%M:%S',
)
logger = logging.getLogger('video_viewer')

RELAY_HEADER_FMT = 'dHdH'
RELAY_HEADER_SIZE = struct.calcsize(RELAY_HEADER_FMT)  # 20
WINDOW_NAME = 'NEV Video'


class VideoViewer:
    def __init__(self):
        Gst.init(None)
        self._pipeline = None
        self._appsrc = None
        self._appsink = None
        self._sub = None
        self._running = False
        self._lock = threading.Lock()
        self._latest_frame = None

    def start(self, session: zenoh.Session):
        self._init_pipeline()
        self._sub = session.declare_subscriber('nev/gcs/camera', self._on_camera)
        self._running = True
        logger.info('Video viewer started')
        self._display_loop()

    def stop(self):
        self._running = False
        if self._sub:
            self._sub.undeclare()
            self._sub = None
        if self._pipeline:
            self._pipeline.set_state(Gst.State.NULL)
            self._pipeline = None
        cv2.destroyAllWindows()

    def _init_pipeline(self):
        hw_pipeline = (
            'appsrc name=src format=time is-live=true do-timestamp=false '
            'caps="video/x-h265,stream-format=byte-stream,alignment=au" ! '
            'h265parse ! '
            'nvh265dec max-display-delay=0 ! '
            'cudadownload ! '
            'videoconvert ! '
            'video/x-raw,format=BGR ! '
            'appsink name=sink drop=true max-buffers=1 sync=false emit-signals=true'
        )
        sw_pipeline = (
            'appsrc name=src format=time is-live=true do-timestamp=false '
            'caps="video/x-h265,stream-format=byte-stream,alignment=au" ! '
            'h265parse ! '
            'avdec_h265 ! '
            'videoconvert ! '
            'video/x-raw,format=BGR ! '
            'appsink name=sink drop=true max-buffers=1 sync=false emit-signals=true'
        )

        try:
            self._pipeline = Gst.parse_launch(hw_pipeline)
            logger.info('Using NVIDIA hardware decoder (nvh265dec)')
        except GLib.Error:
            self._pipeline = Gst.parse_launch(sw_pipeline)
            logger.info('Using software decoder (avdec_h265)')

        self._appsrc = self._pipeline.get_by_name('src')
        self._appsrc.set_property('max-bytes', 512 * 1024)
        self._appsrc.set_property('block', False)

        self._appsink = self._pipeline.get_by_name('sink')
        self._appsink.set_property('emit-signals', True)
        self._appsink.connect('new-sample', self._on_decoded)

        self._pipeline.set_state(Gst.State.PLAYING)

    def _on_camera(self, sample):
        if not self._running or not self._appsrc:
            return
        try:
            raw = bytes(sample.payload)
            if len(raw) <= RELAY_HEADER_SIZE:
                return
            nal = raw[RELAY_HEADER_SIZE:]
            buf = Gst.Buffer.new_wrapped(nal)
            self._appsrc.emit('push-buffer', buf)
        except Exception as e:
            logger.warning(f'Frame error: {e}')

    def _on_decoded(self, sink):
        sample = sink.emit('pull-sample')
        if not isinstance(sample, Gst.Sample):
            return Gst.FlowReturn.OK

        buf = sample.get_buffer()
        caps = sample.get_caps()
        struct_ = caps.get_structure(0)
        width = struct_.get_value('width')
        height = struct_.get_value('height')

        ok, map_info = buf.map(Gst.MapFlags.READ)
        if ok:
            frame = np.frombuffer(map_info.data, dtype=np.uint8).reshape((height, width, 3)).copy()
            buf.unmap(map_info)
            with self._lock:
                self._latest_frame = frame

        return Gst.FlowReturn.OK

    def _display_loop(self):
        # Show black screen immediately
        black = np.zeros((480, 640, 3), dtype=np.uint8)
        cv2.imshow(WINDOW_NAME, black)

        while self._running:
            with self._lock:
                frame = self._latest_frame

            if frame is not None:
                cv2.imshow(WINDOW_NAME, frame)

            key = cv2.waitKey(1) & 0xFF
            if key == 27 or key == ord('q'):  # ESC or Q
                break
            if cv2.getWindowProperty(WINDOW_NAME, cv2.WND_PROP_VISIBLE) < 1:
                break


def main():
    parser = argparse.ArgumentParser(description='NEV Video Viewer')
    parser.add_argument('--config', default='config.yaml')
    parser.add_argument('--server-locator', default=None)
    args = parser.parse_args()

    from nev_teleop_client.config import load_config
    cfg = load_config(args.config, {'server_zenoh_locator': args.server_locator})
    locator = cfg.get('server_zenoh_locator', '')

    conf = zenoh.Config()
    if locator:
        conf.insert_json5('connect/endpoints', json.dumps([locator]))
    session = zenoh.open(conf)
    logger.info(f'Zenoh connected → {locator or "auto-discovery"}')

    viewer = VideoViewer()

    def shutdown(*_):
        viewer.stop()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    viewer.start(session)

    session.close()
    logger.info('Shutdown complete')


if __name__ == '__main__':
    main()
