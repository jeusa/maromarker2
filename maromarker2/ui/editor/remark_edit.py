"""Remark field: free text that can also be filled from a configurable list of options."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox


class RemarkEdit(QComboBox):
    """An editable combo box, used like a ``QLineEdit`` (``text``/``setText``/``textEdited``/
    ``editingFinished``) whose drop-down offers the preset remark options.

    Picking an option behaves like typing it and pressing Enter."""

    textEdited = Signal(str)
    editingFinished = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.NoInsert)
        self.setCurrentIndex(-1)
        self.lineEdit().textEdited.connect(self.textEdited)
        self.lineEdit().editingFinished.connect(self.editingFinished)
        self.textActivated.connect(self._on_option_picked)

    def text(self) -> str:
        return self.lineEdit().text()

    def setText(self, text: str) -> None:
        # no list item stays "current", so the field shows exactly the given text
        self.setCurrentIndex(-1)
        self.lineEdit().setText(text)

    def setPlaceholderText(self, text: str) -> None:
        self.lineEdit().setPlaceholderText(text)

    def options(self) -> list[str]:
        return [self.itemText(i) for i in range(self.count())]

    def set_options(self, options: list[str]) -> None:
        """Replaces the drop-down entries, keeping whatever text is currently in the field."""
        if options == self.options():
            return
        text = self.text()
        was_blocked = self.blockSignals(True)
        self.clear()
        self.addItems(options)
        self.blockSignals(was_blocked)
        self.setText(text)

    def _on_option_picked(self, text: str) -> None:
        self.setText(text)
        self.textEdited.emit(text)
        self.editingFinished.emit()
