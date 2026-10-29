"""Dialog to edit the keypoint editor's marker size/colors and bbox/line visibility."""

from __future__ import annotations

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFormLayout, QPushButton, QSpinBox,
)

from maromarker2.ui.editor.editor_settings import EditorSettings, MARKER_SHAPES


def _color_button(initial: QColor) -> QPushButton:
    button = QPushButton()
    button.setProperty("color", initial)
    _paint_button(button, initial)
    return button


def _paint_button(button: QPushButton, color: QColor) -> None:
    button.setProperty("color", color)
    button.setStyleSheet(f"background-color: {color.name()};")
    button.setText(color.name())


def _shape_combo(initial: str) -> QComboBox:
    combo = QComboBox()
    combo.addItems(MARKER_SHAPES)
    combo.setCurrentText(initial)
    return combo


class EditorSettingsDialog(QDialog):
    """Edits an ``EditorSettings`` instance in place; call ``exec()`` and check the result."""

    def __init__(self, settings: EditorSettings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Keypoint Editor Appearance")
        self._settings = settings

        self.spin_marker_radius = QDoubleSpinBox()
        self.spin_marker_radius.setRange(2.0, 50.0)
        self.spin_marker_radius.setValue(settings.marker_radius)

        self.spin_line_width = QSpinBox()
        self.spin_line_width.setRange(1, 20)
        self.spin_line_width.setValue(settings.line_width)

        self.spin_bbox_margin = QDoubleSpinBox()
        self.spin_bbox_margin.setRange(0.0, 500.0)
        self.spin_bbox_margin.setValue(settings.bbox_margin)

        self.spin_label_font_size = QSpinBox()
        self.spin_label_font_size.setRange(4, 100)
        self.spin_label_font_size.setValue(settings.label_font_size)

        self.spin_opacity = QDoubleSpinBox()
        self.spin_opacity.setRange(0.1, 1.0)
        self.spin_opacity.setSingleStep(0.05)
        self.spin_opacity.setValue(settings.opacity)

        self.combo_head_shape = _shape_combo(settings.head_shape)
        self.combo_tail_shape = _shape_combo(settings.tail_shape)
        self.combo_width_start_shape = _shape_combo(settings.width_start_shape)
        self.combo_width_end_shape = _shape_combo(settings.width_end_shape)

        self.check_show_line = QCheckBox("Show length/width line")
        self.check_show_line.setChecked(settings.show_line)

        self.check_show_bbox = QCheckBox("Show bounding box")
        self.check_show_bbox.setChecked(settings.show_bbox)

        self.button_head_color = _color_button(settings.head_color)
        self.button_tail_color = _color_button(settings.tail_color)
        self.button_width_start_color = _color_button(settings.width_start_color)
        self.button_width_end_color = _color_button(settings.width_end_color)
        self.button_width_color = _color_button(settings.width_color)
        self.button_line_color = _color_button(settings.line_color)
        self.button_bbox_color = _color_button(settings.bbox_color)
        self.button_label_color = _color_button(settings.label_color)
        for button in (
            self.button_head_color, self.button_tail_color, self.button_width_start_color,
            self.button_width_end_color, self.button_width_color, self.button_line_color, self.button_bbox_color, self.button_label_color,
        ):
            button.clicked.connect(lambda _checked=False, b=button: self._pick_color(b))

        form = QFormLayout(self)
        form.addRow("Marker radius", self.spin_marker_radius)
        form.addRow("Line width", self.spin_line_width)
        form.addRow("Bounding box margin", self.spin_bbox_margin)
        form.addRow("ID label font size", self.spin_label_font_size)
        form.addRow("Marker/bbox opacity", self.spin_opacity)
        form.addRow("Head marker shape", self.combo_head_shape)
        form.addRow("Tail marker shape", self.combo_tail_shape)
        form.addRow("Width start marker shape", self.combo_width_start_shape)
        form.addRow("Width end marker shape", self.combo_width_end_shape)
        form.addRow("", self.check_show_line)
        form.addRow("", self.check_show_bbox)
        form.addRow("Head color", self.button_head_color)
        form.addRow("Tail color", self.button_tail_color)
        form.addRow("Width start color", self.button_width_start_color)
        form.addRow("Width end color", self.button_width_end_color)
        form.addRow("Width line color", self.button_width_color)
        form.addRow("Line color", self.button_line_color)
        form.addRow("Bounding box color", self.button_bbox_color)
        form.addRow("ID label color", self.button_label_color)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _pick_color(self, button: QPushButton) -> None:
        current = button.property("color")
        color = QColorDialog.getColor(current, self, "Choose color")
        if color.isValid():
            _paint_button(button, color)

    def _on_accept(self) -> None:
        self._settings.marker_radius = self.spin_marker_radius.value()
        self._settings.line_width = self.spin_line_width.value()
        self._settings.bbox_margin = self.spin_bbox_margin.value()
        self._settings.label_font_size = self.spin_label_font_size.value()
        self._settings.opacity = self.spin_opacity.value()
        self._settings.head_shape = self.combo_head_shape.currentText()
        self._settings.tail_shape = self.combo_tail_shape.currentText()
        self._settings.width_start_shape = self.combo_width_start_shape.currentText()
        self._settings.width_end_shape = self.combo_width_end_shape.currentText()
        self._settings.show_line = self.check_show_line.isChecked()
        self._settings.show_bbox = self.check_show_bbox.isChecked()
        self._settings.head_color = self.button_head_color.property("color")
        self._settings.tail_color = self.button_tail_color.property("color")
        self._settings.width_start_color = self.button_width_start_color.property("color")
        self._settings.width_end_color = self.button_width_end_color.property("color")
        self._settings.width_color = self.button_width_color.property("color")
        self._settings.line_color = self.button_line_color.property("color")
        self._settings.bbox_color = self.button_bbox_color.property("color")
        self._settings.label_color = self.button_label_color.property("color")
        self.accept()
