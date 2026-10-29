import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFrame, QGraphicsSceneMouseEvent, QHeaderView, QSplitter, QToolBar

from maromarker2.models.annotation_store import AnnotationStore
from maromarker2.models.camera_calibration import CameraCalibration
from maromarker2.models.image_set import scan_image_set
from maromarker2.models.taxonomy import DEFAULT_HIERARCHY_PATH, Taxonomy
from maromarker2.ui.editor.annotation_scene import Tool
from maromarker2.ui.editor.editor_widget import EditorWidget, Mode


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _isolated_editor_settings(tmp_path, monkeypatch):
    # EditorSettings() is backed by native QSettings by default; isolate it to an
    # ini file under tmp_path so tests never read/write the real user's persisted defaults.
    from PySide6.QtCore import QSettings
    import maromarker2.ui.editor.editor_settings as editor_settings_module
    ini_path = str(tmp_path / "editor_settings.ini")
    monkeypatch.setattr(
        editor_settings_module, "QSettings", lambda *a, **k: QSettings(ini_path, QSettings.IniFormat)
    )


@pytest.fixture
def image_pairs(pytestconfig):
    return scan_image_set(pytestconfig.rootpath / "sample_data" / "2016_04")


@pytest.fixture
def calibration(pytestconfig):
    config_path = pytestconfig.rootpath / "maromarker2" / "configs" / "config_remos1.json"
    return CameraCalibration.from_json(config_path)


@pytest.fixture
def editor(qapp, image_pairs):
    store = AnnotationStore()
    taxonomy = Taxonomy.load_default()
    return EditorWidget(store, taxonomy, image_pairs)


@pytest.fixture
def editor_with_calibration(qapp, image_pairs, calibration):
    store = AnnotationStore()
    taxonomy = Taxonomy.load_default()
    return EditorWidget(store, taxonomy, image_pairs, calibration=calibration)


def test_loads_first_pair_on_construction(editor, image_pairs):
    assert editor.pair_count() == len(image_pairs)
    assert editor.label_pair.text().startswith(image_pairs[0].file_id)


def test_navigation_next_prev(editor):
    editor.load_pair(1)
    assert editor._pair_index == 1
    editor.load_pair(0)
    assert editor._pair_index == 0


def _add_unchecked_row(editor, file_id):
    row = editor.store.build_row(file_id, left={"head": (1, 1), "tail": (2, 2)}, status="unchecked")
    return editor.store.add_row(row)


def test_navigating_away_marks_rows_of_left_pair_checked(editor, image_pairs):
    first, second = image_pairs[0].file_id, image_pairs[1].file_id
    a = _add_unchecked_row(editor, first)
    b = _add_unchecked_row(editor, second)

    editor.load_pair(1)

    assert editor.store.data.loc[a, "status"] == "checked"
    assert editor.store.data.loc[b, "status"] == "unchecked"
    assert editor.store.is_image_checked(first)

    editor.load_pair(1)  # reselecting the current pair is not navigating away
    assert editor.store.data.loc[b, "status"] == "unchecked"


def test_marking_checked_keeps_user_stamp(editor, image_pairs):
    index = _add_unchecked_row(editor, image_pairs[0].file_id)
    editor.store.data.loc[index, "user_id"] = "bob"
    editor.load_pair(1)
    assert editor.store.data.loc[index, "user_id"] == "bob"


def test_mark_current_pair_checked(editor, image_pairs):
    index = _add_unchecked_row(editor, image_pairs[0].file_id)
    editor.mark_current_pair_checked()
    assert editor.store.data.loc[index, "status"] == "checked"


def test_navigation_ignores_out_of_range(editor):
    editor.load_pair(-1)
    assert editor._pair_index == 0
    editor.load_pair(9999)
    assert editor._pair_index == 0


def test_add_animal_via_scene_updates_counts_label(editor):
    editor._set_tool(Tool.ADD)
    row_index = editor.scene_l.add_animal(QPointF(10, 10), QPointF(20, 20))
    editor._on_store_changed(row_index)

    assert "1 animal(s)" in editor.label_counts.text()


def test_selecting_animal_populates_side_panel(editor):
    row_index = editor.scene_l.add_animal(QPointF(10, 10), QPointF(20, 20), group="fish", species="gadus_morhua")

    editor.scene_l.select_row(row_index)

    assert editor._selected_row == row_index
    assert editor.side_panel.combo_group.currentText() == "fish"
    assert editor.side_panel.combo_species.currentText() == "gadus_morhua"


def test_changing_species_in_side_panel_updates_store(editor):
    row_index = editor.scene_l.add_animal(QPointF(10, 10), QPointF(20, 20), group="fish")
    editor.scene_l.select_row(row_index)

    editor.side_panel.combo_species.setCurrentText("gadus_morhua")

    assert editor.store.data.loc[row_index, "species"] == "gadus_morhua"


def test_delete_selected_removes_animal(editor):
    row_index = editor.scene_l.add_animal(QPointF(10, 10), QPointF(20, 20))
    editor.scene_l.select_row(row_index)

    editor._delete_selected()

    assert row_index not in editor.store.data.index
    assert editor._selected_row is None


def test_remove_tool_on_selected_animal_clears_selection(editor):
    row_index = editor.scene_l.add_animal(QPointF(10, 10), QPointF(20, 20))
    editor.scene_l.select_row(row_index)
    assert editor._selected_row == row_index

    editor.scene_l.remove_row(row_index)

    assert row_index not in editor.store.data.index
    assert editor._selected_row is None
    assert editor._selected_rows == set()


def test_activating_remove_tool_keeps_selection_and_stays_active(editor):
    first = editor.scene_l.add_animal(QPointF(10, 10), QPointF(20, 20))
    second = editor.scene_l.add_animal(QPointF(30, 30), QPointF(40, 40))
    keep = editor.scene_l.add_animal(QPointF(50, 50), QPointF(60, 60))
    editor.scene_l.select_row(first)
    editor.scene_l.select_row(second, additive=True)

    editor.action_remove.trigger()

    assert set(editor.store.data.index) == {first, second, keep}
    assert editor.scene_l.tool == Tool.REMOVE and editor.scene_r.tool == Tool.REMOVE
    assert editor.action_remove.isChecked()


def test_remove_tool_without_selection_only_activates(editor):
    row_index = editor.scene_l.add_animal(QPointF(10, 10), QPointF(20, 20))

    editor.action_remove.trigger()

    assert row_index in editor.store.data.index
    assert editor.scene_l.tool == Tool.REMOVE


def test_remove_tool_click_in_list_removes_animal(editor):
    left_index = editor.scene_l.add_animal(QPointF(10, 10), QPointF(20, 20))
    right_index = editor.scene_r.add_animal(QPointF(30, 30), QPointF(40, 40))
    other = editor.scene_l.add_animal(QPointF(50, 50), QPointF(60, 60))
    editor._perform_match(left_index, right_index)
    editor.action_remove.trigger()

    editor.list_r.table.row_clicked.emit(0, False, False)

    assert set(editor.store.data.index) == {other}
    assert editor.scene_l.tool == Tool.REMOVE


def test_manual_match_merges_rows_via_match_mode(editor):
    editor._set_mode(Mode.MATCH)
    left_index = editor.scene_l.add_animal(QPointF(10, 10), QPointF(20, 20))
    right_index = editor.scene_r.add_animal(QPointF(30, 30), QPointF(40, 40))

    editor.scene_l.select_row(left_index)
    editor.scene_r.select_row(right_index)

    row = editor.store.data.loc[left_index]
    assert right_index not in editor.store.data.index
    assert row["RX1"] == 30.0 and row["RY1"] == 30.0


def test_manual_match_order_independent(editor):
    editor._set_mode(Mode.MATCH)
    left_index = editor.scene_l.add_animal(QPointF(10, 10), QPointF(20, 20))
    right_index = editor.scene_r.add_animal(QPointF(30, 30), QPointF(40, 40))

    editor.scene_r.select_row(right_index)
    editor.scene_l.select_row(left_index)

    row = editor.store.data.loc[left_index]
    assert row["RX1"] == 30.0 and right_index not in editor.store.data.index


def test_manual_match_selects_merged_animal_on_both_sides(editor):
    editor._set_mode(Mode.MATCH)
    left_index = editor.scene_l.add_animal(QPointF(10, 10), QPointF(20, 20))
    right_index = editor.scene_r.add_animal(QPointF(30, 30), QPointF(40, 40))

    editor.scene_l.select_row(left_index)
    editor.scene_r.select_row(right_index)

    assert editor._selected_rows == {left_index}
    assert editor.scene_l._selected_rows == {left_index}
    assert editor.scene_r._selected_rows == {left_index}
    selected_matched_rows = {index.row() for index in editor.matched_list.table.selectionModel().selectedRows()}
    assert selected_matched_rows == {0}


