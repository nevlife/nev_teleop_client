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

    def __init__(self, parent=None, vehicle_id: str = '0', rtp_mode: bool = False):
        super().__init__(parent)
        self._vehicle_id = str(vehicle_id)
        self._pipeline = None
        self._appsrc = None
        self._jitterbuffer = None
        self._sub = None
        self._running = False
        # Opt-in: wrap payload as RTP and run through jitterbuffer/depay.
        # When False, behavior is identical to the legacy raw-AU path.
        self._rtp_mode = rtp_mode

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

        # video_ctl signaling. Populated in start() once the Zenoh session is
        # available. _last_pli_send rate-limits PLI requests to <= 5 Hz.
        self._video_ctl_pub = None
        self._last_pli_send: float = 0.0

        self._label = QLabel()
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setStyleSheet('background-color: #1a1a2e;')
        self._label.setMinimumSize(640, 480)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._label)

        self.frame_ready.connect(self._update_frame)

        Gst.init(None)

    @property
    def vehicle_id(self) -> str:
        return self._vehicle_id

    def start(self, session: zenoh.Session):
        key = f'nev/gcs/{self._vehicle_id}/camera'
        self._sub = session.declare_subscriber(key, self._on_camera)
        ctl_key = f'nev/station/{self._vehicle_id}/video_ctl'
        self._video_ctl_pub = session.declare_publisher(
            ctl_key,
            reliability=zenoh.Reliability.RELIABLE,
            congestion_control=zenoh.CongestionControl.BLOCK,
            priority=zenoh.Priority.INTERACTIVE_HIGH,
        )
        self._init_pipeline()
        self._running = True
        logger.info('VideoWidget[%s] started, key=%s', self._vehicle_id, key)

    def stop(self):
        self._running = False
        if self._sub:
            self._sub.undeclare()
            self._sub = None
        if self._video_ctl_pub:
            try:
                self._video_ctl_pub.undeclare()
            except Exception:
                pass
            self._video_ctl_pub = None
        if self._pipeline:
            self._pipeline.set_state(Gst.State.NULL)
            self._pipeline = None

    def _init_pipeline(self):
        if self._rtp_mode:
            # RTP wire-format over Zenoh TCP. appsrc do-timestamp=true lets
            # the jitterbuffer compute PTS from RTP timestamps.
            rtp_caps = (
                'application/x-rtp,media=video,encoding-name=H265,'
                'clock-rate=90000,payload=96'
            )
            hw_str = (
                'appsrc name=src format=time is-live=true do-timestamp=true '
                f'caps="{rtp_caps}" ! '
                'rtpjitterbuffer name=jbuf latency=80 mode=slave do-lost=true ! '
                'rtph265depay ! '
                'h265parse ! '
                'video/x-h265,stream-format=byte-stream,alignment=au ! '
                'nvh265dec max-display-delay=0 ! cudadownload ! videoconvert ! '
                'video/x-raw,format=RGB ! '
                'appsink name=sink drop=true max-buffers=1 sync=false emit-signals=true'
            )
            sw_str = (
                'appsrc name=src format=time is-live=true do-timestamp=true '
                f'caps="{rtp_caps}" ! '
                'rtpjitterbuffer name=jbuf latency=80 mode=slave do-lost=true ! '
                'rtph265depay ! '
                'h265parse ! '
                'video/x-h265,stream-format=byte-stream,alignment=au ! '
                'avdec_h265 ! videoconvert ! '
                'video/x-raw,format=RGB ! '
                'appsink name=sink drop=true max-buffers=1 sync=false emit-signals=true'
            )
        else:
            hw_str = (
                'appsrc name=src format=time is-live=true do-timestamp=false '
                'caps="video/x-h265,stream-format=byte-stream,alignment=au" ! '
                'h265parse ! '
                'nvh265dec max-display-delay=0 ! '
                'cudadownload ! '
                'videoconvert ! '
                'video/x-raw,format=RGB ! '
                'appsink name=sink drop=true max-buffers=1 sync=false emit-signals=true'
            )
            sw_str = (
                'appsrc name=src format=time is-live=true do-timestamp=false '
                'caps="video/x-h265,stream-format=byte-stream,alignment=au" ! '
                'h265parse ! '
                'avdec_h265 ! '
                'videoconvert ! '
                'video/x-raw,format=RGB ! '
                'appsink name=sink drop=true max-buffers=1 sync=false emit-signals=true'
            )

        try:
            self._pipeline = Gst.parse_launch(hw_str)
            logger.info('Using NVIDIA hardware H.265 decoder (nvh265dec) rtp=%s', self._rtp_mode)
        except GLib.Error:
            self._pipeline = Gst.parse_launch(sw_str)
            logger.info('Using software H.265 decoder (avdec_h265) rtp=%s', self._rtp_mode)

        self._appsrc = self._pipeline.get_by_name('src')
        self._appsrc.set_property('max-bytes', 1024 * 512)
        self._appsrc.set_property('block', False)

        if self._rtp_mode:
            self._jitterbuffer = self._pipeline.get_by_name('jbuf')
            if self._jitterbuffer is not None:
                self._jitterbuffer.connect('on-lost-packet', self._on_rtp_lost)

        sink = self._pipeline.get_by_name('sink')
        sink.set_property('emit-signals', True)
        sink.connect('new-sample', self._on_decoded_sample)

        self._pipeline.set_state(Gst.State.PLAYING)

    def _on_rtp_lost(self, jbuf, stats):
        # Request a fresh keyframe from the bot when the jitterbuffer reports
        # a lost packet. Rate-limited to one PLI per 200 ms to avoid storms.
        logger.warning('RTP packet lost: %s', stats)
        self._send_pli()

    def _send_pli(self) -> None:
        if self._video_ctl_pub is None:
            return
        now = time.monotonic()
        if now - self._last_pli_send < 0.2:
            return
        self._last_pli_send = now
        try:
            self._video_ctl_pub.put(json.dumps({'type': 'pli'}))
        except Exception as e:
            logger.warning('PLI publish error: %s', e)

    def _on_camera(self, sample):
        if not self._running or not self._appsrc:
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

            buf = Gst.Buffer.new_wrapped(nal)
            if self._rtp_mode:
                # appsrc do-timestamp=true assigns PTS; jitterbuffer owns ordering.
                # Decode-latency tracking via _decode_pts_map is disabled here.
                pass
            else:
                self._decode_pts_seq += 1
                pts_ns = self._decode_pts_seq * 66_666_667
                self._decode_pts_map[pts_ns] = time.perf_counter()
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
