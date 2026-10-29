"""Table of all annotated animals for one side (L/R) of the current image pair.

Kept in sync with the scene's selection by ``EditorWidget``; selecting a row here
selects the same animal in both scenes and vice versa (Ctrl+click toggles a row,
Shift+click selects the range from the last clicked row, Ctrl+A selects every row).
Deletion is handled centrally by EditorWidget via the Delete key.
"""

from __future__ import annotations

from PySide6.QtCore import QItemSelectionModel, Qt, Signal
from PySide6.QtGui import QBrush, QKeySequence
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget

from maromarker2.models.annotation_store import AnnotationStore
from maromarker2.models.taxonomy import UNIDENTIFIED
from maromarker2.ui.editor.color_hints import UNMATCHED_TINT, UNSET_CLASS_TINT, ColorHint, is_unidentified
from maromarker2.ui.theme import add_left_outline

_COLUMNS = ("ID", "Matched", "Group", "Subgroup", "Species", "Status", "Manually\ncorrected", "Remarks")
_SUBGROUP_COLUMN = 3
_COLUMN_MARGIN = 16  # px of breathing room added on top of the exact content width


class _AnimalTableWidget(QTableWidget):
    """Mouse-driven row selection is handled entirely through ``row_clicked`` +
    ``AnimalListPanel.select_rows()`` rather than Qt's native click-selection
    handling.

    Qt's native handling races with our own cross-widget sync: a Ctrl+click fires
    ``currentCellChanged`` (and thus our rebuild of the authoritative multi-selection)
    during *mousePressEvent*, but the view then applies its own toggle/select command
    in *mouseReleaseEvent* based on whatever the selection model holds *at that later
    point* — silently flipping the just-clicked row back off. Bypassing Qt's own
    press/release selection handling entirely avoids the race (and, as a side effect,
    makes any checkbox cells in the row non-interactive via click, since checkbox
    toggling is normally implemented inside that same native handling).
    """

    row_clicked = Signal(int, bool, bool)  # table_row, ctrl_held, shift_held
    select_all_requested = Signal()

    def mousePressEvent(self, event) -> None:
        index = self.indexAt(event.position().toPoint())
        self.setFocus()
        # -1 (an invalid table row) signals "clicked empty area"
        self.row_clicked.emit(
            index.row() if index.isValid() else -1,
            bool(event.modifiers() & Qt.ControlModifier),
            bool(event.modifiers() & Qt.ShiftModifier),
        )
        event.accept()

    def keyPressEvent(self, event) -> None:
        # Qt's native select-all would only highlight the rows without telling
        # EditorWidget, so it is routed through the same authoritative selection path.
        if event.matches(QKeySequence.SelectAll):
            self.select_all_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        event.accept()