def test_repeated_pick_on_same_side_replaces_pending(editor):
    editor._set_mode(Mode.MATCH)
    first_left = editor.scene_l.add_animal(QPointF(10, 10), QPointF(20, 20))
    second_left = editor.scene_l.add_animal(QPointF(50, 50), QPointF(60, 60))
    right_index = editor.scene_r.add_animal(QPointF(30, 30), QPointF(40, 40))

    editor.scene_l.select_row(first_left)
    editor.scene_l.select_row(second_left)
    editor.scene_r.select_row(right_index)

    assert editor.store.data.loc[first_left, "RX1"] == -1.0
    assert editor.store.data.loc[second_left, "RX1"] == 30.0


def test_match_mode_info_panel_guides_user(editor):
    assert editor.info_panel.text() == ""
    editor._set_mode(Mode.MATCH)
    assert editor.info_panel.text() == "Matching: Select animal from one side"

    left_index = editor.scene_l.add_animal(QPointF(10, 10), QPointF(20, 20))
    right_index = editor.scene_r.add_animal(QPointF(30, 30), QPointF(40, 40))
    editor.scene_l.select_row(left_index)
    assert editor.info_panel.text() == "Matching: Select animal from other side"

    editor.scene_l.select_row(None)
    assert editor.info_panel.text() == "Matching: Select animal from one side"

    editor.scene_l.select_row(left_index)
    editor.scene_r.select_row(right_index)
    assert editor.info_panel.text() == ""

    editor.scene_l.select_row(None)
    assert editor.info_panel.text() == "Matching: Select animal from one side"

    editor._set_mode(Mode.EDIT)
    assert editor.info_panel.text() == ""


def test_selecting_matched_animal_while_pick_pending_clears_hint(editor):
    editor._set_mode(Mode.MATCH)
    matched_left = editor.scene_l.add_animal(QPointF(10, 10), QPointF(20, 20))
    matched_right = editor.scene_r.add_animal(QPointF(30, 30), QPointF(40, 40))
    editor.scene_l.select_row(matched_left)
    editor.scene_r.select_row(matched_right)
    other = editor.scene_l.add_animal(QPointF(50, 50), QPointF(60, 60))

    editor.scene_l.select_row(other)
    assert editor.info_panel.text() == "Matching: Select animal from other side"

    editor.scene_l.select_row(matched_left)
    assert editor.info_panel.text() == ""
    assert editor._pending_match is None


def test_long_info_message_wraps_instead_of_widening_panel(editor):
    editor.resize(1200, 800)
    editor.show()
    QApplication.processEvents()
    width_before = editor.info_panel.width()
    container_width_before = editor.info_panel.parentWidget().width()

    editor.info_panel.show_message("A very long message " * 20)
    QApplication.processEvents()

    assert editor.info_panel.wordWrap()
    assert editor.info_panel.width() == width_before
    assert editor.info_panel.parentWidget().width() == container_width_before
    assert editor.info_panel.height() > editor.info_panel.fontMetrics().height() * 2


def test_split_selected_restores_two_unmatched_rows(editor):
    left_index = editor.scene_l.add_animal(QPointF(10, 10), QPointF(20, 20))
    right_index = editor.scene_r.add_animal(QPointF(30, 30), QPointF(40, 40))
    editor._perform_match(left_index, right_index)
    editor.scene_l.select_row(left_index)

    editor._split_selected()

    row = editor.store.data.loc[left_index]
    assert row["RX1"] == -1.0
    new_rows = editor.store.data[editor.store.data["LX1"] == -1.0]
    assert len(new_rows) == 1
    assert new_rows.iloc[0]["RX1"] == 30.0


def test_split_selected_noop_when_not_matched(editor):
    row_index = editor.scene_l.add_animal(QPointF(10, 10), QPointF(20, 20))
    editor.scene_l.select_row(row_index)
    count_before = len(editor.store.data)

    editor._split_selected()

    assert len(editor.store.data) == count_before


def test_manual_match_triggers_triangulation_when_calibration_present(editor_with_calibration):
    editor = editor_with_calibration
    left_index = editor.scene_l.add_animal(QPointF(2548.0, 1042.0), QPointF(2552.0, 1138.0))
    right_index = editor.scene_r.add_animal(QPointF(997.0, 990.0), QPointF(1022.0, 1117.0))

    editor._perform_match(left_index, right_index)

    assert editor.store.data.loc[left_index, "length"] == pytest.approx(15.40, rel=0.1)


def test_moving_keypoint_of_matched_animal_recalculates_length(editor_with_calibration):
    editor = editor_with_calibration
    left_index = editor.scene_l.add_animal(QPointF(2548.0, 1042.0), QPointF(2552.0, 1138.0))
    right_index = editor.scene_r.add_animal(QPointF(997.0, 990.0), QPointF(1022.0, 1117.0))
    merged = editor._perform_match(left_index, right_index)
    length_before = editor.store.data.loc[merged, "length"]

    editor.scene_l._markers[merged].tail.setPos(QPointF(2552.0, 1300.0))

    assert editor.store.data.loc[merged, "length"] != pytest.approx(length_before)
    assert editor.matched_list.table.item(0, 4).text() == f"{editor.store.data.loc[merged, 'length']:.1f}"


def test_auto_match_action_disabled_without_calibration(editor):
    editor._set_mode(Mode.MATCH)
    assert editor.action_auto_match.isEnabled() is False


def test_auto_match_action_enabled_with_calibration(editor_with_calibration):
    editor_with_calibration._set_mode(Mode.MATCH)
    assert editor_with_calibration.action_auto_match.isEnabled() is True


def test_auto_match_merges_via_review_dialog(editor_with_calibration, monkeypatch):
    editor = editor_with_calibration
    left_index = editor.scene_l.add_animal(QPointF(2548.0, 1042.0), QPointF(2552.0, 1138.0))
    right_index = editor.scene_r.add_animal(QPointF(997.0, 990.0), QPointF(1022.0, 1117.0))

    import maromarker2.ui.editor.editor_widget as editor_widget_module

    class _AutoAcceptDialog:
        def __init__(self, results, parent=None):
            self._results = results

        def exec(self):
            return True

        def accepted_results(self):
            return self._results

    monkeypatch.setattr(editor_widget_module, "MatchReviewDialog", _AutoAcceptDialog)

    editor._auto_match()

    assert right_index not in editor.store.data.index
    assert editor.store.data.loc[left_index, "RX1"] == 997.0


def test_manual_match_with_differing_classes_is_rejected(editor):
    editor._set_mode(Mode.MATCH)
    left_index = editor.scene_l.add_animal(QPointF(10, 10), QPointF(20, 20))
    right_index = editor.scene_r.add_animal(QPointF(30, 30), QPointF(40, 40))
    editor.store.update_row(left_index, group="fish", species="gadus_morhua")
    editor.store.update_row(right_index, group="jellyfish", species="unidentified")

    editor.scene_l.select_row(left_index)
    editor.scene_r.select_row(right_index)

    assert editor.info_panel.text() == "Matching not possible: Animals have differing classes"
    # remainingTime() is unreliable here (coarse timers may overshoot the interval), so check the interval
    assert editor.info_panel._timer.isActive()
    assert editor.info_panel._timer.interval() == 5000
    assert editor._selected_rows == set()
    assert editor.store.data.loc[left_index, "RX1"] == -1.0
    assert right_index in editor.store.data.index


def test_manual_match_with_unidentified_takes_deepest_class(editor):
    editor._set_mode(Mode.MATCH)
    left_index = editor.scene_l.add_animal(QPointF(10, 10), QPointF(20, 20))
    right_index = editor.scene_r.add_animal(QPointF(30, 30), QPointF(40, 40))
    editor.store.update_row(left_index, group="fish", species="unidentified")
    editor.store.update_row(right_index, group="fish", species="gadus_morhua")

    editor.scene_l.select_row(left_index)
    editor.scene_r.select_row(right_index)

    assert editor.store.data.loc[left_index, "species"] == "gadus_morhua"
    assert right_index not in editor.store.data.index


def test_auto_match_skips_animals_with_differing_classes(editor_with_calibration, monkeypatch):
    editor = editor_with_calibration
    left_index = editor.scene_l.add_animal(QPointF(2548.0, 1042.0), QPointF(2552.0, 1138.0))
    right_index = editor.scene_r.add_animal(QPointF(997.0, 990.0), QPointF(1022.0, 1117.0))
    editor.store.update_row(left_index, group="fish")
    editor.store.update_row(right_index, group="jellyfish")

    import maromarker2.ui.editor.editor_widget as editor_widget_module

    shown = []
    monkeypatch.setattr(editor_widget_module.QMessageBox, "information", staticmethod(lambda *a, **k: shown.append(a)))

    editor._auto_match()

    assert shown
    assert right_index in editor.store.data.index


