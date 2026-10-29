import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import cv2
import numpy as np
import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QSettings
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QFrame

from maromarker2.models.annotation_store import AnnotationStore
from maromarker2.models.image_set import scan_image_set
from maromarker2.models.taxonomy import Taxonomy
from maromarker2.ui.editor.annotation_scene import AnnotationScene
from maromarker2.ui.editor.editor_widget import EditorWidget
from maromarker2.ui.editor.image_adjustments import (
    ImageAdjustmentPanel, ImageAdjustments, apply_adjustments, array_to_qimage, qimage_to_array,
)


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _isolated_editor_settings(tmp_path, monkeypatch):
    import maromarker2.ui.editor.editor_settings as editor_settings_module
    ini_path = str(tmp_path / "editor_settings.ini")
    monkeypatch.setattr(
        editor_settings_module, "QSettings", lambda *a, **k: QSettings(ini_path, QSettings.IniFormat)
    )


@pytest.fixture
def gradient():
    # odd width so QImage scan-line padding is exercised
    row = np.linspace(40, 200, 7, dtype=np.uint8)
    return np.stack([np.tile(row, (5, 1))] * 3, axis=-1)


def test_identity_returns_unchanged_copy(gradient):
    result = apply_adjustments(gradient, ImageAdjustments())
    assert np.array_equal(result, gradient)
    assert result is not gradient


def test_brightness_is_a_clamped_offset(gradient):
    brighter = apply_adjustments(gradient, ImageAdjustments(brightness=50))
    assert np.array_equal(brighter, np.clip(gradient.astype(int) + 50, 0, 255))


def test_negative_brightness_clamps_to_black_without_color_cast():
    pixel = np.array([[[20, 90, 95]]], np.uint8)
    assert np.array_equal(apply_adjustments(pixel, ImageAdjustments(brightness=-100)), [[[0, 0, 0]]])


def test_contrast_preserves_mean_luminance_and_changes_spread():
    rng = np.random.default_rng(1)
    rgb = rng.integers(70, 130, (32, 32, 3), dtype=np.uint8)  # dark-ish, no clipping at +/-50 contrast

    def luma_mean(image):
        return float(np.mean(image.astype(np.float32) @ np.array([0.299, 0.587, 0.114])))

    for contrast in (50, -50):
        result = apply_adjustments(rgb, ImageAdjustments(contrast=contrast))
        assert abs(luma_mean(result) - luma_mean(rgb)) < 1.0
    assert apply_adjustments(rgb, ImageAdjustments(contrast=50)).std() > rgb.std()
    assert apply_adjustments(rgb, ImageAdjustments(contrast=-50)).std() < rgb.std()


def test_brightness_and_contrast_keep_white_balance_and_clahe(gradient):
    expected = apply_adjustments(gradient, ImageAdjustments(clahe=True, white_balance=True))
    combined = apply_adjustments(gradient, ImageAdjustments(clahe=True, white_balance=True, brightness=10))
    assert np.array_equal(combined, np.clip(expected.astype(int) + 10, 0, 255))


def test_clahe_changes_image_and_keeps_input(gradient):
    original = gradient.copy()
    result = apply_adjustments(gradient, ImageAdjustments(clahe=True))
    assert result.shape == gradient.shape and result.dtype == np.uint8
    assert not np.array_equal(result, gradient)
    assert np.array_equal(gradient, original)


def test_qimage_array_roundtrip(gradient):
    image = array_to_qimage(gradient)
    assert (image.width(), image.height()) == (7, 5)
    assert np.array_equal(qimage_to_array(image), gradient)
    assert qimage_to_array(image.convertToFormat(QImage.Format_Grayscale8)).shape == gradient.shape


def test_panel_emits_and_resets(qapp):
    panel = ImageAdjustmentPanel()
    received = []
    panel.adjustments_changed.connect(received.append)

    assert not panel.button_reset.isEnabled()
    panel.slider_brightness.setValue(30)
    panel.check_clahe.setChecked(True)
    panel.flush()
    assert received == [ImageAdjustments(brightness=30, clahe=True)]
    assert panel.label_brightness.text() == "30"
    assert panel.button_reset.isEnabled()

    panel.reset()
    assert received[-1] == ImageAdjustments()
    assert panel.adjustments().is_identity
    assert not panel.button_reset.isEnabled()


def test_scene_adjusts_display_only(qapp, pytestconfig):
    pair = scan_image_set(pytestconfig.rootpath / "sample_data" / "2016_04")[0]
    scene = AnnotationScene(AnnotationStore(), "L")
    scene.load_image(pair.left)
    size = scene.image_size()
    original = scene._pixmap_item.pixmap().toImage()

    scene.set_adjustments(ImageAdjustments(brightness=60))
    assert scene.image_size() == size
    assert scene._pixmap_item.pixmap().toImage() != original

    scene.set_adjustments(ImageAdjustments())
    assert scene._pixmap_item.pixmap().toImage() == original

    # adjustments stay active when the next image is loaded
    scene.set_adjustments(ImageAdjustments(brightness=60))
    scene.load_image(pair.right)
    assert scene._adjustments.brightness == 60
    assert scene.image_size() is not None


