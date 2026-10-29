"""Left-hand list of all loaded image pairs, with their checked state and image remark.

Clicking a row navigates to that image pair (``pair_selected``); clicking its "Checked" box
emits ``checked_toggled``. Above the table, the
remark of the current image pair can be edited (free text or one of the preset options).
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QHeaderView, QLabel, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from maromarker2.models.annotation_store import AnnotationStore
from maromarker2.models.image_set import ImagePair
from maromarker2.ui.editor.remark_edit import RemarkEdit
from maromarker2.ui.theme import add_left_outline

_COLUMNS = ("Image pair", "Animals", "Checked", "Remarks")
_IMAGE, _ANIMALS, _CHECKED, _REMARKS = range(4)


class ImageListPanel(QWidget):
    pair_selected = Signal(int)  # index into the image pair list
    remark_changed = Signal(str)  # new remark for the current image pair
    checked_toggled = Signal(int, bool)  # image pair index, new state of its "Checked" box

    def __init__(self, parent=None):
        super().__init__(parent)
        self._file_ids: list[str] = []

        self.table = QTableWidget(0, len(_COLUMNS))
        self.table.setHorizontalHeaderLabels(_COLUMNS)
        add_left_outline(self.table)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(_IMAGE, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(_ANIMALS, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(_CHECKED, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(_REMARKS, QHeaderView.Stretch)
        header.setMinimumSectionSize(60)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        self.table.itemChanged.connect(self._on_item_changed)

        self.edit_remark = RemarkEdit()
        self._editing_locked = False
        self.edit_remark.setEnabled(False)
        self.edit_remark.editingFinished.connect(lambda: self.remark_changed.emit(self.edit_remark.text()))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QLabel("Image remark for selected image pair"), 0)
        layout.addWidget(self.edit_remark, 0)
        layout.addWidget(self.table, 1)

    # --- content ------------------------------------------------------------ #
    def set_pairs(self, image_pairs: list[ImagePair], store: AnnotationStore) -> None:
        """Rebuilds the whole table (one row per image pair)."""
        self._file_ids = [pair.file_id for pair in image_pairs]
        overview = store.image_overview(self._file_ids)
        was_blocked = self.table.blockSignals(True)
        self.table.setRowCount(len(image_pairs))
        for row, file_id in enumerate(self._file_ids):
            name_item = QTableWidgetItem(file_id)
            name_item.setToolTip(file_id)
            self.table.setItem(row, _IMAGE, name_item)
            self.table.setItem(row, _ANIMALS, self._count_item())
            self.table.setItem(row, _CHECKED, self._checked_item())
            self.table.setItem(row, _REMARKS, QTableWidgetItem())
            self._show_state(row, *overview[file_id])
        self.table.blockSignals(was_blocked)

    def refresh_pair(self, store: AnnotationStore, index: int) -> None:
        """Updates the checked state and remark of one image pair, including the remark editor
        if that pair is the selected one."""
        if not 0 <= index < len(self._file_ids):
            return
        count, checked, remark = store.image_overview([self._file_ids[index]])[self._file_ids[index]]
        was_blocked = self.table.blockSignals(True)
        self._show_state(index, count, checked, remark)
        self.table.blockSignals(was_blocked)
        if index == self.current_index():
            self.edit_remark.setText(remark)

    def select_pair(self, index: int, remark: str = "") -> None:
        """Highlights an image pair (without emitting ``pair_selected``) and shows its remark in the editor."""
        was_blocked = self.table.blockSignals(True)
        if 0 <= index < self.table.rowCount():
            self.table.selectRow(index)
            self.table.setCurrentCell(index, _IMAGE)
            self.table.scrollToItem(self.table.item(index, _IMAGE))
        else:
            self.table.clearSelection()
        self.table.blockSignals(was_blocked)
        self.edit_remark.setEnabled(0 <= index < self.table.rowCount() and not self._editing_locked)
        self.edit_remark.setText(remark)

    def set_editing_locked(self, locked: bool) -> None:
        """While locked the image remark cannot be edited (selecting image pairs still works)."""
        self._editing_locked = locked
        self.edit_remark.setEnabled(0 <= self.current_index() < self.table.rowCount() and not locked)

    def current_index(self) -> int:
        rows = self.table.selectionModel().selectedRows()
        return rows[0].row() if rows else -1

    def set_remark_options(self, options: list[str]) -> None:
        self.edit_remark.set_options(options)

    # --- internals ---------------------------------------------------------- #
    @staticmethod
    def _checked_item() -> QTableWidgetItem:
        # clicking toggles the status of the pair's animals; the box is then re-derived from the store
        item = QTableWidgetItem()
        item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable)
        item.setTextAlignment(Qt.AlignCenter)
        return item

    @staticmethod
    def _count_item() -> QTableWidgetItem:
        item = QTableWidgetItem()
        item.setTextAlignment(Qt.AlignCenter)
        return item

    def _show_state(self, row: int, count: int, checked: bool, remark: str) -> None:
        self.table.item(row, _ANIMALS).setText(str(count))
        self.table.item(row, _CHECKED).setCheckState(Qt.Checked if checked else Qt.Unchecked)
        remark_item = self.table.item(row, _REMARKS)
        remark_item.setText(remark)
        remark_item.setToolTip(remark)

    def _on_selection_changed(self) -> None:
        index = self.current_index()
        if index >= 0:
            self.pair_selected.emit(index)

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() == _CHECKED:
            self.checked_toggled.emit(item.row(), item.checkState() == Qt.Checked)
