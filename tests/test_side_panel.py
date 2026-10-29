import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

import pandas as pd
from PySide6.QtWidgets import QApplication

from maromarker2.models.taxonomy import Taxonomy
from maromarker2.ui.editor.side_panel import SidePanel

SAMPLE_HIERARCHY = {
    "fish": {
        "rank": "group",
        "children": {
            "benthic": {"rank": "subgroup", "children": ["blenidae_sp"]},
            "pelagic": {"rank": "subgroup", "children": ["gadus_morhua", "boreogadus_saida"]},
            "loose_sp": {"rank": "species"},
        },
    },
    "jellyfish": {"rank": "group", "children": ["beroe_sp"]},
    "chaetognatha": {"rank": "group"},
    "apendicularia_sp": {"rank": "species"},
}


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def panel(qapp):
    panel = SidePanel(Taxonomy.from_dict(SAMPLE_HIERARCHY))
    panel.set_enabled(True)
    return panel


def _items(combo):
    return [combo.itemText(i) for i in range(combo.count())]


def _enabled_items(combo):
    return [combo.itemText(i) for i in range(combo.count()) if combo.model().item(i).isEnabled()]


def _row(**fields):
    base = {"group": "", "subgroup": "", "species": "", "object_remarks": ""}
    base.update(fields)
    return pd.Series(base)


def test_group_with_subgroups_enables_subgroup_and_offers_all_species_at_once(panel):
    panel.combo_group.setCurrentText("fish")

    assert panel.combo_subgroup.isEnabled()
    assert _items(panel.combo_subgroup) == ["unidentified", "benthic", "pelagic"]
    assert panel.combo_subgroup.currentText() == "unidentified"
    assert panel.combo_species.currentText() == "unidentified"
    # every species below the group is selectable without choosing a subgroup first
    assert _enabled_items(panel.combo_species) == _items(panel.combo_species)
    assert _items(panel.combo_species) == [
        "unidentified", "blenidae_sp", "boreogadus_saida", "gadus_morhua", "loose_sp",
    ]


def test_group_without_subgroups_disables_subgroup_field(panel):
    panel.combo_group.setCurrentText("fish")
    panel.combo_group.setCurrentText("jellyfish")

    assert not panel.combo_subgroup.isEnabled()
    assert panel.combo_subgroup.currentText() == "unidentified"
    assert _items(panel.combo_species) == ["unidentified", "beroe_sp"]
    assert panel.combo_species.isEnabled()


def test_group_without_children_only_offers_unidentified(panel):
    panel.combo_group.setCurrentText("chaetognatha")

    assert not panel.combo_subgroup.isEnabled()
    assert _items(panel.combo_species) == ["unidentified"]
    # nothing to choose from: the single option is selected and the selector disabled
    assert panel.combo_species.currentText() == "unidentified"
    assert not panel.combo_species.isEnabled()


def test_species_selector_disabled_without_group_when_no_root_species(qapp):
    hierarchy = {k: v for k, v in SAMPLE_HIERARCHY.items() if k != "apendicularia_sp"}
    panel = SidePanel(Taxonomy.from_dict(hierarchy))
    panel.set_enabled(True)

    assert panel.combo_group.isEnabled()
    assert _items(panel.combo_species) == ["unidentified"]
    assert panel.combo_species.currentText() == "unidentified"
    assert not panel.combo_species.isEnabled()
    assert not panel.combo_subgroup.isEnabled()

    panel.combo_group.setCurrentText("jellyfish")
    assert panel.combo_species.isEnabled()

    panel.combo_group.setCurrentText("unidentified")
    assert not panel.combo_species.isEnabled()


def test_set_animal_without_options_disables_species_selector(qapp):
    hierarchy = {k: v for k, v in SAMPLE_HIERARCHY.items() if k != "apendicularia_sp"}
    panel = SidePanel(Taxonomy.from_dict(hierarchy))

    panel.set_animal(_row())

    assert panel.combo_group.isEnabled()
    assert panel.combo_species.currentText() == "unidentified"
    assert not panel.combo_species.isEnabled()
    panel.set_animal(_row(group="chaetognatha"))
    assert not panel.combo_species.isEnabled()
    panel.set_animal(_row(group="jellyfish"))
    assert panel.combo_species.isEnabled()


