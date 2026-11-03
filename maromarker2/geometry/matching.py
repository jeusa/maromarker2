"""Automatic left/right animal matching via a Hungarian-algorithm cost matrix.

Cost combines:
  1. post-rectification epipolar-line closeness (|rectified_L.y - rectified_R.y|), summed over head and tail
  2. |head-to-tail vector length_L - head-to-tail vector length_R| (rectified coordinates)
  3. head-to-tail vector orientation difference between L and R (degrees, rectified coordinates)

Assignments above ``max_cost`` are rejected (left unmatched) rather than forced; pairs
whose group/subgroup/species disagree ("unidentified" matches anything) always are.
Empirically (real config.json + real matched pairs, see tests): true matches usually cost
~20-120, so 300.0 should be a safe default cutoff.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np
from scipy.optimize import linear_sum_assignment

from maromarker2.models.camera_calibration import CameraCalibration
from maromarker2.models.taxonomy import merge_classes

Point = tuple[float, float]

# cost of pairing animals whose classes disagree; far above any ``max_cost`` so such
# pairs are never accepted, yet finite so the assignment problem stays solvable
INCOMPATIBLE_COST = 1e9


@dataclass
class MatchCandidate:
    """One unmatched animal on one side, identified by its row index in the annotation store."""

    row_index: int
    head: Point
    tail: Point
    group: str = ""
    subgroup: str = ""
    species: str = ""


@dataclass
class MatchWeights:
    epipolar: float = 1.0
    length: float = 1.0
    orientation: float = 1.0


@dataclass
class MatchResult:
    left_index: int
    right_index: int
    cost: float


def _rectified_head_and_tail(
    calibration: CameraCalibration, candidate: MatchCandidate, side: str
) -> tuple[np.ndarray, np.ndarray]:
    head = np.array(calibration.undistort_point(candidate.head, side), dtype=np.float64)
    tail = np.array(calibration.undistort_point(candidate.tail, side), dtype=np.float64)
    return head, tail


def _angle_difference_deg(vec_a: np.ndarray, vec_b: np.ndarray) -> float:
    angle_a = np.degrees(np.arctan2(vec_a[1], vec_a[0]))
    angle_b = np.degrees(np.arctan2(vec_b[1], vec_b[0]))
    diff = abs(angle_a - angle_b) % 360.0
    return min(diff, 360.0 - diff)


def build_cost_matrix(
    calibration: CameraCalibration,
    left: Sequence[MatchCandidate],
    right: Sequence[MatchCandidate],
    weights: MatchWeights = MatchWeights(),
) -> np.ndarray:
    """NxM cost matrix for ``left`` candidates (rows) vs. ``right`` candidates (columns)."""
    cost = np.zeros((len(left), len(right)), dtype=np.float64)

    rect_left = [_rectified_head_and_tail(calibration, l, "L") for l in left]
    rect_right = [_rectified_head_and_tail(calibration, r, "R") for r in right]

    for i, l in enumerate(left):
        rect_head_l, rect_tail_l = rect_left[i]
        vec_l = rect_tail_l - rect_head_l
        length_l = float(np.linalg.norm(vec_l))

        for j, r in enumerate(right):
            if merge_classes(l.group, l.subgroup, l.species, r.group, r.subgroup, r.species) is None:
                cost[i, j] = INCOMPATIBLE_COST
                continue
            rect_head_r, rect_tail_r = rect_right[j]
            vec_r = rect_tail_r - rect_head_r
            length_r = float(np.linalg.norm(vec_r))

            epipolar_head = abs(float(rect_head_l[1]) - float(rect_head_r[1]))
            epipolar_tail = abs(float(rect_tail_l[1]) - float(rect_tail_r[1]))
            epipolar = epipolar_head + epipolar_tail
            length_diff = abs(length_l - length_r)
            orientation_diff = _angle_difference_deg(vec_l, vec_r)

            cost[i, j] = (
                weights.epipolar * epipolar
                + weights.length * length_diff
                + weights.orientation * orientation_diff
            )

    return cost


def match_candidates(
    calibration: CameraCalibration,
    left: Sequence[MatchCandidate],
    right: Sequence[MatchCandidate],
    weights: MatchWeights = MatchWeights(),
    max_cost: float = 300.0,
) -> list[MatchResult]:
    """Solve the assignment problem and reject pairs whose cost exceeds ``max_cost``."""
    if not left or not right:
        return []

    cost = build_cost_matrix(calibration, left, right, weights)
    row_ind, col_ind = linear_sum_assignment(cost)

    results = []
    for row, col in zip(row_ind, col_ind):
        pair_cost = float(cost[row, col])
        if pair_cost <= max_cost:
            results.append(MatchResult(left[row].row_index, right[col].row_index, pair_cost))
    return results
