"""Stereo camera calibration + rectification, ported from the legacy
``DistanceMeasurer`` / ``PostProcessing.StereoCorrespondence`` classes.

Loads the legacy ``config.json`` format (``mtx_L``, ``dist_L``, ``mtx_R``, ``dist_R``,
``R``, ``T``), extended with ``image_size`` ([width, height] in pixels the camera
matrices were calibrated for).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Sequence

import cv2
import numpy as np

REQUIRED_KEYS = ("mtx_L", "dist_L", "mtx_R", "dist_R", "R", "T", "image_size")


def _parse_image_size(value, path) -> tuple[int, int]:
    try:
        width, height = value
        width, height = int(width), int(height)
    except (TypeError, ValueError):
        raise ValueError(f"Camera config {path}: image_size must be [width, height], got {value!r}") from None
    if width <= 0 or height <= 0:
        raise ValueError(f"Camera config {path}: image_size must be positive, got {value!r}")
    return width, height


def _validate_matrices(data, path) -> None:
    """Catches hand-edited/corrupt configs up front instead of failing obscurely in OpenCV."""
    for key in ("mtx_L", "mtx_R"):
        matrix = np.array(data[key], dtype=np.float64)
        if matrix.shape != (3, 3) or not np.allclose(matrix[2], (0.0, 0.0, 1.0)):
            raise ValueError(f"Camera config {path}: {key} must be a 3x3 camera matrix with bottom row [0, 0, 1]")
    rotation = np.array(data["R"], dtype=np.float64)
    if rotation.shape != (3, 3) or not np.allclose(rotation @ rotation.T, np.eye(3), atol=1e-3):
        raise ValueError(f"Camera config {path}: R must be a valid 3x3 rotation matrix")


class CameraCalibration:
    """Stereo calibration + rectification maps for one camera rig / image resolution."""

    def __init__(
        self,
        mtx_L: Sequence, dist_L: Sequence,
        mtx_R: Sequence, dist_R: Sequence,
        rotation: Sequence, translation: Sequence,
        image_size: tuple[int, int],
    ):
        self.c_L = np.array(mtx_L, dtype=np.float64)
        self.d_L = np.array(dist_L, dtype=np.float64)
        self.c_R = np.array(mtx_R, dtype=np.float64)
        self.d_R = np.array(dist_R, dtype=np.float64)
        self.rotation = np.array(rotation, dtype=np.float64)
        self.translation = np.array(translation, dtype=np.float64)
        self.image_size = image_size

        self.R1 = np.zeros((3, 3))
        self.R2 = np.zeros((3, 3))
        self.P1 = np.zeros((3, 4))
        self.P2 = np.zeros((3, 4))
        cv2.stereoRectify(
            self.c_L, self.d_L, self.c_R, self.d_R, image_size,
            self.rotation, self.translation, self.R1, self.R2,
            self.P1, self.P2, Q=None,
            flags=cv2.CALIB_ZERO_DISPARITY, alpha=1, newImageSize=(0, 0),
        )

        self.map_L1, self.map_L2 = cv2.initUndistortRectifyMap(
            self.c_L, self.d_L, self.R1, self.P1, image_size, m1type=cv2.CV_32FC2
        )
        self.map_R1, self.map_R2 = cv2.initUndistortRectifyMap(
            self.c_R, self.d_R, self.R2, self.P2, image_size, m1type=cv2.CV_32FC2
        )

    @classmethod
    def from_json(cls, path: str | os.PathLike) -> "CameraCalibration":
        data = json.loads(Path(path).read_text())
        missing = [key for key in REQUIRED_KEYS if key not in data]
        if missing:
            raise ValueError(f"Camera config {path} is missing keys: {missing}")
        image_size = _parse_image_size(data["image_size"], path)
        _validate_matrices(data, path)
        return cls(
            data["mtx_L"], data["dist_L"], data["mtx_R"], data["dist_R"],
            data["R"], data["T"], image_size,
        )

    def image_size_mismatch(self, width: int, height: int) -> str | None:
        """An error message if an image of the given size does not match the size this
        calibration was made for, otherwise None."""
        calibrated_width, calibrated_height = self.image_size
        if (width, height) == (calibrated_width, calibrated_height):
            return None
        return (
            f"The image size ({width} x {height} px) does not match the image size in the camera "
            f"configuration ({calibrated_width} x {calibrated_height} px). Matching and length/height "
            f"measurements would be wrong; please use a camera configuration for this image size."
        )

    def undistort_point(self, point: Sequence[float], side: str = "L") -> np.ndarray:
        """Rectify a pixel coordinate (distorted -> rectified), for use with triangulation."""
        point = np.array(point, dtype=np.float64)
        if side == "L":
            rectified = cv2.undistortPoints(np.expand_dims([point], 1), self.c_L, self.d_L, R=self.R1, P=self.P1)
        elif side == "R":
            rectified = cv2.undistortPoints(np.expand_dims([point], 1), self.c_R, self.d_R, R=self.R2, P=self.P2)
        else:
            raise ValueError(f"side must be 'L' or 'R', got {side!r}")
        return rectified[0][0]

    def distort_point(self, point: Sequence[float], side: str = "L") -> list[float]:
        """Reverse of ``undistort_point``: a rectified coordinate -> original (distorted) coordinate."""
        x, y = int(point[0]), int(point[1])
        rect_map = self.map_L1 if side == "L" else self.map_R1 if side == "R" else None
        if rect_map is None:
            raise ValueError(f"side must be 'L' or 'R', got {side!r}")
        return [float(rect_map[y, x, 0]), float(rect_map[y, x, 1])]
