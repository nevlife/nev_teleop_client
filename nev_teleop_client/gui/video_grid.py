import logging
import math

import zenoh
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QGridLayout, QVBoxLayout, QLabel, QFrame

from .video_widget import VideoWidget

logger = logging.getLogger(__name__)

BG       = '#0d1117'
BG_CARD  = '#161b22'
BORDER   = '#21262d'
TEXT     = '#c9d1d9'
MUTED    = '#8b949e'
FONT     = "Consolas, 'Courier New', monospace"


def _grid_dims(n: int) -> tuple[int, int]:
    """Return (rows, cols) for n tiles. ceil(sqrt(n)) cols, rows fills."""
    if n <= 1:
        return 1, 1
    cols = math.ceil(math.sqrt(n))
    rows = math.ceil(n / cols)
    return rows, cols


class _VideoTile(QFrame):
    """VideoWidget plus a small vehicle-id title bar."""

    def __init__(self, vehicle_id: str, rtp_mode: bool, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f'background:{BG}; border:1px solid {BORDER};')

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        title = QLabel(f'VEH {vehicle_id}')
        title.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        title.setFixedHeight(20)
        title.setStyleSheet(
            f'background:{BG_CARD}; color:{TEXT}; font-family:{FONT};'
            f'font-size:10px; letter-spacing:1px; padding:2px 8px;'
            f'border-bottom:1px solid {BORDER};'
        )
        layout.addWidget(title)

        self.video = VideoWidget(vehicle_id=vehicle_id, rtp_mode=rtp_mode)
        layout.addWidget(self.video, stretch=1)


class VideoGrid(QWidget):
    """Container that lays out N VideoWidget tiles in a square-ish grid."""

    def __init__(self, vehicle_ids: list[str], rtp_mode: bool = False, parent=None):
        super().__init__(parent)
        self._tiles: list[_VideoTile] = []

        ids = list(vehicle_ids) or ['0']

        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        rows, cols = _grid_dims(len(ids))
        for i, vid in enumerate(ids):
            tile = _VideoTile(vehicle_id=str(vid), rtp_mode=rtp_mode)
            r, c = divmod(i, cols)
            layout.addWidget(tile, r, c)
            self._tiles.append(tile)

        # Equalize cell stretch so tiles share space.
        for r in range(rows):
            layout.setRowStretch(r, 1)
        for c in range(cols):
            layout.setColumnStretch(c, 1)

    @property
    def widgets(self) -> list[VideoWidget]:
        return [t.video for t in self._tiles]

    def widget_for(self, vehicle_id: str) -> VideoWidget | None:
        for t in self._tiles:
            if t.video.vehicle_id == str(vehicle_id):
                return t.video
        return None

    def start(self, session: zenoh.Session):
        for t in self._tiles:
            t.video.start(session)
        logger.info('VideoGrid started with %d tile(s)', len(self._tiles))

    def stop(self):
        for t in self._tiles:
            t.video.stop()

    def get_stats(self, vehicle_id: str | None = None) -> dict:
        """Stats for one vehicle (or the first tile if id not given)."""
        if not self._tiles:
            return {}
        if vehicle_id is None:
            return self._tiles[0].video.get_stats()
        w = self.widget_for(vehicle_id)
        return w.get_stats() if w else {}
