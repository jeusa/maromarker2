import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QApplication, QGraphicsItem

from maromarker2.models.annotation_store import AnnotationStore
from maromarker2.ui.editor.annotation_scene import AnnotationScene, Tool


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
def store():
    return AnnotationStore()


@pytest.fixture
def scene(qapp, store):
    s = AnnotationScene(store, "L")
    s.set_file("img1")
    return s


def test_add_animal_creates_store_row(scene, store):
    row_index = scene.add_animal(QPointF(10, 20), QPointF(30, 40), group="fish", species="gadus_morhua")

    row = store.data.loc[row_index]
    assert (row["LX1"], row["LY1"], row["LX2"], row["LY2"]) == (10.0, 20.0, 30.0, 40.0)
    assert row["species"] == "gadus_morhua"
    assert scene.has_row(row_index)


def test_moving_marker_updates_store(scene, store):
    row_index = scene.add_animal(QPointF(10, 20), QPointF(30, 40))

    scene._markers[row_index].head.setPos(QPointF(99, 88))

    row = store.data.loc[row_index]
    assert (row["LX1"], row["LY1"]) == (99.0, 88.0)


def _add_predicted_row(store, box=(100.0, 100.0, 200.0, 180.0)):
    row_index = store.add_row(store.build_row(
        "img1", left={"head": (120, 110), "tail": (180, 170)}, status="unchecked", manually_corrected=False,
    ))
    store.set_predicted_box(row_index, "L", box)
    return row_index


def test_predicted_box_is_drawn_instead_of_keypoint_box(scene, store):
    row_index = _add_predicted_row(store)

    scene.refresh()

    rect = scene._markers[row_index].bbox.rect()
    assert (rect.x(), rect.y(), rect.width(), rect.height()) == (100.0, 100.0, 100.0, 80.0)


def test_moving_marker_replaces_predicted_box_and_marks_row_checked(scene, store):
    row_index = _add_predicted_row(store)
    scene.refresh()

    scene._markers[row_index].head.setPos(QPointF(130, 120))

    assert store.predicted_box(row_index, "L") is None
    margin = scene.settings.bbox_margin
    rect = scene._markers[row_index].bbox.rect()
    assert (rect.x(), rect.y()) == (130 - margin, 120 - margin)
    row = store.data.loc[row_index]
    assert row["status"] == "checked"
    assert row["manually_corrected"]


def test_predicted_box_is_only_drawn_on_its_side(store):
    row_index = _add_predicted_row(store)
    right_scene = AnnotationScene(store, "R")
    right_scene.set_file("img1")
    right_scene.refresh()

    assert not right_scene.has_row(row_index)


def test_markers_movable_follows_setting(scene):
    row_index = scene.add_animal(QPointF(10, 20), QPointF(30, 40))
    head = scene._markers[row_index].head
    assert head.flags() & QGraphicsItem.ItemIsMovable

    scene.set_markers_movable(False)
    assert not head.flags() & QGraphicsItem.ItemIsMovable
    scene.refresh()
    assert not scene._markers[row_index].head.flags() & QGraphicsItem.ItemIsMovable

    scene.set_markers_movable(True)
    assert scene._markers[row_index].head.flags() & QGraphicsItem.ItemIsMovable


def test_select_row_emits_signal(scene):
    row_index = scene.add_animal(QPointF(1, 1), QPointF(2, 2))
    received = []
    scene.animal_selected.connect(lambda row_index, additive: received.append((row_index, additive)))

    scene.select_row(row_index)

    assert received == [(row_index, False)]


def test_select_row_additive_toggles_multiselect(scene):
    first = scene.add_animal(QPointF(1, 1), QPointF(2, 2))
    second = scene.add_animal(QPointF(3, 3), QPointF(4, 4))

    scene.select_row(first)
    scene.select_row(second, additive=True)
    assert scene._selected_rows == {first, second}

    scene.select_row(second, additive=True)
    assert scene._selected_rows == {first}


def test_remove_row_deletes_from_store_and_scene(scene, store):
    row_index = scene.add_animal(QPointF(1, 1), QPointF(2, 2))

    scene.remove_row(row_index)

    assert row_index not in store.data.index
    assert not scene.has_row(row_index)


def test_refresh_rebuilds_markers_from_store(store):
    scene = AnnotationScene(store, "L")
    store.add_row(store.build_row("img2", left={"head": (5, 6), "tail": (7, 8)}))

    scene.set_file("img2")

    assert len(scene._markers) == 1