def test_unidentified_group_offers_root_species_directly(panel):
    assert panel.combo_group.currentText() == "unidentified"
    assert not panel.combo_subgroup.isEnabled()
    assert _items(panel.combo_species) == ["unidentified", "apendicularia_sp"]
    assert _enabled_items(panel.combo_species) == _items(panel.combo_species)


def test_choosing_subgroup_disables_species_outside_of_it(panel):
    panel.combo_group.setCurrentText("fish")
    panel.combo_subgroup.setCurrentText("pelagic")

    assert _enabled_items(panel.combo_species) == ["unidentified", "boreogadus_saida", "gadus_morhua"]
    assert len(_items(panel.combo_species)) == 5  # the others stay listed, just disabled

    panel.combo_subgroup.setCurrentText("unidentified")
    assert _enabled_items(panel.combo_species) == _items(panel.combo_species)


def test_choosing_subgroup_keeps_species_only_if_it_fits(panel):
    received = []
    panel.subgroup_changed.connect(lambda sg, s: received.append((sg, s)))
    panel.combo_group.setCurrentText("fish")

    panel.combo_subgroup.setCurrentText("pelagic")
    panel.combo_species.setCurrentText("gadus_morhua")
    assert panel.combo_species.currentText() == "gadus_morhua"

    panel.combo_subgroup.setCurrentText("benthic")
    assert panel.combo_species.currentText() == "unidentified"
    assert received == [("pelagic", "unidentified"), ("benthic", "unidentified")]


def test_unidentified_subgroup_resets_species_of_a_subgroup(panel):
    received = []
    panel.subgroup_changed.connect(lambda sg, s: received.append((sg, s)))
    panel.combo_group.setCurrentText("fish")
    panel.combo_species.setCurrentText("gadus_morhua")  # also fills in subgroup "pelagic"

    panel.combo_subgroup.setCurrentText("unidentified")

    assert panel.combo_species.currentText() == "unidentified"
    assert received == [("unidentified", "unidentified")]


def test_unidentified_subgroup_keeps_species_directly_under_group(panel):
    received = []
    panel.subgroup_changed.connect(lambda sg, s: received.append((sg, s)))
    panel.combo_group.setCurrentText("fish")
    panel.combo_species.setCurrentText("loose_sp")
    assert panel.combo_subgroup.currentText() == "unidentified"

    panel.combo_subgroup.setCurrentText("pelagic")
    assert panel.combo_species.currentText() == "unidentified"
    received.clear()
    panel.combo_species.setCurrentText("loose_sp")
    panel._on_subgroup_changed("unidentified")

    assert panel.combo_species.currentText() == "loose_sp"
    assert received == [("unidentified", "loose_sp")]


def test_choosing_species_fills_in_its_subgroup(panel):
    received = []
    panel.species_changed.connect(lambda sg, s: received.append((sg, s)))
    panel.combo_group.setCurrentText("fish")

    panel.combo_species.setCurrentText("gadus_morhua")
    assert panel.combo_subgroup.currentText() == "pelagic"

    panel.combo_species.setCurrentText("loose_sp")  # directly under the group: no subgroup
    assert panel.combo_subgroup.currentText() == "unidentified"
    assert received == [("pelagic", "gadus_morhua"), ("unidentified", "loose_sp")]


def test_choosing_unidentified_species_keeps_subgroup(panel):
    received = []
    panel.species_changed.connect(lambda sg, s: received.append((sg, s)))
    panel.combo_group.setCurrentText("fish")
    panel.combo_subgroup.setCurrentText("pelagic")
    panel.combo_species.setCurrentText("gadus_morhua")
    received.clear()

    panel.combo_species.setCurrentText("unidentified")

    assert panel.combo_subgroup.currentText() == "pelagic"
    assert received == [("pelagic", "unidentified")]


