"""Main application window: menu/toolbar/status bar shell.

Owns the loaded project's data layer (image pairs, annotation store, camera
calibration, taxonomy) and hosts the keypoint EditorWidget as its central widget.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QLabel, QMainWindow, QMessageBox, QProgressBar, QPushButton,
)

from maromarker2 import defaults
from maromarker2.models.annotation_store import AnnotationStore
from maromarker2.models.camera_calibration import CameraCalibration
from maromarker2.models.image_set import ImagePair, scan_image_set, suggest_output_filename
from maromarker2.models.project_config import ProjectConfig
from maromarker2.models.taxonomy import DEFAULT_HIERARCHY_PATH, Taxonomy
from maromarker2.predictors.onnx_predictor import validate_model_path
from maromarker2.ui.app_settings import AppSettings
from maromarker2.ui.editor.editor_widget import EditorWidget
from maromarker2.ui.hierarchy_editor.hierarchy_editor_dialog import HierarchyEditorDialog
from maromarker2.ui.project_setup_dialog import ProjectSetupDialog
from maromarker2.ui.remark_options_dialog import RemarkOptionsDialog
from maromarker2.ui.theme import apply_theme


class MainWindow(QMainWindow):
    def __init__(self, project_config: ProjectConfig, settings: AppSettings | None = None):
        super().__init__()
        self.setWindowTitle("MarOMarker2")
        self.resize(900, 600)

        self._settings = settings or AppSettings()
        self.project_config: ProjectConfig | None = None
        self.image_pairs: list[ImagePair] = []
        self.annotation_store: AnnotationStore | None = None
        self.camera_calibration: CameraCalibration | None = None
        self.taxonomy: Taxonomy | None = None
        self.editor: EditorWidget | None = None

        self.setCentralWidget(QLabel("No session loaded. Use File > New Session… to start."))

        self._init_menu()
        self.statusBar().showMessage("No session loaded.")
        self._init_prediction_progress()

        self.load_project(project_config)

    def _init_menu(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        file_menu.addAction("New Session…", self._on_new_session)
        file_menu.addAction("Open Defaults Folder", self._on_open_defaults_folder)
        self._save_action = file_menu.addAction("&Save", self._on_save)
        self._save_action.setShortcut(QKeySequence.Save)
        file_menu.addSeparator()
        file_menu.addAction("E&xit", self.close)

        settings_menu = self.menuBar().addMenu("&Settings")
        settings_menu.addAction("&Remark options…", self._on_remark_options)
        settings_menu.addAction("Edit &hierarchy…", self._on_edit_hierarchy)
        settings_menu.addAction("AI &model…", self._on_ai_model)
        self._appearance_action = settings_menu.addAction("&Appearance…", self._on_appearance)
        self._appearance_action.setEnabled(self.editor is not None)
        self._theme_action = settings_menu.addAction("", self._on_toggle_theme)
        self._update_theme_action_text()

    def _init_prediction_progress(self) -> None:
        """Status bar progress bar + Cancel button, shown only while an AI prediction runs."""
        self._prediction_label = QLabel("Predicting animals…")
        self._prediction_bar = QProgressBar()
        self._prediction_bar.setMaximumWidth(200)
        self._prediction_cancel = QPushButton("Cancel")
        self._prediction_cancel.clicked.connect(self._on_prediction_cancel)
        self._prediction_widgets = (self._prediction_label, self._prediction_bar, self._prediction_cancel)
        for widget in self._prediction_widgets:
            self.statusBar().addPermanentWidget(widget)
            widget.hide()

    def _on_prediction_started(self, total: int) -> None:
        # a single pair gets a busy indicator, many pairs a progress bar
        self._prediction_bar.setRange(0, total if total > 1 else 0)
        self._prediction_bar.setValue(0)
        self._prediction_cancel.setEnabled(True)
        for widget in self._prediction_widgets:
            widget.show()
        self._save_action.setEnabled(False)

    def _on_prediction_progress(self, done: int, total: int) -> None:
        if total > 1:
            self._prediction_bar.setValue(done)

    def _on_prediction_cancel(self) -> None:
        self._prediction_cancel.setEnabled(False)
        if self.editor is not None:
            self.editor.cancel_prediction()

    def _on_prediction_finished(self) -> None:
        for widget in self._prediction_widgets:
            widget.hide()
        self._save_action.setEnabled(self.project_config is not None)

    def _update_theme_action_text(self) -> None:
        self._theme_action.setText("Toggle &Light Theme" if self._settings.dark_theme else "Toggle &Dark Theme")

    def load_project(self, project_config: ProjectConfig) -> None:
        try:
            self.camera_calibration = CameraCalibration.from_json(project_config.camera_config_path)
            self.taxonomy = Taxonomy.load(project_config.hierarchy_path)
            self.image_pairs = scan_image_set(project_config.image_root, prefix=project_config.image_prefix)

            output_csv_path = project_config.output_csv_path
            if output_csv_path is None:
                output_csv_path = project_config.image_root / suggest_output_filename(self.image_pairs)
                project_config.output_csv_path = output_csv_path
            self.annotation_store = AnnotationStore.load_or_create(output_csv_path)
            self.annotation_store.user_id = project_config.user_id
            self.annotation_store.experiment_id = project_config.experiment_id
        except Exception as exc:
            QMessageBox.critical(self, "Failed to Load Session", str(exc))
            return

        self.project_config = project_config
        self._install_editor()
        self._save_action.setEnabled(True)
        self._appearance_action.setEnabled(True)
        self.statusBar().showMessage(
            f"{project_config.image_root} — {len(self.image_pairs)} image pair(s) — "
            f"output: {project_config.output_csv_path} — user: {project_config.user_id}"
        )

    def _install_editor(self) -> None:
        """(Re)creates the editor over the current data layer; used when the session or taxonomy changes."""
        if self.editor is not None:
            self.editor.shutdown()
        self._on_prediction_finished()
        self.editor = EditorWidget(
            self.annotation_store, self.taxonomy, self.image_pairs, calibration=self.camera_calibration,
            ai_model_path=self.project_config.ai_model_path if self.project_config is not None else None,
        )
        self.editor.prediction_started.connect(self._on_prediction_started)
        self.editor.prediction_progress.connect(self._on_prediction_progress)
        self.editor.prediction_finished.connect(self._on_prediction_finished)
        self._apply_remark_options()
        self.setCentralWidget(self.editor)
        self.editor.image_list.table.setFocus() # otherwise Qt focuses the first focusable widget

    def _on_new_session(self) -> None:
        dialog = ProjectSetupDialog(self._settings, parent=self)
        if dialog.exec() and dialog.project_config is not None:
            self.load_project(dialog.project_config)

    def _on_open_defaults_folder(self) -> None:
        folder = defaults.user_defaults_dir()
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _on_edit_hierarchy(self) -> None:
        taxonomy = self.taxonomy or Taxonomy.load_default()
        source = self.project_config.hierarchy_path if self.project_config is not None else DEFAULT_HIERARCHY_PATH
        dialog = HierarchyEditorDialog(taxonomy, source, parent=self)
        dialog.exec()
        if dialog.saved_path is None:
            return
        if self.project_config is None:
            self._settings.last_hierarchy_path = str(dialog.saved_path)
            return
        answer = QMessageBox.question(
            self, "Use hierarchy", f"Use the saved hierarchy in the current session?\n{dialog.saved_path}"
        )
        if answer == QMessageBox.Yes:
            self.use_hierarchy(dialog.saved_path)

    def use_hierarchy(self, path: Path) -> None:
        """Switches the loaded session to the hierarchy file at ``path``, keeping the annotations
        and the currently shown image pair."""
        if self.project_config is None or self.editor is None:
            return
        try:
            taxonomy = Taxonomy.load(path)
        except Exception as exc:
            QMessageBox.critical(self, "Failed to Load Hierarchy", str(exc))
            return
        self.editor.commit_pending_edits()
        pair_index = self.editor.current_pair_index
        self.taxonomy = taxonomy
        self.project_config.hierarchy_path = Path(path)
        self._settings.last_hierarchy_path = str(path)
        self._install_editor()
        self.editor.load_pair(pair_index)
        self.statusBar().showMessage(f"Using hierarchy {path}", 5000)

    def _on_ai_model(self) -> None:
        current = self._settings.last_ai_model_path
        if self.project_config is not None and self.project_config.ai_model_path is not None:
            current = str(self.project_config.ai_model_path)
        start = defaults.browse_dir(current, defaults.AI_MODEL_SUBDIR)
        path, _ = QFileDialog.getOpenFileName(self, "Select AI model", start, "ONNX model (*.onnx)")
        if not path:
            return
        error = validate_model_path(path)
        if error:
            QMessageBox.critical(self, "Invalid AI Model", error)
            return
        self._settings.last_ai_model_path = path
        if self.project_config is not None:
            self.project_config.ai_model_path = Path(path)
        if self.editor is not None:
            self.editor.set_ai_model_path(path)
        self.statusBar().showMessage(f"Using AI model {path}", 5000)

    def closeEvent(self, event) -> None:
        if self.editor is not None:
            self.editor.shutdown()
        super().closeEvent(event)

    def _on_remark_options(self) -> None:
        if RemarkOptionsDialog(self._settings, parent=self).exec():
            self._apply_remark_options()

    def _apply_remark_options(self) -> None:
        if self.editor is not None:
            self.editor.set_remark_options(
                self._settings.object_remark_options, self._settings.image_remark_options
            )

    def _on_toggle_theme(self) -> None:
        self._settings.dark_theme = not self._settings.dark_theme
        apply_theme(QApplication.instance(), self._settings.dark_theme)
        self._update_theme_action_text()

    def _on_appearance(self) -> None:
        if self.editor is not None:
            self.editor.open_appearance_settings()

    def _on_save(self) -> None:
        if self.annotation_store is None or self.project_config is None:
            return
        if self.editor is not None and self.editor.is_predicting:
            return
        if self.editor is not None:
            self.editor.commit_pending_edits()
        path = self.project_config.output_csv_path
        if path is None:
            path, _ = QFileDialog.getSaveFileName(self, "Save annotations as", filter="*.csv")
            if not path:
                return
            path = Path(path)
            self.project_config.output_csv_path = path
        self.annotation_store.save(path)
        self.statusBar().showMessage(f"Saved to {path}", 5000)
