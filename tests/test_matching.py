import pytest

from maromarker2.models.camera_calibration import CameraCalibration
from maromarker2.geometry.matching import MatchCandidate, MatchWeights, match_candidates



@pytest.fixture
def calibration(pytestconfig):
    config_path = pytestconfig.rootpath / "maromarker2" / "configs" / "config_remos1.json"
    return CameraCalibration.from_json(config_path)


def test_match_candidates_pairs_true_matches(calibration):
    # true match: same animal seen on L/R, shifted mostly horizontally (disparity) like a real stereo pair
    left = [
        MatchCandidate(row_index=10, head=(2548.0, 1042.0), tail=(2552.0, 1138.0)),
        MatchCandidate(row_index=11, head=(500.0, 500.0), tail=(560.0, 560.0)),
    ]
    right = [
        MatchCandidate(row_index=20, head=(997.0, 990.0), tail=(1022.0, 1117.0)),
        MatchCandidate(row_index=21, head=(1500.0, 505.0), tail=(1560.0, 565.0)),
    ]

    results = match_candidates(calibration, left, right, max_cost=1e6)

    pairs = {(r.left_index, r.right_index) for r in results}
    assert (10, 20) in pairs
    assert (11, 21) in pairs


def test_match_candidates_rejects_cost_above_threshold(calibration):
    left = [MatchCandidate(row_index=1, head=(100.0, 100.0), tail=(150.0, 150.0))]
    # a right candidate with a wildly different head-tail vector (orientation/length) -> high cost
    right = [MatchCandidate(row_index=2, head=(4000.0, 2500.0), tail=(4000.0, 100.0))]

    results = match_candidates(calibration, left, right, max_cost=5.0)

    assert results == []


def test_match_candidates_empty_inputs(calibration):
    assert match_candidates(calibration, [], [], max_cost=10.0) == []
    assert match_candidates(calibration, [MatchCandidate(1, (0, 0), (1, 1))], [], max_cost=10.0) == []


def test_match_weights_defaults():
    weights = MatchWeights()
    assert weights.epipolar > 0 and weights.length > 0 and weights.orientation > 0


def test_match_candidates_never_pairs_differing_classes(calibration):
    left = [MatchCandidate(row_index=10, head=(2548.0, 1042.0), tail=(2552.0, 1138.0), group="fish", species="a")]
    right = [MatchCandidate(row_index=20, head=(997.0, 990.0), tail=(1022.0, 1117.0), group="jellyfish", species="b")]

    assert match_candidates(calibration, left, right, max_cost=1e6) == []


def test_match_candidates_prefers_compatible_class_and_treats_unidentified_as_wildcard(calibration):
    left = [MatchCandidate(row_index=10, head=(2548.0, 1042.0), tail=(2552.0, 1138.0), group="fish", species="unidentified")]
    right = [
        MatchCandidate(row_index=20, head=(997.0, 990.0), tail=(1022.0, 1117.0), group="fish", species="gadus_morhua"),
        MatchCandidate(row_index=21, head=(1000.0, 1000.0), tail=(1020.0, 1120.0), group="jellyfish", species="x"),
    ]

    results = match_candidates(calibration, left, right, max_cost=1e6)

    assert [(r.left_index, r.right_index) for r in results] == [(10, 20)]


def test_match_candidates_rejects_differing_subgroups(calibration):
    left = [MatchCandidate(row_index=10, head=(2548.0, 1042.0), tail=(2552.0, 1138.0), group="fish", subgroup="benthic")]
    right = [
        MatchCandidate(row_index=20, head=(997.0, 990.0), tail=(1022.0, 1117.0), group="fish", subgroup="pelagic"),
        MatchCandidate(row_index=21, head=(1000.0, 1000.0), tail=(1020.0, 1120.0), group="fish", subgroup="unidentified"),
    ]

    results = match_candidates(calibration, left, right, max_cost=1e6)

    assert [(r.left_index, r.right_index) for r in results] == [(10, 21)]
