import pandas as pd
import pytest

from maromarker2.models.annotation_store import COLUMNS, NO_VALUE, AnnotationStore, file_id_from_path


def test_file_id_from_path_strips_extension_and_side_suffix():
    assert file_id_from_path("TN_Exif_SVL_2012.06.15_11.30.00_L.jpg") == "TN_Exif_SVL_2012.06.15_11.30.00"
    assert file_id_from_path("TN_Exif_SVL_2012.06.15_11.30.00_R.png") == "TN_Exif_SVL_2012.06.15_11.30.00"
    assert file_id_from_path("/some/dir/SVL_2022.06.01_00.50.39_L.jpg") == "SVL_2022.06.01_00.50.39"


def test_new_store_has_legacy_columns_in_order():
    store = AnnotationStore()
    assert list(store.data.columns) == COLUMNS


def test_build_row_left_only_leaves_right_as_no_value():
    store = AnnotationStore()
    row = store.build_row(
        "img1", group="fish", species="gadus_morhua",
        left={"head": (10.0, 20.0), "tail": (30.0, 40.0)},
    )
    assert row["LX1"] == 10.0 and row["LY1"] == 20.0
    assert row["LX2"] == 30.0 and row["LY2"] == 40.0
    assert row["RX1"] == NO_VALUE and row["RY2"] == NO_VALUE
    assert row["LX3"] == NO_VALUE  # width points unset by default


def test_build_row_with_width_points_both_sides():
    store = AnnotationStore()
    row = store.build_row(
        "img1",
        left={"head": (1, 2), "tail": (3, 4), "width_start": (5, 6), "width_end": (7, 8)},
        right={"head": (9, 10), "tail": (11, 12), "width_start": (13, 14), "width_end": (15, 16)},
        length=42.0, height=7.5,
    )
    assert (row["LX3"], row["LY3"], row["LX4"], row["LY4"]) == (5.0, 6.0, 7.0, 8.0)
    assert (row["RX3"], row["RY3"], row["RX4"], row["RY4"]) == (13.0, 14.0, 15.0, 16.0)
    assert row["length"] == 42.0 and row["height"] == 7.5


def test_add_row_and_save_round_trip(tmp_path):
    store = AnnotationStore()
    row = store.build_row("img1", group="fish", species="gadus_morhua", left={"head": (1, 2), "tail": (3, 4)})
    store.add_row(row)

    out_path = tmp_path / "results.csv"
    store.save(out_path)

    reloaded = AnnotationStore.load(out_path)
    assert len(reloaded.data) == 1
    assert reloaded.data.iloc[0]["species"] == "gadus_morhua"
    assert reloaded.data.iloc[0]["LX1"] == 1.0


def test_save_incremental_only_touches_matching_file_id(tmp_path):
    store = AnnotationStore()
    store.add_row(store.build_row("img1", species="a", left={"head": (1, 1), "tail": (2, 2)}))
    path = tmp_path / "results.csv"
    store.save(path)

    # separately, update only img1's rows in-place
    store2 = AnnotationStore()
    store2.add_row(store2.build_row("img1", species="b", left={"head": (9, 9), "tail": (8, 8)}))
    store2.save_incremental(path, "img1")

    on_disk = pd.read_csv(path)
    assert len(on_disk) == 1
    assert on_disk.iloc[0]["species"] == "b"


def test_save_incremental_preserves_other_file_ids(tmp_path):
    store = AnnotationStore()
    store.add_row(store.build_row("img1", species="a", left={"head": (1, 1), "tail": (2, 2)}))
    store.add_row(store.build_row("img2", species="b", left={"head": (3, 3), "tail": (4, 4)}))
    path = tmp_path / "results.csv"
    store.save(path)

    store3 = AnnotationStore()
    store3.add_row(store3.build_row("img1", species="updated", left={"head": (5, 5), "tail": (6, 6)}))
    store3.save_incremental(path, "img1")

    on_disk = pd.read_csv(path)
    assert set(on_disk["file_id"]) == {"img1", "img2"}
    assert on_disk[on_disk["file_id"] == "img1"].iloc[0]["species"] == "updated"
    assert on_disk[on_disk["file_id"] == "img2"].iloc[0]["species"] == "b"


def test_merge_as_match_combines_two_unmatched_rows():
    store = AnnotationStore()
    left_idx = store.add_row(store.build_row("img1", species="a", left={"head": (1, 1), "tail": (2, 2)}))
    right_idx = store.add_row(store.build_row("img1", species="a", right={"head": (9, 9), "tail": (8, 8)}))

    store.merge_as_match(left_idx, right_idx)

    assert len(store.data) == 1
    row = store.data.loc[left_idx]
    assert row["LX1"] == 1.0 and row["RX1"] == 9.0


