"""Display-only image adjustments (brightness / contrast / CLAHE) and their control panel.

Adjustments are only ever applied to a copy of the pixels shown on the canvases: the
image files and the stored keypoint coordinates are never touched, and the adjustment
settings themselves are not persisted.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QImage
from PySide6.QtWidgets import (
    QCheckBox, QGridLayout, QGroupBox, QLabel, QPushButton, QSlider, QVBoxLayout, QWidget,
)

SLIDER_RANGE = 100  # brightness and contrast sliders both run from -SLIDER_RANGE to +SLIDER_RANGE
CLAHE_CLIP_LIMIT = 2.0
CLAHE_TILE_GRID = (8, 8)
EMIT_DELAY_MS = 40  # debounce so dragging a slider doesn't re-render a large image on every tick
LUMA_WEIGHTS = np.array([0.299, 0.587, 0.114], dtype=np.float32)  # RGB -> luminance


@dataclass(frozen=True)
class ImageAdjustments:
    brightness: int = 0  # -100..100, added to every channel (0..255 scale)
    contrast: int = 0  # -100..100, mapped to a gain of 0.25x..4x around the mean luminance (0 = unchanged)
    clahe: bool = False  # contrast-limited adaptive histogram equalization of the lightness
    white_balance: bool = False  # shifts the means of the LAB a and b channels to neutral (128)

    @property
    def is_identity(self) -> bool:
        return self.brightness == 0 and self.contrast == 0 and not self.clahe and not self.white_balance

    @property
    def gain(self) -> float:
        return 2.0 ** (self.contrast / (SLIDER_RANGE / 2))


def qimage_to_array(image: QImage) -> np.ndarray:
    """Returns an (h, w, 3) uint8 RGB copy of the image."""
    image = image.convertToFormat(QImage.Format_RGB888)
    width, height = image.width(), image.height()
    buffer = np.frombuffer(image.constBits(), dtype=np.uint8, count=image.sizeInBytes())
    # scan lines are padded to a 4-byte boundary
    return buffer.reshape(height, image.bytesPerLine())[:, : width * 3].reshape(height, width, 3).copy()


def array_to_qimage(array: np.ndarray) -> QImage:
    """Wraps an (h, w, 3) uint8 RGB array in an independent (copied) QImage."""
    array = np.ascontiguousarray(array)
    height, width = array.shape[:2]
    return QImage(array.data, width, height, width * 3, QImage.Format_RGB888).copy()


def apply_white_balance(lab: np.ndarray) -> np.ndarray:
    """Shifts the means of the A and B channels of an (h, w, 3) uint8 LAB image to 128."""
    l, a, b = cv2.split(lab)
    a = a.astype(np.float32)
    b = b.astype(np.float32)
    a = np.clip(a - (np.mean(a) - 128), 0, 255).astype(np.uint8)
    b = np.clip(b - (np.mean(b) - 128), 0, 255).astype(np.uint8)
    return cv2.merge((l, a, b))


def apply_adjustments(rgb: np.ndarray, adjustments: ImageAdjustments) -> np.ndarray:
    """Returns an adjusted copy of an (h, w, 3) uint8 RGB array; ``rgb`` itself is never modified."""
    if adjustments.is_identity:
        return rgb.copy()
    result = rgb
    if adjustments.white_balance or adjustments.clahe:
        # white balance first, so CLAHE equalizes the lightness of the corrected colors
        lab = cv2.cvtColor(result, cv2.COLOR_RGB2LAB)
        if adjustments.white_balance:
            lab = apply_white_balance(lab)
        if adjustments.clahe:
            clahe = cv2.createCLAHE(clipLimit=CLAHE_CLIP_LIMIT, tileGridSize=CLAHE_TILE_GRID)
            lab[:, :, 0] = clahe.apply(lab[:, :, 0])
        result = cv2.cvtColor(lab, cv2.COLOR_LAB2RGB)
    if adjustments.brightness != 0 or adjustments.contrast != 0:
        # contrast scales around the image's mean luminance, so it changes the spread of
        # the values but not the overall brightness; brightness is a plain offset.
        pixels = result.astype(np.float32)
        pivot = float(np.mean(pixels @ LUMA_WEIGHTS))
        pixels = adjustments.gain * (pixels - pivot) + pivot + adjustments.brightness
        result = np.clip(pixels, 0, 255).astype(np.uint8)
    return result


class ImageAdjustmentPanel(QGroupBox):
    """Brightness / contrast sliders, CLAHE and white balance toggles, a "Hide annotation
    markers" toggle and a Reset button that reverts all of them.

    ``adjustments_changed`` fires (debounced) whenever the image adjustments change. The
    marker toggle is a separate view option, not part of ``ImageAdjustments``, and is
    reported through ``markers_hidden_changed`` instead."""

    adjustments_changed = Signal(object)  # ImageAdjustments
    markers_hidden_changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__("Image view adjustments", parent)
        self.slider_brightness = self._make_slider()
        self.slider_contrast = self._make_slider()
        self.label_brightness = QLabel("0")
        self.label_contrast = QLabel("0")
        self.check_clahe = QCheckBox("CLAHE image enhancement")
        self.check_clahe.setToolTip("Contrast-limited adaptive histogram equalization")
        self.check_white_balance = QCheckBox("White balance correction")
        self.check_white_balance.setToolTip("Shifts the average color of the image to neutral")
        self.check_hide_markers = QCheckBox("Hide annotation markers")
        self.button_reset = QPushButton("Reset")
        self.button_reset.setToolTip(
            "Revert all adjustments and show the markers again (view-only; files are never modified)"
        )

        grid = QGridLayout()
        grid.setColumnStretch(1, 1)
        for row, (name, slider, label) in enumerate((
            ("Brightness", self.slider_brightness, self.label_brightness),
            ("Contrast", self.slider_contrast, self.label_contrast),
        )):
            grid.addWidget(QLabel(name), row, 0)
            grid.addWidget(slider, row, 1)
            grid.addWidget(label, row, 2)
            label.setMinimumWidth(label.fontMetrics().horizontalAdvance("-100"))
            label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        grid.addWidget(self.check_clahe, 2, 0, 1, 2)
        grid.addWidget(self.check_white_balance, 3, 0, 1, 3)
        grid.addWidget(self.check_hide_markers, 4, 0, 1, 3)
        grid.addWidget(self.button_reset, 5, 2, Qt.AlignRight)

        layout = QVBoxLayout(self)
        layout.addLayout(grid)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(EMIT_DELAY_MS)
        self._timer.timeout.connect(self._emit)

        self.slider_brightness.valueChanged.connect(self._on_changed)
        self.slider_contrast.valueChanged.connect(self._on_changed)
        self.check_clahe.toggled.connect(self._on_changed)
        self.check_white_balance.toggled.connect(self._on_changed)
        self.button_reset.clicked.connect(self.reset)
        self.check_hide_markers.toggled.connect(self._on_markers_toggled)
        self._update_state()

    @staticmethod
    def _make_slider() -> QSlider:
        slider = QSlider(Qt.Horizontal)
        slider.setRange(-SLIDER_RANGE, SLIDER_RANGE)
        slider.setValue(0)
        slider.setTickPosition(QSlider.NoTicks)
        return slider

    def adjustments(self) -> ImageAdjustments:
        return ImageAdjustments(
            brightness=self.slider_brightness.value(),
            contrast=self.slider_contrast.value(),
            clahe=self.check_clahe.isChecked(),
            white_balance=self.check_white_balance.isChecked(),
        )

    def markers_hidden(self) -> bool:
        return self.check_hide_markers.isChecked()

    def reset(self) -> None:
        """Reverts every control (image adjustments and marker hiding) and notifies immediately."""
        adjustment_widgets = (
            self.slider_brightness, self.slider_contrast, self.check_clahe, self.check_white_balance,
        )
        for widget in adjustment_widgets:
            widget.blockSignals(True)
        self.slider_brightness.setValue(0)
        self.slider_contrast.setValue(0)
        self.check_clahe.setChecked(False)
        self.check_white_balance.setChecked(False)
        for widget in adjustment_widgets:
            widget.blockSignals(False)
        self.check_hide_markers.setChecked(False)  # notifies by itself if it was checked
        self._update_state()
        self._timer.stop()
        self._emit()

    def flush(self) -> None:
        """Emits any pending change right away instead of waiting for the debounce."""
        if self._timer.isActive():
            self._timer.stop()
            self._emit()

    def _on_changed(self, *_) -> None:
        self._update_state()
        self._timer.start()

    def _on_markers_toggled(self, hidden: bool) -> None:
        self._update_state()
        self.markers_hidden_changed.emit(hidden)

    def _update_state(self) -> None:
        current = self.adjustments()
        self.label_brightness.setText(str(current.brightness))
        self.label_contrast.setText(str(current.contrast))
        self.button_reset.setEnabled(not current.is_identity or self.markers_hidden())

    def _emit(self) -> None:
        self.adjustments_changed.emit(self.adjustments())
