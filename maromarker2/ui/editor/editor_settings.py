"""Persisted keypoint-editor display preferences (marker/line/bbox size & colors)."""

from __future__ import annotations

from PySide6.QtCore import QSettings
from PySide6.QtGui import QColor

ORG_NAME = "MarOMarker"
APP_NAME = "MarOMarkerV2"

DEFAULT_MARKER_RADIUS = 12.0
DEFAULT_LINE_WIDTH = 5
DEFAULT_BBOX_MARGIN = 20.0
DEFAULT_HEAD_COLOR = "#d2255f"
DEFAULT_TAIL_COLOR = "#f2db0d"
DEFAULT_WIDTH_COLOR = "#ffffff"
DEFAULT_WIDTH_START_COLOR = "#2ecc71"
DEFAULT_WIDTH_END_COLOR = "#e67e22"
DEFAULT_LINE_COLOR = "#000000"
DEFAULT_BBOX_COLOR = "#ffffff"
DEFAULT_LABEL_FONT_SIZE = 40
DEFAULT_LABEL_COLOR = "#000000"
DEFAULT_OPACITY = 0.75

MARKER_SHAPES = ("circle", "square", "plus")
DEFAULT_MARKER_SHAPE = "circle"
DEFAULT_HEAD_SHAPE = "circle"
DEFAULT_TAIL_SHAPE = "plus"
DEFAULT_WIDTH_START_SHAPE = "circle"
DEFAULT_WIDTH_END_SHAPE = "plus"



class EditorSettings:
    """Thin QSettings wrapper for the keypoint editor's marker/line/bbox appearance."""

    def __init__(self):
        self._settings = QSettings(ORG_NAME, APP_NAME)

    @property
    def marker_radius(self) -> float:
        return float(self._settings.value("editor/marker_radius", DEFAULT_MARKER_RADIUS))

    @marker_radius.setter
    def marker_radius(self, value: float) -> None:
        self._settings.setValue("editor/marker_radius", float(value))

    @property
    def line_width(self) -> int:
        return int(self._settings.value("editor/line_width", DEFAULT_LINE_WIDTH))

    @line_width.setter
    def line_width(self, value: int) -> None:
        self._settings.setValue("editor/line_width", int(value))

    @property
    def bbox_margin(self) -> float:
        return float(self._settings.value("editor/bbox_margin", DEFAULT_BBOX_MARGIN))

    @bbox_margin.setter
    def bbox_margin(self, value: float) -> None:
        self._settings.setValue("editor/bbox_margin", float(value))

    @property
    def show_bbox(self) -> bool:
        return self._settings.value("editor/show_bbox", True, type=bool)

    @show_bbox.setter
    def show_bbox(self, value: bool) -> None:
        self._settings.setValue("editor/show_bbox", bool(value))

    @property
    def show_line(self) -> bool:
        return self._settings.value("editor/show_line", True, type=bool)

    @show_line.setter
    def show_line(self, value: bool) -> None:
        self._settings.setValue("editor/show_line", bool(value))

    @property
    def head_color(self) -> QColor:
        return QColor(self._settings.value("editor/head_color", DEFAULT_HEAD_COLOR, type=str))

    @head_color.setter
    def head_color(self, value: QColor) -> None:
        self._settings.setValue("editor/head_color", value.name())

    @property
    def tail_color(self) -> QColor:
        return QColor(self._settings.value("editor/tail_color", DEFAULT_TAIL_COLOR, type=str))

    @tail_color.setter
    def tail_color(self, value: QColor) -> None:
        self._settings.setValue("editor/tail_color", value.name())

    @property
    def width_color(self) -> QColor:
        return QColor(self._settings.value("editor/width_color", DEFAULT_WIDTH_COLOR, type=str))

    @width_color.setter
    def width_color(self, value: QColor) -> None:
        self._settings.setValue("editor/width_color", value.name())

    @property
    def width_start_color(self) -> QColor:
        return QColor(self._settings.value("editor/width_start_color", DEFAULT_WIDTH_START_COLOR, type=str))

    @width_start_color.setter
    def width_start_color(self, value: QColor) -> None:
        self._settings.setValue("editor/width_start_color", value.name())

    @property
    def width_end_color(self) -> QColor:
        return QColor(self._settings.value("editor/width_end_color", DEFAULT_WIDTH_END_COLOR, type=str))

    @width_end_color.setter
    def width_end_color(self, value: QColor) -> None:
        self._settings.setValue("editor/width_end_color", value.name())

    @property
    def line_color(self) -> QColor:
        return QColor(self._settings.value("editor/line_color", DEFAULT_LINE_COLOR, type=str))

    @line_color.setter
    def line_color(self, value: QColor) -> None:
        self._settings.setValue("editor/line_color", value.name())

    @property
    def bbox_color(self) -> QColor:
        return QColor(self._settings.value("editor/bbox_color", DEFAULT_BBOX_COLOR, type=str))

    @bbox_color.setter
    def bbox_color(self, value: QColor) -> None:
        self._settings.setValue("editor/bbox_color", value.name())

    @property
    def label_font_size(self) -> int:
        return int(self._settings.value("editor/label_font_size", DEFAULT_LABEL_FONT_SIZE))

    @label_font_size.setter
    def label_font_size(self, value: int) -> None:
        self._settings.setValue("editor/label_font_size", int(value))

    @property
    def label_color(self) -> QColor:
        return QColor(self._settings.value("editor/label_color", DEFAULT_LABEL_COLOR, type=str))

    @label_color.setter
    def label_color(self, value: QColor) -> None:
        self._settings.setValue("editor/label_color", value.name())

    @property
    def opacity(self) -> float:
        return float(self._settings.value("editor/opacity", DEFAULT_OPACITY))

    @opacity.setter
    def opacity(self, value: float) -> None:
        self._settings.setValue("editor/opacity", float(value))

    @property
    def head_shape(self) -> str:
        return self._shape("editor/head_shape", DEFAULT_HEAD_SHAPE)

    @head_shape.setter
    def head_shape(self, value: str) -> None:
        self._settings.setValue("editor/head_shape", str(value))

    @property
    def tail_shape(self) -> str:
        return self._shape("editor/tail_shape", DEFAULT_TAIL_SHAPE)

    @tail_shape.setter
    def tail_shape(self, value: str) -> None:
        self._settings.setValue("editor/tail_shape", str(value))

    @property
    def width_start_shape(self) -> str:
        return self._shape("editor/width_start_shape", DEFAULT_WIDTH_START_SHAPE)

    @width_start_shape.setter
    def width_start_shape(self, value: str) -> None:
        self._settings.setValue("editor/width_start_shape", str(value))

    @property
    def width_end_shape(self) -> str:
        return self._shape("editor/width_end_shape", DEFAULT_WIDTH_END_SHAPE)

    @width_end_shape.setter
    def width_end_shape(self, value: str) -> None:
        self._settings.setValue("editor/width_end_shape", str(value))

    def _shape(self, key: str, default: str = DEFAULT_MARKER_SHAPE) -> str:
        # Stale values (e.g. the removed "cross"/"x" shapes) fall back to the default.
        value = str(self._settings.value(key, default, type=str))
        return value if value in MARKER_SHAPES else default