def test_right_side_scene_only_draws_right_points(store):
    row_index = store.add_row(store.build_row("img3", left={"head": (1, 1), "tail": (2, 2)}))

    scene_l = AnnotationScene(store, "L")
    scene_r = AnnotationScene(store, "R")
    scene_l.set_file("img3")
    scene_r.set_file("img3")

    assert scene_l.has_row(row_index)
    assert not scene_r.has_row(row_index)


def test_cancel_pending_add_removes_temp_marker(scene):
    scene.tool = Tool.ADD
    scene._pending_head = QPointF(5, 5)
    scene._pending_marker = scene.addEllipse(0, 0, 1, 1)

    scene.cancel_pending_add()

    assert scene._pending_head is None
    assert scene._pending_marker is None


def test_marker_radius_follows_settings(store):
    from maromarker2.ui.editor.editor_settings import EditorSettings

    settings = EditorSettings()
    settings.marker_radius = 25.0
    scene = AnnotationScene(store, "L", settings)
    scene.set_file("img_radius")

    row_index = scene.add_animal(QPointF(10, 10), QPointF(20, 20))

    rect = scene._markers[row_index].head.rect()
    assert rect.width() == pytest.approx(50.0)


@pytest.mark.parametrize("shape,has_dot", [("circle", True), ("square", True), ("plus", False)])
def test_marker_shapes(store, shape, has_dot, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    from maromarker2.ui.editor.editor_settings import EditorSettings

    settings = EditorSettings()
    settings.head_shape = shape
    scene = AnnotationScene(store, "L", settings)
    scene.set_file("img_shape")

    row_index = scene.add_animal(QPointF(10, 10), QPointF(20, 20))

    head = scene._markers[row_index].head
    assert len(head.childItems()) == (1 if has_dot else 0)
    assert head.path().boundingRect().width() == pytest.approx(2 * settings.marker_radius)
    assert not scene._markers[row_index].tail.childItems()  # tail keeps its own (default plus) shape


def test_invalid_stored_shape_falls_back_to_circle(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    from maromarker2.ui.editor.editor_settings import EditorSettings

    settings = EditorSettings()
    settings._settings.setValue("editor/head_shape", "x")
    assert settings.head_shape == "circle"


def test_bounding_box_drawn_by_default(store):
    scene = AnnotationScene(store, "L")
    scene.set_file("img_bbox")

    row_index = scene.add_animal(QPointF(10, 10), QPointF(20, 20))

    assert scene._markers[row_index].bbox is not None


def test_bounding_box_hidden_when_setting_disabled(store):
    from maromarker2.ui.editor.editor_settings import EditorSettings

    settings = EditorSettings()
    settings.show_bbox = False
    scene = AnnotationScene(store, "L", settings)
    scene.set_file("img_no_bbox")

    row_index = scene.add_animal(QPointF(10, 10), QPointF(20, 20))

    assert scene._markers[row_index].bbox is None


def test_head_tail_line_hidden_when_setting_disabled(store):
    from maromarker2.ui.editor.editor_settings import EditorSettings

    settings = EditorSettings()
    settings.show_line = False
    scene = AnnotationScene(store, "L", settings)
    scene.set_file("img_no_line")

    row_index = scene.add_animal(QPointF(10, 10), QPointF(20, 20))

    assert scene._markers[row_index].line is None


def test_refresh_does_not_restore_selection_of_deleted_row(store):
    scene = AnnotationScene(store, "L")
    scene.set_file("img_stale_selection")
    row_index = scene.add_animal(QPointF(10, 10), QPointF(20, 20))
    scene.select_row(row_index)

    store.remove_row(row_index)
    received = []
    scene.animal_selected.connect(received.append)
    scene.refresh()

    assert received == []
    assert scene._selected_row is None


def test_no_dimming_when_nothing_selected(scene):
    row_index = scene.add_animal(QPointF(1, 1), QPointF(2, 2))

    base_opacity = scene.settings.opacity
    assert all(item.opacity() == pytest.approx(base_opacity) for item in scene._markers[row_index].items())


def test_non_selected_animals_are_dimmed_when_one_is_selected(scene):
    from maromarker2.ui.editor.annotation_scene import UNSELECTED_OPACITY

    first = scene.add_animal(QPointF(1, 1), QPointF(2, 2))
    second = scene.add_animal(QPointF(10, 10), QPointF(20, 20))

    scene.select_row(first)

    base_opacity = scene.settings.opacity
    assert all(item.opacity() == pytest.approx(base_opacity) for item in scene._markers[first].items())
    assert all(
        item.opacity() == pytest.approx(base_opacity * UNSELECTED_OPACITY)
        for item in scene._markers[second].items()
    )


def test_deselecting_restores_full_opacity(scene):
    first = scene.add_animal(QPointF(1, 1), QPointF(2, 2))
    second = scene.add_animal(QPointF(10, 10), QPointF(20, 20))
    scene.select_row(first)

    scene.select_row(None)

    base_opacity = scene.settings.opacity
    assert all(item.opacity() == pytest.approx(base_opacity) for item in scene._markers[first].items())
    assert all(item.opacity() == pytest.approx(base_opacity) for item in scene._markers[second].items())


def test_bbox_is_thicker_when_its_animal_is_selected(scene):
    from maromarker2.ui.editor.annotation_scene import BBOX_SELECTED_EXTRA_WIDTH

    row_index = scene.add_animal(QPointF(10, 10), QPointF(20, 20))
    bbox = scene._markers[row_index].bbox
    line_width = scene.settings.line_width
    assert bbox.pen().width() == line_width

    scene.select_row(row_index)
    assert bbox.pen().width() == line_width + BBOX_SELECTED_EXTRA_WIDTH

    scene.select_row(None)
    assert bbox.pen().width() == line_width


def test_bbox_pen_width_follows_line_width_setting(store):
    from maromarker2.ui.editor.editor_settings import EditorSettings
    settings = EditorSettings()
    settings.line_width = 7
    scene = AnnotationScene(store, "L", settings)
    scene.set_file("img1")
    row_index = scene.add_animal(QPointF(10, 10), QPointF(20, 20))
    assert scene._markers[row_index].bbox.pen().width() == 7
    assert scene._markers[row_index].line.pen().width() == 7


def test_hit_area_created_in_place_of_hidden_bbox(store):
    from maromarker2.ui.editor.editor_settings import EditorSettings

    settings = EditorSettings()
    settings.show_bbox = False
    scene = AnnotationScene(store, "L", settings)
    scene.set_file("img_hit_area")

    row_index = scene.add_animal(QPointF(10, 10), QPointF(20, 20))

    markers = scene._markers[row_index]
    assert markers.bbox is None
    assert markers.hit_area is not None


def test_margin_around_points_stays_clickable_when_bbox_and_line_hidden(store):
    from PySide6.QtGui import QTransform
    from PySide6.QtWidgets import QGraphicsView
    from maromarker2.ui.editor.editor_settings import EditorSettings

    settings = EditorSettings()
    settings.show_bbox = False
    settings.show_line = False
    scene = AnnotationScene(store, "L", settings)
    scene.set_file("img_hit_area_click")
    QGraphicsView(scene)  # gives scene.views() a view so itemAt() hit-testing works

    row_index = scene.add_animal(QPointF(10, 10), QPointF(100, 10))
    markers = scene._markers[row_index]
    assert markers.bbox is None
    assert markers.line is None

    # A point roughly midway between head and tail — not on either marker dot and
    # not exactly on the (hidden) head-tail line — should still hit the invisible
    # margin area and resolve to this animal's row.
    item = scene.itemAt(QPointF(55, 10), QTransform())
    assert item is markers.hit_area


def test_drawing_does_not_stamp_user_but_dragging_does(qapp):
    store = AnnotationStore(user_id="alice")
    store.add_row(store.build_row("img1", left={"head": (10, 20), "tail": (30, 40)}))
    store.data["user_id"] = "bob"
    scene = AnnotationScene(store, "L")
    scene.set_file("img1")
    assert store.data.loc[0, "user_id"] == "bob"

    scene._markers[0].head.setPos(QPointF(50, 60))
    assert store.data.loc[0, "user_id"] == "alice"


def test_width_keypoints_use_their_own_color_and_shape(store):
    from PySide6.QtGui import QColor
    from maromarker2.ui.editor.editor_settings import EditorSettings
    settings = EditorSettings()
    settings.width_start_color = QColor("#112233")
    settings.width_end_color = QColor("#445566")
    settings.width_start_shape = "square"
    settings.width_end_shape = "plus"
    scene = AnnotationScene(store, "L", settings)
    scene.set_file("img1")
    row_index = scene.add_animal(QPointF(10, 10), QPointF(110, 10))
    scene.add_width(row_index, QPointF(60, 5), QPointF(60, 20))

    markers = scene._markers[row_index]
    assert markers.width_start.brush().color().name() == "#112233"
    assert markers.width_end.brush().color().name() == "#445566"
    assert markers.width_start.path() != markers.width_end.path()
