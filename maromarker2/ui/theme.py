"""Application-wide look: Fusion style + explicit palettes, identical on every OS."""

from __future__ import annotations

from string import Template

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QTableView

HIGHLIGHT = "#308cc6"

_COLORS = {
    True: dict(window="#2b2d30", base="#262729", alt="#26282b", text="#e6e6e6", button="#3c3f43",
               border="#a9aeb4", hover="#4e5258", pressed="#2f3236", disabled="#7d8187", separator="#c8ccd0"),
    False: dict(window="#efefef", base="#ffffff", alt="#f5f5f5", text="#1e1e1e", button="#fafafa",
                border="#7a7a7a", hover="#e2e8ee", pressed="#d0d0d0", disabled="#9a9a9a", separator="#5a5a5a"),
}

_STYLESHEET = Template("""
QToolBar { spacing: 4px; }
QToolButton {
    background: $button; color: $text;
    border: 1px solid $border; border-radius: 3px; padding: 3px 8px;
}
QToolButton:hover { background: $hover; border-color: $highlight; }
QToolButton:pressed { background: $pressed; }
QToolButton:disabled { color: $disabled; background: transparent; border-color: $disabled; }
QToolButton:checked { background: $highlight; color: #ffffff; border-color: $highlight; }
QToolButton:checked:hover { border-color: #ffffff; }
QFrame#viewSeparator { background: $separator; border-radius: 2px; }
QTableView { gridline-color: $border; }
QHeaderView::section {
    background: $button; color: $text; padding: 2px 6px;
    border: 1px solid $border; border-left: 0;
}
QHeaderView#tableOutline::section:vertical {
    background: $border; border: 0; padding: 0; color: transparent;
}
QTableView QTableCornerButton::section { background: $border; border: 0; }
""")


def add_left_outline(table: QTableView) -> None:
    """Gives a table a light line along the left edge of its header and rows only.

    The table widget's own frame can't be styled per side without also outlining the
    empty area around the content, so a 1px-wide vertical header (one section per row,
    painted in the border colour) plays the part of the left border instead."""
    header = table.verticalHeader()
    header.setObjectName("tableOutline")
    header.setVisible(True)
    header.setFixedWidth(1)
    header.setSectionsClickable(False)
    header.setHighlightSections(False)
    table.horizontalHeader().setHighlightSections(False)


def apply_theme(app: QApplication, dark: bool) -> None:
    c = _COLORS[dark]
    app.setStyle("Fusion")

    palette = QPalette()
    for role, color in (
        (QPalette.Window, c["window"]), (QPalette.WindowText, c["text"]),
        (QPalette.Base, c["base"]), (QPalette.AlternateBase, c["alt"]),
        (QPalette.Text, c["text"]), (QPalette.Button, c["button"]), (QPalette.ButtonText, c["text"]),
        (QPalette.ToolTipBase, c["base"]), (QPalette.ToolTipText, c["text"]),
        (QPalette.Highlight, HIGHLIGHT), (QPalette.HighlightedText, "#ffffff"),
        (QPalette.PlaceholderText, c["disabled"]), (QPalette.Link, HIGHLIGHT),
    ):
        palette.setColor(role, QColor(color))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        palette.setColor(QPalette.Disabled, role, QColor(c["disabled"]))
    app.setPalette(palette)

    app.setStyleSheet(_STYLESHEET.substitute(c, highlight=HIGHLIGHT))
