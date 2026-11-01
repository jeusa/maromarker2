"""QApplication bootstrap. Run with: python -m maromarker2.main"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from maromarker2 import defaults
from maromarker2.ui.app_settings import AppSettings
from maromarker2.ui.main_window import MainWindow
from maromarker2.ui.project_setup_dialog import ProjectSetupDialog
from maromarker2.ui.theme import apply_theme

LOG_DIR = Path.home() / ".maromarker2"
LOG_FILE = LOG_DIR / "maromarker2.log"
ICON_PATH = Path(__file__).parent / "resources" / "maromarker2.svg"


def _configure_logging() -> None:
    """Logs to stdout AND a rotating file. The file handler exists so that logs
    survive a native crash (e.g. a Qt/PySide6 segfault) that takes stdout down with
    it — after a crash, check LOG_FILE for the last logged action."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    file_handler = RotatingFileHandler(LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3)
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logging.basicConfig(level=logging.INFO, handlers=[stream_handler, file_handler])


_configure_logging()


def main() -> int:
    app = QApplication(sys.argv)
    app.setOrganizationName("MarOMarker")
    app.setApplicationName("MarOMarker2")
    app.setDesktopFileName("maromarker2")
    if ICON_PATH.is_file():
        app.setWindowIcon(QIcon(str(ICON_PATH)))

    settings = AppSettings()
    try:
        defaults.ensure_user_defaults()
    except Exception:
        logging.getLogger(__name__).warning("Could not set up the user defaults folder", exc_info=True)
    apply_theme(app, settings.dark_theme)
    setup_dialog = ProjectSetupDialog(settings)
    if not setup_dialog.exec() or setup_dialog.project_config is None:
        return 0

    window = MainWindow(setup_dialog.project_config, settings)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
