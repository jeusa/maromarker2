"""Dialog presenting proposed automatic L/R matches (cost-sorted) for user confirmation
before they are committed to the AnnotationStore. Ports the legacy "Check matching" step.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QHeaderView, QLabel,
    QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from maromarker2.geometry.matching import MatchResult
from maromarker2.ui.theme import add_left_outline


class MatchReviewDialog(QDialog):
    """Shows one row per proposed match; unchecked rows are rejected on accept."""

    def __init__(self, results: list[MatchResult], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Confirm Automatic Matches")
        self.results = sorted(results, key=lambda r: r.cost)
        self._checkboxes: list[QCheckBox] = []

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            f"{len(self.results)} proposed match(es). Uncheck any you want to reject, then OK."
        ))

        table = QTableWidget(len(self.results), 4)
        table.setHorizontalHeaderLabels(["Accept", "Left row", "Right row", "Cost"])
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        add_left_outline(table)
        for row, result in enumerate(self.results):
            checkbox = QCheckBox()
            checkbox.setChecked(True)
            table.setCellWidget(row, 0, checkbox)
            self._checkboxes.append(checkbox)
            table.setItem(row, 1, QTableWidgetItem(str(result.left_index)))
            table.setItem(row, 2, QTableWidgetItem(str(result.right_index)))
            table.setItem(row, 3, QTableWidgetItem(f"{result.cost:.2f}"))
        layout.addWidget(table)
        self.table = table

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def accepted_results(self) -> list[MatchResult]:
        """The subset of ``results`` whose checkbox is still checked."""
        return [result for result, checkbox in zip(self.results, self._checkboxes) if checkbox.isChecked()]
