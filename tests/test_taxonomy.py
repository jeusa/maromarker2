import pytest

from maromarker2.models.taxonomy import Taxonomy, merge_classes

SAMPLE = {
    "fish": {
        "rank": "group",
        "children": {
            "benthic": {"rank": "subgroup", "children": ["blenidae_sp"]},
            "pelagic": {"rank": "subgroup", "children": ["gadus_morhua", "boreogadus_saida"]},
        },
    },
    "jellyfish": {"rank": "group", "children": ["beroe_sp", "cyanea_capillata"]},
    "chaetognatha": {"rank": "group"},
    "apendicularia_sp": {"rank": "species"},
}


def test_from_dict_builds_groups_and_species():
    tax = Taxonomy.from_dict(SAMPLE)

    assert tax.groups() == ["fish", "jellyfish", "chaetognatha"]
    assert set(tax.species_in_group("fish")) == {"blenidae_sp", "gadus_morhua", "boreogadus_saida"}
    assert set(tax.species_in_group("jellyfish")) == {"beroe_sp", "cyanea_capillata"}
    assert tax.species_in_group("chaetognatha") == []
    assert "gadus_morhua" in tax


def test_subgroup_queries():
    tax = Taxonomy.from_dict(SAMPLE)

    assert tax.has_subgroup_level
    assert tax.subgroups_in_group("fish") == ["benthic", "pelagic"]
    assert tax.has_subgroups("fish")
    assert not tax.has_subgroups("jellyfish")
    assert not tax.has_subgroups("chaetognatha")
    assert tax.species_in_subgroup("fish", "pelagic") == ["boreogadus_saida", "gadus_morhua"]
    assert tax.subgroup_of_species("gadus_morhua") == "pelagic"
    assert tax.subgroup_of_species("beroe_sp") is None


def test_hierarchy_without_subgroups_has_no_subgroup_level():
    tax = Taxonomy.from_dict({"fish": {"rank": "group", "children": ["gadus_morhua"]}})
    assert not tax.has_subgroup_level


def test_species_without_group_is_listed_separately():
    tax = Taxonomy.from_dict(SAMPLE)

    assert tax.ungrouped_species() == ["apendicularia_sp"]
    assert "apendicularia_sp" in tax.all_species()
    assert "apendicularia_sp" not in tax.groups()
    assert tax.group_of_species("apendicularia_sp") is None


def test_group_can_mix_subgroups_and_direct_species():
    tax = Taxonomy.from_dict({
        "fish": {
            "rank": "group",
            "children": {
                "pelagic": {"rank": "subgroup", "children": ["gadus_morhua"]},
                "loose_sp": {"rank": "species"},
            },
        },
    })

    assert set(tax.species_in_group("fish")) == {"gadus_morhua", "loose_sp"}
    assert tax.subgroups_in_group("fish") == ["pelagic"]
    assert tax.subgroup_of_species("loose_sp") is None


def test_group_of_species_resolves_through_subgroup():
    tax = Taxonomy.from_dict(SAMPLE)
    assert tax.group_of_species("gadus_morhua") == "fish"
    assert tax.group_of_species("beroe_sp") == "jellyfish"
    assert tax.group_of_species("unknown") is None


def test_to_dict_round_trips_structure():
    tax = Taxonomy.from_dict(SAMPLE)

    assert tax.to_dict() == SAMPLE
    assert Taxonomy.from_dict(tax.to_dict()).to_dict() == SAMPLE


def test_save_and_load_round_trip(tmp_path):
    tax = Taxonomy.from_dict(SAMPLE)
    path = tmp_path / "hierarchy.yaml"
    tax.save(path)

    assert Taxonomy.load(path).to_dict() == SAMPLE


@pytest.mark.parametrize("hierarchy", [
    {"fish": ["gadus_morhua"]},  # legacy format: no explicit rank
    {"fish": {"benthic": ["blenidae_sp"]}},  # legacy nested format
    {"fish": {"rank": "subgroup"}},  # subgroup directly under the root
    {"fish": {"rank": "group", "children": {"x": {"rank": "group"}}}},  # group inside a group
    {"fish": {"rank": "group", "children": {"x": {"rank": "subgroup", "children": {"y": {"rank": "subgroup"}}}}}},
    {"fish_sp": {"rank": "species", "children": ["x"]}},  # species with children
])
def test_invalid_hierarchies_are_rejected(hierarchy):
    with pytest.raises(ValueError):
        Taxonomy.from_dict(hierarchy)


def test_add_rename_remove_species():
    tax = Taxonomy.from_dict(SAMPLE)

    tax.add_species("jellyfish", "mnemiopsis_leidyi")
    assert "mnemiopsis_leidyi" in tax.species_in_group("jellyfish")

    tax.rename_node("mnemiopsis_leidyi", "mnemiopsis_leidyi_renamed")
    assert "mnemiopsis_leidyi_renamed" in tax.all_species()
    assert "mnemiopsis_leidyi" not in tax.all_species()

    tax.remove_node("mnemiopsis_leidyi_renamed")
    assert "mnemiopsis_leidyi_renamed" not in tax.all_species()


