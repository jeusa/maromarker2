"""Small highlighted banner for short, transient messages (e.g. "Undid: Edit animal")."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QLabel, QSizePolicy

DEFAULT_TIMEOUT_MS = 5000


class InfoPanel(QLabel):
    """Always-visible highlighted box; the text is cleared (back to the persistent
    hint, if any) after the timeout, or stays until `clear` when timeout_ms is None."""

    def __init__(self, parent=None):
        super().__init__(parent)
        palette = self.palette()
        palette.setColor(QPalette.Window, palette.color(QPalette.Highlight))
        palette.setColor(QPalette.WindowText, palette.color(QPalette.HighlightedText))
        self.setPalette(palette)
        self.setAutoFillBackground(True)
        self.setAlignment(Qt.AlignCenter)
        self.setMargin(4)
        self.setWordWrap(True)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.clear)
        self._hint = ""

    def show_message(self, text: str, timeout_ms: int | None = DEFAULT_TIMEOUT_MS) -> None:
        self._timer.stop()
        self.setText(text)
        if timeout_ms is not None:
            self._timer.start(timeout_ms)

    def set_hint(self, text: str) -> None:
        """Sets the persistent message shown whenever no transient message is active."""
        self._hint = text
        if not self._timer.isActive():
            self.setText(text)

    def clear(self) -> None:
        self._timer.stop()
        self.setText(self._hint)
