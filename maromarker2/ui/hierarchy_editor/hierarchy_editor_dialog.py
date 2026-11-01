"""Dialog to edit a copy of the hierarchy and save it as a new YAML file."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QDialog, QFileDialog, QHBoxLayout, QInputDialog, QLabel, QMessageBox, QPushButton, QVBoxLayout,
)

from maromarker2 import defaults
from maromarker2.models.taxonomy import DEFAULT_HIERARCHY_PATH, Taxonomy
from maromarker2.ui.hierarchy_editor.hierarchy_tree_widget import HierarchyTreeWidget


def _same_file(a: Path, b: Path) -> bool:
    return a.resolve() == b.resolve()


class HierarchyEditorDialog(QDialog):
    """Edits a private copy of ``taxonomy``; nothing is written until "Save As…" and the
    bundled default hierarchy is never overwritten. ``saved_path`` is the last file written."""

    def __init__(self, taxonomy: Taxonomy, source_path: Path | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit hierarchy")
        self.resize(520, 560)

        self.source_path = source_path
        self.saved_path: Path | None = None
        self.modified = False

        self.tree = HierarchyTreeWidget(taxonomy.copy())
        self.tree.changed.connect(self._on_changed)
        self.tree.itemSelectionChanged.connect(self._update_buttons)
        self.tree.itemDoubleClicked.connect(lambda *_: self._on_rename())

        self.btn_add_group = QPushButton("Add group")
        self.btn_add_subgroup = QPushButton("Add subgroup")
        self.btn_add_species = QPushButton("Add species")
        self.btn_rename = QPushButton("Rename")
        self.btn_remove = QPushButton("Remove")
        self.btn_add_group.clicked.connect(lambda: self._on_add("group"))
        self.btn_add_subgroup.clicked.connect(lambda: self._on_add("subgroup"))
        self.btn_add_species.clicked.connect(lambda: self._on_add("species"))
        self.btn_rename.clicked.connect(self._on_rename)
        self.btn_remove.clicked.connect(self._on_remove)

        self.btn_save_as = QPushButton("Save As…")
        self.btn_save_as.clicked.connect(self._on_save_as)
        self.btn_close = QPushButton("Close")
        self.btn_close.clicked.connect(self.reject)

        edit_row = QHBoxLayout()
        for button in (
            self.btn_add_group, self.btn_add_subgroup, self.btn_add_species, self.btn_rename, self.btn_remove,
        ):
            edit_row.addWidget(button)

        bottom_row = QHBoxLayout()
        bottom_row.addStretch(1)
        bottom_row.addWidget(self.btn_save_as)
        bottom_row.addWidget(self.btn_close)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Select a node, then add entries below it (nothing selected = top level)."))
        layout.addWidget(self.tree, 1)
        layout.addLayout(edit_row)
        layout.addLayout(bottom_row)
        self._update_buttons()

    @property
    def taxonomy(self) -> Taxonomy:
        return self.tree.taxonomy

    # --- editing ----------------------------------------------------------- #
    def _update_buttons(self) -> None:
        node = self.tree.selected_node()
        self.btn_add_group.setEnabled(self.tree.can_add("group"))
        self.btn_add_subgroup.setEnabled(self.tree.can_add("subgroup"))
        self.btn_add_species.setEnabled(self.tree.can_add("species"))
        self.btn_rename.setEnabled(node is not None)
        self.btn_remove.setEnabled(node is not None)

    def _on_changed(self) -> None:
        self.modified = True
        self._update_buttons()

    def _ask_name(self, title: str, initial: str = "") -> str | None:
        name, ok = QInputDialog.getText(self, title, "Name:", text=initial)
        return name if ok else None

    def _run(self, action) -> None:
        try:
            action()
        except ValueError as exc:
            QMessageBox.warning(self, "Edit hierarchy", str(exc))

    def _on_add(self, rank: str) -> None:
        name = self._ask_name(f"Add {rank}")
        if name is not None:
            self._run(lambda: self.tree.add_node(name, rank))

    def _on_rename(self) -> None:
        node = self.tree.selected_node()
        if node is None:
            return
        name = self._ask_name(f"Rename {node.rank}", node.name)
        if name is not None:
            self._run(lambda: self.tree.rename_selected(name))

    def _on_remove(self) -> None:
        node = self.tree.selected_node()
        if node is None:
            return
        text = f"Remove {node.rank} '{node.name}'?"
        if node.children:
            text = f"Remove {node.rank} '{node.name}' and everything below it?"
        if QMessageBox.question(self, "Remove", text) == QMessageBox.Yes:
            self._run(self.tree.remove_selected)

    # --- saving ------------------------------------------------------------ #
    def save_to(self, path: str | Path) -> Path:
        """Writes the hierarchy to ``path`` (``.yaml`` appended if it has no YAML suffix)."""
        path = Path(path)
        if path.suffix.lower() not in (".yaml", ".yml"):
            path = path.with_name(path.name + ".yaml")
        if _same_file(path, DEFAULT_HIERARCHY_PATH):
            raise ValueError("The bundled default hierarchy cannot be overwritten; choose another file name.")
        self.taxonomy.save(path)
        self.saved_path = path
        self.modified = False
        return path

    def _on_save_as(self) -> bool:
        start_dir = str(defaults.user_defaults_dir() / defaults.HIERARCHY_SUBDIR)
        if self.source_path is not None and not _same_file(self.source_path, DEFAULT_HIERARCHY_PATH):
            start_dir = str(self.source_path.parent)
        path, _ = QFileDialog.getSaveFileName(
            self, "Save hierarchy as", str(Path(start_dir) / "hierarchy_custom.yaml"), "YAML (*.yaml *.yml)"
        )
        if not path:
            return False
        try:
            saved = self.save_to(path)
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, "Save hierarchy", str(exc))
            return False
        QMessageBox.information(self, "Save hierarchy", f"Hierarchy saved to {saved}")
        return True

    def reject(self) -> None:
        if self.modified:
            answer = QMessageBox.question(
                self, "Unsaved changes", "Save the changes to a new hierarchy file before closing?",
                QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
            )
            if answer == QMessageBox.Cancel or (answer == QMessageBox.Save and not self._on_save_as()):
                return
        super().reject()