def test_editor_panel_sits_below_side_panel_and_info_panel(qapp, pytestconfig):
    pairs = scan_image_set(pytestconfig.rootpath / "sample_data" / "2016_04")
    editor = EditorWidget(AnnotationStore(), Taxonomy.load_default(), pairs)
    layout = editor.side_panel.parentWidget().layout()
    order = [w for w in (layout.itemAt(i).widget() for i in range(layout.count())) if w.__class__ is not QFrame]
    assert order == [editor.side_panel, editor.info_panel, editor.adjustment_panel]
    # a frame line directly above the adjustment panel
    widgets = [layout.itemAt(i).widget() for i in range(layout.count())]
    assert isinstance(widgets[widgets.index(editor.adjustment_panel) - 1], QFrame)

    editor.adjustment_panel.slider_contrast.setValue(40)
    editor.adjustment_panel.flush()
    assert editor.scene_l._adjustments.contrast == 40
    assert editor.scene_r._adjustments.contrast == 40
    editor.load_pair(1)
    assert editor.scene_l._adjustments.contrast == 40
    editor.adjustment_panel.reset()
    assert editor.scene_l._adjustments.is_identity
    assert editor.scene_r._adjustments.is_identity


def test_hide_markers_toggle(qapp, pytestconfig):
    from PySide6.QtCore import QPointF
    from maromarker2.ui.editor.annotation_scene import Tool

    pairs = scan_image_set(pytestconfig.rootpath / "sample_data" / "2016_04")
    editor = EditorWidget(AnnotationStore(), Taxonomy.load_default(), pairs)
    panel = editor.adjustment_panel
    row = editor.scene_l.add_animal(QPointF(10, 10), QPointF(40, 40))

    def all_items(scene):
        return [item for markers in scene._markers.values() for item in markers.items()]

    assert all(item.isVisible() for item in all_items(editor.scene_l))
    panel.check_hide_markers.setChecked(True)
    assert not editor.scene_l.markers_visible() and not editor.scene_r.markers_visible()
    assert all_items(editor.scene_l) and not any(item.isVisible() for item in all_items(editor.scene_l))

    # markers drawn while hidden (refresh, other pair) stay hidden; the store is untouched
    editor.scene_l.refresh()
    assert not any(item.isVisible() for item in all_items(editor.scene_l))
    assert row in editor.store.data.index

    # Reset also shows the markers again, and re-hiding works afterwards
    assert panel.button_reset.isEnabled()
    panel.reset()
    assert not panel.check_hide_markers.isChecked()
    assert editor.scene_l.markers_visible() and editor.scene_r.markers_visible()
    assert all(item.isVisible() for item in all_items(editor.scene_l))
    panel.check_hide_markers.setChecked(True)

    # canvas clicks don't edit while hidden
    blocked = []
    editor.scene_l.edit_blocked.connect(lambda: blocked.append(True))
    editor._set_tool(Tool.ADD)
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QGraphicsSceneMouseEvent
    from PySide6.QtCore import QEvent
    event = QGraphicsSceneMouseEvent(QEvent.GraphicsSceneMousePress)
    event.setButton(Qt.LeftButton)
    event.setScenePos(QPointF(100, 100))
    editor.scene_l.mousePressEvent(event)
    assert blocked and editor.scene_l._pending_head is None

    panel.check_hide_markers.setChecked(False)
    assert all(item.isVisible() for item in all_items(editor.scene_l))


def test_white_balance_matches_reference_method():
    rng = np.random.default_rng(0)
    rgb = np.clip(rng.normal(120, 40, (16, 16, 3)) * np.array([1.0, 0.8, 0.5]), 0, 255).astype(np.uint8)

    result = apply_adjustments(rgb, ImageAdjustments(white_balance=True))

    lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB)
    l, a, b = cv2.split(lab)
    a = np.clip(a.astype(np.float32) - (np.mean(a.astype(np.float32)) - 128), 0, 255).astype(np.uint8)
    b = np.clip(b.astype(np.float32) - (np.mean(b.astype(np.float32)) - 128), 0, 255).astype(np.uint8)
    expected = cv2.cvtColor(cv2.merge((l, a, b)), cv2.COLOR_LAB2RGB)
    assert np.array_equal(result, expected)

    corrected = cv2.cvtColor(result, cv2.COLOR_RGB2LAB)
    assert abs(corrected[:, :, 1].mean() - 128) < 1.5 and abs(corrected[:, :, 2].mean() - 128) < 1.5
    assert not np.array_equal(result, rgb)


def test_white_balance_combines_with_clahe_and_reset(qapp, gradient):
    both = apply_adjustments(gradient, ImageAdjustments(white_balance=True, clahe=True))
    assert both.shape == gradient.shape and both.dtype == np.uint8

    panel = ImageAdjustmentPanel()
    received = []
    panel.adjustments_changed.connect(received.append)
    panel.check_white_balance.setChecked(True)
    panel.flush()
    assert received[-1] == ImageAdjustments(white_balance=True)
    assert panel.button_reset.isEnabled()
    panel.reset()
    assert received[-1] == ImageAdjustments()
    assert not panel.check_white_balance.isChecked()


def test_reset_button_enabled_for_hidden_markers_only(qapp):
    panel = ImageAdjustmentPanel()
    hidden = []
    panel.markers_hidden_changed.connect(hidden.append)
    assert not panel.button_reset.isEnabled()
    panel.check_hide_markers.setChecked(True)
    assert panel.button_reset.isEnabled()
    panel.button_reset.click()
    assert hidden == [True, False]
    assert not panel.button_reset.isEnabled()
