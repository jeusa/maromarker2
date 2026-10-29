"""Tree view over a Taxonomy with add/rename/remove of group, subgroup and species nodes."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QAbstractItemView, QTreeWidget, QTreeWidgetItem

from maromarker2.models.taxonomy import Taxonomy, TaxonomyNode

_NODE_ROLE = Qt.UserRole


class HierarchyTreeWidget(QTreeWidget):
    """Edits the ``Taxonomy`` it was given in place; emits ``changed`` after every edit.

    Raises ``ValueError`` from the editing methods on invalid input (empty/duplicate names,
    ranks that don't fit under the selected node)."""

    changed = Signal()

    def __init__(self, taxonomy: Taxonomy | None = None, parent=None):
        super().__init__(parent)
        self.setHeaderLabels(["Name", "Rank"])
        self.setColumnWidth(0, 320)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.taxonomy = taxonomy if taxonomy is not None else Taxonomy()
        self.refresh(expand_all=True)

    def set_taxonomy(self, taxonomy: Taxonomy) -> None:
        self.taxonomy = taxonomy
        self.refresh(expand_all=True)

    def mousePressEvent(self, event) -> None:
        if self.itemAt(event.position().toPoint()) is None:
            self.clearSelection()
            self.setCurrentItem(None)
        super().mousePressEvent(event)

    # --- selection --------------------------------------------------------- #
    def selected_node(self) -> TaxonomyNode | None:
        items = self.selectedItems()
        return items[0].data(0, _NODE_ROLE) if items else None

    def select_node(self, node: TaxonomyNode) -> None:
        item = self._item_of(node)
        if item is not None:
            self.setCurrentItem(item)

    def _all_items(self) -> list[QTreeWidgetItem]:
        items: list[QTreeWidgetItem] = []
        stack = [self.topLevelItem(i) for i in range(self.topLevelItemCount())]
        while stack:
            item = stack.pop()
            items.append(item)
            stack.extend(item.child(i) for i in range(item.childCount()))
        return items

    def _item_of(self, node: TaxonomyNode) -> QTreeWidgetItem | None:
        return next((item for item in self._all_items() if item.data(0, _NODE_ROLE) is node), None)

    def _parent_for_add(self) -> TaxonomyNode:
        """The node new entries are added under: the selection, or the root if none."""
        return self.selected_node() or self.taxonomy.root

    def can_add(self, rank: str) -> bool:
        return rank in self.taxonomy.allowed_child_ranks(self._parent_for_add())

    # --- editing ----------------------------------------------------------- #
    def add_node(self, name: str, rank: str) -> TaxonomyNode:
        node = self.taxonomy.add_node(self._parent_for_add(), name, rank)
        self._after_edit(select=node)
        return node

    def rename_selected(self, new_name: str) -> None:
        node = self.selected_node()
        if node is None:
            raise ValueError("Nothing is selected.")
        self.taxonomy.rename_tree_node(node, new_name)
        self._after_edit(select=node)

    def remove_selected(self) -> None:
        node = self.selected_node()
        if node is None:
            raise ValueError("Nothing is selected.")
        parent = node.parent
        self.taxonomy.remove_tree_node(node)
        self._after_edit(select=parent if parent is not self.taxonomy.root else None)

    def _after_edit(self, select: TaxonomyNode | None) -> None:
        self.refresh(select=select)
        self.changed.emit()

    # --- display ----------------------------------------------------------- #
    def refresh(self, select: TaxonomyNode | None = None, expand_all: bool = False) -> None:
        """Rebuilds the tree, keeping the expansion state; selects (and reveals) ``select``."""
        expanded = {id(item.data(0, _NODE_ROLE)) for item in self._all_items() if item.isExpanded()}
        self.clear()
        self._populate(self.invisibleRootItem(), self.taxonomy.root, expanded, expand_all)
        if select is not None:
            node = select
            while node.parent is not None:
                node = node.parent
                item = self._item_of(node)
                if item is not None:
                    item.setExpanded(True)
            self.select_node(select)

    def _populate(
        self, parent_item: QTreeWidgetItem, node: TaxonomyNode, expanded: set[int], expand_all: bool
    ) -> None:
        for child in node.children.values():
            item = QTreeWidgetItem(parent_item, [child.name, child.rank])
            item.setData(0, _NODE_ROLE, child)
            self._populate(item, child, expanded, expand_all)
            if expand_all or id(child) in expanded:
                item.setExpanded(True)