class AnimalListPanel(QWidget):
    animal_selected = Signal(object, bool)  # row_index: int | None, additive: bool

    rows_selected = Signal(object, bool)  # row_indices: set[int], additive: bool (Shift range / Ctrl+A)

    def __init__(self, side: str, show_subgroup: bool = True, parent=None):
        super().__init__(parent)
        self.side = side
        self._anchor: int | None = None  # row_index that Shift+click ranges start from
        self._color_hint: ColorHint | None = None

        self.table = _AnimalTableWidget(0, len(_COLUMNS))
        self.table.setHorizontalHeaderLabels(_COLUMNS)
        self.table.setColumnHidden(_SUBGROUP_COLUMN, not show_subgroup)
        add_left_outline(self.table)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        for col in range(len(_COLUMNS)):
            header.setSectionResizeMode(col, QHeaderView.Interactive)
        self.table.row_clicked.connect(self._on_row_clicked)
        self.table.select_all_requested.connect(self._on_select_all)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.table)

    def _row_index_at(self, table_row: int) -> int | None:
        if table_row < 0:
            return None
        item = self.table.item(table_row, 0)
        return item.data(Qt.UserRole) if item is not None else None

    def _table_row_of(self, row_index: int | None) -> int | None:
        if row_index is None:
            return None
        for table_row in range(self.table.rowCount()):
            if self._row_index_at(table_row) == row_index:
                return table_row
        return None

    def _on_row_clicked(self, table_row: int, additive: bool, shift: bool) -> None:
        row_index = self._row_index_at(table_row)
        anchor_row = self._table_row_of(self._anchor)
        if shift and row_index is not None and anchor_row is not None:
            low, high = sorted((anchor_row, table_row))
            rows = {self._row_index_at(row) for row in range(low, high + 1)}
            # Ctrl+Shift adds the range to the existing selection instead of replacing it
            self.rows_selected.emit(rows, additive)
            return
        self._anchor = row_index
        self.animal_selected.emit(row_index, additive)

    def _on_select_all(self) -> None:
        rows = {self._row_index_at(row) for row in range(self.table.rowCount())}
        self.rows_selected.emit(rows, False)

    def clear(self) -> None:
        was_blocked = self.table.signalsBlocked()
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        self.table.blockSignals(was_blocked)

    @staticmethod
    def _checkbox_item(checked: bool) -> QTableWidgetItem:
        item = QTableWidgetItem()
        item.setFlags((item.flags() | Qt.ItemIsUserCheckable) & ~Qt.ItemIsEditable)
        item.setCheckState(Qt.Checked if checked else Qt.Unchecked)
        item.setTextAlignment(Qt.AlignCenter)
        return item

    def set_color_hint(self, hint: ColorHint | None) -> None:
        """Chooses which unset cells are tinted; applied on the next refresh()."""
        self._color_hint = hint

    def refresh(self, store: AnnotationStore, file_id: str) -> None:
        """Rebuilds the table from rows that have at least one point on this side."""
        was_blocked = self.table.signalsBlocked()
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        x_col = f"{self.side}X1"
        for row_index, row in store.rows_for_file(file_id).iterrows():
            value = row.get(x_col)
            if value is None or float(value) <= 0:
                continue
            matched = float(row.get("LX1", -1)) > 0 and float(row.get("RX1", -1)) > 0
            table_row = self.table.rowCount()
            self.table.insertRow(table_row)

            id_item = QTableWidgetItem(str(row_index))
            id_item.setData(Qt.UserRole, int(row_index))
            self.table.setItem(table_row, 0, id_item)

            # display-only: flags deliberately omit interactivity so clicking the row
            # (which bypasses Qt's native handling, see _AnimalTableWidget) can never
            # toggle it; matched state is derived purely from the store's own data.
            self.table.setItem(table_row, 1, self._checkbox_item(matched))

            self.table.setItem(table_row, 2, QTableWidgetItem(str(row.get("group") or "").lower() or UNIDENTIFIED))
            self.table.setItem(table_row, 3, QTableWidgetItem(str(row.get("subgroup") or "").lower() or UNIDENTIFIED))
            self.table.setItem(table_row, 4, QTableWidgetItem(str(row.get("species") or "").lower() or UNIDENTIFIED))
            self.table.setItem(table_row, 5, QTableWidgetItem(str(row.get("status") or "")))
            self.table.setItem(table_row, 6, self._checkbox_item(bool(row.get("manually_corrected"))))
            self.table.setItem(table_row, 7, QTableWidgetItem(str(row.get("object_remarks") or "")))

            if self._color_hint == ColorHint.CLASS:
                for column, field in ((2, "group"), (3, "subgroup"), (4, "species")):
                    if is_unidentified(row, field):
                        self.table.item(table_row, column).setBackground(QBrush(UNSET_CLASS_TINT))
            elif self._color_hint == ColorHint.MATCH and not matched:
                self.table.item(table_row, 1).setBackground(QBrush(UNMATCHED_TINT))
        self.table.blockSignals(was_blocked)

    def content_column_widths(self) -> list[int]:
        """Ideal width for each column (exact content/header width + a small margin),
        letting ``EditorWidget`` apply the same widths to both the L and R panels."""
        header = self.table.horizontalHeader()
        for col in range(len(_COLUMNS)):
            header.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        widths = [header.sectionSize(col) + _COLUMN_MARGIN for col in range(len(_COLUMNS))]
        for col in range(len(_COLUMNS)):
            header.setSectionResizeMode(col, QHeaderView.Interactive)
        return widths

    def apply_column_widths(self, widths: list[int]) -> None:
        for col, width in enumerate(widths):
            self.table.setColumnWidth(col, width)

    def select_rows(self, row_indices: set) -> None:
        was_blocked = self.table.signalsBlocked()
        self.table.blockSignals(True)
        self.table.clearSelection()
        selection_model = self.table.selectionModel()
        model = self.table.model()
        first_match = -1
        first_index = None
        for table_row in range(self.table.rowCount()):
            if self._row_index_at(table_row) in row_indices:
                selection_model.select(
                    model.index(table_row, 0), QItemSelectionModel.Select | QItemSelectionModel.Rows
                )
                if first_match < 0:
                    first_match = table_row
                    first_index = self._row_index_at(table_row)
        # keep the Shift anchor on a selected row, even when the selection changed elsewhere
        if self._anchor not in row_indices:
            self._anchor = first_index
        if first_match >= 0:
            selection_model.setCurrentIndex(model.index(first_match, 0), QItemSelectionModel.NoUpdate)
        else:
            selection_model.clearCurrentIndex()
        self.table.blockSignals(was_blocked)


