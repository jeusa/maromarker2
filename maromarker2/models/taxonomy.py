"""Editable group/subgroup/species taxonomy for annotation.

YAML schema: every node names its ``rank`` explicitly and lists its ``children``
(optional). Top-level nodes are ``group`` or ``species`` (a species directly under
the root has no group); a group's children are ``subgroup`` or ``species`` nodes; a
subgroup's children are species. A plain list of names under ``children`` means
"these are all species"; a mapping holds named nodes that each carry their own rank::

    jellyfish:
      rank: group
      children:
        ctenophora:
          rank: subgroup
          children: [beroe_sp, mertensia_ovum]
    fish:
      rank: group
      children: [gadus_morhua, boreogadus_saida]
    chaetognatha:
      rank: group
    apendicularia_sp:
      rank: species

Deliberately does not import ``underwater_detection.uyolo`` (that package's
``__init__.py`` pulls in ultralytics/torch) — this is a small, independent
reimplementation using PyYAML.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path
from typing import Optional

import yaml

DEFAULT_HIERARCHY_PATH = Path(__file__).parent.parent / "configs" / "hierarchy_full.yaml"

ROOT_NAME = "animal"

# shared pseudo-group/subgroup/species value meaning "not (yet) classified"; not part of
# the taxonomy tree itself, offered alongside real entries in the editor UI.
UNIDENTIFIED = "unidentified"

# ranks a node may have below a given parent rank
_ALLOWED_CHILD_RANKS = {
    "root": ("group", "species"),
    "group": ("subgroup", "species"),
    "subgroup": ("species",),
}


def _normalize_class(value: object) -> str:
    text = str(value or "").strip().lower()
    return "" if text == UNIDENTIFIED else text


def _merge_class_value(a: object, b: object) -> str | None:
    """Merges two group (or subgroup/species) values where "unidentified"/empty is a wildcard.
    Returns the more specific value (``a`` if both are wildcards), or None if they conflict."""
    norm_a, norm_b = _normalize_class(a), _normalize_class(b)
    if norm_a and norm_b and norm_a != norm_b:
        return None
    return b if norm_b and not norm_a else a


def merge_classes(
    group_a: object, subgroup_a: object, species_a: object,
    group_b: object, subgroup_b: object, species_b: object,
) -> tuple[str, str, str] | None:
    """Combined (group, subgroup, species) of two animals being matched, or None if their
    classes disagree. "unidentified" agrees with anything and the deepest annotation wins."""
    group = _merge_class_value(group_a, group_b)
    subgroup = _merge_class_value(subgroup_a, subgroup_b)
    species = _merge_class_value(species_a, species_b)
    if group is None or subgroup is None or species is None:
        return None
    return str(group or ""), str(subgroup or ""), str(species or "")


class TaxonomyNode:
    def __init__(self, name: str, rank: str, parent: Optional["TaxonomyNode"] = None):
        self.name = name
        self.rank = rank  # "root" | "group" | "subgroup" | "species"
        self.parent = parent
        self.children: dict[str, "TaxonomyNode"] = {}

    @property
    def is_leaf(self) -> bool:
        return not self.children

    def add_child(self, name: str, rank: str) -> "TaxonomyNode":
        node = TaxonomyNode(name, rank, parent=self)
        self.children[name] = node
        return node

    def species_nodes(self) -> list["TaxonomyNode"]:
        """All species nodes at or below this node (an empty group has none)."""
        if self.rank == "species":
            return [self]
        result: list[TaxonomyNode] = []
        for child in self.children.values():
            result.extend(child.species_nodes())
        return result

    def ancestor_with_rank(self, rank: str) -> Optional["TaxonomyNode"]:
        node = self
        while node is not None:
            if node.rank == rank:
                return node
            node = node.parent
        return None


class Taxonomy:
    """An editable group -> subgroup -> species tree, loadable from/savable to YAML.

    A group may lead to subgroups, directly to species, to both, or to nothing; species
    may also sit directly under the root (they then have no group)."""

    def __init__(self):
        self.root = TaxonomyNode(ROOT_NAME, rank="root")
        self._species_index: dict[str, TaxonomyNode] = {}

    # --- loading / saving -------------------------------------------------- #
    @classmethod
    def from_dict(cls, hierarchy: dict) -> "Taxonomy":
        tree = cls()
        tree._parse_nodes(tree.root, hierarchy, "the hierarchy root")
        tree._reindex()
        return tree

    @classmethod
    def load(cls, path: str | os.PathLike) -> "Taxonomy":
        with open(path, "r") as fh:
            data = yaml.safe_load(fh) or {}
        return cls.from_dict(data)

    @classmethod
    def load_default(cls) -> "Taxonomy":
        return cls.load(DEFAULT_HIERARCHY_PATH)

    def to_dict(self) -> dict:
        return self._nodes_to_dict(self.root)

    def save(self, path: str | os.PathLike) -> None:
        with open(path, "w") as fh:
            yaml.safe_dump(self.to_dict(), fh, sort_keys=False)

    def _parse_nodes(self, parent: TaxonomyNode, nodes, where: str) -> None:
        """Parses the ``name -> {rank, children}`` mapping of ``parent``'s children."""
        allowed = _ALLOWED_CHILD_RANKS.get(parent.rank, ())
        if not isinstance(nodes, dict):
            raise ValueError(f"Expected a mapping of named nodes in {where}, got {type(nodes).__name__}")
        for name, spec in nodes.items():
            name = str(name)
            if not isinstance(spec, dict) or "rank" not in spec:
                raise ValueError(f"Node {name!r} in {where} must be a mapping with an explicit 'rank'")
            rank = spec["rank"]
            if rank not in allowed:
                raise ValueError(
                    f"Node {name!r} in {where} has rank {rank!r}; expected one of {', '.join(allowed) or 'none'}"
                )
            child = parent.add_child(name, rank=rank)
            self._parse_children(child, spec.get("children"))

    def _parse_children(self, node: TaxonomyNode, children) -> None:
        if not children:
            return
        if node.rank == "species":
            raise ValueError(f"Species {node.name!r} cannot have children")
        if isinstance(children, dict):
            self._parse_nodes(node, children, f"{node.rank} {node.name!r}")
            return
        # a plain list names species directly
        if not isinstance(children, (list, tuple)):
            raise ValueError(f"'children' of {node.rank} {node.name!r} must be a list or a mapping")
        for species_name in children:
            node.add_child(str(species_name), rank="species")

    def _nodes_to_dict(self, parent: TaxonomyNode) -> dict:
        return {name: self._node_to_value(child) for name, child in parent.children.items()}

    def _node_to_value(self, node: TaxonomyNode) -> dict:
        value: dict = {"rank": node.rank}
        if node.children:
            if all(child.rank == "species" and not child.children for child in node.children.values()):
                value["children"] = list(node.children.keys())
            else:
                value["children"] = self._nodes_to_dict(node)
        return value

    def _reindex(self) -> None:
        self._species_index = {node.name: node for node in self.root.species_nodes()}

    # --- queries -------------------------------------------------------- #
    def groups(self) -> list[str]:
        return [name for name, node in self.root.children.items() if node.rank == "group"]

    def _group(self, group_name: str) -> TaxonomyNode | None:
        node = self.root.children.get(group_name)
        return node if node is not None and node.rank == "group" else None

    def ungrouped_species(self) -> list[str]:
        """Species directly under the root, i.e. without a group."""
        return sorted(name for name, node in self.root.children.items() if node.rank == "species")

    def subgroups_in_group(self, group_name: str) -> list[str]:
        group = self._group(group_name)
        if group is None:
            return []
        return [name for name, node in group.children.items() if node.rank == "subgroup"]

    def has_subgroups(self, group_name: str) -> bool:
        return bool(self.subgroups_in_group(group_name))

    @property
    def has_subgroup_level(self) -> bool:
        """Whether any group of the loaded hierarchy has subgroups."""
        return any(self.has_subgroups(group) for group in self.groups())

    def species_in_group(self, group_name: str) -> list[str]:
        """All species below the group: those of every subgroup plus those attached directly."""
        group = self._group(group_name)
        if group is None:
            return []
        return sorted(node.name for node in group.species_nodes())

    def species_in_subgroup(self, group_name: str, subgroup_name: str) -> list[str]:
        subgroup = self._find_subgroup(group_name, subgroup_name)
        if subgroup is None:
            return []
        return sorted(node.name for node in subgroup.species_nodes())

    def all_species(self) -> list[str]:
        return sorted(self._species_index.keys())

    def group_of_species(self, species_name: str) -> str | None:
        node = self._species_index.get(species_name)
        if node is None:
            return None
        group = node.ancestor_with_rank("group")
        return group.name if group is not None else None

    def subgroup_of_species(self, species_name: str) -> str | None:
        node = self._species_index.get(species_name)
        if node is None:
            return None
        subgroup = node.ancestor_with_rank("subgroup")
        return subgroup.name if subgroup is not None else None

    def resolve_label_path(self, labels: Sequence[str]) -> tuple[str, str, str]:
        """(group, subgroup, species) for a top-down label path (e.g. from a detection model).

        The deepest label that names a taxonomy node wins and the levels above it are
        inferred from the tree; unresolved levels are ``UNIDENTIFIED``. A subgroup matches
        by its name if that is unique in the taxonomy, or as ``"<group>_<subgroup>"``."""
        subgroups = [node for group in self.root.children.values() for node in group.children.values()
                     if node.rank == "subgroup"]
        for label in reversed(list(labels)):
            node = self._species_index.get(label) or self._group(label)
            if node is None:
                node = next((s for s in subgroups if label == f"{s.parent.name}_{s.name}"), None)
            if node is None:
                same_name = [s for s in subgroups if s.name == label]
                node = same_name[0] if len(same_name) == 1 else None
            if node is None:
                continue
            group = node.ancestor_with_rank("group")
            subgroup = node.ancestor_with_rank("subgroup")
            return (
                group.name if group is not None else UNIDENTIFIED,
                subgroup.name if subgroup is not None else UNIDENTIFIED,
                node.name if node.rank == "species" else UNIDENTIFIED,
            )
        return UNIDENTIFIED, UNIDENTIFIED, UNIDENTIFIED

    def __contains__(self, name: str) -> bool:
        return name in self._species_index or name in self.root.children

    # --- editing -------------------------------------------------------- #
    def add_group(self, name: str) -> None:
        if name not in self.root.children:
            self.root.add_child(name, rank="group")

    def add_subgroup(self, group_name: str, name: str) -> None:
        group = self._group(group_name)
        if group is None:
            raise KeyError(f"Unknown group: {group_name}")
        if name not in group.children:
            group.add_child(name, rank="subgroup")

    def _find_subgroup(self, group_name: str | None, subgroup_name: str) -> Optional[TaxonomyNode]:
        group = self._group(group_name) if group_name is not None else None
        if group is None:
            return None
        node = group.children.get(subgroup_name)
        return node if node is not None and node.rank == "subgroup" else None

    def add_species(self, group_name: str, species_name: str, subgroup: str | None = None) -> None:
        """Add a species under ``group_name`` (and optionally a specific ``subgroup`` of it).

        ``group_name`` must always be given explicitly because subgroup names (e.g. "benthic",
        "pelagic") may be reused across multiple unrelated groups and would otherwise be ambiguous.
        """
        group = self._group(group_name)
        if group is None:
            raise KeyError(f"Unknown group: {group_name}")
        parent = group if subgroup is None else self._find_subgroup(group_name, subgroup)
        if parent is None:
            raise KeyError(f"Unknown subgroup: {subgroup!r} under group {group_name!r}")
        parent.add_child(species_name, rank="species")
        self._reindex()

    # node-based editing (used by the hierarchy editor, where the node is known exactly)
    def copy(self) -> "Taxonomy":
        return Taxonomy.from_dict(self.to_dict())

    @staticmethod
    def normalize_name(name: str) -> str:
        """Names are stored lowercase with underscores (matching the annotation CSV values)."""
        return "_".join(str(name).strip().lower().split())

    @staticmethod
    def allowed_child_ranks(node: TaxonomyNode) -> tuple[str, ...]:
        return _ALLOWED_CHILD_RANKS.get(node.rank, ())

    def _check_name(self, name: str, rank: str, parent: TaxonomyNode, ignore: TaxonomyNode | None = None) -> None:
        if not name:
            raise ValueError("The name must not be empty.")
        if name == UNIDENTIFIED:
            raise ValueError(f"{UNIDENTIFIED!r} is reserved.")
        if any(sibling.name == name and sibling is not ignore for sibling in parent.children.values()):
            raise ValueError(f"{parent.name!r} already contains {name!r}.")
        # a species is identified by its name alone in annotations, so it must be unique tree-wide
        if rank == "species" and self._species_index.get(name) not in (None, ignore):
            raise ValueError(f"The species {name!r} already exists.")

    def add_node(self, parent: TaxonomyNode, name: str, rank: str) -> TaxonomyNode:
        name = self.normalize_name(name)
        if rank not in self.allowed_child_ranks(parent):
            raise ValueError(f"A {parent.rank} cannot contain a {rank}.")
        self._check_name(name, rank, parent)
        node = parent.add_child(name, rank=rank)
        self._reindex()
        return node

    def remove_tree_node(self, node: TaxonomyNode) -> None:
        if node.parent is None:
            raise ValueError("The root cannot be removed.")
        del node.parent.children[node.name]
        node.parent = None
        self._reindex()

    def rename_tree_node(self, node: TaxonomyNode, new_name: str) -> None:
        if node.parent is None:
            raise ValueError("The root cannot be renamed.")
        new_name = self.normalize_name(new_name)
        self._check_name(new_name, node.rank, node.parent, ignore=node)
        # rebuild to keep the sibling order
        siblings = list(node.parent.children.values())
        node.name = new_name
        node.parent.children = {sibling.name: sibling for sibling in siblings}
        self._reindex()

    def remove_node(self, name: str, group: str | None = None) -> None:
        node = self._find_subgroup(group, name) if group is not None else None
        if node is None:
            node = self._species_index.get(name)
        if node is None:
            node = self.root.children.get(name)
        if node is None or node.parent is None:
            raise KeyError(f"Unknown node: {name}")
        del node.parent.children[name]
        self._reindex()

    def rename_node(self, old_name: str, new_name: str, group: str | None = None) -> None:
        node = self._find_subgroup(group, old_name) if group is not None else None
        if node is None:
            node = self._species_index.get(old_name) or self.root.children.get(old_name)
        if node is None:
            raise KeyError(f"Unknown node: {old_name}")
        parent = node.parent
        if parent is not None:
            del parent.children[old_name]
            node.name = new_name
            parent.children[new_name] = node
        self._reindex()
