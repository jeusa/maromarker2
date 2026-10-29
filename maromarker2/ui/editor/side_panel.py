"""Per-selected-animal editor: group/subgroup/species (backed by a Taxonomy) and remark."""

from __future__ import annotations

import pandas as pd
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QFormLayout, QWidget

from maromarker2.models.taxonomy import UNIDENTIFIED, Taxonomy
from maromarker2.ui.editor.remark_edit import RemarkEdit

MIXED_PLACEHOLDER = "(multiple values)"


def _class_value(value) -> str:
    return str(value or "").lower() or UNIDENTIFIED


class SidePanel(QWidget):
    group_changed = Signal(str, str, str)  # (group, subgroup, species): both reset with the group
    subgroup_changed = Signal(str, str)  # (subgroup, species): species is kept only if it fits the subgroup
    # (subgroup, species): subgroup follows the species' own subgroup; None leaves it unchanged
    species_changed = Signal(object, str)
    remark_changed = Signal(str)

    def __init__(self, taxonomy: Taxonomy, parent=None):
        super().__init__(parent)
        self.taxonomy = taxonomy
        self._panel_enabled = False

        self.combo_group = QComboBox()
        self.combo_group.addItems([UNIDENTIFIED] + taxonomy.groups())
        self.combo_subgroup = QComboBox()
        self.combo_species = QComboBox()
        self.edit_remark = RemarkEdit()

        form = QFormLayout(self)
        form.addRow("Group", self.combo_group)
        form.addRow("Subgroup", self.combo_subgroup)
        form.addRow("Species", self.combo_species)
        form.addRow("Remark", self.edit_remark)
        # hierarchies without a subgroup level don't need the field at all
        form.setRowVisible(self.combo_subgroup, taxonomy.has_subgroup_level)

        self._refresh_subgroups(UNIDENTIFIED)
        self._refresh_species(UNIDENTIFIED)
        self.set_enabled(False)

        self.combo_group.currentTextChanged.connect(self._on_group_changed)
        self.combo_subgroup.currentTextChanged.connect(self._on_subgroup_changed)
        self.combo_species.currentTextChanged.connect(self._on_species_changed)
        self.edit_remark.textEdited.connect(self._on_remark_edited)
        self.edit_remark.editingFinished.connect(self._on_remark_finished)
        self._remark_mixed = False

    def set_remark_options(self, options: list[str]) -> None:
        self.edit_remark.set_options(options)

    def set_enabled(self, enabled: bool) -> None:
        self._panel_enabled = enabled
        for widget in (self.combo_group, self.edit_remark):
            widget.setEnabled(enabled)
        self._update_availability()

    def _update_availability(self) -> None:
        """A subgroup/species selector with nothing but "unidentified" to offer has no
        choice to make, so it is disabled (showing that single option)."""
        self.combo_subgroup.setEnabled(self._panel_enabled and self.combo_subgroup.count() > 1)
        self.combo_species.setEnabled(self._panel_enabled and self.combo_species.count() > 1)

    def _subgroup_options(self, group: str) -> list[str]:
        if not group or group == UNIDENTIFIED:
            return [UNIDENTIFIED]
        return [UNIDENTIFIED] + self.taxonomy.subgroups_in_group(group)

    def _species_options(self, group: str) -> list[str]:
        # "" (mixed groups) offers nothing to pick; "unidentified" offers the species
        # that live directly under the root, which need no group.
        if not group:
            return [UNIDENTIFIED]
        if group == UNIDENTIFIED:
            return [UNIDENTIFIED] + self.taxonomy.ungrouped_species()
        return [UNIDENTIFIED] + self.taxonomy.species_in_group(group)

    def _refresh_subgroups(self, group: str, default_subgroup: str | None = None) -> None:
        was_blocked = self.combo_subgroup.signalsBlocked()
        self.combo_subgroup.blockSignals(True)
        self.combo_subgroup.clear()
        self.combo_subgroup.addItems(self._subgroup_options(group))
        if default_subgroup is not None:
            self.combo_subgroup.setCurrentText(default_subgroup)
        self.combo_subgroup.blockSignals(was_blocked)

    def _refresh_species(
        self, group: str, subgroup: str = UNIDENTIFIED, default_species: str | None = None,
    ) -> None:
        """Lists the species of the group; with a subgroup chosen, species outside of it are
        shown disabled."""
        was_blocked = self.combo_species.signalsBlocked()
        self.combo_species.blockSignals(True)
        self.combo_species.clear()
        options = self._species_options(group)
        self.combo_species.addItems(options)
        if group and group != UNIDENTIFIED and subgroup and subgroup != UNIDENTIFIED:
            allowed = set(self.taxonomy.species_in_subgroup(group, subgroup)) | {UNIDENTIFIED}
            model = self.combo_species.model()
            for row, name in enumerate(options):
                if name not in allowed:
                    model.item(row).setEnabled(False)
        if default_species is not None:
            self.combo_species.setCurrentText(default_species)
        self.combo_species.blockSignals(was_blocked)

    def _on_group_changed(self, group: str) -> None:
        # a user-driven group change always resets subgroup and species to "unidentified"
        # first, rather than keeping stale values from the previously selected group.
        # Emitted as one signal so it becomes a single (valid) undo step.
        self._refresh_subgroups(group, default_subgroup=UNIDENTIFIED)
        self._refresh_species(group, default_species=UNIDENTIFIED)
        self._update_availability()
        self.group_changed.emit(group, UNIDENTIFIED, UNIDENTIFIED)

    def _on_subgroup_changed(self, subgroup: str) -> None:
        group = self.combo_group.currentText()
        species = self.combo_species.currentText()
        if self.combo_species.currentIndex() < 0:  # mixed species across the selection
            species = UNIDENTIFIED
        elif species != UNIDENTIFIED:
            if subgroup == UNIDENTIFIED:
                # a species inside a subgroup implies that subgroup; only species directly
                # under the group are compatible with an unidentified subgroup
                if self.taxonomy.subgroup_of_species(species):
                    species = UNIDENTIFIED
            elif species not in self.taxonomy.species_in_subgroup(group, subgroup):
                species = UNIDENTIFIED
        self._refresh_species(group, subgroup, default_species=species)
        self.subgroup_changed.emit(subgroup, species)

    def _on_species_changed(self, species: str) -> None:
        subgroup = None
        if species != UNIDENTIFIED:
            # a species fixes its subgroup (none if it hangs directly under its group)
            subgroup = self.taxonomy.subgroup_of_species(species) or UNIDENTIFIED
            self._set_subgroup_text(subgroup)
        elif self.combo_subgroup.currentIndex() >= 0:
            subgroup = self.combo_subgroup.currentText()
        self.species_changed.emit(subgroup, species)

    def _set_subgroup_text(self, subgroup: str) -> None:
        was_blocked = self.combo_subgroup.signalsBlocked()
        self.combo_subgroup.blockSignals(True)
        self.combo_subgroup.setCurrentText(subgroup)
        self.combo_subgroup.blockSignals(was_blocked)

    def _on_remark_edited(self, _text: str) -> None:
        self._remark_mixed = False

    def _on_remark_finished(self) -> None:
        # an untouched "(multiple values)" field must not overwrite every selected remark
        if not self._remark_mixed:
            self.remark_changed.emit(self.edit_remark.text())

    def _block_field_signals(self, blocked: bool) -> None:
        for widget in (self.combo_group, self.combo_subgroup, self.combo_species, self.edit_remark):
            widget.blockSignals(blocked)

    def _reset_mixed_state(self) -> None:
        self._remark_mixed = False
        self.edit_remark.setPlaceholderText("")

    def set_animal(self, row: pd.Series | None) -> None:
        """Populates the fields from a store row without emitting change signals."""
        self._reset_mixed_state()
        if row is None:
            self.set_enabled(False)
            return

        self.set_enabled(True)
        self._block_field_signals(True)

        self.combo_group.setCurrentText(_class_value(row.get("group")))
        group = self.combo_group.currentText()
        self._refresh_subgroups(group, default_subgroup=_class_value(row.get("subgroup")))
        self._refresh_species(
            group, self.combo_subgroup.currentText(), default_species=_class_value(row.get("species")),
        )
        self._update_availability()
        self.edit_remark.setText(str(row.get("object_remarks") or ""))

        self._block_field_signals(False)

    @staticmethod
    def _common_value(values) -> str | None:
        """The single value shared by all entries, or None if they differ."""
        unique = set(values)
        return unique.pop() if len(unique) == 1 else None

    def set_animals(self, rows: pd.DataFrame) -> None:
        """Populates the fields from one or more store rows without emitting change
        signals. Fields whose values differ between the rows are shown as blank /
        "(multiple values)" / partially checked until the user picks a value, which
        is then applied to every row."""
        if len(rows) <= 1:
            self.set_animal(rows.iloc[0] if len(rows) == 1 else None)
            return

        self._reset_mixed_state()
        self.set_enabled(True)
        self._block_field_signals(True)

        group = self._common_value(_class_value(v) for v in rows["group"])
        if group is None:
            self.combo_group.setCurrentIndex(-1)
            self._refresh_subgroups("")
            self.combo_subgroup.setCurrentIndex(-1)
            self._refresh_species("")
            self.combo_species.setCurrentIndex(-1)
        else:
            self.combo_group.setCurrentText(group)
            group = self.combo_group.currentText()
            subgroup = self._common_value(_class_value(v) for v in rows["subgroup"])
            self._refresh_subgroups(group, default_subgroup=subgroup)
            if subgroup is None:
                self.combo_subgroup.setCurrentIndex(-1)
            self._refresh_species(group, subgroup or UNIDENTIFIED)
            species = self._common_value(_class_value(v) for v in rows["species"])
            if species is None:
                self.combo_species.setCurrentIndex(-1)
            else:
                self.combo_species.setCurrentText(species)
        self._update_availability()

        remark = self._common_value(str(v or "") for v in rows["object_remarks"])
        self.edit_remark.setText(remark or "")
        if remark is None:
            self._remark_mixed = True
            self.edit_remark.setPlaceholderText(MIXED_PLACEHOLDER)

        self._block_field_signals(False)