def test_merge_as_match_takes_deepest_class():
    store = AnnotationStore()
    left_idx = store.add_row(store.build_row("img1", group="fish", species="unidentified", left={"head": (1, 1), "tail": (2, 2)}))
    right_idx = store.add_row(store.build_row("img1", group="fish", species="gadus_morhua", right={"head": (9, 9), "tail": (8, 8)}))

    store.merge_as_match(left_idx, right_idx)

    assert store.data.loc[left_idx, "group"] == "fish"
    assert store.data.loc[left_idx, "species"] == "gadus_morhua"


def test_merge_as_match_rejects_differing_classes():
    store = AnnotationStore()
    left_idx = store.add_row(store.build_row("img1", group="fish", species="a", left={"head": (1, 1), "tail": (2, 2)}))
    right_idx = store.add_row(store.build_row("img1", group="jellyfish", species="a", right={"head": (9, 9), "tail": (8, 8)}))

    with pytest.raises(ValueError):
        store.merge_as_match(left_idx, right_idx)

    assert len(store.data) == 2


def test_split_match_restores_two_unmatched_rows():
    store = AnnotationStore()
    idx = store.add_row(store.build_row(
        "img1", species="a",
        left={"head": (1, 1), "tail": (2, 2)},
        right={"head": (9, 9), "tail": (8, 8)},
    ))

    new_idx = store.split_match(idx)

    assert len(store.data) == 2
    left_row = store.data.loc[idx]
    right_row = store.data.loc[new_idx]
    assert left_row["LX1"] == 1.0 and left_row["RX1"] == NO_VALUE
    assert right_row["RX1"] == 9.0 and right_row["LX1"] == NO_VALUE


def test_predicted_boxes_follow_matches_and_splits():
    store = AnnotationStore()
    left_idx = store.add_row(store.build_row("img1", left={"head": (1, 1), "tail": (2, 2)}))
    right_idx = store.add_row(store.build_row("img1", right={"head": (9, 9), "tail": (8, 8)}))
    store.set_predicted_box(left_idx, "L", (0, 0, 5, 5))
    store.set_predicted_box(right_idx, "R", (7, 7, 12, 12))

    store.merge_as_match(left_idx, right_idx)

    assert store.predicted_box(left_idx, "L") == (0.0, 0.0, 5.0, 5.0)
    assert store.predicted_box(left_idx, "R") == (7.0, 7.0, 12.0, 12.0)

    new_idx = store.split_match(left_idx)

    assert store.predicted_box(new_idx, "R") == (7.0, 7.0, 12.0, 12.0)


def test_predicted_boxes_are_not_saved(tmp_path):
    store = AnnotationStore()
    idx = store.add_row(store.build_row("img1", left={"head": (1, 1), "tail": (2, 2)}))
    store.set_predicted_box(idx, "L", (0, 0, 5, 5))
    path = tmp_path / "out.csv"

    store.save(path)

    assert list(pd.read_csv(path).columns) == COLUMNS
    assert AnnotationStore.load(path).predicted_box(0, "L") is None


def test_load_rejects_mismatched_columns(tmp_path):
    path = tmp_path / "bad.csv"
    pd.DataFrame({"a": [1], "b": [2]}).to_csv(path, index=False)
    with pytest.raises(ValueError):
        AnnotationStore.load(path)


def test_row_indices_are_not_reused_after_removal():
    store = AnnotationStore()
    first = store.add_row({"file_id": "a"})
    second = store.add_row({"file_id": "a"})
    store.remove_row(second)

    assert store.add_row({"file_id": "b"}) == second + 1
    assert first == 0


def test_replace_file_rows_only_touches_that_file():
    store = AnnotationStore()
    store.add_row({"file_id": "a"})
    kept = store.add_row({"file_id": "b"})
    snapshot = store.rows_for_file("a").copy()
    store.update_row(0, species="x")

    store.replace_file_rows("a", snapshot)

    assert store.data.loc[0, "species"] == ""
    assert list(store.data.index) == [0, kept]


def test_ids_are_stamped_on_added_rows_and_written(tmp_path):
    store = AnnotationStore(experiment_id="exp1", user_id="alice")
    index = store.add_row({"file_id": "a"})
    path = tmp_path / "out.csv"
    store.save(path)
    saved = pd.read_csv(path)
    assert saved.loc[0, "user_id"] == "alice"
    assert saved.loc[0, "experiment_id"] == "exp1"
    assert index == 0


