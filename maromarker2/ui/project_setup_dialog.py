"""Dialog to configure a new annotation session: image folder, camera calibration,
taxonomy, output CSV, and user/experiment IDs. Replaces the legacy WelcomeWindow +
PageData folder/date pickers with a simplified, calendar-free flow.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout,
    QLineEdit, QMessageBox, QPushButton, QWidget,
)

from maromarker2 import defaults
from maromarker2.models.camera_calibration import CameraCalibration
from maromarker2.models.project_config import ProjectConfig
from maromarker2.models.taxonomy import Taxonomy
from maromarker2.predictors.onnx_predictor import validate_model_path
from maromarker2.ui.app_settings import AppSettings


def _initial_path(saved: str, default: Path | None) -> str:
    """The remembered file if it still exists, else the default (remembered paths can go stale)."""
    if saved and Path(saved).is_file():
        return saved
    return str(default) if default else ""


def _browse_row(line_edit: QLineEdit, on_browse) -> QWidget:
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    browse_button = QPushButton("Browse…")
    browse_button.clicked.connect(on_browse)
    layout.addWidget(line_edit)
    layout.addWidget(browse_button)
    return row


class ProjectSetupDialog(QDialog):
    """Collects everything needed to start/resume an annotation session."""

    def __init__(self, settings: AppSettings | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("New MarOMarker Session")
        self.resize(560, 260)

        self._settings = settings or AppSettings()
        self.project_config: ProjectConfig | None = None

        self.edit_image_root = QLineEdit(self._settings.last_image_root)
        self.edit_prefix = QLineEdit(self._settings.last_image_prefix)
        self.edit_output_csv = QLineEdit(self._settings.last_output_csv_path)
        self.edit_camera_config = QLineEdit(
            _initial_path(self._settings.last_camera_config_path, defaults.default_camera_config_path())
        )
        self.edit_hierarchy = QLineEdit(
            _initial_path(self._settings.last_hierarchy_path, defaults.default_hierarchy_path())
        )
        if self._settings.has_ai_model_choice and not self._settings.last_ai_model_path:
            ai_model_text = ""  # the user deliberately cleared the model
        else:
            ai_model_text = _initial_path(self._settings.last_ai_model_path, defaults.default_ai_model_path())
        self.edit_ai_model = QLineEdit(ai_model_text)
        self.edit_user_id = QLineEdit(self._settings.last_user_id)
        self.edit_experiment_id = QLineEdit(self._settings.last_experiment_id)

        self.edit_output_csv.setPlaceholderText("Leave empty to auto-name from image dates")
        self.edit_ai_model.setPlaceholderText("Optional; needs inference_args.yaml next to it (leave empty for none)")

        form = QFormLayout()
        form.addRow("Image directory*", _browse_row(self.edit_image_root, self._on_browse_image_root))
        form.addRow("Filename prefix filter", self.edit_prefix)
        form.addRow("Output CSV path", _browse_row(self.edit_output_csv, self._on_browse_output_csv))
        form.addRow("Camera config (.json)*", _browse_row(self.edit_camera_config, self._on_browse_camera_config))
        form.addRow("Hierarchy (.yaml)*", _browse_row(self.edit_hierarchy, self._on_browse_hierarchy))
        form.addRow("AI model (.onnx)", _browse_row(self.edit_ai_model, self._on_browse_ai_model))
        form.addRow("User ID*", self.edit_user_id)
        form.addRow("Experiment ID", self.edit_experiment_id)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

        self.setLayout(form)

    def _on_browse_image_root(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select image directory")
        if path:
            self.edit_image_root.setText(path)

    def _on_browse_output_csv(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Select output CSV path", filter="*.csv")
        if path:
            self.edit_output_csv.setText(path)

    def _on_browse_camera_config(self) -> None:
        start = defaults.browse_dir(self.edit_camera_config.text(), defaults.CAMERA_CONFIG_SUBDIR)
        path, _ = QFileDialog.getOpenFileName(self, "Select camera configuration file", start, "*.json")
        if path:
            self.edit_camera_config.setText(path)

    def _on_browse_hierarchy(self) -> None:
        start = defaults.browse_dir(self.edit_hierarchy.text(), defaults.HIERARCHY_SUBDIR)
        path, _ = QFileDialog.getOpenFileName(self, "Select hierarchy file", start, "*.yaml *.yml")
        if path:
            self.edit_hierarchy.setText(path)

    def _on_browse_ai_model(self) -> None:
        start = defaults.browse_dir(self.edit_ai_model.text(), defaults.AI_MODEL_SUBDIR)
        path, _ = QFileDialog.getOpenFileName(self, "Select AI model", start, "*.onnx")
        if path:
            self.edit_ai_model.setText(path)

    def _on_accept(self) -> None:
        error = self._validate()
        if error:
            QMessageBox.critical(self, "Invalid Session Configuration", error)
            return

        image_root = Path(self.edit_image_root.text())
        camera_config_path = Path(self.edit_camera_config.text())
        hierarchy_path = Path(self.edit_hierarchy.text())
        output_text = self.edit_output_csv.text().strip()
        ai_model_text = self.edit_ai_model.text().strip()

        self.project_config = ProjectConfig(
            image_root=image_root,
            camera_config_path=camera_config_path,
            hierarchy_path=hierarchy_path,
            user_id=self.edit_user_id.text().strip(),
            output_csv_path=Path(output_text) if output_text else None,
            image_prefix=self.edit_prefix.text().strip(),
            experiment_id=self.edit_experiment_id.text().strip(),
            ai_model_path=Path(ai_model_text) if ai_model_text else None,
        )

        self._settings.last_image_root = str(image_root)
        self._settings.last_camera_config_path = str(camera_config_path)
        self._settings.last_hierarchy_path = str(hierarchy_path)
        self._settings.last_user_id = self.project_config.user_id
        # optional fields are saved as-is so clearing them also clears the remembered value
        self._settings.last_image_prefix = self.project_config.image_prefix
        self._settings.last_output_csv_path = output_text
        self._settings.last_experiment_id = self.project_config.experiment_id
        self._settings.last_ai_model_path = ai_model_text

        self.accept()

    def _validate(self) -> str | None:
        if not self.edit_user_id.text().strip():
            return "Please specify a user ID."

        image_root = Path(self.edit_image_root.text())
        if not image_root.is_dir():
            return "Please specify a valid image directory."

        camera_config_path = Path(self.edit_camera_config.text())
        if not camera_config_path.is_file():
            return "Please specify a valid camera configuration file."
        try:
            CameraCalibration.from_json(camera_config_path)
        except Exception as exc:
            return f"Camera configuration file could not be loaded: {exc}"

        hierarchy_path = Path(self.edit_hierarchy.text())
        if not hierarchy_path.is_file():
            return "Please specify a valid hierarchy (.yaml) file."
        try:
            Taxonomy.load(hierarchy_path)
        except Exception as exc:
            return f"Hierarchy file could not be loaded: {exc}"

        output_text = self.edit_output_csv.text().strip()
        if output_text and not Path(output_text).parent.is_dir():
            return "The output CSV path's parent directory does not exist."

        ai_model_text = self.edit_ai_model.text().strip()
        if ai_model_text:
            model_error = validate_model_path(ai_model_text)
            if model_error:
                return model_error

        return None
