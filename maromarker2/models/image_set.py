"""Folder-based stereo image set loading (replaces the legacy calendar widget).

Images are expected to follow the ``{prefix}_{yyyy.MM.dd}_{HH.MM.ss}_{L|R}.{ext}``
naming convention used by the camera rig (see ``data/example_images``). The date/time
is optional for loading to succeed, but required for chronological sorting and for
auto-naming the output CSV.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

DEFAULT_EXTENSIONS = (".jpg", ".jpeg", ".png")

_SIDE_SUFFIX_RE = re.compile(r"_(L|R)$", re.IGNORECASE)
_DATETIME_RE = re.compile(r"(\d{4}\.\d{2}\.\d{2})_(\d{2}\.\d{2}\.\d{2})")


@dataclass
class ImagePair:
    """A left/right image pair (either side may be missing) sharing one ``file_id``."""

    file_id: str
    left: Path | None = None
    right: Path | None = None
    timestamp: datetime | None = None

    @property
    def is_stereo(self) -> bool:
        return self.left is not None and self.right is not None


def parse_stem(stem: str) -> tuple[str, str | None]:
    """Split a filename stem into (base file_id, side) where side is "L", "R" or None."""
    match = _SIDE_SUFFIX_RE.search(stem)
    if not match:
        return stem, None
    return stem[: match.start()], match.group(1).upper()


def parse_timestamp(base_file_id: str) -> datetime | None:
    """Extract a ``yyyy.MM.dd_HH.MM.ss`` timestamp embedded in a file_id, if present."""
    match = _DATETIME_RE.search(base_file_id)
    if not match:
        return None
    try:
        return datetime.strptime(f"{match.group(1)}_{match.group(2)}", "%Y.%m.%d_%H.%M.%S")
    except ValueError:
        return None


def scan_image_set(
    root: str | Path,
    prefix: str = "",
    extensions: tuple[str, ...] = DEFAULT_EXTENSIONS,
    recursive: bool = False,
) -> list[ImagePair]:
    """Scan ``root`` for images, group them into L/R pairs by file_id, sorted chronologically."""
    root = Path(root)
    pattern = "**/*" if recursive else "*"
    pairs: dict[str, ImagePair] = {}

    for path in sorted(root.glob(pattern)):
        if not path.is_file() or path.suffix.lower() not in extensions:
            continue
        if prefix and not path.name.startswith(prefix):
            continue

        base, side = parse_stem(path.stem)
        pair = pairs.setdefault(base, ImagePair(file_id=base))
        if side == "L":
            pair.left = path
        elif side == "R":
            pair.right = path
        else:
            # no L/R suffix: treat as a mono image, kept in the left slot
            pair.left = pair.left or path
        if pair.timestamp is None:
            pair.timestamp = parse_timestamp(base)

    return sorted(pairs.values(), key=lambda p: (p.timestamp is None, p.timestamp, p.file_id))


def suggest_output_filename(pairs: list[ImagePair]) -> str:
    """Derive a default output CSV name from the date range of a scanned image set."""
    timestamps = [p.timestamp for p in pairs if p.timestamp is not None]
    if not timestamps:
        return "results.csv"

    start, end = min(timestamps), max(timestamps)
    if (start.year, start.month) == (end.year, end.month):
        return f"results_{start.year:04d}_{start.month:02d}.csv"
    return f"results_{start:%Y-%m-%d}_to_{end:%Y-%m-%d}.csv"