def test_loaded_csv_with_empty_ids_accepts_new_ids(tmp_path):
    path = tmp_path / "in.csv"
    store = AnnotationStore()
    store.add_row({"file_id": "a"})
    store.save(path)
    loaded = AnnotationStore.load(path)
    loaded.update_row(0, user_id="jhamer")
    assert loaded.data.loc[0, "user_id"] == "jhamer"


def test_manually_corrected_is_a_bool_column_through_save_and_load(tmp_path):
    path = tmp_path / "in.csv"
    store = AnnotationStore()
    store.add_row(store.build_row("a", manually_corrected=True))
    store.add_row(store.build_row("b", manually_corrected=True))
    assert store.data["manually_corrected"].dtype == bool
    store.save(path)
    loaded = AnnotationStore.load(path)
    assert loaded.data["manually_corrected"].dtype == bool
    loaded.update_row(0, manually_corrected=False)
    loaded.update_row(0, manually_corrected=True)
    assert loaded.data["manually_corrected"].tolist() == [True, True]


def test_load_reads_legacy_manually_corrected_values_as_bool(tmp_path):
    path = tmp_path / "in.csv"
    store = AnnotationStore()
    store.add_row(store.build_row("a", manually_corrected=True))
    store.add_row(store.build_row("b", manually_corrected=False))
    store.add_row(store.build_row("c", manually_corrected=False))
    store.save(path)
    df = pd.read_csv(path).astype({"manually_corrected": object})
    df.loc[0, "manually_corrected"] = "true"
    df.loc[2, "manually_corrected"] = None
    df.to_csv(path, index=False)
    assert AnnotationStore.load(path).data["manually_corrected"].tolist() == [True, False, False]


def test_split_match_clears_length_and_height():
    store = AnnotationStore()
    row = store.build_row("a", left={"head": (1, 1), "tail": (2, 2)}, right={"head": (3, 3), "tail": (4, 4)},
                          length=10.0, height=2.0)
    index = store.add_row(row)
    new_index = store.split_match(index)
    for i in (index, new_index):
        assert store.data.loc[i, "length"] == NO_VALUE
        assert store.data.loc[i, "height"] == NO_VALUE


def test_save_sorts_by_file_id_and_clears_unmatched_lengths(tmp_path):
    store = AnnotationStore()
    store.add_row(store.build_row("b", left={"head": (1, 1), "tail": (2, 2)}, length=5.0))
    store.add_row(store.build_row("a", left={"head": (1, 1), "tail": (2, 2)},
                                  right={"head": (3, 3), "tail": (4, 4)}, length=7.0))
    path = tmp_path / "out.csv"
    store.save(path)
    saved = pd.read_csv(path)
    assert list(saved["file_id"]) == ["a", "b"]
    assert list(saved["length"]) == [7.0, NO_VALUE]
    assert list(store.data["file_id"]) == ["b", "a"]
    assert store.data.loc[0, "length"] == 5.0


def test_edited_rows_get_current_user_and_experiment():
    store = AnnotationStore(experiment_id="exp1", user_id="alice")
    index = store.add_row({"file_id": "a"})
    store.user_id = "bob"
    store.update_row(index, species="x")
    assert store.data.loc[index, "user_id"] == "bob"

    store.user_id = "carol"
    other = store.add_row({"file_id": "a"})
    store.user_id = "dave"
    store.merge_as_match(index, other)
    assert store.data.loc[index, "user_id"] == "dave"


def test_subgroup_is_written_to_csv_after_group(tmp_path):
    store = AnnotationStore()
    store.add_row(store.build_row(
        "img1", group="fish", subgroup="pelagic", species="gadus_morhua", left={"head": (1, 2), "tail": (3, 4)},
    ))
    path = tmp_path / "out.csv"
    store.save(path)

    header = path.read_text().splitlines()[0].split(",")
    assert header[2:5] == ["group", "subgroup", "species"]
    assert AnnotationStore.load(path).data.iloc[0]["subgroup"] == "pelagic"


def test_legacy_csv_without_subgroup_column_is_migrated_on_load(tmp_path):
    store = AnnotationStore()
    store.add_row(store.build_row("img1", group="Fish", species="Gadus_Morhua", left={"head": (1, 2), "tail": (3, 4)}))
    path = tmp_path / "legacy.csv"
    store.data.drop(columns="subgroup").to_csv(path, index=False)

    loaded = AnnotationStore.load(path)

    assert list(loaded.data.columns) == COLUMNS
    row = loaded.data.iloc[0]
    assert (row["group"], row["subgroup"], row["species"]) == ("fish", "", "gadus_morhua")