def test_set_animal_populates_fields_without_emitting_signals(panel):
    received = []
    panel.group_changed.connect(lambda g, sg, s: received.append(g))
    panel.subgroup_changed.connect(lambda sg, s: received.append(sg))
    panel.species_changed.connect(lambda sg, s: received.append(s))

    panel.set_animal(_row(group="fish", subgroup="pelagic", species="gadus_morhua", object_remarks="note"))

    assert panel.combo_group.currentText() == "fish"
    assert panel.combo_subgroup.currentText() == "pelagic"
    assert panel.combo_subgroup.isEnabled()
    assert panel.combo_species.currentText() == "gadus_morhua"
    assert panel.edit_remark.text() == "note"
    assert received == []


def test_set_animal_for_group_without_subgroups_disables_subgroup(panel):
    panel.set_animal(_row(group="jellyfish", species="beroe_sp"))

    assert not panel.combo_subgroup.isEnabled()
    assert panel.combo_subgroup.currentText() == "unidentified"
    assert panel.combo_species.currentText() == "beroe_sp"


def test_set_animal_with_root_species(panel):
    panel.set_animal(_row(species="apendicularia_sp"))

    assert panel.combo_group.currentText() == "unidentified"
    assert panel.combo_species.currentText() == "apendicularia_sp"
    assert not panel.combo_subgroup.isEnabled()


def test_set_animal_none_disables_fields(panel):
    panel.set_animal(None)
    assert not panel.combo_group.isEnabled()
    assert not panel.combo_subgroup.isEnabled()
    assert not panel.combo_species.isEnabled()


def test_set_animals_shows_mixed_subgroups_and_species(panel):
    rows = pd.DataFrame([
        _row(group="fish", subgroup="pelagic", species="gadus_morhua"),
        _row(group="fish", subgroup="benthic", species="blenidae_sp"),
    ])
    panel.set_animals(rows)

    assert panel.combo_group.currentText() == "fish"
    assert panel.combo_subgroup.isEnabled()
    assert panel.combo_subgroup.currentIndex() == -1
    assert panel.combo_species.currentIndex() == -1


def test_set_animals_with_mixed_groups_disables_subgroup(panel):
    rows = pd.DataFrame([_row(group="fish"), _row(group="jellyfish")])
    panel.set_animals(rows)

    assert panel.combo_group.currentIndex() == -1
    assert not panel.combo_subgroup.isEnabled()
    assert panel.combo_subgroup.currentIndex() == -1
    assert not panel.combo_species.isEnabled()  # only "unidentified" could be offered


def test_unidentified_species_with_mixed_subgroups_leaves_subgroup_unchanged(panel):
    received = []
    panel.species_changed.connect(lambda sg, s: received.append((sg, s)))
    panel.set_animals(pd.DataFrame([
        _row(group="fish", subgroup="pelagic", species="gadus_morhua"),
        _row(group="fish", subgroup="benthic", species="blenidae_sp"),
    ]))

    panel.combo_species.setCurrentText("unidentified")

    assert received == [(None, "unidentified")]


def test_group_changed_signal_emitted_on_user_change(panel):
    received = []
    other_received = []
    panel.group_changed.connect(lambda g, sg, s: received.append((g, sg, s)))
    panel.subgroup_changed.connect(lambda sg, s: other_received.append(sg))
    panel.species_changed.connect(lambda sg, s: other_received.append(s))

    panel.combo_group.setCurrentText("jellyfish")

    assert received == [("jellyfish", "unidentified", "unidentified")]
    assert other_received == []  # group + subgroup + species reset arrive as one signal


def test_subgroup_row_hidden_when_hierarchy_has_no_subgroup_level(qapp):
    flat = SidePanel(Taxonomy.from_dict({"fish": {"rank": "group", "children": ["gadus_morhua"]}}))
    flat.show()
    assert not flat.combo_subgroup.isVisible()

    nested = SidePanel(Taxonomy.from_dict(SAMPLE_HIERARCHY))
    nested.show()
    assert nested.combo_subgroup.isVisible()