def test_auto_match_noop_when_no_unmatched_candidates(editor_with_calibration, monkeypatch):
    import maromarker2.ui.editor.editor_widget as editor_widget_module

    monkeypatch.setattr(editor_widget_module.QMessageBox, "information", staticmethod(lambda *a, **k: None))

    editor_with_calibration._auto_match()  # should not raise with nothing to match


# --- animal list panels ------------------------------------------------------ #

def test_list_panels_show_added_animals(editor):
    left_index = editor.scene_l.add_animal(QPointF(10, 10), QPointF(20, 20))
    right_index = editor.scene_r.add_animal(QPointF(30, 30), QPointF(40, 40))

    assert editor.list_l.table.rowCount() == 1
    assert editor.list_r.table.rowCount() == 1
    assert editor.list_l.table.item(0, 0).data(Qt.UserRole) == left_index
    assert editor.list_r.table.item(0, 0).data(Qt.UserRole) == right_index


def test_list_panels_show_group_species_and_match_state(editor):
    left_index = editor.scene_l.add_animal(
        QPointF(10, 10), QPointF(20, 20), group="fish", subgroup="pelagic", species="gadus_morhua",
    )
    right_index = editor.scene_r.add_animal(QPointF(30, 30), QPointF(40, 40))

    assert editor.list_l.table.item(0, 2).text() == "fish"
    assert editor.list_l.table.item(0, 3).text() == "pelagic"
    assert editor.list_l.table.item(0, 4).text() == "gadus_morhua"
    assert editor.list_l.table.item(0, 1).checkState() == Qt.Unchecked

    editor._perform_match(left_index, right_index)

    assert editor.list_l.table.item(0, 1).checkState() == Qt.Checked


def test_selecting_in_list_selects_in_scene(editor):
    row_index = editor.scene_l.add_animal(QPointF(10, 10), QPointF(20, 20))

    editor.list_l.table.row_clicked.emit(0, False, False)

    assert editor._selected_row == row_index
    assert editor.scene_l._selected_row == row_index


def test_selecting_in_scene_selects_in_list(editor):
    row_index = editor.scene_l.add_animal(QPointF(10, 10), QPointF(20, 20))

    editor.scene_l.select_row(row_index)

    assert editor.list_l.table.item(editor.list_l.table.currentRow(), 0).data(Qt.UserRole) == row_index


def test_delete_via_list_panel_removes_animal(editor):
    row_index = editor.scene_l.add_animal(QPointF(10, 10), QPointF(20, 20))

    editor.list_l.table.row_clicked.emit(0, False, False)
    editor._delete_selected()

    assert row_index not in editor.store.data.index
    assert editor.list_l.table.rowCount() == 0


def test_navigating_pairs_refreshes_list_panels(editor):
    editor.scene_l.add_animal(QPointF(10, 10), QPointF(20, 20))
    assert editor.list_l.table.rowCount() == 1

    editor.load_pair(1)

    assert editor.list_l.table.rowCount() == 0



# --- linked zoom/pan between the two canvases --------------------------------- #

def test_zooming_one_canvas_mirrors_to_the_other(editor):
    editor.canvas_l.zoom_by(1.25)

    assert editor.canvas_r._scale == pytest.approx(editor.canvas_l._scale)


def test_panning_one_canvas_mirrors_to_the_other(editor):
    editor.canvas_l.pan_by(15, -7)

    assert editor.canvas_r.horizontalScrollBar().value() == editor.canvas_l.horizontalScrollBar().value()
    assert editor.canvas_r.verticalScrollBar().value() == editor.canvas_l.verticalScrollBar().value()


