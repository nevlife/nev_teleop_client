"""
GStreamer H.265 video decoder widget for Qt.

Receives H.265 NAL units from Zenoh (nev/gcs/camera) and decodes them
using NVIDIA nvh265dec (hardware) with fallback to avdec_h265 (software).

Frame format from server: [8B timestamp (double)] [2B encode_ms (uint16)] [NAL bytes]
"""
import json
import logging
import struct
import threading
import time

import gi
gi.require_version('Gst', '1.0')
gi.require_version('GstVideo', '1.0')
from gi.repository import Gst, GLib

import zenoh
import numpy as np
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel

logger = logging.getLogger(__name__)

CAMERA_HEADER_BYTES = 10  # double(8) + uint16(2)


class VideoWidget(QWidget):
    """Displays H.265 video from Zenoh via GStreamer hardware decoding."""

    frame_ready = Signal(QImage)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pipeline = None
        self._appsrc = None
        self._sub = None
        self._running = False

        # Stats
        self._lock = threading.Lock()
        self._rx_bytes = 0
        self._frame_count = 0
        self._last_stats_time = time.time()
        self._video_delay_ms = 0.0
        self._encode_ms = 0.0
        self._bw_mbps = 0.0
        self._fps = 0.0

        self._label = QLabel()
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setStyleSheet('background-color: #1a1a2e;')
        self._label.setMinimumSize(640, 480)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._label)

        self.frame_ready.connect(self._update_frame)

        Gst.init(None)

    def start(self, session: zenoh.Session):
        """Subscribe to camera topic and start decode pipeline."""
        self._sub = session.declare_subscriber('nev/gcs/camera', self._on_camera)
        self._init_pipeline()
        self._running = True
        logger.info('VideoWidget started')

    def stop(self):
        self._running = False
        if self._sub:
            self._sub.undeclare()
            self._sub = None
        if self._pipeline:
            self._pipeline.set_state(Gst.State.NULL)
            self._pipeline = None

    def _init_pipeline(self):
        # Try hardware decoder first, fallback to software
        pipeline_str = (
            'appsrc name=src format=time is-live=true do-timestamp=true '
            'caps="video/x-h265,stream-format=byte-stream,alignment=au" ! '
            'h265parse ! '
            'nvh265dec ! '
            'videoconvert ! '
            'video/x-raw,format=RGB ! '
            'appsink name=sink drop=true max-buffers=2 sync=false emit-signals=true'
        )
        try:
            self._pipeline = Gst.parse_launch(pipeline_str)
            logger.info('Using NVIDIA hardware H.265 decoder (nvh265dec)')
        except GLib.Error:
            pipeline_str = (
                'appsrc name=src format=time is-live=true do-timestamp=true '
                'caps="video/x-h265,stream-format=byte-stream,alignment=au" ! '
                'h265parse ! '
                'avdec_h265 ! '
                'videoconvert ! '
                'video/x-raw,format=RGB ! '
                'appsink name=sink drop=true max-buffers=2 sync=false emit-signals=true'
            )
            self._pipeline = Gst.parse_launch(pipeline_str)
            logger.info('Using software H.265 decoder (avdec_h265)')

        self._appsrc = self._pipeline.get_by_name('src')
        self._appsrc.set_property('max-bytes', 1024 * 1024 * 4)
        self._appsrc.set_property('block', False)

        sink = self._pipeline.get_by_name('sink')
        sink.set_property('emit-signals', True)
        sink.connect('new-sample', self._on_decoded_sample)

        self._pipeline.set_state(Gst.State.PLAYING)

    def _on_camera(self, sample):
        """Zenoh callback: receive H.265 NAL from server."""
        if not self._running or not self._appsrc:
            return
        try:
            raw = bytes(sample.payload)
            if len(raw) <= CAMERA_HEADER_BYTES:
                return
            ts, encode_ms = struct.unpack_from('dH', raw, 0)
            nal = raw[CAMERA_HEADER_BYTES:]

            video_delay_ms = (time.time() - ts) * 1000.0

            with self._lock:
                self._rx_bytes += len(nal)
                self._video_delay_ms = video_delay_ms
                self._encode_ms = encode_ms

            buf = Gst.Buffer.new_wrapped(nal)
            self._appsrc.emit('push-buffer', buf)
        except Exception as e:
            logger.warning(f'Camera frame error: {e}')

    def _on_decoded_sample(self, sink):
        """GStreamer callback: decoded frame ready."""
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
            try:
                data = bytes(map_info.data)
                img = QImage(data, width, height, width * 3,
                             QImage.Format.Format_RGB888).copy()
                with self._lock:
                    self._frame_count += 1
                self.frame_ready.emit(img)
            finally:
                buf.unmap(map_info)

        return Gst.FlowReturn.OK

    def _update_frame(self, img: QImage):
        """Update display on Qt main thread."""
        pixmap = QPixmap.fromImage(img)
        scaled = pixmap.scaled(
            self._label.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._label.setPixmap(scaled)

    def get_stats(self) -> dict:
        """Return current video stats (call periodically from main thread)."""
        now = time.time()
        with self._lock:
            dt = now - self._last_stats_time
            if dt > 0:
                self._bw_mbps = round(self._rx_bytes * 8 / (dt * 1e6), 3)
                self._fps = round(self._frame_count / dt, 1)
            self._rx_bytes = 0
            self._frame_count = 0
            self._last_stats_time = now
            return {
                'bw_mbps': self._bw_mbps,
                'fps': self._fps,
                'delay_ms': round(self._video_delay_ms, 1),
                'encode_ms': self._encode_ms,
            }
