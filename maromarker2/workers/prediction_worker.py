"""Runs a KeypointPredictor over image pairs; meant to live on a QThread (inference takes seconds).

The worker only predicts; whoever receives ``pair_done`` applies the results to the
annotation store on the main thread.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Signal

from maromarker2.predictors import KeypointPredictor

# (file_id, left image or None, right image or None)
PredictionJob = tuple[str, Path | None, Path | None]


class PredictionWorker(QObject):
    progress = Signal(int, int)  # pairs done, total pairs
    pair_done = Signal(str, object)  # file_id, {"L": [PredictedAnimal], "R": [PredictedAnimal]}
    failed = Signal(str)
    finished = Signal()

    def __init__(self, predictor: KeypointPredictor, jobs: list[PredictionJob], parent=None):
        super().__init__(parent)
        self.predictor = predictor
        self.jobs = jobs
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        try:
            for done, (file_id, left, right) in enumerate(self.jobs):
                if self._cancelled:
                    break
                results = {
                    side: self.predictor.predict(str(path))
                    for side, path in (("L", left), ("R", right)) if path is not None
                }
                self.pair_done.emit(file_id, results)
                self.progress.emit(done + 1, len(self.jobs))
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            self.finished.emit()