def test_add_group_and_subgroup():
    tax = Taxonomy.from_dict(SAMPLE)
    tax.add_group("crustacea")
    tax.add_subgroup("crustacea", "pelagic")
    tax.add_species("crustacea", "mysis_oculata", subgroup="pelagic")

    assert "crustacea" in tax.groups()
    assert tax.group_of_species("mysis_oculata") == "crustacea"
    assert tax.subgroup_of_species("mysis_oculata") == "pelagic"


def test_add_species_ambiguous_subgroup_name_requires_explicit_group():
    tax = Taxonomy.from_dict(SAMPLE)
    tax.add_group("crustacea")
    tax.add_subgroup("crustacea", "pelagic")

    tax.add_species("fish", "new_fish_species", subgroup="pelagic")
    tax.add_species("crustacea", "new_crustacean_species", subgroup="pelagic")

    assert tax.group_of_species("new_fish_species") == "fish"
    assert tax.group_of_species("new_crustacean_species") == "crustacea"


def test_load_default_bundled_hierarchy():
    tax = Taxonomy.load_default()

    assert tax.groups() == ["jellyfish", "fish", "crustacea", "mollusca", "chaetognatha", "tunicata"]
    assert tax.has_subgroup_level
    assert tax.subgroups_in_group("jellyfish") == ["ctenophora", "hydrozoa", "real_jellyfish"]
    assert tax.subgroups_in_group("fish") == ["benthic", "pelagic"]
    assert tax.subgroup_of_species("gadus_morhua") == "pelagic"
    assert tax.group_of_species("apendicularia_sp") == "tunicata"
    assert "chaetognatha_sp" in tax.species_in_group("chaetognatha")
    assert tax.ungrouped_species() == []
    assert Taxonomy.from_dict(tax.to_dict()).to_dict() == tax.to_dict()


def test_merge_classes_unidentified_is_wildcard_and_deepest_wins():
    assert merge_classes("fish", "unidentified", "unidentified", "fish", "pelagic", "gadus_morhua") == (
        "fish", "pelagic", "gadus_morhua",
    )
    assert merge_classes("fish", "pelagic", "gadus_morhua", "fish", "", "") == ("fish", "pelagic", "gadus_morhua")
    assert merge_classes("unidentified", "", "unidentified", "fish", "", "unidentified") == (
        "fish", "", "unidentified",
    )
    assert merge_classes("", "", "", "", "", "") == ("", "", "")


def test_merge_classes_rejects_differing_classes():
    assert merge_classes("fish", "", "gadus_morhua", "jellyfish", "", "unidentified") is None
    assert merge_classes("fish", "pelagic", "", "fish", "benthic", "") is None
    assert merge_classes("fish", "", "gadus_morhua", "fish", "", "boreogadus_saida") is None


def test_resolve_label_path_species_infers_higher_levels():
    tax = Taxonomy.from_dict(SAMPLE)

    assert tax.resolve_label_path(["fish", "pelagic", "gadus_morhua"]) == ("fish", "pelagic", "gadus_morhua")
    assert tax.resolve_label_path(["gadus_morhua"]) == ("fish", "pelagic", "gadus_morhua")
    assert tax.resolve_label_path(["jellyfish", "beroe_sp"]) == ("jellyfish", "unidentified", "beroe_sp")
    assert tax.resolve_label_path(["apendicularia_sp"]) == ("unidentified", "unidentified", "apendicularia_sp")


def test_resolve_label_path_group_and_group_prefixed_subgroup():
    tax = Taxonomy.from_dict(SAMPLE)
    tax.add_group("crustacea")
    tax.add_subgroup("crustacea", "pelagic")

    assert tax.resolve_label_path(["jellyfish"]) == ("jellyfish", "unidentified", "unidentified")
    # "pelagic" exists under two groups, so only the group-prefixed name identifies it
    assert tax.resolve_label_path(["fish", "fish_pelagic"]) == ("fish", "pelagic", "unidentified")
    assert tax.resolve_label_path(["crustacea_pelagic"]) == ("crustacea", "pelagic", "unidentified")
    assert tax.resolve_label_path(["pelagic"]) == ("unidentified", "unidentified", "unidentified")
    assert tax.resolve_label_path(["benthic"]) == ("fish", "benthic", "unidentified")


def test_resolve_label_path_keeps_deepest_known_level_and_marks_unknown_unidentified():
    tax = Taxonomy.from_dict(SAMPLE)

    assert tax.resolve_label_path(["fish", "fish_unknown", "unknown_species"]) == (
        "fish", "unidentified", "unidentified",
    )
    assert tax.resolve_label_path(["something", "else"]) == ("unidentified", "unidentified", "unidentified")
    assert tax.resolve_label_path([]) == ("unidentified", "unidentified", "unidentified")
