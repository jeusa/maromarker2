import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QPointF, Qt
from PySide6.QtWidgets import QApplication

from maromarker2.models.annotation_store import AnnotationStore
from maromarker2.models.image_set import scan_image_set
from maromarker2.models.taxonomy import Taxonomy
from maromarker2.ui.editor.editor_widget import EditorWidget
from maromarker2.ui.editor.remark_edit import RemarkEdit


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _isolated_editor_settings(tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings
    import maromarker2.ui.editor.editor_settings as editor_settings_module
    ini_path = str(tmp_path / "editor_settings.ini")
    monkeypatch.setattr(
        editor_settings_module, "QSettings", lambda *a, **k: QSettings(ini_path, QSettings.IniFormat)
    )


@pytest.fixture
def editor(qapp, pytestconfig):
    pairs = scan_image_set(pytestconfig.rootpath / "sample_data" / "2016_04")
    return EditorWidget(AnnotationStore(), Taxonomy.load_default(), pairs)


def _table_text(editor, row, column):
    return editor.image_list.table.item(row, column).text()


def _checked(editor, row):
    return editor.image_list.table.item(row, 2).checkState() == Qt.Checked


def test_remark_edit_picks_options_and_accepts_free_text(qapp):
    edit = RemarkEdit()
    edit.set_options(["blurry", "dark"])
    edited, finished = [], []
    edit.textEdited.connect(edited.append)
    edit.editingFinished.connect(lambda: finished.append(edit.text()))

    assert edit.options() == ["blurry", "dark"]
    assert edit.text() == ""

    edit.textActivated.emit("dark")
    assert edit.text() == "dark" and edited == ["dark"] and finished == ["dark"]

    edit.setText("free text")
    edit.set_options(["other"])  # replacing the options keeps what is typed
    assert edit.text() == "free text" and edit.options() == ["other"]


def test_image_list_shows_every_pair(editor):
    table = editor.image_list.table
    assert table.rowCount() == len(editor.image_pairs)
    assert [_table_text(editor, row, 0) for row in range(table.rowCount())] == [
        pair.file_id for pair in editor.image_pairs
    ]
    assert editor.image_list.current_index() == 0


def test_clicking_an_image_pair_navigates_to_it(editor):
    editor.image_list.table.selectRow(2)

    assert editor._pair_index == 2
    assert editor.label_pair.text().startswith(editor.image_pairs[2].file_id)
    assert editor.image_list.current_index() == 2


def test_navigating_elsewhere_moves_the_list_selection(editor):
    editor.load_pair(3)
    assert editor.image_list.current_index() == 3


def test_checked_column_requires_all_animals_checked(editor):
    assert not _checked(editor, 0)  # no animals

    first = editor.scene_l.add_animal(QPointF(10, 10), QPointF(20, 20))
    assert _checked(editor, 0)

    second = editor.scene_l.add_animal(QPointF(30, 30), QPointF(40, 40))
    editor.store.update_row(second, status="unchecked")
    editor._refresh_animal_lists()
    assert not _checked(editor, 0)

    editor.store.update_row(second, status="checked")
    editor._refresh_animal_lists()
    assert _checked(editor, 0)
    assert first in editor.store.data.index


def test_clicking_checked_box_checks_all_animals_of_that_pair(editor):
    editor.scene_l.add_animal(QPointF(10, 10), QPointF(20, 20))
    second = editor.scene_l.add_animal(QPointF(30, 30), QPointF(40, 40))
    editor.store.update_row(second, status="unchecked")
    editor._refresh_animal_lists()
    assert not _checked(editor, 0)

    editor.image_list.table.item(0, 2).setCheckState(Qt.Checked)

    assert set(editor.store.data["status"]) == {"checked"}
    assert _checked(editor, 0)


def test_unchecking_only_affects_animals_not_manually_corrected(editor):
    first = editor.scene_l.add_animal(QPointF(10, 10), QPointF(20, 20))
    second = editor.scene_l.add_animal(QPointF(30, 30), QPointF(40, 40))
    editor.store.update_row(first, manually_corrected=True)
    editor.store.update_row(second, manually_corrected=False)
    editor._refresh_animal_lists()
    assert _checked(editor, 0)

    editor.image_list.table.item(0, 2).setCheckState(Qt.Unchecked)

    assert editor.store.data.loc[first, "status"] == "checked"
    assert editor.store.data.loc[second, "status"] == "unchecked"
    assert not _checked(editor, 0)


def test_toggling_checked_box_of_another_pair_leaves_the_current_one_alone(editor):
    editor.scene_l.add_animal(QPointF(10, 10), QPointF(20, 20))
    editor.load_pair(1)
    row = editor.scene_l.add_animal(QPointF(10, 10), QPointF(20, 20))
    editor.load_pair(0)  # leaving a pair marks it checked, so unset it afterwards
    editor.store.update_row(row, status="unchecked", manually_corrected=False)
    editor.image_list.refresh_pair(editor.store, 1)
    editor.image_list.table.item(1, 2).setCheckState(Qt.Checked)

    assert editor.store.data.loc[row, "status"] == "checked"
    assert editor._pair_index == 0
    assert _checked(editor, 0) and _checked(editor, 1)


def test_checked_box_is_ignored_while_editing_is_locked(editor):
    row = editor.scene_l.add_animal(QPointF(10, 10), QPointF(20, 20))
    editor.store.update_row(row, status="unchecked")
    editor._refresh_animal_lists()
    editor._editing_locked = True

    editor.image_list.table.item(0, 2).setCheckState(Qt.Checked)

    assert editor.store.data.loc[row, "status"] == "unchecked"
    assert not _checked(editor, 0)


def test_checked_box_toggle_is_undoable_and_redoable(editor):
    first = editor.scene_l.add_animal(QPointF(10, 10), QPointF(20, 20))
    editor.store.update_row(first, status="unchecked", manually_corrected=False)
    editor._refresh_animal_lists()

    editor.image_list.table.item(0, 2).setCheckState(Qt.Checked)
    assert editor.store.data.loc[first, "status"] == "checked"

    editor._undo()
    assert editor.store.data.loc[first, "status"] == "unchecked"
    assert not _checked(editor, 0)

    editor._redo()
    assert editor.store.data.loc[first, "status"] == "checked"
    assert _checked(editor, 0)


def test_checked_box_toggle_without_changes_adds_no_undo_step(editor):
    editor.image_list.table.item(0, 2).setCheckState(Qt.Checked)  # no animals

    assert not editor._undo_stack.canUndo()


def test_checked_box_toggle_of_another_pair_is_undoable(editor):
    editor.load_pair(1)
    row = editor.scene_l.add_animal(QPointF(10, 10), QPointF(20, 20))
    editor.load_pair(0)  # leaving a pair marks it checked, so unset it afterwards
    editor.store.update_row(row, status="unchecked", manually_corrected=False)
    editor.image_list.refresh_pair(editor.store, 1)
    editor.image_list.table.item(1, 2).setCheckState(Qt.Checked)

    editor._undo()

    assert editor.store.data.loc[row, "status"] == "unchecked"
    assert not _checked(editor, 1)
    assert editor._pair_index == 0

    editor._redo()

    assert editor.store.data.loc[row, "status"] == "checked"
    assert _checked(editor, 1)
    assert editor._pair_index == 0


def test_image_remark_edit_updates_store_list_and_is_undoable(editor):
    editor.scene_l.add_animal(QPointF(10, 10), QPointF(20, 20))
    editor.scene_l.add_animal(QPointF(30, 30), QPointF(40, 40))

    editor.image_list.edit_remark.setText("blurry")
    editor.image_list.edit_remark.editingFinished.emit()

    assert set(editor.store.data["image_remarks"]) == {"blurry"}
    assert _table_text(editor, 0, 3) == "blurry"

    editor._undo()
    assert set(editor.store.data["image_remarks"]) == {""}
    assert _table_text(editor, 0, 3) == ""
    assert editor.image_list.edit_remark.text() == ""

    editor._redo()
    assert _table_text(editor, 0, 3) == "blurry"
    assert editor.image_list.edit_remark.text() == "blurry"


def test_image_remark_applies_to_the_current_pair_and_shows_when_returning(editor):
    editor.scene_l.add_animal(QPointF(10, 10), QPointF(20, 20))
    editor.image_list.edit_remark.setText("first")
    editor.image_list.edit_remark.editingFinished.emit()

    editor.load_pair(1)
    assert editor.image_list.edit_remark.text() == ""
    assert _table_text(editor, 0, 3) == "first"

    editor.load_pair(0)
    assert editor.image_list.edit_remark.text() == "first"


def test_unconfirmed_image_remark_is_kept_when_navigating_away(editor):
    editor.scene_l.add_animal(QPointF(10, 10), QPointF(20, 20))
    editor.image_list.edit_remark.setText("typed only")  # no Enter / focus change

    editor.load_pair(1)

    assert editor.store.data["image_remarks"].tolist() == ["typed only"]
    assert _table_text(editor, 0, 3) == "typed only"


def test_image_remark_of_a_pair_without_animals_is_shown_and_not_lost(editor):
    editor.image_list.edit_remark.setText("nothing here")
    editor.image_list.edit_remark.editingFinished.emit()

    assert _table_text(editor, 0, 3) == "nothing here"
    row = editor.scene_l.add_animal(QPointF(10, 10), QPointF(20, 20))
    assert editor.store.data.loc[row, "image_remarks"] == "nothing here"


def test_remark_options_reach_both_remark_fields(editor):
    editor.set_remark_options(["alive", "damaged"], ["blurry", "dark", "bubbles"])

    assert editor.side_panel.edit_remark.options() == ["alive", "damaged"]
    assert editor.image_list.edit_remark.options() == ["blurry", "dark", "bubbles"]


def test_picking_an_object_remark_option_updates_selected_animal(editor):
    editor.set_remark_options(["alive", "damaged"], [])
    row = editor.scene_l.add_animal(QPointF(10, 10), QPointF(20, 20))
    editor.scene_l.select_row(row)

    editor.side_panel.edit_remark.textActivated.emit("damaged")

    assert editor.store.data.loc[row, "object_remarks"] == "damaged"


def test_picking_an_image_remark_option_updates_the_pair(editor):
    editor.set_remark_options([], ["blurry"])
    editor.scene_l.add_animal(QPointF(10, 10), QPointF(20, 20))

    editor.image_list.edit_remark.textActivated.emit("blurry")

    assert editor.store.image_remark(editor._current_file_id()) == "blurry"


def test_animals_column_counts_annotated_animals_per_pair(editor):
    assert _table_text(editor, 0, 1) == "0"

    editor.scene_l.add_animal(QPointF(10, 10), QPointF(20, 20))
    right = editor.scene_r.add_animal(QPointF(30, 30), QPointF(40, 40))
    assert _table_text(editor, 0, 1) == "2"

    editor.scene_l.select_row(right)
    editor._delete_selected()
    assert _table_text(editor, 0, 1) == "1"
    assert _table_text(editor, 1, 1) == "0"


def test_remark_field_sits_directly_above_the_image_list(editor):
    layout = editor.image_list.layout()
    widgets = [layout.itemAt(i).widget() for i in range(layout.count())]

    assert widgets.index(editor.image_list.edit_remark) + 1 == widgets.index(editor.image_list.table)

