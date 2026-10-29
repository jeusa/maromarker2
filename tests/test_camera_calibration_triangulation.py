import pytest

from maromarker2.models.camera_calibration import CameraCalibration
from maromarker2.geometry.triangulation import measure_distance



@pytest.fixture
def calibration(pytestconfig):
    config_path = pytestconfig.rootpath / "maromarker2" / "configs" / "config_remos1.json"
    return CameraCalibration.from_json(config_path)


def test_from_json_builds_rectification(calibration):
    assert calibration.P1.shape == (3, 4)
    assert calibration.P2.shape == (3, 4)


def test_undistort_distort_roundtrip_is_close(calibration):
    point = (2000.0, 1500.0)
    rectified = calibration.undistort_point(point, "L")
    restored = calibration.distort_point(rectified, "L")
    assert restored[0] == pytest.approx(point[0], abs=2.0)
    assert restored[1] == pytest.approx(point[1], abs=2.0)


def test_measure_distance_matches_legacy_known_result(calibration):
    # euplokamis_dunlapae row from csv_files/maromarker_annotations_2021-2023.csv,
    # computed by the legacy app as length=15.40 (mm) using the same config.json
    head_l, tail_l = (2548.0, 1042.0), (2552.0, 1138.0)
    head_r, tail_r = (997.0, 990.0), (1022.0, 1117.0)

    length = measure_distance(calibration, head_l, tail_l, head_r, tail_r)

    assert length == pytest.approx(15.40, rel=0.1)


def test_measure_distance_returns_none_when_unmatched(calibration):
    assert measure_distance(calibration, (1, 1), (2, 2), (-1, -1), (-1, -1)) is None


def _write_config(tmp_path, pytestconfig, **overrides):
    import json
    data = json.loads((pytestconfig.rootpath / "maromarker2" / "configs" / "config_remos1.json").read_text())
    data.update(overrides)
    data = {key: value for key, value in data.items() if value is not None}
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data))
    return path


def test_image_size_is_read_from_config(calibration):
    assert calibration.image_size == (4272, 2848)


def test_from_json_requires_image_size(tmp_path, pytestconfig):
    path = _write_config(tmp_path, pytestconfig, image_size=None)
    with pytest.raises(ValueError, match="image_size"):
        CameraCalibration.from_json(path)


def test_from_json_rejects_malformed_image_size(tmp_path, pytestconfig):
    path = _write_config(tmp_path, pytestconfig, image_size=[4272])
    with pytest.raises(ValueError, match="image_size"):
        CameraCalibration.from_json(path)


def test_from_json_rejects_invalid_matrices(tmp_path, pytestconfig):
    bad_mtx = [[4563.0, 0.0, 2129.0], [0.0, 4581.0, 1398.0], [0.0, 0.0, 0.0]]
    with pytest.raises(ValueError, match="mtx_L"):
        CameraCalibration.from_json(_write_config(tmp_path, pytestconfig, mtx_L=bad_mtx))
    bad_rotation = [[-0.05, 0.02, 0.007], [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]]
    with pytest.raises(ValueError, match="rotation"):
        CameraCalibration.from_json(_write_config(tmp_path, pytestconfig, R=bad_rotation))


def test_image_size_mismatch_message(calibration):
    assert calibration.image_size_mismatch(4272, 2848) is None
    message = calibration.image_size_mismatch(4277, 2848)
    assert "4277 x 2848" in message and "4272 x 2848" in message
