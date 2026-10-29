"""Automatic left/right animal matching via a Hungarian-algorithm cost matrix.

Cost combines (per user spec):
  1. post-rectification epipolar-line closeness (|rectified_L.y - rectified_R.y|)
  2. pre-rectification geometric distance between raw L/R head positions
  3. |head-to-tail vector length_L - head-to-tail vector length_R|
  4. head-to-tail vector orientation difference between L and R (degrees)

Assignments above ``max_cost`` are rejected (left unmatched) rather than forced; pairs
whose group/subgroup/species disagree ("unidentified" matches anything) always are.
Empirically (real config.json + real matched pairs, see tests): true matches cost
~10-80 with default weights, mismatches cost 500+, so 150.0 is a safe default cutoff.
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
    geometric: float = 0.01
    length: float = 0.5
    orientation: float = 1.0


@dataclass
class MatchResult:
    left_index: int
    right_index: int
    cost: float


def _head_tail_vector(candidate: MatchCandidate) -> np.ndarray:
    return np.array(candidate.tail, dtype=np.float64) - np.array(candidate.head, dtype=np.float64)


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

    for i, l in enumerate(left):
        rect_head_l = calibration.undistort_point(l.head, "L")
        vec_l = _head_tail_vector(l)
        length_l = float(np.linalg.norm(vec_l))

        for j, r in enumerate(right):
            if merge_classes(l.group, l.subgroup, l.species, r.group, r.subgroup, r.species) is None:
                cost[i, j] = INCOMPATIBLE_COST
                continue
            rect_head_r = calibration.undistort_point(r.head, "R")
            vec_r = _head_tail_vector(r)
            length_r = float(np.linalg.norm(vec_r))

            epipolar = abs(float(rect_head_l[1]) - float(rect_head_r[1]))
            geometric = float(np.linalg.norm(np.array(l.head) - np.array(r.head)))
            length_diff = abs(length_l - length_r)
            orientation_diff = _angle_difference_deg(vec_l, vec_r)

            cost[i, j] = (
                weights.epipolar * epipolar
                + weights.geometric * geometric
                + weights.length * length_diff
                + weights.orientation * orientation_diff
            )

    return cost


def match_candidates(
    calibration: CameraCalibration,
    left: Sequence[MatchCandidate],
    right: Sequence[MatchCandidate],
    weights: MatchWeights = MatchWeights(),
    max_cost: float = 150.0,
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
