"""Runs the Hungarian-matching cost computation (``geometry.matching``) off the GUI call stack.

A plain ``QObject`` with a ``run()`` method so it can either be called synchronously
(cheap — a handful of animals per image pair) or moved to a ``QThread`` for larger batches.
"""

from __future__ import annotations

from typing import Sequence

from PySide6.QtCore import QObject, Signal

from maromarker2.geometry.matching import MatchCandidate, MatchResult, MatchWeights, match_candidates
from maromarker2.models.camera_calibration import CameraCalibration


class MatchingWorker(QObject):
    """Computes proposed L/R matches for one image pair's unmatched animals."""

    finished = Signal(list)  # list[MatchResult]

    def __init__(
        self,
        calibration: CameraCalibration,
        left: Sequence[MatchCandidate],
        right: Sequence[MatchCandidate],
        weights: MatchWeights | None = None,
        max_cost: float = 150.0,
        parent=None,
    ):
        super().__init__(parent)
        self.calibration = calibration
        self.left = left
        self.right = right
        self.weights = weights or MatchWeights()
        self.max_cost = max_cost

    def run(self) -> None:
        results = match_candidates(self.calibration, self.left, self.right, self.weights, self.max_cost)
        self.finished.emit(results)
