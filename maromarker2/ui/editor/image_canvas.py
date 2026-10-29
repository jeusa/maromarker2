"""QGraphicsView wrapper: wheel/button zoom and middle/right-mouse-drag panning.

Two canvases can be linked via ``set_linked`` so that zooming/panning/fitting one
view applies the same change to its sibling, keeping both on the same crop.
"""

from __future__ import annotations

from contextlib import contextmanager

from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QGraphicsView

MIN_SCALE = 0.05
MAX_SCALE = 20.0
ZOOM_STEP = 1.25
PAN_BUTTONS = (Qt.MiddleButton, Qt.RightButton)


class ImageCanvas(QGraphicsView):
    """Displays an AnnotationScene; left-click is left free for the scene's own
    add/select/remove/drag interactions, so panning uses the middle or right mouse button."""

    def __init__(self, scene, parent=None):
        super().__init__(scene, parent)
        self.setRenderHint(QPainter.Antialiasing)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorUnderMouse)
        self._scale = 1.0
        self._pan_start = None
        self._pan_button = None
        self._linked: "ImageCanvas | None" = None
        self._auto_fit = True  # re-fit on resize until the user explicitly zooms/pans
        self._scroll_sync_blocked = False
        self.horizontalScrollBar().valueChanged.connect(
            lambda value: self._mirror_scrollbar("horizontal", value)
        )
        self.verticalScrollBar().valueChanged.connect(
            lambda value: self._mirror_scrollbar("vertical", value)
        )
        self.horizontalScrollBar().actionTriggered.connect(lambda *_: self._on_scrollbar_action())
        self.verticalScrollBar().actionTriggered.connect(lambda *_: self._on_scrollbar_action())

    @contextmanager
    def _no_scroll_sync(self):
        """Suppresses mirroring of scrollbar changes made by zoom/fit/resize logic,
        which positions the linked view explicitly instead."""
        previous = self._scroll_sync_blocked
        self._scroll_sync_blocked = True
        try:
            yield
        finally:
            self._scroll_sync_blocked = previous

    def _mirror_scrollbar(self, orientation: str, value: int) -> None:
        """Copies a scrollbar position (drag, arrows, wheel, panning) onto the linked view."""
        other = self._linked
        if self._scroll_sync_blocked or other is None:
            return
        bar = other.horizontalScrollBar() if orientation == "horizontal" else other.verticalScrollBar()
        with other._no_scroll_sync():
            bar.setValue(value)

    def _on_scrollbar_action(self) -> None:
        self._auto_fit = False
        if self._linked is not None:
            self._linked._auto_fit = False

    def set_linked(self, other: "ImageCanvas | None") -> None:
        """Makes ``other`` mirror this view's zoom/pan/fit, and vice versa."""
        self._linked = other

    def wheelEvent(self, event) -> None:
        """The wheel only zooms; scrolling is left to the scroll bars and the keyboard."""
        delta = event.angleDelta().y()
        if delta:
            self.zoom_by(ZOOM_STEP if delta > 0 else 1 / ZOOM_STEP)
        event.accept()

    def _scroll_by_raw(self, dx: int, dy: int) -> None:
        self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() + dx)
        self.verticalScrollBar().setValue(self.verticalScrollBar().value() + dy)

    def zoom_by(self, factor: float, _sync: bool = True) -> None:
        new_scale = self._scale * factor
        if new_scale < MIN_SCALE or new_scale > MAX_SCALE:
            return
        self._scale = new_scale
        with self._no_scroll_sync():
            self.scale(factor, factor)
        self._auto_fit = False
        if _sync and self._linked is not None:
            self._linked._auto_fit = False
            # Mirror this view's resulting center scene coordinate onto the linked view
            # directly, instead of letting it anchor its own zoom under the mouse cursor
            # (the cursor isn't over that view, so that anchor point is unrelated and the
            # two views would drift apart onto different crops).
            center_point = self.mapToScene(self.viewport().rect().center())
            self._linked._zoom_to_absolute(new_scale, center_point)

    def _zoom_to_absolute(self, scale: float, center_scene_point) -> None:
        """Applies an absolute scale and centers on a specific scene point (used to keep
        a linked view showing the same scene coordinate as its sibling after a zoom)."""
        with self._no_scroll_sync():
            self.resetTransform()
            self.scale(scale, scale)
            self._scale = scale
            self.centerOn(center_scene_point)
        self._auto_fit = False

    def zoom_reset(self) -> None:
        with self._no_scroll_sync():
            self.resetTransform()
        self._scale = 1.0

    def fit_to_window(self, _sync: bool = True) -> None:
        """Fits this view's scene into its viewport. When linked, the view whose
        viewport currently has the larger area decides the shared zoom level, and
        that same scale is then applied to the other view, so both sides always
        stay at an identical zoom even if their on-screen sizes differ."""
        other = self._linked
        if _sync and other is not None and other._has_fittable_scene():
            bigger, smaller = (self, other) if self._viewport_area() >= other._viewport_area() else (other, self)
            bigger._fit_self()
            smaller._apply_scale(bigger._scale)
            bigger._auto_fit = True
            smaller._auto_fit = True
            return
        self._fit_self()
        self._auto_fit = True

    def _has_fittable_scene(self) -> bool:
        return self.scene() is not None and not self.scene().sceneRect().isEmpty()

    def _viewport_area(self) -> int:
        size = self.viewport().size()
        return size.width() * size.height()

    def _fit_self(self) -> None:
        if self._has_fittable_scene():
            with self._no_scroll_sync():
                self.fitInView(self.scene().sceneRect(), Qt.KeepAspectRatio)
            self._scale = self.transform().m11()

    def _apply_scale(self, scale: float) -> None:
        if not self._has_fittable_scene():
            return
        with self._no_scroll_sync():
            self.resetTransform()
            self.scale(scale, scale)
            self._scale = scale
            self.centerOn(self.scene().sceneRect().center())

    def pan_by(self, dx: int, dy: int, _sync: bool = True) -> None:
        self._scroll_by_raw(dx, dy)  # the scrollbar signal mirrors this onto the linked view
        self._on_scrollbar_action()

    def resizeEvent(self, event) -> None:
        with self._no_scroll_sync():
            super().resizeEvent(event)
        if self._auto_fit:
            self.fit_to_window()

    def mousePressEvent(self, event) -> None:
        if event.button() in PAN_BUTTONS and self._pan_start is None:
            self._pan_button = event.button()
            self._pan_start = event.pos()
            self.setCursor(Qt.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._pan_start is not None:
            delta = event.pos() - self._pan_start
            self._pan_start = event.pos()
            self.pan_by(-delta.x(), -delta.y())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._pan_start is not None and event.button() == self._pan_button:
            self._pan_start = None
            self._pan_button = None
            self.setCursor(Qt.ArrowCursor)
            event.accept()
            return
        super().mouseReleaseEvent(event)