def test_scrollbar_moves_mirror_to_the_other_canvas(editor):
    editor.canvas_l.zoom_by(4.0)
    h_l, v_l = editor.canvas_l.horizontalScrollBar(), editor.canvas_l.verticalScrollBar()
    h_r, v_r = editor.canvas_r.horizontalScrollBar(), editor.canvas_r.verticalScrollBar()
    assert h_l.maximum() > 0 and v_l.maximum() > 0

    h_l.setValue(h_l.maximum() // 2)
    v_l.setValue(v_l.maximum() // 3)
    assert (h_r.value(), v_r.value()) == (h_l.value(), v_l.value())

    v_r.setValue(v_r.maximum() // 2)
    assert v_l.value() == v_r.value()


def test_toolbar_zoom_only_applies_factor_once(editor):
    editor._zoom(1.25)

    assert editor.canvas_l._scale == pytest.approx(editor.canvas_r._scale)


# --- undo/redo ----------------------------------------------------------------- #

def test_undo_redo_add_animal(editor):
    row_index = editor.scene_l.add_animal(QPointF(10, 10), QPointF(20, 20))
    assert row_index in editor.store.data.index

    editor._undo_stack.undo()
    assert row_index not in editor.store.data.index

    editor._undo_stack.redo()
    assert row_index in editor.store.data.index


def test_undo_redo_delete_animal(editor):
    row_index = editor.scene_l.add_animal(QPointF(10, 10), QPointF(20, 20))
    editor._delete_row(row_index)
    assert row_index not in editor.store.data.index

    editor._undo_stack.undo()
    assert row_index in editor.store.data.index

    editor._undo_stack.redo()
    assert row_index not in editor.store.data.index


def test_undo_history_is_limited(editor):
    from maromarker2.ui.editor.editor_widget import UNDO_LIMIT

    assert editor._undo_stack.undoLimit() == UNDO_LIMIT

    # a small limit keeps the test fast; the cap logic is the same
    limit = 5
    editor._undo_stack.setUndoLimit(limit)
    for i in range(limit + 5):
        editor.scene_l.add_animal(QPointF(10 + i, 10), QPointF(20 + i, 20))

    assert editor._undo_stack.count() == limit
    for _ in range(limit + 5):
        editor._undo()
    assert len(editor.store.data) == 5


def test_undo_after_switching_pairs_navigates_back_and_keeps_other_pairs(editor):
    first_id = editor.image_pairs[0].file_id
    row_a = editor.scene_l.add_animal(QPointF(10, 10), QPointF(20, 20))
    editor.load_pair(1)
    row_b = editor.scene_l.add_animal(QPointF(30, 30), QPointF(40, 40))
    assert editor._undo_stack.count() == 2

    editor._undo()  # the add on pair 1
    assert editor._pair_index == 1
    assert row_b not in editor.store.data.index
    assert row_a in editor.store.data.index

    editor._undo()  # the add on pair 0: navigates back to it
    assert editor._pair_index == 0
    assert row_a not in editor.store.data.index

    editor._redo()
    assert editor._pair_index == 0
    assert editor.store.data.loc[row_a, "file_id"] == first_id

    editor._redo()
    assert editor._pair_index == 1
    assert row_b in editor.store.data.index
    assert row_a in editor.store.data.index


def test_undo_snapshots_only_hold_current_pair_rows(editor):
    editor.scene_l.add_animal(QPointF(10, 10), QPointF(20, 20))
    editor.load_pair(1)
    editor.scene_l.add_animal(QPointF(30, 30), QPointF(40, 40))

    command = editor._undo_stack.command(1)
    second_id = editor.image_pairs[1].file_id
    assert set(command._after_rows["file_id"]) == {second_id}
    assert command._before_rows.empty


def test_undo_redo_field_edit(editor):
    row_index = editor.scene_l.add_animal(QPointF(10, 10), QPointF(20, 20), group="fish")
    editor._selected_row = row_index

    editor._update_selected(species="gadus_morhua")
    assert editor.store.data.loc[row_index, "species"] == "gadus_morhua"

    editor._undo_stack.undo()
    assert editor.store.data.loc[row_index, "species"] != "gadus_morhua"

    editor._undo_stack.redo()
    assert editor.store.data.loc[row_index, "species"] == "gadus_morhua"


def test_undo_group_change_restores_group_and_species_together(editor):
    row_index = editor.scene_l.add_animal(
        QPointF(10, 10), QPointF(20, 20), group="fish", species="gadus_morhua"
    )
    editor._selected_row = row_index
    editor.side_panel.set_animal(editor.store.data.loc[row_index])
    undo_count = editor._undo_stack.count()

    editor.side_panel.combo_group.setCurrentText("jellyfish")
    assert editor._undo_stack.count() == undo_count + 1
    assert editor.store.data.loc[row_index, "group"] == "jellyfish"
    assert editor.store.data.loc[row_index, "species"] == "unidentified"

    editor._undo_stack.undo()
    assert editor.store.data.loc[row_index, "group"] == "fish"
    assert editor.store.data.loc[row_index, "species"] == "gadus_morhua"

    editor._undo_stack.redo()
    assert editor.store.data.loc[row_index, "group"] == "jellyfish"
    assert editor.store.data.loc[row_index, "species"] == "unidentified"


def test_undo_redo_match(editor):
    left_index = editor.scene_l.add_animal(QPointF(10, 10), QPointF(20, 20))
    right_index = editor.scene_r.add_animal(QPointF(30, 30), QPointF(40, 40))

    editor._perform_match(left_index, right_index)
    assert right_index not in editor.store.data.index
    assert editor.store.data.loc[left_index, "RX1"] == 30.0

    editor._undo_stack.undo()
    assert right_index in editor.store.data.index
    assert editor.store.data.loc[left_index, "RX1"] == -1.0

    editor._undo_stack.redo()
    assert right_index not in editor.store.data.index
    assert editor.store.data.loc[left_index, "RX1"] == 30.0


def test_undo_redo_buttons_have_fixed_text(editor):
    editor.scene_l.add_animal(QPointF(10, 10), QPointF(20, 20))
    texts = [a.text() for toolbar in editor.findChildren(QToolBar) for a in toolbar.actions()]
    assert "Undo" in texts
    assert "Redo" in texts


def test_info_panel_reports_undo_and_redo(editor):
    row_index = editor.scene_l.add_animal(QPointF(10, 10), QPointF(20, 20), group="fish")
    editor._selected_row = row_index
    editor._update_selected(species="gadus_morhua")
    assert editor.info_panel.text() == ""

    editor._undo()
    assert editor.info_panel.isVisibleTo(editor)
    assert editor.info_panel.text() == "Undid: Edit animal"

    editor._redo()
    assert editor.info_panel.text() == "Redid: Edit animal"

    editor.info_panel.clear()
    assert editor.info_panel.text() == ""
    assert editor.info_panel.isVisibleTo(editor)  # the highlighted box stays


def test_undo_with_empty_stack_is_noop(editor):
    editor._undo_stack.clear()
    editor._undo()
    assert editor.info_panel.text() == ""


def test_undo_redo_shortcuts_are_ctrl_z_and_ctrl_y(editor):
    undo_actions = [a for a in editor.actions() if a.shortcut().toString() == "Ctrl+Z"]
    redo_actions = [a for a in editor.actions() if a.shortcut().toString() == "Ctrl+Y"]
    # the actions live on a toolbar, which is a child widget, not editor itself
    for toolbar in editor.findChildren(QToolBar):
        undo_actions += [a for a in toolbar.actions() if a.shortcut().toString() == "Ctrl+Z"]
        redo_actions += [a for a in toolbar.actions() if a.shortcut().toString() == "Ctrl+Y"]

    assert undo_actions
    assert redo_actions


# --- Edit/Match modes ---------------------------------------------------------- #

def test_default_mode_is_edit_with_editing_actions_visible(editor):
    assert editor._mode == Mode.EDIT
    assert editor.action_edit.isVisible() is True
    assert editor.action_add.isVisible() is True
    assert editor.action_remove.isVisible() is True
    assert editor.action_split_match.isVisible() is False
    assert editor.action_auto_match.isVisible() is False


def test_set_mode_match_shows_matching_actions_only(editor):
    editor._set_mode(Mode.MATCH)

    assert editor.action_edit.isVisible() is False
    assert editor.action_add.isVisible() is False
    assert editor.action_remove.isVisible() is False
    assert editor.action_split_match.isVisible() is True
    assert editor.action_auto_match.isVisible() is True


def test_mode_bar_actions_switch_mode(editor):
    editor.action_mode_match.trigger()
    assert editor._mode == Mode.MATCH

    editor.action_mode_edit.trigger()
    assert editor._mode == Mode.EDIT


# --- selection: list/scene parity + multi-select -------------------------------- #

def test_ctrl_click_multiselects_on_same_side(editor):
    first = editor.scene_l.add_animal(QPointF(10, 10), QPointF(20, 20))
    second = editor.scene_l.add_animal(QPointF(30, 30), QPointF(40, 40))

    editor.scene_l.select_row(first)
    editor.scene_l.select_row(second, additive=True)

    assert editor._selected_rows == {first, second}
    assert editor.scene_l._selected_rows == {first, second}
    assert editor.list_l.table.selectionModel().selectedRows().__len__() == 2


def test_edit_mode_selection_confined_to_sides_with_points(editor):
    left_index = editor.scene_l.add_animal(QPointF(10, 10), QPointF(20, 20))
    right_index = editor.scene_r.add_animal(QPointF(30, 30), QPointF(40, 40))

    editor.scene_l.select_row(left_index)
    assert editor.scene_l._selected_rows == {left_index}
    assert editor.scene_r._selected_rows == set()

    editor.scene_r.select_row(right_index)
    assert editor.scene_r._selected_rows == {right_index}
    assert editor.scene_l._selected_rows == set()


def test_edit_mode_selecting_matched_animal_highlights_both_sides(editor):
    left_index = editor.scene_l.add_animal(QPointF(10, 10), QPointF(20, 20))
    right_index = editor.scene_r.add_animal(QPointF(30, 30), QPointF(40, 40))
    editor._perform_match(left_index, right_index)

    editor.scene_l.select_row(left_index)

    assert editor._mode == Mode.EDIT
    assert editor._selected_rows == {left_index}
    assert editor.scene_l._selected_rows == {left_index}
    assert editor.scene_r._selected_rows == {left_index}
    for panel in (editor.list_l, editor.list_r):
        assert {i.row() for i in panel.table.selectionModel().selectedRows()} == {0}
    assert {i.row() for i in editor.matched_list.table.selectionModel().selectedRows()} == {0}

    editor.scene_r.select_row(None)
    assert editor.scene_l._selected_rows == set()
    assert editor.scene_r._selected_rows == set()
    assert editor.list_r.table.selectionModel().selectedRows() == []


def _add_three_left(editor):
    return [
        editor.scene_l.add_animal(QPointF(10 * i, 10 * i), QPointF(10 * i + 5, 10 * i + 5))
        for i in (1, 2, 3)
    ]


def _click_list_row(table, table_row, modifiers=Qt.NoModifier):
    rect = table.visualRect(table.model().index(table_row, 0))
    QTest.mouseClick(table.viewport(), Qt.LeftButton, modifiers, rect.center())


def test_shift_click_selects_range_in_list(editor):
    rows = _add_three_left(editor)
    table = editor.list_l.table
    table.resize(300, 200)
    table.show()
    QApplication.processEvents()

    _click_list_row(table, 0)
    _click_list_row(table, 2, Qt.ShiftModifier)

    assert editor._selected_rows == set(rows)
    assert editor.scene_l._selected_rows == set(rows)
    assert {i.row() for i in table.selectionModel().selectedRows()} == {0, 1, 2}

    # a second Shift+click re-ranges from the same anchor
    _click_list_row(table, 1, Qt.ShiftModifier)
    assert editor._selected_rows == set(rows[:2])

    table.hide()


def test_ctrl_shift_click_adds_range_to_selection(editor):
    rows = _add_three_left(editor)
    fourth = editor.scene_l.add_animal(QPointF(80, 80), QPointF(90, 90))
    table = editor.list_l.table
    table.resize(300, 200)
    table.show()
    QApplication.processEvents()

    _click_list_row(table, 0)
    _click_list_row(table, 2, Qt.ControlModifier)
    _click_list_row(table, 3, Qt.ControlModifier | Qt.ShiftModifier)

    assert editor._selected_rows == {rows[0], rows[2], fourth}

    table.hide()


def test_ctrl_a_in_list_selects_all_rows(editor):
    rows = _add_three_left(editor)
    table = editor.list_l.table
    table.resize(300, 200)
    table.show()
    table.setFocus()
    QApplication.processEvents()

    QTest.keyClick(table, Qt.Key_A, Qt.ControlModifier)

    assert editor._selected_rows == set(rows)
    assert editor.scene_l._selected_rows == set(rows)
    assert {i.row() for i in table.selectionModel().selectedRows()} == {0, 1, 2}

    editor._delete_selected()
    assert not any(row in editor.store.data.index for row in rows)

    table.hide()


def test_matched_list_supports_multi_selection(editor):
    pairs = []
    for i in (1, 2):
        left = editor.scene_l.add_animal(QPointF(10 * i, 10 * i), QPointF(10 * i + 5, 10 * i + 5))
        right = editor.scene_r.add_animal(QPointF(50 * i, 50 * i), QPointF(50 * i + 5, 50 * i + 5))
        pairs.append(editor._perform_match(left, right))

    table = editor.matched_list.table
    table.resize(300, 200)
    table.show()
    table.setFocus()
    QApplication.processEvents()
    QTest.keyClick(table, Qt.Key_A, Qt.ControlModifier)

    assert editor._selected_rows == set(pairs)
    assert editor.scene_l._selected_rows == set(pairs)
    assert editor.scene_r._selected_rows == set(pairs)

    table.hide()


def test_update_selected_applies_to_all_selected_animals(editor):
    first = editor.scene_l.add_animal(QPointF(10, 10), QPointF(20, 20), group="fish")
    second = editor.scene_r.add_animal(QPointF(30, 30), QPointF(40, 40), group="jellyfish")
    other = editor.scene_l.add_animal(QPointF(50, 50), QPointF(60, 60), group="fish")
    editor.scene_l.select_row(first)
    editor.scene_r.select_row(second, additive=True)
    assert editor._selected_rows == {first, second}

    editor.side_panel.combo_group.setCurrentText("fish")
    editor.side_panel.combo_species.setCurrentText("gadus_morhua")

    for row in (first, second):
        assert editor.store.data.loc[row, "group"] == "fish"
        assert editor.store.data.loc[row, "species"] == "gadus_morhua"
    assert editor.store.data.loc[other, "species"] != "gadus_morhua"
    assert editor._selected_rows == {first, second}

    editor._undo_stack.undo()  # species
    editor._undo_stack.undo()  # group

    assert editor._selected_rows == {first, second}


def test_multi_selection_side_panel_shows_mixed_values_without_overwriting(editor):
    first = editor.scene_l.add_animal(QPointF(10, 10), QPointF(20, 20), group="fish")
    second = editor.scene_l.add_animal(QPointF(30, 30), QPointF(40, 40), group="jellyfish")
    editor.store.update_row(first, object_remarks="a")
    editor.store.update_row(second, object_remarks="b")
    editor.scene_l.select_row(first)
    editor.scene_l.select_row(second, additive=True)

    panel = editor.side_panel
    assert panel.combo_group.isEnabled()
    assert panel.combo_group.currentIndex() == -1
    assert panel.edit_remark.text() == ""

    panel.edit_remark.editingFinished.emit()  # untouched mixed field must not overwrite
    assert editor.store.data.loc[first, "object_remarks"] == "a"
    assert editor.store.data.loc[second, "object_remarks"] == "b"

    panel.edit_remark.textEdited.emit("x")
    panel.edit_remark.setText("x")
    panel.edit_remark.editingFinished.emit()
    assert editor.store.data.loc[first, "object_remarks"] == "x"
    assert editor.store.data.loc[second, "object_remarks"] == "x"


def test_side_panel_has_no_status_or_manually_corrected_fields(editor):
    assert not hasattr(editor.side_panel, "combo_status")
    assert not hasattr(editor.side_panel, "check_manually_corrected")


def test_split_selected_splits_all_selected_matches(editor):
    pairs = []
    for i in (1, 2):
        left = editor.scene_l.add_animal(QPointF(10 * i, 10 * i), QPointF(10 * i + 5, 10 * i + 5))
        right = editor.scene_r.add_animal(QPointF(50 * i, 50 * i), QPointF(50 * i + 5, 50 * i + 5))
        pairs.append(editor._perform_match(left, right))
    editor._set_mode(Mode.MATCH)
    editor.scene_l.select_row(pairs[0])
    editor.scene_l.select_row(pairs[1], additive=True)
    undo_count = editor._undo_stack.count()

    editor.action_split_match.trigger()

    data = editor.store.data
    assert len(data) == 4
    assert not ((data["LX1"] > 0) & (data["RX1"] > 0)).any()
    assert editor._undo_stack.count() == undo_count + 1
    assert editor._selected_rows == set(pairs)
    assert editor.scene_r._selected_rows == set()

    editor._undo_stack.undo()
    assert len(editor.store.data) == 2
    assert editor._selected_rows == set(pairs)


def test_list_selection_helpers_ignore_unknown_rows(editor):
    editor._on_rows_selected({123456}, False)
    assert editor._selected_rows == set()


def test_list_selection_behaves_like_scene_selection(editor):
    row_index = editor.scene_l.add_animal(QPointF(10, 10), QPointF(20, 20), group="fish")

    editor.list_l.animal_selected.emit(row_index, False)

    assert editor._selected_row == row_index
    assert editor.scene_l._selected_row == row_index
    assert editor.side_panel.combo_group.currentText() == "fish"


def test_delete_selected_removes_multiple_rows(editor):
    first = editor.scene_l.add_animal(QPointF(10, 10), QPointF(20, 20))
    second = editor.scene_l.add_animal(QPointF(30, 30), QPointF(40, 40))
    editor.scene_l.select_row(first)
    editor.scene_l.select_row(second, additive=True)

    editor._delete_selected()

    assert first not in editor.store.data.index
    assert second not in editor.store.data.index
    assert editor._selected_rows == set()


# --- match mode: counterpart auto-select + split/remove match ------------------- #

def test_match_mode_selecting_matched_animal_selects_both_sides(editor):
    left_index = editor.scene_l.add_animal(QPointF(10, 10), QPointF(20, 20))
    right_index = editor.scene_r.add_animal(QPointF(30, 30), QPointF(40, 40))
    editor._perform_match(left_index, right_index)

    editor._set_mode(Mode.MATCH)
    editor.scene_l.select_row(left_index)

    assert editor.scene_l._selected_rows == {left_index}
    assert editor.scene_r._selected_rows == {left_index}


def test_split_match_reachable_in_match_mode(editor):
    left_index = editor.scene_l.add_animal(QPointF(10, 10), QPointF(20, 20))
    right_index = editor.scene_r.add_animal(QPointF(30, 30), QPointF(40, 40))
    editor._perform_match(left_index, right_index)

    editor._set_mode(Mode.MATCH)
    editor.scene_l.select_row(left_index)
    editor.action_split_match.trigger()

    row = editor.store.data.loc[left_index]
    assert row["RX1"] == -1.0


# --- misc layout/cleanup --------------------------------------------------------- #

def test_animal_list_panel_has_no_delete_button(editor):
    assert not hasattr(editor.list_l, "button_delete")


def test_canvas_and_list_panel_share_a_resizable_splitter(editor):
    # one shared vertical splitter trades canvas height for list height on BOTH
    # sides at once, rather than each side having its own independent handle.
    splitter = editor.canvas_list_splitter
    assert isinstance(splitter, QSplitter)
    assert splitter.orientation() == Qt.Vertical
    assert splitter.count() == 2

    canvas_split, lists_split = splitter.widget(0), splitter.widget(1)
    assert canvas_split is editor.canvas_split
    assert lists_split is editor.lists_split
    assert editor.canvas_l.parentWidget() is canvas_split
    assert editor.canvas_r.parentWidget() is canvas_split
    assert editor.list_l.parentWidget() is lists_split
    assert editor.list_r.parentWidget() is lists_split


def test_dragging_canvas_splitter_also_moves_list_splitter(editor):
    # a "cross" splitter: moving the canvas L/R divider moves the list L/R
    # divider by the same amount, and vice versa (Qt has no native 2D grid
    # splitter, so this is emulated by syncing two independent QSplitters).
    editor.canvas_split.resize(600, 300)
    editor.lists_split.resize(600, 300)
    QApplication.processEvents()

    editor.canvas_split.setSizes([400, 200])
    editor.canvas_split.splitterMoved.emit(400, 1)

    assert editor.lists_split.sizes() == editor.canvas_split.sizes()

    editor.lists_split.setSizes([100, 500])
    editor.lists_split.splitterMoved.emit(100, 1)

    assert editor.canvas_split.sizes() == editor.lists_split.sizes()


def test_fit_applies_same_scale_regardless_of_viewport_size(editor):
    editor.canvas_l.resize(400, 300)
    editor.canvas_r.resize(200, 150)
    QApplication.processEvents()

    editor._fit_views()

    assert editor.canvas_l._scale == pytest.approx(editor.canvas_r._scale)


# --- real mouse-driven ctrl+click multi-select regression (issue #1) ------------ #

def test_ctrl_click_in_list_panel_visually_highlights_all_selected_rows(editor):
    """Regression test: ctrl+clicking a 3rd row used to leave the 2nd row's visual
    highlight toggled back off, even though the app's own selection set was correct.
    Uses real QTest mouse clicks (not row_clicked.emit / scene.select_row) so it
    actually exercises AnimalListPanel's mousePressEvent/mouseReleaseEvent handling."""
    first = editor.scene_l.add_animal(QPointF(10, 10), QPointF(20, 20))
    second = editor.scene_l.add_animal(QPointF(30, 30), QPointF(40, 40))
    third = editor.scene_l.add_animal(QPointF(50, 50), QPointF(60, 60))

    table = editor.list_l.table
    table.resize(300, 200)
    table.show()
    QApplication.processEvents()

    def _click(table_row: int, ctrl: bool) -> None:
        rect = table.visualRect(table.model().index(table_row, 0))
        modifiers = Qt.ControlModifier if ctrl else Qt.NoModifier
        QTest.mouseClick(table.viewport(), Qt.LeftButton, modifiers, rect.center())

    _click(0, False)
    _click(1, True)
    _click(2, True)

    selected_table_rows = {index.row() for index in table.selectionModel().selectedRows()}
    assert selected_table_rows == {0, 1, 2}
    assert editor._selected_rows == {first, second, third}

    table.hide()


def test_clicking_empty_list_panel_area_deselects(editor):
    """Regression: clicking empty space in the list panel visually cleared the
    table's highlight (Qt's native behavior) but never told EditorWidget/the scenes,
    leaving the animal still selected/highlighted on the canvas."""
    row_index = editor.scene_l.add_animal(QPointF(10, 10), QPointF(20, 20))

    table = editor.list_l.table
    table.resize(300, 200)
    table.show()
    QApplication.processEvents()

    rect = table.visualRect(table.model().index(0, 0))
    QTest.mouseClick(table.viewport(), Qt.LeftButton, Qt.NoModifier, rect.center())
    assert editor._selected_rows == {row_index}

    empty_point = table.viewport().rect().bottomLeft() + QPoint(5, -5)
    QTest.mouseClick(table.viewport(), Qt.LeftButton, Qt.NoModifier, empty_point)

    assert editor._selected_rows == set()
    assert table.selectionModel().selectedRows() == []
    assert editor.scene_l._selected_rows == set()

    table.hide()


# --- Matched checkbox / column layout (issue #3) -------------------------------- #

def test_matched_checkbox_is_not_toggleable_by_clicking(editor):
    editor.scene_l.add_animal(QPointF(10, 10), QPointF(20, 20))

    table = editor.list_l.table
    table.resize(300, 200)
    table.show()
    QApplication.processEvents()

    assert table.item(0, 1).checkState() == Qt.Unchecked
    rect = table.visualRect(table.model().index(0, 1))
    QTest.mouseClick(table.viewport(), Qt.LeftButton, Qt.NoModifier, rect.center())

    assert table.item(0, 1).checkState() == Qt.Unchecked
    table.hide()


def test_list_panel_columns_and_status_flags(editor):
    first = editor.scene_l.add_animal(QPointF(10, 10), QPointF(20, 20))
    second = editor.scene_l.add_animal(QPointF(30, 30), QPointF(40, 40))
    editor.store.update_row(second, status="unchecked", manually_corrected=False)
    editor._refresh_animal_lists()

    table = editor.list_l.table
    assert table.columnCount() == 8
    labels = [table.horizontalHeaderItem(i).text() for i in range(8)]
    assert labels == ["ID", "Matched", "Group", "Subgroup", "Species", "Status", "Manually\ncorrected", "Remarks"]
    assert table.item(0, 1).textAlignment() == Qt.AlignCenter
    assert table.item(0, 5).text() == "checked"
    assert table.item(0, 6).checkState() == Qt.Checked
    assert table.item(1, 5).text() == "unchecked"
    assert table.item(1, 6).checkState() == Qt.Unchecked
    assert table.item(0, 7).text() == ""
    assert first != second


def test_list_panel_columns_do_not_stretch_to_fill_width(editor):
    editor.scene_l.add_animal(QPointF(10, 10), QPointF(20, 20))
    table = editor.list_l.table
    header = table.horizontalHeader()
    assert header.stretchLastSection() is False

    table.resize(2000, 200)
    QApplication.processEvents()
    total_column_width = sum(table.columnWidth(col) for col in range(4))
    assert total_column_width < 500


def test_list_panel_columns_match_width_between_sides(editor):
    # left gets a long species name, right stays short — both sides' matching
    # columns must still end up the same width (sized off the wider side).
    editor.scene_l.add_animal(QPointF(10, 10), QPointF(20, 20), group="fish", species="a_very_long_species_name")
    editor.scene_r.add_animal(QPointF(30, 30), QPointF(40, 40))

    for col in range(7):
        assert editor.list_l.table.columnWidth(col) == editor.list_r.table.columnWidth(col)
    assert editor.list_l.table.columnWidth(4) > 0  # species


# --- unidentified group/species defaults (issue #4) ----------------------------- #

def test_selecting_animal_with_blank_group_species_shows_unidentified(editor):
    row_index = editor.scene_l.add_animal(QPointF(10, 10), QPointF(20, 20))

    editor.scene_l.select_row(row_index)

    assert editor.side_panel.combo_group.currentText() == "unidentified"
    assert editor.side_panel.combo_species.currentText() == "unidentified"


# --- toolbar / mode-bar action relocation (issue #5) ---------------------------- #

def test_prev_next_and_zoom_fit_live_in_mode_bar_not_editor_toolbar(editor):
    toolbars = {toolbar.windowTitle(): toolbar for toolbar in editor.findChildren(QToolBar)}
    mode_bar = toolbars["Mode"]
    editor_toolbar = toolbars["Editor"]

    relocated = {"Prev", "Next", "Fit", "Zoom In", "Zoom Out"}
    mode_bar_texts = {action.text() for action in mode_bar.actions()}
    toolbar_texts = {action.text() for action in editor_toolbar.actions()}

    assert relocated <= mode_bar_texts
    assert not (relocated & toolbar_texts)
    # undo/redo stay in the editor toolbar
    assert any(action.text().startswith("Undo") for action in editor_toolbar.actions())
    assert any(action.text().startswith("Redo") for action in editor_toolbar.actions())


def _mode_bar_action(editor, text):
    mode_bar = {t.windowTitle(): t for t in editor.findChildren(QToolBar)}["Mode"]
    return next(a for a in mode_bar.actions() if a.text() == text)


def test_view_buttons_order_and_separator(editor):
    mode_bar = {t.windowTitle(): t for t in editor.findChildren(QToolBar)}["Mode"]
    items = [a.text() for a in mode_bar.actions()]
    start = items.index("L")
    assert items[start:start + 3] == ["L", "R", "L|R"]
    assert items[start + 4:start + 8] == ["Reset View", "Fit", "Zoom In", "Zoom Out"]
    # a visible separator widget sits between the two groups
    assert mode_bar.widgetForAction(mode_bar.actions()[start + 3]).findChild(QFrame) is not None

    editor.resize(1400, 900)
    editor.show()
    QApplication.processEvents()
    widths = {mode_bar.widgetForAction(mode_bar.actions()[start + i]).width() for i in range(3)}
    assert len(widths) == 1
    editor.close()


def test_view_button_highlights_current_view(editor):
    editor.show()
    checked = lambda: [t for t in ("L", "R", "L|R") if _mode_bar_action(editor, t).isChecked()]
    assert checked() == ["L|R"]
    _mode_bar_action(editor, "L").trigger()
    assert checked() == ["L"]
    _mode_bar_action(editor, "R").trigger()
    assert checked() == ["R"]
    _mode_bar_action(editor, "L|R").trigger()
    editor.canvas_split.splitterMoved.emit(10, 1)
    assert checked() == []
    _mode_bar_action(editor, "L").trigger()
    _mode_bar_action(editor, "Reset View").trigger()
    assert checked() == ["L|R"]
    editor.close()


def test_view_buttons_collapse_sides_and_reset(editor):
    editor.resize(1400, 900)
    editor.show()
    QApplication.processEvents()
    default_main = editor.main_split.sizes()
    default_canvas = editor.canvas_split.sizes()

    _mode_bar_action(editor, "L").trigger()
    QApplication.processEvents()
    assert editor.canvas_l.isVisible() and editor.list_l.isVisible()
    assert not editor.canvas_r.isVisible() and not editor.list_r.isVisible()

    _mode_bar_action(editor, "R").trigger()
    QApplication.processEvents()
    assert editor.canvas_r.isVisible() and editor.list_r.isVisible()
    assert not editor.canvas_l.isVisible() and not editor.list_l.isVisible()

    _mode_bar_action(editor, "L|R").trigger()
    QApplication.processEvents()
    left, right = editor.canvas_split.sizes()
    assert editor.canvas_l.isVisible() and editor.canvas_r.isVisible()
    assert abs(left - right) <= 2
    assert editor.lists_split.sizes() == editor.canvas_split.sizes()

    editor.main_split.setSizes([300, 1000])
    editor.canvas_split.setSizes([100, 900])
    _mode_bar_action(editor, "L").trigger()
    _mode_bar_action(editor, "Reset View").trigger()
    QApplication.processEvents()
    assert all(w.isVisible() for w in (editor.canvas_l, editor.canvas_r, editor.list_l, editor.list_r))
    assert editor.main_split.sizes() == default_main
    assert editor.canvas_split.sizes() == default_canvas
    editor.close()


# --- Matched list panel (issue #6) ----------------------------------------------- #

def test_matched_list_shows_matched_pairs(editor):
    left_index = editor.scene_l.add_animal(
        QPointF(10, 10), QPointF(20, 20), group="fish", subgroup="pelagic", species="gadus_morhua",
    )
    right_index = editor.scene_r.add_animal(QPointF(30, 30), QPointF(40, 40))

    assert editor.matched_list.table.rowCount() == 0

    editor._perform_match(left_index, right_index)

    assert editor.matched_list.table.rowCount() == 1
    assert editor.matched_list.table.item(0, 0).data(Qt.UserRole) == left_index
    assert editor.matched_list.table.item(0, 1).text() == "fish"
    assert editor.matched_list.table.item(0, 2).text() == "pelagic"
    assert editor.matched_list.table.item(0, 3).text() == "gadus_morhua"
    assert editor.matched_list.table.item(0, 4).text() == "—"
    assert editor.matched_list.table.item(0, 5).text() == "—"


def test_selecting_in_matched_list_selects_animal_on_both_sides(editor):
    left_index = editor.scene_l.add_animal(QPointF(10, 10), QPointF(20, 20))
    right_index = editor.scene_r.add_animal(QPointF(30, 30), QPointF(40, 40))
    editor._perform_match(left_index, right_index)

    editor.matched_list.table.selectRow(0)

    assert editor._selected_row == left_index
    assert editor.scene_l._selected_rows == {left_index}
    assert editor.scene_r._selected_rows == {left_index}


def test_selecting_unmatched_animal_clears_matched_list_selection(editor):
    left_index = editor.scene_l.add_animal(QPointF(10, 10), QPointF(20, 20))
    right_index = editor.scene_r.add_animal(QPointF(30, 30), QPointF(40, 40))
    editor._perform_match(left_index, right_index)
    other = editor.scene_l.add_animal(QPointF(50, 50), QPointF(60, 60))

    editor.scene_l.select_row(left_index)
    assert editor.matched_list.table.currentRow() == 0

    editor.scene_l.select_row(other)
    assert editor.matched_list.table.currentRow() == -1


# --- lowercase enforcement for group/species (issue #7) ------------------------- #

def test_update_selected_lowercases_group_and_species(editor):
    row_index = editor.scene_l.add_animal(QPointF(10, 10), QPointF(20, 20))
    editor._selected_row = row_index

    editor._update_selected(group="Fish", species="Gadus_Morhua")

    assert editor.store.data.loc[row_index, "group"] == "fish"
    assert editor.store.data.loc[row_index, "species"] == "gadus_morhua"



# --- camera config image size vs. loaded image size ----------------------------- #

@pytest.fixture
def captured_errors(monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    errors = []
    monkeypatch.setattr(QMessageBox, "critical", lambda parent, title, text: errors.append((title, text)))
    return errors


def test_matching_image_size_shows_no_error(qapp, image_pairs, calibration, captured_errors):
    EditorWidget(AnnotationStore(), Taxonomy.load_default(), image_pairs, calibration=calibration)

    assert captured_errors == []


def test_mismatching_image_size_shows_error(qapp, image_pairs, calibration, captured_errors):
    calibration.image_size = (1000, 800)

    EditorWidget(AnnotationStore(), Taxonomy.load_default(), image_pairs, calibration=calibration)

    assert len(captured_errors) == 1
    title, text = captured_errors[0]
    assert title == "Image Size Mismatch"
    assert "4272 x 2848" in text and "1000 x 800" in text


def test_no_size_check_without_calibration(editor, captured_errors):
    editor.load_pair(1)

    assert captured_errors == []


def test_opening_pair_recalculates_length_of_matched_animals(qapp, image_pairs, calibration):
    store = AnnotationStore(user_id="alice")
    file_id = image_pairs[0].file_id
    row = store.build_row(
        file_id, left={"head": (2548.0, 1042.0), "tail": (2552.0, 1138.0)},
        right={"head": (997.0, 990.0), "tail": (1022.0, 1117.0)}, length=999.0, user_id="bob",
    )
    index = store.add_row(row)
    store.data.loc[index, "user_id"] = "bob"

    editor = EditorWidget(store, Taxonomy.load_default(), image_pairs, calibration=calibration)

    assert editor.store.data.loc[index, "length"] == pytest.approx(15.40, rel=0.1)
    assert editor.store.data.loc[index, "user_id"] == "bob"


# --- subgroup ----------------------------------------------------------------- #

SUBGROUP_HIERARCHY = {
    "fish": {
        "rank": "group",
        "children": {
            "benthic": {"rank": "subgroup", "children": ["blenidae_sp"]},
            "pelagic": {"rank": "subgroup", "children": ["gadus_morhua"]},
        },
    },
    "chaetognatha": {"rank": "group"},
}


@pytest.fixture
def subgroup_editor(qapp, image_pairs):
    return EditorWidget(AnnotationStore(), Taxonomy.from_dict(SUBGROUP_HIERARCHY), image_pairs)


def test_bundled_hierarchy_shows_subgroup_ui(editor):
    assert editor.side_panel.taxonomy.has_subgroup_level
    assert not editor.list_l.table.isColumnHidden(3)
    assert not editor.list_r.table.isColumnHidden(3)
    assert not editor.matched_list.table.isColumnHidden(2)


def test_subgroup_ui_hidden_for_hierarchy_without_subgroups(qapp, image_pairs):
    flat = Taxonomy.from_dict({"fish": {"rank": "group", "children": ["gadus_morhua"]}})
    editor = EditorWidget(AnnotationStore(), flat, image_pairs)

    assert editor.list_l.table.isColumnHidden(3)
    assert editor.list_r.table.isColumnHidden(3)
    assert editor.matched_list.table.isColumnHidden(2)


def test_changing_subgroup_and_species_in_side_panel_updates_store(subgroup_editor):
    editor = subgroup_editor
    row_index = editor.scene_l.add_animal(QPointF(10, 10), QPointF(20, 20), group="fish")
    editor.scene_l.select_row(row_index)

    editor.side_panel.combo_subgroup.setCurrentText("pelagic")
    assert editor.store.data.loc[row_index, "subgroup"] == "pelagic"

    editor.side_panel.combo_species.setCurrentText("gadus_morhua")
    assert editor.store.data.loc[row_index, "species"] == "gadus_morhua"
    assert editor.store.data.loc[row_index, "subgroup"] == "pelagic"
    assert editor.list_l.table.item(0, 3).text() == "pelagic"


def test_picking_species_sets_its_subgroup_in_store(subgroup_editor):
    editor = subgroup_editor
    row_index = editor.scene_l.add_animal(QPointF(10, 10), QPointF(20, 20), group="fish")
    editor.scene_l.select_row(row_index)

    editor.side_panel.combo_species.setCurrentText("gadus_morhua")

    assert editor.store.data.loc[row_index, "subgroup"] == "pelagic"
    editor._undo_stack.undo()
    assert editor.store.data.loc[row_index, "subgroup"] == ""
    assert editor.store.data.loc[row_index, "species"] == ""


def test_setting_subgroup_unidentified_resets_species(subgroup_editor):
    editor = subgroup_editor
    row_index = editor.scene_l.add_animal(
        QPointF(10, 10), QPointF(20, 20), group="fish", subgroup="pelagic", species="gadus_morhua",
    )
    editor.scene_l.select_row(row_index)

    editor.side_panel.combo_subgroup.setCurrentText("unidentified")

    assert editor.store.data.loc[row_index, "subgroup"] == "unidentified"
    assert editor.store.data.loc[row_index, "species"] == "unidentified"
    assert editor.side_panel.combo_species.currentText() == "unidentified"
    editor._undo_stack.undo()
    assert editor.store.data.loc[row_index, "subgroup"] == "pelagic"
    assert editor.store.data.loc[row_index, "species"] == "gadus_morhua"


def test_changing_group_resets_subgroup(subgroup_editor):
    editor = subgroup_editor
    row_index = editor.scene_l.add_animal(
        QPointF(10, 10), QPointF(20, 20), group="fish", subgroup="pelagic", species="gadus_morhua",
    )
    editor.scene_l.select_row(row_index)

    editor.side_panel.combo_group.setCurrentText("chaetognatha")

    assert editor.store.data.loc[row_index, "subgroup"] == "unidentified"
    assert not editor.side_panel.combo_subgroup.isEnabled()
    editor._undo_stack.undo()
    assert editor.store.data.loc[row_index, "subgroup"] == "pelagic"
    assert editor.store.data.loc[row_index, "group"] == "fish"


def test_new_animal_starts_unidentified_regardless_of_side_panel(subgroup_editor):
    editor = subgroup_editor
    editor.side_panel.set_enabled(True)
    editor.side_panel.combo_group.setCurrentText("fish")
    editor.side_panel.combo_subgroup.setCurrentText("benthic")

    editor._set_tool(Tool.ADD)
    for point in (QPointF(10, 10), QPointF(20, 20)):
        event = QGraphicsSceneMouseEvent(QEvent.GraphicsSceneMousePress)
        event.setButton(Qt.LeftButton)
        event.setScenePos(point)
        editor.scene_l.mousePressEvent(event)

    row = editor.store.data.iloc[-1]
    assert (row["group"], row["subgroup"], row["species"]) == ("", "", "")
    assert editor.side_panel.combo_group.currentText() == "unidentified"
    assert editor.side_panel.combo_subgroup.currentText() == "unidentified"
    assert editor.side_panel.combo_species.currentText() == "unidentified"


def _click(scene, x, y):
    event = QGraphicsSceneMouseEvent(QEvent.GraphicsSceneMousePress)
    event.setButton(Qt.LeftButton)
    event.setScenePos(QPointF(x, y))
    scene.mousePressEvent(event)


def test_add_mode_with_selected_animal_places_width_keypoints(editor):
    row_index = editor.scene_l.add_animal(QPointF(100, 100), QPointF(300, 100))
    editor.scene_l.select_row(row_index)
    editor._set_tool(Tool.ADD)

    _click(editor.scene_l, 200, 60)
    assert editor.store.data.loc[row_index, "LX3"] < 0  # only pending so far
    _click(editor.scene_l, 200, 140)

    row = editor.store.data.loc[row_index]
    assert (row["LX3"], row["LY3"], row["LX4"], row["LY4"]) == (200, 60, 200, 140)
    assert len(editor.store.data) == 1
    assert editor.scene_l._markers[row_index].width_start is not None
    assert editor._selected_rows == {row_index}

    # the animal now has width keypoints, so the next two clicks add a new animal
    _click(editor.scene_l, 400, 400)
    _click(editor.scene_l, 450, 400)
    assert len(editor.store.data) == 2


def test_width_keypoints_are_undoable(editor):
    row_index = editor.scene_l.add_animal(QPointF(100, 100), QPointF(300, 100))
    editor.scene_l.select_row(row_index)
    editor._set_tool(Tool.ADD)
    _click(editor.scene_l, 200, 60)
    _click(editor.scene_l, 200, 140)

    editor._undo_stack.undo()
    assert editor.store.data.loc[row_index, "LX3"] < 0
    editor._undo_stack.redo()
    assert editor.store.data.loc[row_index, "LX3"] == 200


def test_far_clicks_with_selected_animal_add_a_new_animal(editor):
    row_index = editor.scene_l.add_animal(QPointF(100, 100), QPointF(300, 100))
    editor.scene_l.select_row(row_index)
    editor._set_tool(Tool.ADD)

    _click(editor.scene_l, 200, 900)  # too far for a width point: head of a new animal
    assert editor.scene_l._pending_head is not None and editor.scene_l._pending_width_row is None
    _click(editor.scene_l, 300, 900)

    assert len(editor.store.data) == 2
    assert editor.store.data.loc[row_index, "LX3"] < 0
    new_row = editor.store.data.iloc[-1]
    assert (new_row["LX1"], new_row["LY1"], new_row["LX2"], new_row["LY2"]) == (200, 900, 300, 900)


def test_far_second_width_click_turns_first_point_into_head_of_new_animal(editor):
    row_index = editor.scene_l.add_animal(QPointF(100, 100), QPointF(300, 100))
    editor.scene_l.select_row(row_index)
    editor._set_tool(Tool.ADD)

    _click(editor.scene_l, 200, 60)
    assert editor.scene_l._pending_width_row == row_index
    _click(editor.scene_l, 200, 900)

    assert editor.scene_l._pending_width_row is None and editor.scene_l._pending_head is None
    assert len(editor.store.data) == 2
    assert editor.store.data.loc[row_index, "LX3"] < 0
    new_row = editor.store.data.iloc[-1]
    assert (new_row["LX1"], new_row["LY1"], new_row["LX2"], new_row["LY2"]) == (200, 60, 200, 900)


def test_changing_selection_abandons_pending_width_point(editor):
    first = editor.scene_l.add_animal(QPointF(100, 100), QPointF(300, 100))
    second = editor.scene_l.add_animal(QPointF(100, 500), QPointF(300, 500))
    editor.scene_l.select_row(first)
    editor._set_tool(Tool.ADD)
    _click(editor.scene_l, 200, 60)

    editor.scene_l.select_row(second)
    assert editor.scene_l._pending_width_row is None
    assert editor.scene_l._pending_marker is None


def test_escape_deselects_everything_and_cancels_pending(editor):
    row_index = editor.scene_l.add_animal(QPointF(100, 100), QPointF(300, 100))
    editor.scene_l.select_row(row_index)
    editor._set_tool(Tool.ADD)
    _click(editor.scene_l, 200, 60)

    editor._cancel_pending()

    assert editor._selected_rows == set()
    assert editor.scene_l._selected_rows == set()
    assert editor.scene_l._pending_marker is None
    assert editor.scene_l._pending_width_row is None


def test_escape_shortcut_is_bound_to_deselect(editor):
    row_index = editor.scene_l.add_animal(QPointF(100, 100), QPointF(300, 100))
    editor.scene_l.select_row(row_index)
    action = next(a for a in editor.actions() if a.shortcut().toString() == "Esc")
    action.trigger()
    assert editor._selected_rows == set()


def _animal_with_width(editor):
    row_index = editor.scene_l.add_animal(QPointF(100, 100), QPointF(300, 100))
    editor.scene_l.add_width(row_index, QPointF(200, 60), QPointF(200, 140))
    return row_index


def test_deleting_with_selected_width_keypoint_removes_only_width(editor):
    row_index = _animal_with_width(editor)
    editor.store.update_row(row_index, height=12.0)
    editor._set_tool(Tool.EDIT)

    _click(editor.scene_l, 200, 60)  # the first width keypoint
    assert editor.scene_l.selected_width_row() == row_index
    editor._delete_selected()

    row = editor.store.data.loc[row_index]
    assert (row["LX3"], row["LY3"], row["LX4"], row["LY4"]) == (-1, -1, -1, -1)
    assert row["height"] == -1
    assert (row["LX1"], row["LX2"]) == (100, 300)
    assert editor.scene_l._markers[row_index].width_start is None
    assert editor._selected_rows == {row_index}

    editor._undo_stack.undo()
    assert editor.store.data.loc[row_index, "LX3"] == 200
    assert editor.store.data.loc[row_index, "height"] == 12.0


def test_deleting_with_selected_length_keypoint_removes_whole_animal(editor):
    row_index = _animal_with_width(editor)
    editor._set_tool(Tool.EDIT)

    _click(editor.scene_l, 100, 100)  # the head keypoint
    assert editor.scene_l.selected_width_row() is None
    editor._delete_selected()

    assert row_index not in editor.store.data.index


def test_width_selection_is_dropped_when_selection_changes(editor):
    row_index = _animal_with_width(editor)
    editor._set_tool(Tool.EDIT)
    _click(editor.scene_l, 200, 60)
    assert editor.scene_l.selected_width_row() == row_index

    editor.list_l.animal_selected.emit(row_index, False)  # same animal selected from the list
    assert editor.scene_l.selected_width_row() is None
    editor._delete_selected()
    assert row_index not in editor.store.data.index


def test_removing_width_only_affects_clicked_side(editor):
    row_index = _animal_with_width(editor)
    editor.store.update_row(row_index, RX1=100.0, RY1=100.0, RX2=300.0, RY2=100.0,
                            RX3=200.0, RY3=60.0, RX4=200.0, RY4=140.0)
    editor.scene_l.refresh()
    editor.scene_r.refresh()
    editor._set_tool(Tool.EDIT)

    _click(editor.scene_l, 200, 60)
    editor._delete_selected()

    row = editor.store.data.loc[row_index]
    assert row["LX3"] == -1 and row["RX3"] == 200


def test_remove_tool_on_width_keypoint_removes_only_width(editor):
    row_index = _animal_with_width(editor)
    editor._set_tool(Tool.REMOVE)

    _click(editor.scene_l, 200, 140)  # the second width keypoint
    row = editor.store.data.loc[row_index]
    assert (row["LX3"], row["LX4"]) == (-1, -1)
    assert row["LX1"] == 100

    _click(editor.scene_l, 300, 100)  # the tail keypoint removes the animal
    assert row_index not in editor.store.data.index
