"""QGraphicsScene for one image side (L or R): draws and edits animal head/tail
(and optional width) markers, backed directly by an ``AnnotationStore``.
"""

from __future__ import annotations

from enum import Enum, auto
from typing import Callable

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QPainterPath, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (
    QGraphicsEllipseItem, QGraphicsItem, QGraphicsLineItem, QGraphicsPathItem,
    QGraphicsPixmapItem, QGraphicsRectItem, QGraphicsScene, QGraphicsSimpleTextItem,
)

from maromarker2.models.annotation_store import NO_VALUE, AnnotationStore
from maromarker2.ui.editor.color_hints import (
    CLASS_FIELDS, UNMATCHED_LINE_COLOR, UNSET_CLASS_LINE_COLOR, ColorHint, all_classes_unidentified, is_matched,
)
from maromarker2.ui.editor.editor_settings import EditorSettings
from maromarker2.ui.editor.image_adjustments import (
    ImageAdjustments, apply_adjustments, array_to_qimage, qimage_to_array,
)

SELECTED_PEN_WIDTH = 3
UNSELECTED_OPACITY = 0.35
BBOX_SELECTED_EXTRA_WIDTH = 2  # added to the line width while the animal is selected
LABEL_MARGIN = 8
CENTER_DOT_RADIUS = 2.5
# width keypoints must lie within this multiple of the animal's length from its head-tail line
MAX_WIDTH_DISTANCE_FACTOR = 1.

_POINT_SUFFIXES = {
    "head": ("X1", "Y1"), "tail": ("X2", "Y2"),
    "width_start": ("X3", "Y3"), "width_end": ("X4", "Y4"),
}

_SIDE_TO_BUILD_KEY = {"L": "left", "R": "right"}


class Tool(Enum):
    EDIT = auto()
    ADD = auto()
    REMOVE = auto()


def _marker_path(shape: str, radius: float) -> QPainterPath:
    """Outline of a marker shape; every shape fits the same radius-sized bounding square."""
    path = QPainterPath()
    if shape == "square":
        path.addRect(QRectF(-radius, -radius, 2 * radius, 2 * radius))
    elif shape == "plus":
        t = max(1.5, radius / 6)  # half the arm thickness
        points = [
            (-t, -radius), (t, -radius), (t, -t), (radius, -t), (radius, t), (t, t),
            (t, radius), (-t, radius), (-t, t), (-radius, t), (-radius, -t), (-t, -t),
        ]
        path.addPolygon(QPolygonF([QPointF(x, y) for x, y in points]))
        path.closeSubpath()
    else:
        path.addEllipse(QRectF(-radius, -radius, 2 * radius, 2 * radius))
    return path


class _MarkerItem(QGraphicsPathItem):
    """A draggable point marker that reports its new scene position back to the scene.

    Built from stock Qt path/ellipse items only (no custom painting). Circle and square
    markers get a fixed-size black center dot as a child item.
    """

    def __init__(
        self, color: QColor, radius: float, on_moved: Callable[[QPointF], None], shape: str = "circle",
    ):
        super().__init__(_marker_path(shape, radius))
        self._radius = radius
        self.setBrush(QBrush(color))
        self.setPen(QPen(Qt.black, 1))
        if shape in ("circle", "square"):
            dot = QGraphicsEllipseItem(
                -CENTER_DOT_RADIUS, -CENTER_DOT_RADIUS, 2 * CENTER_DOT_RADIUS, 2 * CENTER_DOT_RADIUS, self,
            )
            dot.setBrush(QBrush(Qt.black))
            dot.setPen(QPen(Qt.NoPen))
            dot.setAcceptedMouseButtons(Qt.NoButton)
        self.setFlags(
            QGraphicsItem.ItemIsMovable
            | QGraphicsItem.ItemIsSelectable
            | QGraphicsItem.ItemSendsGeometryChanges
        )
        self.setZValue(2)
        self._on_moved = on_moved

    def set_movable(self, movable: bool) -> None:
        self.setFlag(QGraphicsItem.ItemIsMovable, movable)

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionHasChanged:
            self._on_moved(self.pos())
        return super().itemChange(change, value)

    def rect(self) -> QRectF:
        """The shape-agnostic bounding square, sized by radius."""
        r = self._radius
        return QRectF(-r, -r, 2 * r, 2 * r)

    def set_highlighted(self, highlighted: bool) -> None:
        width = SELECTED_PEN_WIDTH if highlighted else 1
        pen = self.pen()
        pen.setWidth(width)
        pen.setColor(Qt.white if highlighted else Qt.black)
        self.setPen(pen)


