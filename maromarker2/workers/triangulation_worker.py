"""Recalculates length/height for one newly-matched animal (``geometry.triangulation``).

Same QObject + ``run()`` pattern as ``MatchingWorker`` — cheap enough to call synchronously
per match, but kept as a standalone unit so it can be moved to a ``QThread`` if ever batched.
"""

from __future__ import annotations

from typing import Mapping, Sequence

from PySide6.QtCore import QObject, Signal

from maromarker2.geometry.triangulation import measure_distance
from maromarker2.models.camera_calibration import CameraCalibration

Point = Sequence[float]


class TriangulationWorker(QObject):
    """Computes length (head/tail) and, if width points exist, height for one matched row."""

    finished = Signal(int, object, object)  # row_index, length: float | None, height: float | None

    def __init__(
        self,
        calibration: CameraCalibration,
        row_index: int,
        left: Mapping[str, Point],
        right: Mapping[str, Point],
        parent=None,
    ):
        super().__init__(parent)
        self.calibration = calibration
        self.row_index = row_index
        self.left = left
        self.right = right

    def run(self) -> None:
        length = measure_distance(
            self.calibration, self.left["head"], self.left["tail"], self.right["head"], self.right["tail"],
        )
        height = None
        if all(key in self.left and key in self.right for key in ("width_start", "width_end")):
            height = measure_distance(
                self.calibration, self.left["width_start"], self.left["width_end"],
                self.right["width_start"], self.right["width_end"],
            )
        self.finished.emit(self.row_index, length, height)
