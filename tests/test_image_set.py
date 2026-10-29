from datetime import datetime

from maromarker2.models.image_set import ImagePair, parse_stem, parse_timestamp, scan_image_set, suggest_output_filename


def test_parse_stem_detects_side_suffix():
    assert parse_stem("SVL_Remos3_2022.06.01_00.50.39_L") == ("SVL_Remos3_2022.06.01_00.50.39", "L")
    assert parse_stem("SVL_Remos3_2022.06.01_00.50.39_R") == ("SVL_Remos3_2022.06.01_00.50.39", "R")
    assert parse_stem("SVL_Remos3_2022.06.01_00.50.39") == ("SVL_Remos3_2022.06.01_00.50.39", None)


def test_parse_timestamp_extracts_date_and_time():
    ts = parse_timestamp("SVL_Remos3_2022.06.01_00.50.39")
    assert ts == datetime(2022, 6, 1, 0, 50, 39)


def test_parse_timestamp_returns_none_when_absent():
    assert parse_timestamp("no_date_here") is None


def _touch(path, name):
    (path / name).write_bytes(b"\xff\xd8\xff")  # minimal jpeg-ish bytes, content unused


def test_scan_image_set_pairs_left_and_right(tmp_path):
    _touch(tmp_path, "SVL_Remos3_2022.06.01_00.50.39_L.jpg")
    _touch(tmp_path, "SVL_Remos3_2022.06.01_00.50.39_R.jpg")
    _touch(tmp_path, "SVL_Remos3_2022.06.02_01.00.00_L.jpg")  # mono, no right pair

    pairs = scan_image_set(tmp_path)

    assert len(pairs) == 2
    stereo, mono = pairs
    assert stereo.is_stereo
    assert mono.left is not None and mono.right is None
    # sorted chronologically
    assert stereo.timestamp < mono.timestamp


def test_scan_image_set_filters_by_prefix(tmp_path):
    _touch(tmp_path, "AAA_2022.06.01_00.50.39_L.jpg")
    _touch(tmp_path, "BBB_2022.06.01_00.50.39_L.jpg")

    pairs = scan_image_set(tmp_path, prefix="AAA")

    assert len(pairs) == 1
    assert pairs[0].file_id.startswith("AAA")


def test_scan_image_set_ignores_non_image_files(tmp_path):
    _touch(tmp_path, "SVL_2022.06.01_00.50.39_L.jpg")
    (tmp_path / "readme.txt").write_text("not an image")

    pairs = scan_image_set(tmp_path)

    assert len(pairs) == 1


def test_suggest_output_filename_same_month():
    pairs = [
        ImagePair("a", timestamp=datetime(2022, 6, 1)),
        ImagePair("b", timestamp=datetime(2022, 6, 20)),
    ]
    assert suggest_output_filename(pairs) == "results_2022_06.csv"


def test_suggest_output_filename_date_range():
    pairs = [
        ImagePair("a", timestamp=datetime(2022, 6, 1)),
        ImagePair("b", timestamp=datetime(2022, 8, 20)),
    ]
    assert suggest_output_filename(pairs) == "results_2022-06-01_to_2022-08-20.csv"


def test_suggest_output_filename_no_timestamps():
    assert suggest_output_filename([ImagePair("a")]) == "results.csv"


def test_scan_real_example_images(pytestconfig):
    repo_root = pytestconfig.rootpath
    example_dir = repo_root / "sample_data" / "date_range"

    pairs = scan_image_set(example_dir)

    assert len(pairs) > 0
    assert all(p.timestamp is not None for p in pairs)
    assert any(p.is_stereo for p in pairs)
    assert any(not p.is_stereo for p in pairs)  # the mono TN_Exif_SVL_2012.10.12/14... entries
