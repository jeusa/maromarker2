"""Smoke tests for the Phase 4 UI shell, run with QT_QPA_PLATFORM=offscreen."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

PySide6 = pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from maromarker2.models.project_config import ProjectConfig
from maromarker2.models.taxonomy import DEFAULT_HIERARCHY_PATH
from maromarker2.ui.main_window import MainWindow
from maromarker2.ui.project_setup_dialog import ProjectSetupDialog
from maromarker2.ui.remark_options_dialog import RemarkOptionsDialog, parse_options


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def settings(tmp_path, monkeypatch):
    # isolate QSettings so tests never touch the real user's persisted defaults
    import maromarker2.ui.app_settings as app_settings_module
    from PySide6.QtCore import QSettings
    ini_path = str(tmp_path / "settings.ini")
    monkeypatch.setattr(
        app_settings_module, "QSettings", lambda *a, **k: QSettings(ini_path, QSettings.IniFormat)
    )
    return app_settings_module.AppSettings()


@pytest.fixture
def project_config(pytestconfig, tmp_path):
    repo_root = pytestconfig.rootpath
    return ProjectConfig(
        image_root=repo_root /  "sample_data" / "2016_04",
        camera_config_path=repo_root / "maromarker2" / "configs" / "config_remos1.json",
        hierarchy_path=DEFAULT_HIERARCHY_PATH,
        user_id="tst",
        output_csv_path=tmp_path / "results.csv",
    )


def test_project_setup_dialog_constructs(qapp, settings):
    dialog = ProjectSetupDialog(settings)
    assert dialog.edit_hierarchy.text() == str(DEFAULT_HIERARCHY_PATH)


def test_project_setup_dialog_validate_rejects_missing_fields(qapp, settings):
    dialog = ProjectSetupDialog(settings)
    dialog.edit_image_root.setText("")
    assert dialog._validate() is not None


def test_project_setup_dialog_validate_accepts_real_fixtures(qapp, settings, pytestconfig):
    repo_root = pytestconfig.rootpath
    dialog = ProjectSetupDialog(settings)
    dialog.edit_image_root.setText(str(repo_root / "sample_data" / "2016_04"))
    dialog.edit_camera_config.setText(str(repo_root / "maromarker2" / "configs" / "config_remos1.json"))
    dialog.edit_hierarchy.setText(str(DEFAULT_HIERARCHY_PATH))
    dialog.edit_user_id.setText("tst")

    assert dialog._validate() is None


def test_project_setup_dialog_remembers_last_values(qapp, settings, pytestconfig, tmp_path):
    repo_root = pytestconfig.rootpath
    dialog = ProjectSetupDialog(settings)
    dialog.edit_image_root.setText(str(repo_root / "sample_data" / "2016_04"))
    dialog.edit_camera_config.setText(str(repo_root / "maromarker2" / "configs" / "config_remos1.json"))
    dialog.edit_hierarchy.setText(str(DEFAULT_HIERARCHY_PATH))
    dialog.edit_user_id.setText("tst")
    dialog.edit_prefix.setText("TN_Exif_Remos1")
    dialog.edit_output_csv.setText(str(tmp_path / "out.csv"))
    dialog.edit_experiment_id.setText("exp7")
    dialog._on_accept()

    reopened = ProjectSetupDialog(settings)
    assert reopened.edit_user_id.text() == "tst"
    assert reopened.edit_prefix.text() == "TN_Exif_Remos1"
    assert reopened.edit_output_csv.text() == str(tmp_path / "out.csv")
    assert reopened.edit_experiment_id.text() == "exp7"

    # clearing optional fields clears the remembered values
    reopened.edit_prefix.setText("")
    reopened.edit_output_csv.setText("")
    reopened.edit_experiment_id.setText("")
    reopened._on_accept()
    cleared = ProjectSetupDialog(settings)
    assert cleared.edit_prefix.text() == ""
    assert cleared.edit_output_csv.text() == ""
    assert cleared.edit_experiment_id.text() == ""


def test_main_window_loads_project_and_lists_pairs(qapp, settings, project_config):
    window = MainWindow(project_config, settings)

    assert window.annotation_store is not None
    assert window.camera_calibration is not None
    assert window.taxonomy is not None
    assert len(window.image_pairs) > 0
    assert window.editor is not None
    assert window.editor.pair_count() == len(window.image_pairs)


def test_main_window_does_not_focus_remark_fields_on_startup(qapp, settings, project_config):
    window = MainWindow(project_config, settings)
    window.show()
    qapp.processEvents()
    window.activateWindow()
    qapp.processEvents()

    focused = qapp.focusWidget()
    assert focused is None or not window.editor.image_list.edit_remark.isAncestorOf(focused)
    assert not window.editor.image_list.edit_remark.hasFocus()
    window.close()


def test_main_window_shows_progress_and_disables_save_during_prediction(qapp, settings, project_config):
    import threading
    from PySide6.QtTest import QTest
    from maromarker2.predictors import KeypointPredictor

    class GatedPredictor(KeypointPredictor):
        def __init__(self):
            self.entered, self.gate = threading.Event(), threading.Event()

        def predict(self, image_path):
            self.entered.set()
            assert self.gate.wait(10)
            return []

    def wait_until(condition):
        for _ in range(1000):
            if condition():
                return
            QTest.qWait(10)
        pytest.fail("condition not reached")

    window = MainWindow(project_config, settings)
    predictor = GatedPredictor()
    window.editor.set_predictor(predictor)
    assert window._prediction_bar.isHidden()

    window.editor._predict_selected()
    wait_until(predictor.entered.is_set)

    assert not window._save_action.isEnabled()
    assert not window._prediction_bar.isHidden() and not window._prediction_cancel.isHidden()
    saved = []
    window.annotation_store.save = lambda path: saved.append(path)
    window._on_save()
    assert saved == []

    window._prediction_cancel.click()
    predictor.gate.set()
    wait_until(lambda: not window.editor.is_predicting)

    assert window._save_action.isEnabled()
    assert window._prediction_bar.isHidden()


def test_main_window_save_writes_csv(qapp, settings, project_config):
    window = MainWindow(project_config, settings)
    window.annotation_store.add_row(
        window.annotation_store.build_row("some_file", species="x", left={"head": (1, 1), "tail": (2, 2)})
    )

    window._on_save()

    assert project_config.output_csv_path.is_file()


def test_main_window_save_keeps_status(qapp, settings, project_config):
    window = MainWindow(project_config, settings)
    file_id = window.editor.image_pairs[window.editor.current_pair_index].file_id
    store = window.annotation_store
    index = store.add_row(store.build_row(file_id, left={"head": (1, 1), "tail": (2, 2)}, status="unchecked"))

    window._on_save()

    assert store.data.loc[index, "status"] == "unchecked"


def test_ctrl_s_saves_csv(qapp, settings, project_config):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    window = MainWindow(project_config, settings)
    window.annotation_store.add_row(
        window.annotation_store.build_row("some_file", species="x", left={"head": (1, 1), "tail": (2, 2)})
    )
    window.show()
    window.activateWindow()
    qapp.processEvents()

    assert window._save_action.shortcut().toString() == "Ctrl+S"
    QTest.keyClick(window, Qt.Key_S, Qt.ControlModifier)

    assert project_config.output_csv_path.is_file()
    window.close()


def test_theme_menu_action_toggles_theme_and_label(qapp, settings, project_config):
    settings.dark_theme = False
    window = MainWindow(project_config, settings)
    assert window._theme_action.text() == "Toggle Dark &Theme"
    window._theme_action.trigger()
    assert settings.dark_theme is True
    assert window._theme_action.text() == "Toggle Light &Theme"
    window._theme_action.trigger()
    assert settings.dark_theme is False
    assert window._theme_action.text() == "Toggle Dark &Theme"


def test_remark_options_are_persisted_per_type(qapp, settings):
    assert settings.object_remark_options == ["not determined", "animal incomplete"]
    assert settings.image_remark_options == [
        "low turbidity", "medium turbidity", "high turbidity", "wrong illumination", "without flashlight",
    ]

    settings.object_remark_options = ["alive"]
    settings.image_remark_options = ["blurry", "dark"]

    assert settings.object_remark_options == ["alive"]
    assert settings.image_remark_options == ["blurry", "dark"]


def test_remark_options_can_be_cleared_without_falling_back_to_defaults(qapp, settings):
    settings.object_remark_options = []

    assert settings.object_remark_options == []


def test_parse_options_trims_and_drops_blanks_and_duplicates():
    assert parse_options(" a \n\n b\na\n   \n") == ["a", "b"]


def test_remark_options_dialog_saves_both_lists(qapp, settings):
    settings.object_remark_options = ["old"]
    dialog = RemarkOptionsDialog(settings)
    assert dialog.edit_object_options.toPlainText() == "old"

    dialog.edit_object_options.setPlainText("alive\ndamaged")
    dialog.edit_image_options.setPlainText("blurry")
    dialog._on_accept()

    assert settings.object_remark_options == ["alive", "damaged"]
    assert settings.image_remark_options == ["blurry"]


def test_status_options_always_include_checked_and_unchecked(qapp, settings):
    assert settings.status_options == ["unchecked", "checked"]

    settings.status_options = ["uncertain", "checked"]
    assert settings.status_options == ["uncertain", "checked", "unchecked"]

    dialog = RemarkOptionsDialog(settings)
    dialog.edit_status_options.setPlainText("unchecked\nchecked\nto review")
    dialog._on_accept()
    assert settings.status_options == ["unchecked", "checked", "to review"]


def test_main_window_applies_saved_status_options_to_editor(qapp, settings, project_config):
    settings.status_options = ["unchecked", "checked", "to review"]

    window = MainWindow(project_config, settings)

    combo = window.editor.side_panel.combo_status
    assert [combo.itemText(i) for i in range(combo.count())] == ["unchecked", "checked", "to review"]


def test_main_window_applies_saved_remark_options_to_editor(qapp, settings, project_config):
    settings.object_remark_options = ["alive"]
    settings.image_remark_options = ["blurry"]

    window = MainWindow(project_config, settings)

    assert window.editor.side_panel.edit_remark.options() == ["alive"]
    assert window.editor.image_list.edit_remark.options() == ["blurry"]

    settings.image_remark_options = ["dark"]
    window._apply_remark_options()
    assert window.editor.image_list.edit_remark.options() == ["dark"]
