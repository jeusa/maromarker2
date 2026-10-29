"""Color hints that flag unset animal data in the lists and on the canvases.

CLASS (Edit/AI mode): class fields left "unidentified" are tinted red; an animal with
every class field unidentified gets bright red bbox/length/width lines.
MATCH (Match mode): unmatched animals are tinted yellow in the "Matched" column and get
bright yellow bbox/length/width lines.
"""

from __future__ import annotations

from enum import Enum, auto

from PySide6.QtGui import QColor

from maromarker2.models.taxonomy import UNIDENTIFIED

CLASS_FIELDS = ("group", "subgroup", "species")

UNSET_CLASS_TINT = QColor(255, 0, 0, 90)
UNMATCHED_TINT = QColor(255, 215, 0, 110)
UNSET_CLASS_LINE_COLOR = QColor("#ff0000")
UNMATCHED_LINE_COLOR = QColor("#ffff00")


class ColorHint(Enum):
    CLASS = auto()
    MATCH = auto()


def is_unidentified(row, field: str) -> bool:
    return (str(row.get(field) or "").lower() or UNIDENTIFIED) == UNIDENTIFIED


def is_matched(row) -> bool:
    return float(row.get("LX1", -1)) > 0 and float(row.get("RX1", -1)) > 0


def all_classes_unidentified(row, class_fields: tuple[str, ...] = CLASS_FIELDS) -> bool:
    return all(is_unidentified(row, field) for field in class_fields)
