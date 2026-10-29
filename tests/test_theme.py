import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication

from maromarker2.ui.theme import HIGHLIGHT, apply_theme


def test_apply_theme_sets_dark_and_light_palettes():
    app = QApplication.instance() or QApplication([])
    apply_theme(app, True)
    assert app.palette().color(QPalette.Window).lightness() < 100
    apply_theme(app, False)
    assert app.palette().color(QPalette.Window).lightness() > 200
    assert app.palette().color(QPalette.Highlight).name() == HIGHLIGHT


def test_dark_theme_setting_roundtrip(tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings
    import maromarker2.ui.app_settings as app_settings

    ini = str(tmp_path / "s.ini")
    monkeypatch.setattr(app_settings, "QSettings", lambda *a, **k: QSettings(ini, QSettings.IniFormat))
    settings = app_settings.AppSettings()
    assert settings.dark_theme is True
    settings.dark_theme = False
    assert app_settings.AppSettings().dark_theme is False
