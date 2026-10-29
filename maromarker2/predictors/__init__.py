"""Extension seam for future automatic keypoint prediction (e.g. a YOLOPose model).

Not implemented yet; the editor can call a concrete ``KeypointPredictor`` once one
exists without further redesign.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class PredictedAnimal:
    head: tuple[float, float]
    tail: tuple[float, float]
    group: str = ""
    species: str = ""
    width_start: tuple[float, float] | None = None
    width_end: tuple[float, float] | None = None
    subgroup: str = ""
    bbox: tuple[float, float, float, float] | None = None  # (x1, y1, x2, y2) in image pixels
    conf: float = 0.0


class KeypointPredictor(ABC):
    """Interface a future automatic predictor must implement to plug into the editor."""

    @abstractmethod
    def predict(self, image_path: str) -> list[PredictedAnimal]:
        """Return predicted animals (keypoints + optional group/species) for one image."""
        raise NotImplementedError