class _AnimalMarkers:
    """The visual items representing one animal's points on one side."""

    def __init__(self):
        self.head: _MarkerItem | None = None
        self.tail: _MarkerItem | None = None
        self.width_start: _MarkerItem | None = None
        self.width_end: _MarkerItem | None = None
        self.line: QGraphicsLineItem | None = None
        self.width_line: QGraphicsLineItem | None = None
        self.label: QGraphicsSimpleTextItem | None = None
        self.bbox: QGraphicsRectItem | None = None
        self.hit_area: QGraphicsRectItem | None = None

    def items(self) -> list:
        return [i for i in (
            self.head, self.tail, self.width_start, self.width_end,
            self.line, self.width_line, self.label, self.bbox, self.hit_area,
        ) if i is not None]

    def markers(self) -> list[_MarkerItem]:
        return [m for m in (self.head, self.tail, self.width_start, self.width_end) if m is not None]


class AnnotationScene(QGraphicsScene):
    """Displays one image (L or R) and the matching-side points of each animal for one file_id."""

    animal_selected = Signal(object, bool)  # row_index: int | None, additive: bool
    animal_added = Signal(int)
    animal_removed = Signal(int)
    animal_moved = Signal(int)
    store_changed = Signal(object, str)  # (before rows of the current file, description) for undo recording
    edit_blocked = Signal()  # a canvas click was ignored because the markers are hidden

    def __init__(
        self, store: AnnotationStore, side: str, settings: EditorSettings | None = None, parent=None,
        class_fields: tuple[str, ...] = CLASS_FIELDS,
    ):
        super().__init__(parent)
        if side not in ("L", "R"):
            raise ValueError(f"side must be 'L' or 'R', got {side!r}")
        self.store = store
        self.side = side
        self.settings = settings if settings is not None else EditorSettings()
        self.class_fields = class_fields
        self._color_hint: ColorHint | None = None
        self.file_id: str | None = None
        self.tool = Tool.EDIT
        self._markers_movable = True
        self._markers_visible = True
        self._drawing = False

        self._pixmap_item: QGraphicsPixmapItem | None = None
        self._original_pixmap: QPixmap | None = None
        self._original_array: np.ndarray | None = None  # RGB copy, converted lazily for adjustments
        self._adjustments = ImageAdjustments()
        self._markers: dict[int, _AnimalMarkers] = {}
        self._selected_row: int | None = None
        self._selected_rows: set[int] = set()
        self._pending_head: QPointF | None = None
        self._pending_marker: QGraphicsItem | None = None
        self._pending_width_row: int | None = None  # animal whose first width point is placed
        self._pending_width_start: QPointF | None = None
        # the animal whose width keypoint was explicitly clicked (and is the only selection)
        self._selected_width_row: int | None = None
        self._drag_before_snapshot = None

    # --- image / data loading --------------------------------------------- #
    def reset_scene(self) -> None:
        """Removes every item this scene tracks.

        Deliberately avoids QGraphicsScene.clear(): that deletes the C++ items without
        PySide knowing, leaving dangling Python wrappers (markers, pixmap item, pending
        marker) whose later deallocation segfaults. removeItem() hands ownership back to
        Python so the wrappers are destroyed safely.
        """
        for markers in self._markers.values():
            for item in markers.items():
                self.removeItem(item)
        self._markers.clear()
        if self._pending_marker is not None:
            self.removeItem(self._pending_marker)
        if self._pixmap_item is not None:
            self.removeItem(self._pixmap_item)
        self._pixmap_item = None
        self._original_pixmap = None
        self._original_array = None
        self._selected_row = None
        self._selected_rows = set()
        self._pending_head = None
        self._pending_marker = None
        self._pending_width_row = None
        self._pending_width_start = None
        self._selected_width_row = None

    def load_image(self, image_path: str) -> None:
        pixmap = QPixmap(str(image_path))
        self.reset_scene()
        self._original_pixmap = pixmap
        self._original_array = None
        self._pixmap_item = self.addPixmap(self._adjusted_pixmap())
        self._pixmap_item.setZValue(-1)
        self.setSceneRect(QRectF(pixmap.rect()))

    def set_adjustments(self, adjustments: ImageAdjustments) -> None:
        """Changes the display-only brightness/contrast/CLAHE; the original image and
        all marker coordinates are unaffected."""
        if adjustments == self._adjustments:
            return
        self._adjustments = adjustments
        if self._pixmap_item is not None:
            self._pixmap_item.setPixmap(self._adjusted_pixmap())

    def _adjusted_pixmap(self) -> QPixmap:
        if self._adjustments.is_identity or self._original_pixmap is None or self._original_pixmap.isNull():
            return self._original_pixmap
        if self._original_array is None:
            self._original_array = qimage_to_array(self._original_pixmap.toImage())
        return QPixmap.fromImage(array_to_qimage(apply_adjustments(self._original_array, self._adjustments)))

    def image_size(self) -> tuple[int, int] | None:
        """(width, height) of the loaded image in pixels, or None if none loaded."""
        if self._pixmap_item is None or self._pixmap_item.pixmap().isNull():
            return None
        pixmap = self._pixmap_item.pixmap()
        return pixmap.width(), pixmap.height()

    def set_file(self, file_id: str) -> None:
        self.file_id = file_id
        self.refresh()

    def refresh(self) -> None:
        """Redraws all animal markers for the current file_id from the store."""
        for markers in self._markers.values():
            for item in markers.items():
                self.removeItem(item)
        self._markers.clear()
        selected_rows = self._selected_rows
        self._selected_row = None
        self._selected_rows = set()

        if self.file_id is not None:
            for row_index, row in self.store.rows_for_file(self.file_id).iterrows():
                self._draw_row(row_index, row)

        # only restore rows that still exist after the rebuild (e.g. some may have been
        # deleted/merged by the mutation that triggered this refresh); applied silently,
        # without re-emitting animal_selected.
        still_valid = {row_index for row_index in selected_rows if row_index in self._markers}
        if still_valid:
            self.set_selected_rows(still_valid)
        self._drop_stale_pending_width()

    def set_color_hint(self, hint: ColorHint | None) -> None:
        """Chooses which unset data (if any) is flagged with bright bbox/line colors."""
        if hint == self._color_hint:
            return
        self._color_hint = hint
        self.refresh()

    def _hint_color(self, row) -> QColor | None:
        if self._color_hint == ColorHint.CLASS and all_classes_unidentified(row, self.class_fields):
            return UNSET_CLASS_LINE_COLOR
        if self._color_hint == ColorHint.MATCH and not is_matched(row):
            return UNMATCHED_LINE_COLOR
        return None

    def _point(self, row, name: str) -> QPointF | None:
        suffix_x, suffix_y = _POINT_SUFFIXES[name]
        x, y = row[f"{self.side}{suffix_x}"], row[f"{self.side}{suffix_y}"]
        if x is None or y is None or x <= 0 or y <= 0:
            return None
        return QPointF(float(x), float(y))

    def _draw_row(self, row_index: int, row) -> None:
        head = self._point(row, "head")
        tail = self._point(row, "tail")
        if head is None and tail is None:
            return

        settings = self.settings
        hint_color = self._hint_color(row)
        markers = _AnimalMarkers()
        if head is not None:
            markers.head = self._make_marker(row_index, "head", settings.head_color, head, settings.head_shape)
        if tail is not None:
            markers.tail = self._make_marker(row_index, "tail", settings.tail_color, tail, settings.tail_shape)
        if head is not None and tail is not None and settings.show_line:
            markers.line = self.addLine(
                head.x(), head.y(), tail.x(), tail.y(), QPen(hint_color or settings.line_color, settings.line_width)
            )
            markers.line.setZValue(1)

        label_anchor = self._lower_point(head, tail)
        markers.label = self.addSimpleText(f"#{row_index}")
        font = markers.label.font()
        font.setPointSize(settings.label_font_size)
        markers.label.setFont(font)
        markers.label.setBrush(QBrush(settings.label_color))
        markers.label.setPos(label_anchor.x() + LABEL_MARGIN, label_anchor.y() + LABEL_MARGIN)
        markers.label.setZValue(2)

        width_start = self._point(row, "width_start")
        width_end = self._point(row, "width_end")
        if width_start is not None:
            markers.width_start = self._make_marker(
                row_index, "width_start", settings.width_start_color, width_start, settings.width_start_shape,
            )
        if width_end is not None:
            markers.width_end = self._make_marker(
                row_index, "width_end", settings.width_end_color, width_end, settings.width_end_shape,
            )
        if width_start is not None and width_end is not None and settings.show_line:
            markers.width_line = self.addLine(
                width_start.x(), width_start.y(), width_end.x(), width_end.y(),
                QPen(hint_color or settings.width_color, settings.line_width, Qt.DashLine),
            )

        rect = self._box_rect(row_index, markers)
        if rect is not None:
            if settings.show_bbox:
                markers.bbox = self.addRect(rect, QPen(hint_color or settings.bbox_color, settings.line_width))
                markers.bbox.setZValue(0.5)
            else:
                # Invisible stand-in for the bbox: keeps a generous, always-present
                # clickable margin around an animal's points (also covering the
                # head/tail line area) even when the bbox/line are not drawn.
                markers.hit_area = self.addRect(rect, QPen(Qt.NoPen), QBrush(Qt.NoBrush))
                markers.hit_area.setZValue(0.5)

        self._markers[row_index] = markers
        opacity = self._opacity_for(row_index in self._selected_rows, bool(self._selected_rows))
        for item in markers.items():
            item.setOpacity(opacity)
            item.setVisible(self._markers_visible)

    def _box_rect(self, row_index: int, markers: "_AnimalMarkers") -> QRectF | None:
        """The model-predicted box while the animal's keypoints are untouched, else the keypoint box."""
        predicted = self.store.predicted_box(row_index, self.side)
        if predicted is not None:
            x1, y1, x2, y2 = predicted
            return QRectF(x1, y1, x2 - x1, y2 - y1)
        return self._bbox_rect(markers)

    def _bbox_rect(self, markers: "_AnimalMarkers") -> QRectF | None:
        positions = [m.pos() for m in markers.markers()]
        if not positions:
            return None
        margin = self.settings.bbox_margin
        xs = [p.x() for p in positions]
        ys = [p.y() for p in positions]
        return QRectF(
            min(xs) - margin, min(ys) - margin,
            (max(xs) - min(xs)) + 2 * margin, (max(ys) - min(ys)) + 2 * margin,
        )

    @staticmethod
    def _lower_point(a: QPointF | None, b: QPointF | None) -> QPointF | None:
        """Returns whichever of two points is lower in the image (greater y)."""
        if a is None:
            return b
        if b is None:
            return a
        return a if a.y() >= b.y() else b

    def _make_marker(
        self, row_index: int, point_name: str, color: QColor, pos: QPointF, shape: str = "circle",
    ) -> _MarkerItem:
        marker = _MarkerItem(
            color, self.settings.marker_radius,
            lambda new_pos, ri=row_index, pn=point_name: self._on_marker_moved(ri, pn, new_pos),
            shape,
        )
        marker.set_movable(self._markers_movable)
        self._drawing = True
        try:
            marker.setPos(pos)
        finally:
            self._drawing = False
        self.addItem(marker)
        return marker

    def set_markers_visible(self, visible: bool) -> None:
        """Shows/hides every annotation item (view-only; the store is unaffected). While
        hidden, clicking the canvas does not edit anything."""
        self._markers_visible = visible
        if not visible:
            self.cancel_pending_add()
        for markers in self._markers.values():
            for item in markers.items():
                item.setVisible(visible)

    def markers_visible(self) -> bool:
        return self._markers_visible

    def set_markers_movable(self, movable: bool) -> None:
        """Enables/disables dragging of keypoints (selection stays possible either way)."""
        self._markers_movable = movable
        for markers in self._markers.values():
            for marker in markers.markers():
                marker.set_movable(movable)

    # --- editing ------------------------------------------------------------ #
    def _on_marker_moved(self, row_index: int, point_name: str, new_pos: QPointF) -> None:
        if self._drawing:
            return  # initial placement while drawing is not an edit
        suffix_x, suffix_y = _POINT_SUFFIXES[point_name]
        self.store.clear_predicted_box(row_index, self.side)
        # moving a keypoint is a manual check of the animal (relevant for model predictions)
        self.store.update_row(row_index, status="checked", manually_corrected=True, **{
            f"{self.side}{suffix_x}": new_pos.x(), f"{self.side}{suffix_y}": new_pos.y(),
        })
        self._redraw_connectors(row_index)
        self.animal_moved.emit(row_index)

    def _redraw_connectors(self, row_index: int) -> None:
        markers = self._markers.get(row_index)
        if markers is None:
            return
        if markers.line is not None and markers.head is not None and markers.tail is not None:
            markers.line.setLine(
                markers.head.pos().x(), markers.head.pos().y(),
                markers.tail.pos().x(), markers.tail.pos().y(),
            )
        if markers.width_line is not None and markers.width_start is not None and markers.width_end is not None:
            markers.width_line.setLine(
                markers.width_start.pos().x(), markers.width_start.pos().y(),
                markers.width_end.pos().x(), markers.width_end.pos().y(),
            )
        if markers.bbox is not None or markers.hit_area is not None:
            rect = self._box_rect(row_index, markers)
            if rect is not None:
                if markers.bbox is not None:
                    markers.bbox.setRect(rect)
                if markers.hit_area is not None:
                    markers.hit_area.setRect(rect)
        label_anchor = self._lower_point(
            markers.head.pos() if markers.head is not None else None,
            markers.tail.pos() if markers.tail is not None else None,
        )
        if markers.label is not None and label_anchor is not None:
            markers.label.setPos(label_anchor.x() + LABEL_MARGIN, label_anchor.y() + LABEL_MARGIN)

    def select_row(self, row_index: int | None, additive: bool = False) -> None:
        """User-initiated selection (mouse click); updates local highlighting and notifies listeners."""
        if row_index is None:
            rows = set()
        elif additive:
            rows = set(self._selected_rows)
            if row_index in rows:
                rows.discard(row_index)
            else:
                rows.add(row_index)
        else:
            rows = {row_index}
        self.set_selected_rows(rows)
        self.animal_selected.emit(row_index, additive)

    def set_selected_rows(self, row_indices: set[int]) -> None:
        """Pure visual sync (no signal emission) — used to mirror selection state driven
        elsewhere (another scene, a list panel, or refresh())."""
        self._selected_rows = set(row_indices)
        self._selected_width_row = None
        self._selected_row = next(iter(self._selected_rows)) if len(self._selected_rows) == 1 else None
        any_selected = bool(self._selected_rows)
        for ri, markers in self._markers.items():
            highlighted = ri in self._selected_rows
            for marker in markers.markers():
                marker.set_highlighted(highlighted)
            # Dim every non-selected animal once something IS selected, so the selected
            # one stands out; with nothing selected, everything stays at full opacity.
            opacity = self._opacity_for(highlighted, any_selected)
            for item in markers.items():
                item.setOpacity(opacity)
            if markers.bbox is not None:
                pen = markers.bbox.pen()
                pen.setWidth(self.settings.line_width + (BBOX_SELECTED_EXTRA_WIDTH if highlighted else 0))
                markers.bbox.setPen(pen)
        self._drop_stale_pending_width()

    def _drop_stale_pending_width(self) -> None:
        """A half-placed width pair is abandoned as soon as its animal is no longer the selection."""
        if self._pending_width_row is not None and self._selected_rows != {self._pending_width_row}:
            self.cancel_pending_add()

    def _opacity_for(self, highlighted: bool, any_selected: bool) -> float:
        base = self.settings.opacity
        return base if (highlighted or not any_selected) else base * UNSELECTED_OPACITY

    def has_row(self, row_index: int) -> bool:
        return row_index in self._markers

    def _file_rows_snapshot(self):
        """Copy of the current file's rows, used as the "before" state for undo."""
        return self.store.rows_for_file(self.file_id).copy()

    def remove_row(self, row_index: int) -> None:
        before = self._file_rows_snapshot()
        markers = self._markers.pop(row_index, None)
        if markers is not None:
            for item in markers.items():
                self.removeItem(item)
        self.store.remove_row(row_index)
        self._selected_rows.discard(row_index)
        if self._selected_row == row_index:
            self._selected_row = None
        self.animal_removed.emit(row_index)
        self.store_changed.emit(before, "Remove animal")

    def add_animal(
        self, head: QPointF, tail: QPointF, group: str = "", species: str = "", subgroup: str = "",
    ) -> int:
        before = self._file_rows_snapshot()
        side_key = _SIDE_TO_BUILD_KEY[self.side]
        row = self.store.build_row(
            self.file_id, group=group, subgroup=subgroup, species=species,
            **{side_key: {"head": (head.x(), head.y()), "tail": (tail.x(), tail.y())}},
        )
        row_index = self.store.add_row(row)
        self._draw_row(row_index, self.store.data.loc[row_index])
        self.animal_added.emit(row_index)
        self.store_changed.emit(before, "Add animal")
        return row_index

    def cancel_pending_add(self) -> None:
        if self._pending_marker is not None:
            self.removeItem(self._pending_marker)
            self._pending_marker = None
        self._pending_head = None
        self._pending_width_row = None
        self._pending_width_start = None

    def _width_target_row(self) -> int | None:
        """The single selected animal that has head/tail but no width keypoints on this side."""
        if len(self._selected_rows) != 1:
            return None
        row_index = next(iter(self._selected_rows))
        markers = self._markers.get(row_index)
        if markers is None or markers.head is None or markers.tail is None:
            return None
        if markers.width_start is not None or markers.width_end is not None:
            return None
        return row_index

    def _is_too_far_for_width(self, row_index: int, pos: QPointF) -> bool:
        """Whether ``pos`` is too far from the animal's length keypoints to be one of its width keypoints."""
        markers = self._markers[row_index]
        head, tail = markers.head.pos(), markers.tail.pos()
        axis = np.array([tail.x() - head.x(), tail.y() - head.y()])
        length = float(np.linalg.norm(axis))
        offset = np.array([pos.x() - head.x(), pos.y() - head.y()])
        if length == 0:
            distance = float(np.linalg.norm(offset))
        else:
            t = min(max(float(offset @ axis) / length**2, 0.0), 1.0)
            distance = float(np.linalg.norm(offset - t * axis))
        return distance > MAX_WIDTH_DISTANCE_FACTOR * length

    def _handle_width_click(self, row_index: int, pos: QPointF) -> None:
        if self._pending_width_row is None:
            color = self.settings.width_start_color
            self._pending_width_row = row_index
            self._pending_width_start = pos
            self._pending_marker = self.addPath(
                _marker_path(self.settings.width_start_shape, self.settings.marker_radius),
                QPen(Qt.black, 1), QBrush(color),
            )
            self._pending_marker.setPos(pos)
            self._pending_marker.setZValue(2)
            return
        start = self._pending_width_start
        self.cancel_pending_add()
        self.add_width(row_index, start, pos)

    def selected_width_row(self) -> int | None:
        """The animal whose width keypoint is explicitly selected, if any."""
        row_index = self._selected_width_row
        if row_index is None or self._selected_rows != {row_index}:
            return None
        markers = self._markers.get(row_index)
        if markers is None or markers.width_start is None or markers.width_end is None:
            return None
        return row_index

    def remove_width(self, row_index: int) -> None:
        """Deletes the animal's width keypoints on this side, keeping the animal itself."""
        before = self._file_rows_snapshot()
        fields = {}
        for name in ("width_start", "width_end"):
            suffix_x, suffix_y = _POINT_SUFFIXES[name]
            fields[f"{self.side}{suffix_x}"] = NO_VALUE
            fields[f"{self.side}{suffix_y}"] = NO_VALUE
        # the height needs width points on both sides, so it is no longer valid
        self.store.update_row(
            row_index, status="checked", manually_corrected=True, height=NO_VALUE, **fields,
        )
        self.refresh()
        self.animal_moved.emit(row_index)
        self.store_changed.emit(before, "Remove width keypoints")

    def add_width(self, row_index: int, start: QPointF, end: QPointF) -> None:
        """Sets the animal's two width keypoints on this side."""
        before = self._file_rows_snapshot()
        fields = {}
        for name, point in (("width_start", start), ("width_end", end)):
            suffix_x, suffix_y = _POINT_SUFFIXES[name]
            fields[f"{self.side}{suffix_x}"] = point.x()
            fields[f"{self.side}{suffix_y}"] = point.y()
        # like moving a keypoint, adding width points is a manual check of the animal
        self.store.update_row(row_index, status="checked", manually_corrected=True, **fields)
        self.refresh()
        self.animal_moved.emit(row_index)
        self.store_changed.emit(before, "Add width keypoints")

    # --- mouse interaction ---------------------------------------------------- #
    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.LeftButton:
            super().mousePressEvent(event)
            return

        if not self._markers_visible:
            self.edit_blocked.emit()
            event.accept()
            return

        if self.tool == Tool.ADD:
            pos = event.scenePos()
            width_row = self._pending_width_row
            if width_row is None and self._pending_head is None:
                width_row = self._width_target_row()
            if width_row is not None and not self._is_too_far_for_width(width_row, pos):
                self._handle_width_click(width_row, pos)
            else:
                # a click too far from the animal's length keypoints (or a click with no
                # animal to add width to) starts or completes a new animal instead; a pending
                # first width point becomes the head of that animal
                head = self._pending_head
                if self._pending_width_row is not None:
                    head = self._pending_width_start
                if head is None:
                    radius = self.settings.marker_radius
                    color = self.settings.head_color
                    self._pending_head = pos
                    self._pending_marker = self.addEllipse(
                        pos.x() - radius, pos.y() - radius,
                        2 * radius, 2 * radius, QPen(color, 1), QBrush(color),
                    )
                else:
                    self.cancel_pending_add()
                    row_index = self.add_animal(head, pos)
                    self.select_row(row_index)
            event.accept()
            return

        if self.tool == Tool.REMOVE:
            row_index = self._row_at(event)
            if row_index is not None:
                if self._width_marker_hit(event):
                    self.remove_width(row_index)
                else:
                    self.remove_row(row_index)
            event.accept()
            return

        super().mousePressEvent(event)
        if self.tool == Tool.EDIT:
            self._drag_before_snapshot = self._file_rows_snapshot()
            additive = bool(event.modifiers() & Qt.ControlModifier)
            row_index = self._row_at(event)
            self.select_row(row_index, additive=additive)
            if row_index is not None and self._selected_rows == {row_index} and self._width_marker_hit(event):
                self._selected_width_row = row_index

    def mouseReleaseEvent(self, event) -> None:
        super().mouseReleaseEvent(event)
        if self.tool == Tool.EDIT and self._drag_before_snapshot is not None:
            before = self._drag_before_snapshot
            self._drag_before_snapshot = None
            if not self.store.rows_for_file(self.file_id).equals(before):
                self.store_changed.emit(before, "Move marker")

    def _top_item_at(self, event) -> QGraphicsItem | None:
        views = self.views()
        transform = views[0].transform() if views else None
        item = self.itemAt(event.scenePos(), transform) if transform is not None else None
        while item is not None and item.parentItem() is not None:
            item = item.parentItem()
        return item

    def _row_at(self, event) -> int | None:
        item = self._top_item_at(event)
        for row_index, markers in self._markers.items():
            if item in markers.items():
                return row_index
        return None

    def _width_marker_hit(self, event) -> bool:
        item = self._top_item_at(event)
        return item is not None and any(
            item is markers.width_start or item is markers.width_end for markers in self._markers.values()
        )
