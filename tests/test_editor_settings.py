import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QSettings
from PySide6.QtGui import QColor


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def settings(tmp_path, monkeypatch):
    # isolate QSettings so tests never touch the real user's persisted defaults
    import maromarker2.ui.editor.editor_settings as editor_settings_module
    ini_path = str(tmp_path / "editor_settings.ini")
    monkeypatch.setattr(
        editor_settings_module, "QSettings", lambda *a, **k: QSettings(ini_path, QSettings.IniFormat)
    )
    return editor_settings_module.EditorSettings()


def test_defaults_are_reasonable(settings):
    assert settings.marker_radius > 0
    assert settings.line_width >= 1
    assert settings.show_line is True
    assert settings.show_bbox is True


def test_marker_radius_round_trips(settings):
    settings.marker_radius = 18.0
    assert settings.marker_radius == 18.0


def test_show_bbox_and_show_line_round_trip(settings):
    settings.show_bbox = False
    settings.show_line = False
    assert settings.show_bbox is False
    assert settings.show_line is False


def test_color_round_trips(settings):
    settings.head_color = QColor("#123456")
    assert settings.head_color.name() == "#123456"


def test_width_keypoints_have_distinct_default_colors_and_shapes(settings):
    assert settings.width_start_color != settings.width_end_color
    assert settings.width_start_shape != settings.width_end_shape


def test_width_keypoint_colors_and_shapes_round_trip(settings):
    settings.width_start_color = QColor("#112233")
    settings.width_end_color = QColor("#445566")
    settings.width_start_shape = "plus"
    settings.width_end_shape = "circle"
    assert settings.width_start_color.name() == "#112233"
    assert settings.width_end_color.name() == "#445566"
    assert (settings.width_start_shape, settings.width_end_shape) == ("plus", "circle")


def test_invalid_width_shape_falls_back_to_its_default(settings):
    settings._settings.setValue("editor/width_end_shape", "x")
    assert settings.width_end_shape == "plus"


def test_dialog_edits_width_keypoint_appearance(settings, qapp):
    from maromarker2.ui.editor.editor_settings_dialog import EditorSettingsDialog, _paint_button
    dialog = EditorSettingsDialog(settings)
    dialog.combo_width_start_shape.setCurrentText("circle")
    dialog.combo_width_end_shape.setCurrentText("square")
    _paint_button(dialog.button_width_start_color, QColor("#010203"))
    _paint_button(dialog.button_width_end_color, QColor("#040506"))
    dialog._on_accept()
    assert (settings.width_start_shape, settings.width_end_shape) == ("circle", "square")
    assert settings.width_start_color.name() == "#010203"
    assert settings.width_end_color.name() == "#040506"


def test_length_keypoint_default_shapes(settings):
    assert settings.head_shape == "circle"
    assert settings.tail_shape == "plus"
