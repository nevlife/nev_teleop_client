import logging
import struct
import threading
import time

import gi
gi.require_version('Gst', '1.0')
gi.require_version('GstVideo', '1.0')
from gi.repository import Gst, GLib

import zenoh
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel

logger = logging.getLogger(__name__)

RELAY_HEADER_FMT  = 'dHdH'
RELAY_HEADER_SIZE = struct.calcsize(RELAY_HEADER_FMT)


def _ms(a: float, b: float) -> str:
    return f'{(b - a) * 1000:.1f}ms'


class VideoWidget(QWidget):

    frame_ready = Signal(bytes, int, int)

    def __init__(self, codec='h264', hw_accel=True, parent=None):
        super().__init__(parent)
        self._pipeline = None
        self._appsrc = None
        self._sub = None
        self._running = False
        self._codec = codec
        self._hw_accel = hw_accel

        self._lock = threading.Lock()
        self._rx_bytes = 0
        self._frame_count = 0
        self._last_stats_time = time.time()
        self._encode_ms = 0.0
        self._veh_to_srv_ms = 0.0
        self._srv_to_cli_ms = 0.0
        self._decode_ms = 0.0
        self._frame_size_sum = 0
        self._frame_size_count = 0
        self._frame_size_avg = 0
        self._bw_mbps = 0.0
        self._fps = 0.0
        self._decode_max_ms = 0.0
        self._render_max_ms = 0.0

        self._decode_pts_map = {}
        self._decode_pts_seq = 0

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
        self._sub = session.declare_subscriber('nev/gcs/camera', self._on_camera)
        self._init_pipeline(self._codec)
        self._running = True
        logger.info(f'VideoWidget started (codec={self._codec})')

    def stop(self):
        self._running = False
        if self._sub:
            self._sub.undeclare()
            self._sub = None
        if self._pipeline:
            self._pipeline.set_state(Gst.State.NULL)
            self._pipeline = None
            self._appsrc = None
            self._codec = None

    def _init_pipeline(self, codec: str):
        if codec == 'h264':
            caps = 'video/x-h264,stream-format=byte-stream,alignment=au'
            if self._hw_accel:
                parse_dec = 'h264parse ! nvh264dec max-display-delay=0 ! cudadownload'
            else:
                parse_dec = 'h264parse ! avdec_h264'
        else:
            caps = 'video/x-h265,stream-format=byte-stream,alignment=au'
            if self._hw_accel:
                parse_dec = 'h265parse ! nvh265dec max-display-delay=0 ! cudadownload'
            else:
                parse_dec = 'h265parse ! avdec_h265'

        pipeline_str = (
            f'appsrc name=src format=time is-live=true do-timestamp=false '
            f'caps="{caps}" ! '
            f'{parse_dec} ! '
            f'videoconvert ! '
            f'video/x-raw,format=RGB ! '
            f'appsink name=sink drop=true max-buffers=1 sync=false emit-signals=true'
        )
        self._pipeline = Gst.parse_launch(pipeline_str)
        dec_type = 'HW' if self._hw_accel else 'SW'
        logger.info(f'{codec.upper()} decoder ({dec_type})')

        self._appsrc = self._pipeline.get_by_name('src')
        self._appsrc.set_property('max-bytes', 1024 * 512)
        self._appsrc.set_property('block', False)

        sink = self._pipeline.get_by_name('sink')
        sink.set_property('emit-signals', True)
        sink.connect('new-sample', self._on_decoded_sample)

        self._pipeline.set_state(Gst.State.PLAYING)

    def _on_camera(self, sample):
        if not self._running:
            return
        try:
            raw = bytes(sample.payload)
            if len(raw) <= RELAY_HEADER_SIZE:
                return
            vehicle_ts, encode_ms, server_rx_ts, veh_to_srv_ms = struct.unpack_from(
                RELAY_HEADER_FMT, raw, 0,
            )
            nal = raw[RELAY_HEADER_SIZE:]

            now = time.time()
            srv_to_cli_ms = max(0.0, (now - server_rx_ts) * 1000.0)

            nal_len = len(nal)
            with self._lock:
                self._rx_bytes += nal_len
                self._encode_ms = encode_ms
                self._veh_to_srv_ms = veh_to_srv_ms
                self._srv_to_cli_ms = srv_to_cli_ms
                self._frame_size_sum += nal_len
                self._frame_size_count += 1

            self._decode_pts_seq += 1
            pts_ns = self._decode_pts_seq * 66_666_667
            self._decode_pts_map[pts_ns] = time.perf_counter()
            buf = Gst.Buffer.new_wrapped(nal)
            buf.pts = pts_ns
            buf.dts = pts_ns
            self._appsrc.emit('push-buffer', buf)
        except Exception as e:
            logger.warning(f'Camera frame error: {e}')

    def _on_decoded_sample(self, sink):
        t0 = time.perf_counter()

        sample = sink.emit('pull-sample')
        if not isinstance(sample, Gst.Sample):
            return Gst.FlowReturn.OK

        buf = sample.get_buffer()

        decode_ms = 0.0
        pts = buf.pts
        push_time = self._decode_pts_map.pop(pts, None)
        if push_time is not None:
            decode_ms = (t0 - push_time) * 1000.0
        if len(self._decode_pts_map) > 30:
            oldest_keys = sorted(self._decode_pts_map)[:len(self._decode_pts_map) - 10]
            for k in oldest_keys:
                del self._decode_pts_map[k]
        caps = sample.get_caps()
        struct_ = caps.get_structure(0)
        width = struct_.get_value('width')
        height = struct_.get_value('height')

        ok, map_info = buf.map(Gst.MapFlags.READ)
        if ok:
            try:
                t1 = time.perf_counter()
                data = bytes(map_info.data)
                t2 = time.perf_counter()
                with self._lock:
                    self._frame_count += 1
                    if decode_ms > 0:
                        self._decode_ms = decode_ms
                        if decode_ms > self._decode_max_ms:
                            self._decode_max_ms = decode_ms
                self.frame_ready.emit(data, width, height)
                t3 = time.perf_counter()
                logger.debug(
                    f'[decode] dec={decode_ms:.1f}ms pull={_ms(t0,t1)} copy={_ms(t1,t2)} emit={_ms(t2,t3)} total={_ms(t0,t3)}'
                )
            finally:
                buf.unmap(map_info)

        return Gst.FlowReturn.OK

    def _update_frame(self, data: bytes, width: int, height: int):
        t0 = time.perf_counter()
        img = QImage(data, width, height, width * 3, QImage.Format.Format_RGB888)
        t1 = time.perf_counter()
        pixmap = QPixmap.fromImage(img)
        t2 = time.perf_counter()
        scaled = pixmap.scaled(
            self._label.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.FastTransformation,
        )
        t3 = time.perf_counter()
        self._label.setPixmap(scaled)
        t4 = time.perf_counter()
        render_ms = (t4 - t0) * 1000.0
        with self._lock:
            if render_ms > self._render_max_ms:
                self._render_max_ms = render_ms
        logger.debug(
            f'[render] qimage={_ms(t0,t1)} pixmap={_ms(t1,t2)} scale={_ms(t2,t3)} set={_ms(t3,t4)} total={_ms(t0,t4)}'
        )

    def get_stats(self) -> dict:
        now = time.time()
        with self._lock:
            dt = now - self._last_stats_time
            if dt > 0:
                self._bw_mbps = round(self._rx_bytes * 8 / (dt * 1e6), 3)
                self._fps = round(self._frame_count / dt, 1)
            if self._frame_size_count > 0:
                self._frame_size_avg = round(self._frame_size_sum / self._frame_size_count)
            self._rx_bytes = 0
            self._frame_count = 0
            self._frame_size_sum = 0
            self._frame_size_count = 0
            self._last_stats_time = now
            stats = {
                'bw_mbps': self._bw_mbps,
                'fps': self._fps,
                'encode_ms': self._encode_ms,
                'veh_to_srv_ms': self._veh_to_srv_ms,
                'srv_to_cli_ms': round(self._srv_to_cli_ms, 1),
                'decode_ms': round(self._decode_ms, 1),
                'decode_max_ms': round(self._decode_max_ms, 1),
                'render_max_ms': round(self._render_max_ms, 1),
                'frame_size': self._frame_size_avg,
            }
            self._decode_max_ms = 0.0
            self._render_max_ms = 0.0
            return stats
