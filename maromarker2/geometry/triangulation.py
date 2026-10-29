"""Stereo triangulation, ported from the legacy ``DistanceMeasurer`` class.

Computes real-world distances (mm, in the calibration's units) between two
matched 3D points reconstructed from their left/right pixel coordinates. Used
both for animal length (head/tail points) and height (width points).
"""

from __future__ import annotations

from typing import Sequence

import cv2
import numpy as np

from maromarker2.models.camera_calibration import CameraCalibration

NO_VALUE = -1.0


def _is_valid(point: Sequence[float] | None) -> bool:
    return point is not None and point[0] > 0 and point[1] > 0


def triangulate_batch(
    calibration: CameraCalibration,
    points_L0: Sequence[Sequence[float]],
    points_L1: Sequence[Sequence[float]],
    points_R0: Sequence[Sequence[float]],
    points_R1: Sequence[Sequence[float]],
) -> list[float | None]:
    """Batched 3D distance between point0 and point1 for N animals.

    All point arrays must already be rectified (see ``CameraCalibration.undistort_point``)
    and have shape (N, 2). Returns None for an animal where either side's points are all zero.
    """
    points_L0 = np.asarray(points_L0, dtype=np.float64)
    points_L1 = np.asarray(points_L1, dtype=np.float64)
    points_R0 = np.asarray(points_R0, dtype=np.float64)
    points_R1 = np.asarray(points_R1, dtype=np.float64)

    world0 = cv2.triangulatePoints(calibration.P1, calibration.P2, points_L0.T, points_R0.T)
    world1 = cv2.triangulatePoints(calibration.P1, calibration.P2, points_L1.T, points_R1.T)

    distances: list[float | None] = []
    for i in range(world0.shape[1]):
        if not (np.any(points_L0[i]) or np.any(points_L1[i])) or not (np.any(points_R0[i]) or np.any(points_R1[i])):
            distances.append(None)
            continue
        p0 = world0[:3, i] / world0[3, i]
        p1 = world1[:3, i] / world1[3, i]
        distances.append(float(cv2.norm(p0, p1, normType=cv2.NORM_L2)))
    return distances


def distance_between_rectified(
    calibration: CameraCalibration,
    point0_L: Sequence[float], point1_L: Sequence[float],
    point0_R: Sequence[float], point1_R: Sequence[float],
) -> float | None:
    """Single-pair convenience wrapper around ``triangulate_batch``."""
    return triangulate_batch(calibration, [point0_L], [point1_L], [point0_R], [point1_R])[0]


def measure_distance(
    calibration: CameraCalibration,
    point0_L: Sequence[float], point1_L: Sequence[float],
    point0_R: Sequence[float], point1_R: Sequence[float],
) -> float | None:
    """Real-world distance (mm) between two annotated point pairs given ORIGINAL pixel coordinates.

    Undistorts/rectifies all four points before triangulating. Returns None if any of the
    four points is missing (<=0, i.e. the legacy sentinel / unset).
    """
    if not (_is_valid(point0_L) and _is_valid(point0_R)):
        return None
    if not (_is_valid(point1_L) and _is_valid(point1_R)):
        return None

    rect0_L = calibration.undistort_point(point0_L, "L")
    rect1_L = calibration.undistort_point(point1_L, "L")
    rect0_R = calibration.undistort_point(point0_R, "R")
    rect1_R = calibration.undistort_point(point1_R, "R")
    return distance_between_rectified(calibration, rect0_L, rect1_L, rect0_R, rect1_R)
