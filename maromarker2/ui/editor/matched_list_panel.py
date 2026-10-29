"""Table of currently matched animal pairs for the current image pair.

A matched pair shares a single row_index across both the L and R sides (see
``AnnotationStore.merge_as_match``), so selecting a row here selects that same
animal on both sides at once (wired up by ``EditorWidget``). Standard
Ctrl/Shift/Ctrl+A multi-selection is supported natively.
"""

from __future__ import annotations

from PySide6.QtCore import QItemSelectionModel, Qt, Signal
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget

from maromarker2.models.annotation_store import NO_VALUE, AnnotationStore
from maromarker2.models.taxonomy import UNIDENTIFIED
from maromarker2.ui.theme import add_left_outline

_COLUMNS = ("ID", "Group", "Subgroup", "Species", "Length", "Width")
_SUBGROUP_COLUMN = 2


class MatchedListPanel(QWidget):
    matches_selected = Signal(object)  # row_indices: set[int]

    def __init__(self, show_subgroup: bool = True, parent=None):
        super().__init__(parent)

        self.table = QTableWidget(0, len(_COLUMNS))
        self.table.setHorizontalHeaderLabels(_COLUMNS)
        self.table.setColumnHidden(_SUBGROUP_COLUMN, not show_subgroup)
        add_left_outline(self.table)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        header.setSectionResizeMode(3, QHeaderView.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeToContents)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.table)

    def _row_index_at(self, table_row: int) -> int | None:
        if table_row < 0:
            return None
        item = self.table.item(table_row, 0)
        return item.data(Qt.UserRole) if item is not None else None

    def _on_selection_changed(self) -> None:
        selected = {self._row_index_at(index.row()) for index in self.table.selectionModel().selectedRows()}
        self.matches_selected.emit(selected - {None})

    def clear(self) -> None:
        was_blocked = self.table.signalsBlocked()
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        self.table.blockSignals(was_blocked)

    def refresh(self, store: AnnotationStore, file_id: str) -> None:
        was_blocked = self.table.signalsBlocked()
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        rows = store.rows_for_file(file_id)
        matched = rows[(rows["LX1"] > 0) & (rows["RX1"] > 0)]
        for row_index, row in matched.iterrows():
            table_row = self.table.rowCount()
            self.table.insertRow(table_row)
            id_item = QTableWidgetItem(str(row_index))
            id_item.setData(Qt.UserRole, int(row_index))
            self.table.setItem(table_row, 0, id_item)
            self.table.setItem(table_row, 1, QTableWidgetItem(str(row.get("group") or "").lower() or UNIDENTIFIED))
            self.table.setItem(table_row, 2, QTableWidgetItem(str(row.get("subgroup") or "").lower() or UNIDENTIFIED))
            self.table.setItem(table_row, 3, QTableWidgetItem(str(row.get("species") or "").lower() or UNIDENTIFIED))
            for column, field in ((4, "length"), (5, "height")):
                size = float(row.get(field, NO_VALUE))
                self.table.setItem(table_row, column, QTableWidgetItem(f"{size:.1f}" if size > 0 else "—"))
        self.table.blockSignals(was_blocked)

    def select_rows(self, row_indices: set) -> None:
        was_blocked = self.table.signalsBlocked()
        self.table.blockSignals(True)
        self.table.clearSelection()
        selection_model = self.table.selectionModel()
        model = self.table.model()
        first_match = -1
        for table_row in range(self.table.rowCount()):
            if self._row_index_at(table_row) in row_indices:
                selection_model.select(
                    model.index(table_row, 0), QItemSelectionModel.Select | QItemSelectionModel.Rows
                )
                if first_match < 0:
                    first_match = table_row
        if first_match >= 0:
            selection_model.setCurrentIndex(model.index(first_match, 0), QItemSelectionModel.NoUpdate)
        else:
            selection_model.clearCurrentIndex()
        self.table.blockSignals(was_blocked)
