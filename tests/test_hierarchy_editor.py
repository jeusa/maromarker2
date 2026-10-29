"""Tests for the hierarchy editor (tree widget, dialog, main window integration)."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

PySide6 = pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QMessageBox

from maromarker2.models.project_config import ProjectConfig
from maromarker2.models.taxonomy import DEFAULT_HIERARCHY_PATH, Taxonomy
from maromarker2.ui.hierarchy_editor.hierarchy_editor_dialog import HierarchyEditorDialog
from maromarker2.ui.hierarchy_editor.hierarchy_tree_widget import HierarchyTreeWidget
from maromarker2.ui.main_window import MainWindow

SAMPLE = {
    "fish": {
        "rank": "group",
        "children": {
            "benthic": {"rank": "subgroup", "children": ["blenidae_sp"]},
            "pelagic": {"rank": "subgroup", "children": ["gadus_morhua"]},
        },
    },
    "jellyfish": {"rank": "group", "children": ["beroe_sp"]},
}


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def settings(tmp_path, monkeypatch):
    import maromarker2.ui.app_settings as app_settings_module
    from PySide6.QtCore import QSettings
    ini_path = str(tmp_path / "settings.ini")
    monkeypatch.setattr(
        app_settings_module, "QSettings", lambda *a, **k: QSettings(ini_path, QSettings.IniFormat)
    )
    return app_settings_module.AppSettings()


def _select(tree, *path):
    node = tree.taxonomy.root
    for name in path:
        node = node.children[name]
    tree.select_node(node)
    return node


def test_taxonomy_node_editing_validates_names():
    tax = Taxonomy.from_dict(SAMPLE)

    node = tax.add_node(tax.root.children["jellyfish"], "  Mnemiopsis Leidyi ", "species")
    assert node.name == "mnemiopsis_leidyi"
    assert tax.group_of_species("mnemiopsis_leidyi") == "jellyfish"

    with pytest.raises(ValueError):
        tax.add_node(tax.root.children["jellyfish"], "mnemiopsis_leidyi", "species")  # sibling duplicate
    with pytest.raises(ValueError):
        tax.add_node(tax.root.children["fish"], "beroe_sp", "species")  # species unique tree-wide
    with pytest.raises(ValueError):
        tax.add_node(tax.root, "", "group")
    with pytest.raises(ValueError):
        tax.add_node(tax.root, "unidentified", "group")
    with pytest.raises(ValueError):
        tax.add_node(tax.root.children["jellyfish"], "sub", "group")  # rank not allowed there
    with pytest.raises(ValueError):
        tax.add_node(tax.root.children["fish"].children["benthic"], "x", "group")


def test_taxonomy_rename_and_remove_keep_order_and_index():
    tax = Taxonomy.from_dict(SAMPLE)

    tax.rename_tree_node(tax.root.children["fish"].children["benthic"], "Deep Sea")
    assert tax.subgroups_in_group("fish") == ["deep_sea", "pelagic"]

    tax.rename_tree_node(tax.root.children["jellyfish"].children["beroe_sp"], "beroe_new")
    assert "beroe_new" in tax and "beroe_sp" not in tax

    tax.remove_tree_node(tax.root.children["fish"])
    assert tax.groups() == ["jellyfish"]
    assert "gadus_morhua" not in tax


def test_taxonomy_copy_is_independent():
    tax = Taxonomy.from_dict(SAMPLE)
    clone = tax.copy()
    clone.add_node(clone.root, "crustacea", "group")
    assert "crustacea" not in tax.groups()
    assert tax.to_dict() == SAMPLE


def test_tree_widget_add_rename_remove(qapp):
    tree = HierarchyTreeWidget(Taxonomy.from_dict(SAMPLE))
    emitted = []
    tree.changed.connect(lambda: emitted.append(1))

    assert tree.can_add("group") and not tree.can_add("subgroup")
    tree.add_node("crustacea", "group")
    assert tree.selected_node().name == "crustacea"
    assert tree.can_add("subgroup") and tree.can_add("species")

    tree.add_node("calanus", "species")
    tree.select_node(tree.taxonomy.root.children["crustacea"])
    tree.add_node("copepods", "subgroup")
    assert not tree.can_add("subgroup")

    tree.rename_selected("copepoda")
    assert tree.taxonomy.subgroups_in_group("crustacea") == ["copepoda"]

    tree.remove_selected()
    assert tree.selected_node().name == "crustacea"
    assert tree.taxonomy.subgroups_in_group("crustacea") == []
    assert len(emitted) == 5

    top_names = [tree.topLevelItem(i).text(0) for i in range(tree.topLevelItemCount())]
    assert top_names == ["fish", "jellyfish", "crustacea"]


def test_tree_widget_rejects_invalid_edit_without_change_signal(qapp):
    tree = HierarchyTreeWidget(Taxonomy.from_dict(SAMPLE))
    emitted = []
    tree.changed.connect(lambda: emitted.append(1))
    with pytest.raises(ValueError):
        tree.add_node("fish", "group")
    assert emitted == []


def test_dialog_edits_a_copy_and_saves_new_file(qapp, tmp_path):
    original = Taxonomy.from_dict(SAMPLE)
    dialog = HierarchyEditorDialog(original, None)
    _select(dialog.tree, "jellyfish")
    dialog.tree.add_node("cyanea", "species")
    assert dialog.modified
    assert "cyanea" not in original

    saved = dialog.save_to(tmp_path / "custom")
    assert saved == tmp_path / "custom.yaml"
    assert not dialog.modified and dialog.saved_path == saved
    assert Taxonomy.load(saved).species_in_group("jellyfish") == ["beroe_sp", "cyanea"]


def test_dialog_never_overwrites_bundled_default(qapp):
    dialog = HierarchyEditorDialog(Taxonomy.load_default(), DEFAULT_HIERARCHY_PATH)
    before = DEFAULT_HIERARCHY_PATH.read_text()
    with pytest.raises(ValueError):
        dialog.save_to(DEFAULT_HIERARCHY_PATH)
    assert DEFAULT_HIERARCHY_PATH.read_text() == before


def test_dialog_button_state_follows_selection(qapp):
    dialog = HierarchyEditorDialog(Taxonomy.from_dict(SAMPLE))
    assert dialog.btn_add_group.isEnabled() and not dialog.btn_rename.isEnabled()
    _select(dialog.tree, "fish")
    assert dialog.btn_add_subgroup.isEnabled() and dialog.btn_add_species.isEnabled()
    assert dialog.btn_rename.isEnabled() and dialog.btn_remove.isEnabled()
    _select(dialog.tree, "fish", "pelagic", "gadus_morhua")
    assert not dialog.btn_add_species.isEnabled()


def test_reject_with_unsaved_changes_can_be_cancelled(qapp, monkeypatch):
    dialog = HierarchyEditorDialog(Taxonomy.from_dict(SAMPLE))
    dialog.tree.add_node("crustacea", "group")
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Cancel)
    dialog.show()
    dialog.reject()
    assert dialog.isVisible()
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Discard)
    dialog.reject()
    assert not dialog.isVisible()


def test_main_window_switches_hierarchy_keeping_pair(qapp, settings, pytestconfig, tmp_path):
    root = pytestconfig.rootpath
    config = ProjectConfig(
        image_root=root / "sample_data" / "2016_04",
        camera_config_path=root / "maromarker2" / "configs" / "config_remos1.json",
        hierarchy_path=DEFAULT_HIERARCHY_PATH,
        user_id="tst",
        output_csv_path=tmp_path / "results.csv",
    )
    window = MainWindow(config, settings)
    window.editor.load_pair(1)

    custom = Taxonomy.from_dict(SAMPLE)
    path = tmp_path / "custom.yaml"
    custom.save(path)
    window.use_hierarchy(path)

    assert window.project_config.hierarchy_path == path
    assert settings.last_hierarchy_path == str(path)
    assert window.editor.current_pair_index == 1
    assert window.editor.side_panel.taxonomy.groups() == ["fish", "jellyfish"]


def test_clicking_empty_area_deselects(qapp):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    dialog = HierarchyEditorDialog(Taxonomy.from_dict(SAMPLE))
    dialog.show()
    _select(dialog.tree, "fish")
    assert dialog.btn_add_species.isEnabled()
    assert dialog.btn_rename.isEnabled()

    viewport = dialog.tree.viewport()
    QTest.mouseClick(viewport, Qt.LeftButton, pos=QPoint(5, viewport.height() - 5))

    assert dialog.tree.selected_node() is None
    assert dialog.btn_add_group.isEnabled() and not dialog.btn_add_subgroup.isEnabled()
    assert not dialog.btn_rename.isEnabled()
    assert dialog.tree.columnWidth(0) >= 300
