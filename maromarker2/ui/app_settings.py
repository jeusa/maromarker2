"""Persisted app-level state (QSettings): the last used project setup values plus UI preferences."""

from __future__ import annotations

import json

from PySide6.QtCore import QSettings

ORG_NAME = "MarOMarker"
APP_NAME = "MarOMarker2"

DEFAULT_OBJECT_REMARK_OPTIONS = ["not determined", "animal incomplete"]
DEFAULT_IMAGE_REMARK_OPTIONS = [
    "low turbidity", "medium turbidity", "high turbidity", "wrong illumination", "without flashlight",
]


class AppSettings:
    """Thin wrapper around QSettings for the handful of remembered defaults."""

    def __init__(self):
        self._settings = QSettings(ORG_NAME, APP_NAME)

    @property
    def last_image_root(self) -> str:
        return self._settings.value("last_image_root", "", type=str)

    @last_image_root.setter
    def last_image_root(self, value: str) -> None:
        self._settings.setValue("last_image_root", value)

    @property
    def last_camera_config_path(self) -> str:
        return self._settings.value("last_camera_config_path", "", type=str)

    @last_camera_config_path.setter
    def last_camera_config_path(self, value: str) -> None:
        self._settings.setValue("last_camera_config_path", value)

    @property
    def last_hierarchy_path(self) -> str:
        return self._settings.value("last_hierarchy_path", "", type=str)

    @last_hierarchy_path.setter
    def last_hierarchy_path(self, value: str) -> None:
        self._settings.setValue("last_hierarchy_path", value)

    @property
    def has_ai_model_choice(self) -> bool:
        """True once the user has confirmed a session, including an empty (= no AI model) choice."""
        return self._settings.contains("last_ai_model_path")

    @property
    def last_ai_model_path(self) -> str:
        return self._settings.value("last_ai_model_path", "", type=str)

    @last_ai_model_path.setter
    def last_ai_model_path(self, value: str) -> None:
        self._settings.setValue("last_ai_model_path", value)

    @property
    def dark_theme(self) -> bool:
        return self._settings.value("dark_theme", True, type=bool)

    @dark_theme.setter
    def dark_theme(self, value: bool) -> None:
        self._settings.setValue("dark_theme", value)

    @property
    def last_user_id(self) -> str:
        return self._settings.value("last_user_id", "", type=str)

    @last_user_id.setter
    def last_user_id(self, value: str) -> None:
        self._settings.setValue("last_user_id", value)

    @property
    def last_image_prefix(self) -> str:
        return self._settings.value("last_image_prefix", "", type=str)

    @last_image_prefix.setter
    def last_image_prefix(self, value: str) -> None:
        self._settings.setValue("last_image_prefix", value)

    @property
    def last_output_csv_path(self) -> str:
        return self._settings.value("last_output_csv_path", "", type=str)

    @last_output_csv_path.setter
    def last_output_csv_path(self, value: str) -> None:
        self._settings.setValue("last_output_csv_path", value)

    @property
    def last_experiment_id(self) -> str:
        return self._settings.value("last_experiment_id", "", type=str)

    @last_experiment_id.setter
    def last_experiment_id(self, value: str) -> None:
        self._settings.setValue("last_experiment_id", value)

    @property
    def object_remark_options(self) -> list[str]:
        return self._remark_options("object_remark_options", DEFAULT_OBJECT_REMARK_OPTIONS)

    @object_remark_options.setter
    def object_remark_options(self, options: list[str]) -> None:
        self._settings.setValue("object_remark_options", json.dumps(list(options)))

    @property
    def image_remark_options(self) -> list[str]:
        return self._remark_options("image_remark_options", DEFAULT_IMAGE_REMARK_OPTIONS)

    @image_remark_options.setter
    def image_remark_options(self, options: list[str]) -> None:
        self._settings.setValue("image_remark_options", json.dumps(list(options)))

    def _remark_options(self, key: str, defaults: list[str]) -> list[str]:
        # the defaults apply until the user saves their own list (which may be empty);
        # stored as JSON because QSettings round-trips empty/single-item lists unreliably
        if not self._settings.contains(key):
            return list(defaults)
        try:
            options = json.loads(self._settings.value(key, "[]", type=str))
        except ValueError:
            return list(defaults)
        return [str(option) for option in options] if isinstance(options, list) else []
