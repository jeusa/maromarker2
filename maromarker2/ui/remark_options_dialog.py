"""Dialog to define the selectable options for the object remark and image remark fields."""

from __future__ import annotations

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QPlainTextEdit, QVBoxLayout

from maromarker2.ui.app_settings import AppSettings


def parse_options(text: str) -> list[str]:
    """One option per line; blank lines and repeated options are dropped."""
    options: list[str] = []
    for line in text.splitlines():
        option = line.strip()
        if option and option not in options:
            options.append(option)
    return options


class RemarkOptionsDialog(QDialog):
    """Edits the preset remark choices (one per line) of both remark types."""

    def __init__(self, settings: AppSettings | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Remark options")
        self.resize(480, 420)

        self._settings = settings or AppSettings()

        self.edit_object_options = QPlainTextEdit("\n".join(self._settings.object_remark_options))
        self.edit_image_options = QPlainTextEdit("\n".join(self._settings.image_remark_options))

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Object remark options (one per line)"))
        layout.addWidget(self.edit_object_options)
        layout.addWidget(QLabel("Image remark options (one per line)"))
        layout.addWidget(self.edit_image_options)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_accept(self) -> None:
        self._settings.object_remark_options = parse_options(self.edit_object_options.toPlainText())
        self._settings.image_remark_options = parse_options(self.edit_image_options.toPlainText())
        self.accept()
