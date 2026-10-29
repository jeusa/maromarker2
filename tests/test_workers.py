import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from maromarker2.geometry.matching import MatchCandidate, MatchResult
from maromarker2.models.camera_calibration import CameraCalibration
from maromarker2.workers.matching_worker import MatchingWorker
from maromarker2.workers.triangulation_worker import TriangulationWorker



@pytest.fixture
def calibration(pytestconfig):
    config_path = pytestconfig.rootpath / "maromarker2" / "configs" / "config_remos1.json"
    return CameraCalibration.from_json(config_path)


def test_matching_worker_emits_results(calibration):
    left = [MatchCandidate(0, (2548.0, 1042.0), (2552.0, 1138.0))]
    right = [MatchCandidate(1, (997.0, 990.0), (1022.0, 1117.0))]
    received = []

    worker = MatchingWorker(calibration, left, right)
    worker.finished.connect(received.append)
    worker.run()

    assert len(received) == 1
    results = received[0]
    assert results == [MatchResult(0, 1, results[0].cost)]


def test_matching_worker_emits_empty_list_when_no_candidates(calibration):
    received = []
    worker = MatchingWorker(calibration, [], [])
    worker.finished.connect(received.append)
    worker.run()

    assert received == [[]]


def test_triangulation_worker_computes_length(calibration):
    left = {"head": (2548.0, 1042.0), "tail": (2552.0, 1138.0)}
    right = {"head": (997.0, 990.0), "tail": (1022.0, 1117.0)}
    received = []

    worker = TriangulationWorker(calibration, 7, left, right)
    worker.finished.connect(lambda *args: received.append(args))
    worker.run()

    assert len(received) == 1
    row_index, length, height = received[0]
    assert row_index == 7
    assert length == pytest.approx(15.40, rel=0.1)
    assert height is None


def test_triangulation_worker_computes_height_when_width_points_present(calibration):
    left = {
        "head": (2548.0, 1042.0), "tail": (2552.0, 1138.0),
        "width_start": (2500.0, 1060.0), "width_end": (2600.0, 1060.0),
    }
    right = {
        "head": (997.0, 990.0), "tail": (1022.0, 1117.0),
        "width_start": (950.0, 1000.0), "width_end": (1050.0, 1000.0),
    }
    received = []

    worker = TriangulationWorker(calibration, 3, left, right)
    worker.finished.connect(lambda *args: received.append(args))
    worker.run()

    row_index, length, height = received[0]
    assert height is not None
