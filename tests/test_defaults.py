"""Tests for the bundled defaults and their persistent per-user copy."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import json
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from maromarker2 import defaults
from maromarker2.ui.app_settings import AppSettings
from maromarker2.ui.project_setup_dialog import ProjectSetupDialog


@pytest.fixture
def bundled(tmp_path, monkeypatch):
    """Replaces the bundled files with small stand-ins so the tests stay fast."""
    root = tmp_path / "bundle"
    root.mkdir()
    files = []
    for name, relative in (
        ("cam.json", "camera_configs/cam.json"),
        ("hier.yaml", "hierarchy/hier.yaml"),
        ("model.onnx", "ai_model/model.onnx"),
    ):
        source = root / name
        source.write_text(f"v1 {name}")
        files.append((source, Path(relative)))
    monkeypatch.setattr(defaults, "BUNDLED_FILES", tuple(files))
    monkeypatch.setattr(defaults, "DEFAULT_CAMERA_CONFIG", files[0])
    monkeypatch.setattr(defaults, "DEFAULT_HIERARCHY", files[1])
    monkeypatch.setattr(defaults, "DEFAULT_AI_MODEL", files[2])
    return files


@pytest.fixture
def settings(tmp_path, monkeypatch):
    import maromarker2.ui.app_settings as app_settings_module
    from PySide6.QtCore import QSettings

    ini_path = str(tmp_path / "settings.ini")
    monkeypatch.setattr(
        app_settings_module, "QSettings", lambda *a, **k: QSettings(ini_path, QSettings.IniFormat)
    )
    return AppSettings()


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


def test_real_bundled_files_exist():
    for source, _ in defaults.BUNDLED_FILES:
        assert source.is_file(), source
    assert defaults.DEFAULT_CAMERA_CONFIG[0].name == "config_remos1.json"


def test_ensure_user_defaults_copies_files(bundled):
    root = defaults.ensure_user_defaults()
    for source, relative in bundled:
        assert (root / relative).read_text() == source.read_text()
    assert not list(root.rglob("*.part"))


def test_ensure_user_defaults_keeps_user_edits(bundled):
    root = defaults.ensure_user_defaults()
    edited = root / bundled[1][1]
    edited.write_text("my own hierarchy")
    bundled[1][0].write_text("v2 hier.yaml")

    defaults.ensure_user_defaults()
    assert edited.read_text() == "my own hierarchy"


def test_ensure_user_defaults_refreshes_untouched_copies(bundled):
    root = defaults.ensure_user_defaults()
    bundled[0][0].write_text("v2 cam.json")

    defaults.ensure_user_defaults()
    assert (root / bundled[0][1]).read_text() == "v2 cam.json"
    manifest = json.loads((root / ".bundled_hashes.json").read_text())
    assert bundled[0][1].as_posix() in manifest


def test_ensure_user_defaults_restores_deleted_file(bundled):
    root = defaults.ensure_user_defaults()
    (root / bundled[2][1]).unlink()

    defaults.ensure_user_defaults()
    assert (root / bundled[2][1]).is_file()


def test_default_paths_prefer_user_copy_then_bundle(bundled):
    assert defaults.default_camera_config_path() == bundled[0][0]
    root = defaults.ensure_user_defaults()
    assert defaults.default_camera_config_path() == root / bundled[0][1]
    assert defaults.default_hierarchy_path() == root / bundled[1][1]
    assert defaults.default_ai_model_path() == root / bundled[2][1]


def test_ensure_user_defaults_survives_unwritable_target(bundled, tmp_path, monkeypatch):
    blocker = tmp_path / "blocker"
    blocker.write_text("a file, not a folder")
    monkeypatch.setattr(defaults, "user_defaults_dir", lambda: blocker / "defaults")

    defaults.ensure_user_defaults()  # must not raise
    assert defaults.default_camera_config_path() == bundled[0][0]


def test_browse_dir(bundled, tmp_path):
    existing = tmp_path / "somewhere" / "file.json"
    existing.parent.mkdir()
    assert defaults.browse_dir(str(existing), defaults.CAMERA_CONFIG_SUBDIR) == str(existing.parent)
    expected = str(defaults.user_defaults_dir() / defaults.CAMERA_CONFIG_SUBDIR)
    assert defaults.browse_dir("", defaults.CAMERA_CONFIG_SUBDIR) == expected
    assert defaults.browse_dir(str(tmp_path / "gone" / "x.json"), defaults.CAMERA_CONFIG_SUBDIR) == expected


def test_dialog_prefills_defaults_from_user_copy(qapp, settings, bundled):
    root = defaults.ensure_user_defaults()
    dialog = ProjectSetupDialog(settings)
    assert dialog.edit_camera_config.text() == str(root / bundled[0][1])
    assert dialog.edit_hierarchy.text() == str(root / bundled[1][1])
    assert dialog.edit_ai_model.text() == str(root / bundled[2][1])


def test_dialog_ignores_stale_remembered_paths(qapp, settings, bundled):
    root = defaults.ensure_user_defaults()
    settings.last_camera_config_path = "/tmp/.mount_gone/usr/bin/maromarker2/configs/config_remos1.json"
    settings.last_hierarchy_path = "/tmp/.mount_gone/hierarchy.yaml"
    settings.last_ai_model_path = "/tmp/.mount_gone/best.onnx"

    dialog = ProjectSetupDialog(settings)
    assert dialog.edit_camera_config.text() == str(root / bundled[0][1])
    assert dialog.edit_hierarchy.text() == str(root / bundled[1][1])
    assert dialog.edit_ai_model.text() == str(root / bundled[2][1])


def test_dialog_keeps_valid_remembered_paths(qapp, settings, bundled, tmp_path):
    defaults.ensure_user_defaults()
    custom = tmp_path / "custom.json"
    custom.write_text("{}")
    settings.last_camera_config_path = str(custom)

    assert ProjectSetupDialog(settings).edit_camera_config.text() == str(custom)


def test_dialog_remembers_cleared_ai_model(qapp, settings, bundled):
    defaults.ensure_user_defaults()
    settings.last_ai_model_path = ""

    assert ProjectSetupDialog(settings).edit_ai_model.text() == ""