def test_save_incremental_migrates_legacy_csv_on_disk(tmp_path):
    legacy_store = AnnotationStore()
    legacy_store.add_row(legacy_store.build_row("img1", species="a", left={"head": (1, 1), "tail": (2, 2)}))
    path = tmp_path / "legacy.csv"
    legacy_store.data.drop(columns="subgroup").to_csv(path, index=False)

    store = AnnotationStore()
    store.add_row(store.build_row("img2", subgroup="pelagic", species="b", left={"head": (3, 3), "tail": (4, 4)}))
    store.save_incremental(path, "img2")

    on_disk = pd.read_csv(path)
    assert list(on_disk.columns) == COLUMNS
    assert on_disk.set_index("file_id").loc["img2", "subgroup"] == "pelagic"
    assert on_disk.set_index("file_id").loc["img1", "species"] == "a"


def test_merge_as_match_takes_deepest_subgroup():
    store = AnnotationStore()
    left_idx = store.add_row(store.build_row("img1", group="fish", left={"head": (1, 1), "tail": (2, 2)}))
    right_idx = store.add_row(store.build_row(
        "img1", group="fish", subgroup="pelagic", species="gadus_morhua", right={"head": (9, 9), "tail": (8, 8)},
    ))

    store.merge_as_match(left_idx, right_idx)

    assert store.data.loc[left_idx, "subgroup"] == "pelagic"
    assert store.data.loc[left_idx, "species"] == "gadus_morhua"


def test_merge_as_match_rejects_differing_subgroups():
    store = AnnotationStore()
    left_idx = store.add_row(store.build_row("img1", group="fish", subgroup="benthic", left={"head": (1, 1), "tail": (2, 2)}))
    right_idx = store.add_row(store.build_row("img1", group="fish", subgroup="pelagic", right={"head": (9, 9), "tail": (8, 8)}))

    with pytest.raises(ValueError):
        store.merge_as_match(left_idx, right_idx)


def _animal(store, file_id, **kwargs):
    return store.add_row(store.build_row(file_id, left={"head": (1, 1), "tail": (2, 2)}, **kwargs))


def test_image_checked_requires_animals_that_are_all_checked():
    store = AnnotationStore()
    assert not store.is_image_checked("img1")  # no animals at all

    first = _animal(store, "img1")
    second = _animal(store, "img1", status="unchecked")
    assert not store.is_image_checked("img1")

    store.update_row(second, status="checked")
    assert store.is_image_checked("img1")
    assert first in store.data.index


def test_set_image_remark_applies_to_all_rows_of_the_image_only():
    store = AnnotationStore()
    first = _animal(store, "img1")
    second = _animal(store, "img1")
    other = _animal(store, "img2")

    store.set_image_remark("img1", "blurry")

    assert store.image_remark("img1") == "blurry"
    assert store.data.loc[[first, second], "image_remarks"].tolist() == ["blurry", "blurry"]
    assert store.data.loc[other, "image_remarks"] == ""


def test_new_animal_inherits_the_image_remark():
    store = AnnotationStore()
    _animal(store, "img1")
    store.set_image_remark("img1", "blurry")

    new_index = _animal(store, "img1")

    assert store.data.loc[new_index, "image_remarks"] == "blurry"


def test_image_remark_without_animals_is_kept_until_an_animal_is_added():
    store = AnnotationStore()
    store.set_image_remark("img1", "empty")
    assert store.image_remark("img1") == "empty"

    index = _animal(store, "img1")

    assert store.data.loc[index, "image_remarks"] == "empty"


def test_removing_the_last_animal_keeps_the_image_remark():
    store = AnnotationStore()
    index = _animal(store, "img1")
    store.set_image_remark("img1", "blurry")

    store.remove_row(index)

    assert store.image_remark("img1") == "blurry"


def test_image_overview_reports_count_checked_state_and_remark_per_image():
    store = AnnotationStore()
    _animal(store, "a")
    _animal(store, "a", status="unchecked")
    _animal(store, "b")
    store.set_image_remark("b", "note")
    store.set_image_remark("c", "no animals")

    assert store.image_overview(["a", "b", "c", "d"]) == {
        "a": (2, False, ""), "b": (1, True, "note"), "c": (0, False, "no animals"), "d": (0, False, ""),
    }
    assert AnnotationStore().image_overview(["a"]) == {"a": (0, False, "")}
