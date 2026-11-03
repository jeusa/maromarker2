"""Pandas-backed annotation store with the legacy MarOMarker CSV schema.

The column layout and sentinel values exactly match
``fish_detection/MarOMarker/src/Models.py`` (``TableModel.getColumns`` /
``_create_row``) so CSV files remain interchangeable with the legacy app.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping, Sequence

import pandas as pd

from maromarker2.models.taxonomy import merge_classes

# exact column order of the legacy TableModel.getColumns()
COLUMNS: list[str] = [
    "file_id", "object_remarks", "group", "subgroup", "species",
    "LX1", "LY1", "LX2", "LY2", "LX3", "LY3", "LX4", "LY4",
    "RX1", "RY1", "RX2", "RY2", "RX3", "RY3", "RX4", "RY4",
    "length", "height", "image_remarks", "status",
    "manually_corrected", "experiment_id", "user_id",
]

# legacy sentinel for "no value" (both the float coordinate/length columns and int-ish ones)
NO_VALUE = -1.0

_POINT_COLUMNS_L = {"head": ("LX1", "LY1"), "tail": ("LX2", "LY2"), "width_start": ("LX3", "LY3"), "width_end": ("LX4", "LY4")}
_POINT_COLUMNS_R = {"head": ("RX1", "RY1"), "tail": ("RX2", "RY2"), "width_start": ("RX3", "RY3"), "width_end": ("RX4", "RY4")}


def file_id_from_path(image_path: str | os.PathLike) -> str:
    """Derive the shared L/R ``file_id`` from an image path, mirroring the legacy stripping logic."""
    stem = Path(image_path).name
    for ext in (".jpg", ".jpeg", ".png"):
        if stem.lower().endswith(ext):
            stem = stem[: -len(ext)]
            break
    for suffix in ("_L", "_R", "_l", "_r"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    return stem


def _as_bool(column: pd.Series) -> pd.Series:
    """A True/False column however it was read ("True"/"true" strings, bools, or blanks/NaN
    for legacy files, which count as False)."""
    return column.map(lambda value: str(value).strip().lower() == "true").astype(bool)


def _migrate_legacy_columns(df: pd.DataFrame) -> pd.DataFrame:
    """CSVs written before subgroups existed lack the "subgroup" column: add it (empty)
    right after "group" so they load like current files."""
    if "subgroup" not in df.columns and list(df.columns) == [c for c in COLUMNS if c != "subgroup"]:
        df = df.copy()
        df.insert(COLUMNS.index("subgroup"), "subgroup", "")
    return df


class AnnotationStore:
    """Holds the full annotation table (one row per animal) for a project."""

    def __init__(self, data: pd.DataFrame | None = None, experiment_id: str = "", user_id: str = ""):
        self.data: pd.DataFrame = data if data is not None else pd.DataFrame(columns=COLUMNS).astype(
            {"manually_corrected": bool}
        )
        # stamped onto every row that is added
        self.experiment_id = experiment_id
        self.user_id = user_id
        # row indices are never reused, so undo snapshots of other images can't collide with new rows
        self._next_index: int = self._max_index() + 1
        # The CSV keeps an image's remark only on its animal rows, so a remark on an image
        # without animals lives here (for this session) until the first animal is added.
        self._rowless_image_remarks: dict[str, str] = {}
        # Model-predicted boxes (x1, y1, x2, y2) per (row index, side), shown instead of the box
        # derived from the keypoints until those are moved. Never written to the CSV; entries of
        # removed rows stay so that undo can bring the animal back with its box.
        self._predicted_boxes: dict[tuple[int, str], tuple[float, float, float, float]] = {}

    def _max_index(self) -> int:
        return -1 if self.data.empty else int(self.data.index.max())

    # --- loading / saving ------------------------------------------------ #
    @classmethod
    def load(cls, path: str | os.PathLike) -> "AnnotationStore":
        df = _migrate_legacy_columns(pd.read_csv(path, sep=","))
        if list(df.columns) != COLUMNS:
            raise ValueError(
                f"Cannot load {path}: expected columns {COLUMNS}, got {list(df.columns)}"
            )
        # normalize group/subgroup/species casing: older CSVs mix "Fish"/"fish"-style capitalization,
        # which must match the (all-lowercase) taxonomy entries for the editor's combo boxes.
        for col in ("group", "subgroup", "species"):
            df[col] = df[col].astype(object).fillna("").astype(str).str.lower()
        # all-empty ID columns are read as float64 (NaN), which can't hold the user's name
        for col in ("object_remarks", "image_remarks", "experiment_id", "user_id"):
            df[col] = df[col].astype(object).fillna("").astype(str)
        df["manually_corrected"] = _as_bool(df["manually_corrected"])
        return cls(df)

    @classmethod
    def load_or_create(cls, path: str | os.PathLike) -> "AnnotationStore":
        if os.path.isfile(path):
            return cls.load(path)
        return cls()

    def save(self, path: str | os.PathLike) -> None:
        """Overwrite the full CSV file with the current in-memory table (sorted by ``file_id``)."""
        self._output_frame(self.data).to_csv(path, sep=",", index=False)

    @staticmethod
    def _output_frame(data: pd.DataFrame) -> pd.DataFrame:
        """A copy of ``data`` ready for writing: sorted by ``file_id``, and with length/height
        cleared for animals that are not matched (they can only be measured with both sides)."""
        out = data.sort_values(by="file_id", kind="stable").copy()
        unmatched = ~((out["LX1"] > 0) & (out["RX1"] > 0))
        out.loc[unmatched, ["length", "height"]] = NO_VALUE
        return out

    def save_incremental(self, path: str | os.PathLike, file_id: str) -> None:
        """Update only the rows for ``file_id`` in an existing CSV, like legacy ``exportToCsv``.

        If the file does not exist yet, falls back to writing the whole table.
        """
        if not os.path.isfile(path):
            self.save(path)
            return

        on_disk = _migrate_legacy_columns(pd.read_csv(path, sep=","))
        on_disk = on_disk[on_disk["file_id"] != file_id]
        current = self.data[self.data["file_id"] == file_id]
        combined = pd.concat([on_disk, current], ignore_index=True)
        self._output_frame(combined).to_csv(path, sep=",", index=False)

    # --- queries ----------------------------------------------------------- #
    def rows_for_file(self, file_id: str) -> pd.DataFrame:
        return self.data[self.data["file_id"] == file_id]

    def unmatched(self, file_id: str, side: str) -> pd.DataFrame:
        """Rows for ``file_id`` that have no counterpart on the given side ("L" or "R")."""
        rows = self.rows_for_file(file_id)
        x_col = "LX1" if side == "L" else "RX1"
        return rows[rows[x_col].isna() | (rows[x_col] <= 0)]

    def image_remark(self, file_id: str) -> str:
        """The remark of the image pair: the first non-empty remark among its animals' rows."""
        rows = self.rows_for_file(file_id)
        if rows.empty:
            return self._rowless_image_remarks.get(file_id, "")
        remarks = rows["image_remarks"].astype(object).fillna("").astype(str)
        return next((remark for remark in remarks if remark), "")

    def is_image_checked(self, file_id: str) -> bool:
        """True if the image pair has animals and none of them has the status "unchecked"."""
        rows = self.rows_for_file(file_id)
        return not rows.empty and not bool((rows["status"].astype(str).str.lower() == "unchecked").any())

    def image_overview(self, file_ids: Sequence[str]) -> dict[str, tuple[int, bool, str]]:
        """``{file_id: (animal_count, is_image_checked, image_remark)}`` for many images at once."""
        counts: dict[str, int] = {}
        checked: dict[str, bool] = {}
        remarks: dict[str, str] = {}
        if not self.data.empty:
            file_column = self.data["file_id"]
            counts = file_column.value_counts().to_dict()
            checked = (self.data["status"].astype(str).str.lower() != "unchecked").groupby(file_column).all().to_dict()
            remark_column = self.data["image_remarks"].astype(object).fillna("").astype(str)
            remarks = remark_column.where(remark_column != "").groupby(file_column).first().fillna("").to_dict()
        return {
            file_id: (
                int(counts.get(file_id, 0)),
                bool(checked.get(file_id, False)),
                remarks[file_id] if file_id in counts else self._rowless_image_remarks.get(file_id, ""),
            )
            for file_id in file_ids
        }

    # --- mutation ----------------------------------------------------------- #
    def predicted_box(self, index: int, side: str) -> tuple[float, float, float, float] | None:
        return self._predicted_boxes.get((index, side))

    def set_predicted_box(self, index: int, side: str, box: Sequence[float]) -> None:
        self._predicted_boxes[(index, side)] = tuple(float(v) for v in box)

    def clear_predicted_box(self, index: int, side: str) -> None:
        self._predicted_boxes.pop((index, side), None)

    def _copy_predicted_box(self, source: int, target: int, side: str) -> None:
        box = self._predicted_boxes.get((source, side))
        if box is not None:
            self._predicted_boxes[(target, side)] = box

    def set_image_remark(self, file_id: str, remark: str) -> None:
        """Sets the remark on every animal row of the image pair (the CSV has no per-image rows)."""
        rows = self.rows_for_file(file_id)
        if rows.empty:
            self._rowless_image_remarks[file_id] = remark
            return
        self._rowless_image_remarks.pop(file_id, None)
        for index in rows.index:
            self.update_row(index, image_remarks=remark)

    def add_row(self, row: Mapping[str, object]) -> int:
        """Append a single row (dict keyed by column name, missing columns default to NO_VALUE/""). Returns new index.
        A row without an image remark takes over the remark of its image pair."""
        full_row = self._fill_defaults(row)
        if not full_row["image_remarks"]:
            full_row["image_remarks"] = self.image_remark(full_row["file_id"])
        self._stamp_ids(full_row)
        new_index = max(self._next_index, self._max_index() + 1)
        self._next_index = new_index + 1
        new_df = pd.DataFrame([full_row], columns=COLUMNS, index=[new_index])
        self.data = pd.concat([self.data, new_df])
        return new_index

    def replace_file_rows(self, file_id: str, rows: pd.DataFrame) -> None:
        """Replace all rows of ``file_id`` with ``rows`` (e.g. an undo snapshot); other files are untouched."""
        rest = self.data[self.data["file_id"] != file_id]
        self.data = pd.concat([rest, rows]).sort_index()
        self._next_index = max(self._next_index, self._max_index() + 1)

    def update_row(self, index: int, *, stamp: bool = True, **fields) -> None:
        """Set fields on a row; unless ``stamp`` is False, also stamp it with the current user/experiment."""
        if stamp:
            self._stamp_ids(fields)
        for key, value in fields.items():
            self.data.loc[index, key] = value

    def mark_file_checked(self, file_id: str) -> bool:
        """Sets every "unchecked" row of the image pair to "checked" (other statuses stay), without
        stamping user/experiment ids. Returns True if any row changed."""
        rows = self.rows_for_file(file_id)
        changed = rows.index[rows["status"].astype(str).str.lower() == "unchecked"]
        for index in changed:
            self.update_row(index, stamp=False, status="checked")
        return len(changed) > 0

    def mark_file_unchecked(self, file_id: str) -> bool:
        """Sets every "checked" row of the image pair that was not manually corrected to "unchecked"
        (other statuses stay), without stamping user/experiment ids. Returns True if any row changed."""
        rows = self.rows_for_file(file_id)
        changed = rows.index[
            (rows["status"].astype(str).str.lower() == "checked") & ~rows["manually_corrected"].astype(bool)
        ]
        for index in changed:
            self.update_row(index, stamp=False, status="unchecked")
        return len(changed) > 0

    def remove_row(self, index: int) -> None:
        """No-op if ``index`` is already gone. A matched animal has its row removed
        once per scene (L and R each call this for the same shared row_index), so
        this must tolerate being called more than once for the same index."""
        if index in self.data.index:
            file_id = self.data.loc[index, "file_id"]
            remark = self.image_remark(file_id)
            self.data = self.data.drop(index=index)
            if remark and self.rows_for_file(file_id).empty:
                self._rowless_image_remarks[file_id] = remark

    def build_row(
        self,
        file_id: str,
        group: str = "",
        subgroup: str = "",
        species: str = "",
        remark: str = "",
        left: Mapping[str, Sequence[float]] | None = None,
        right: Mapping[str, Sequence[float]] | None = None,
        length: float = NO_VALUE,
        height: float = NO_VALUE,
        image_remark: str = "",
        status: str = "checked",
        manually_corrected: bool = True,
        experiment_id: str = "",
        user_id: str = "",
    ) -> dict:
        """Build a row dict for one animal.

        ``left``/``right`` are optional mappings with any of the keys "head", "tail",
        "width_start", "width_end" -> (x, y). Omitted keys/sides default to NO_VALUE,
        i.e. an unmatched animal (only one side given) or a matched pair (both given).
        """
        row: dict = {col: NO_VALUE for col in COLUMNS}
        row.update({
            "file_id": file_id,
            "object_remarks": remark,
            "group": group,
            "subgroup": subgroup,
            "species": species,
            "length": length,
            "height": height,
            "image_remarks": image_remark,
            "status": status,
            "manually_corrected": bool(manually_corrected),
            "experiment_id": experiment_id,
            "user_id": user_id,
        })
        for side_points, columns in ((left, _POINT_COLUMNS_L), (right, _POINT_COLUMNS_R)):
            if not side_points:
                continue
            for key, (x_col, y_col) in columns.items():
                point = side_points.get(key)
                if point is not None:
                    row[x_col], row[y_col] = float(point[0]), float(point[1])
        return row

    def merged_classes(self, index_left: int, index_right: int) -> tuple[str, str, str] | None:
        """The (group, subgroup, species) a match of the two rows would get, or None if their classes disagree."""
        left, right = self.data.loc[index_left], self.data.loc[index_right]
        return merge_classes(
            left["group"], left["subgroup"], left["species"],
            right["group"], right["subgroup"], right["species"],
        )

    def merge_as_match(self, index_left: int, index_right: int) -> int:
        """Merge an unmatched-L row and an unmatched-R row into one matched row; drops the R row.
        The merged row gets the deepest class of the two; raises ValueError if the classes disagree."""
        classes = self.merged_classes(index_left, index_right)
        if classes is None:
            raise ValueError("Animals have differing classes")
        right = self.data.loc[index_right]
        for col in ("RX1", "RY1", "RX2", "RY2", "RX3", "RY3", "RX4", "RY4"):
            self.data.loc[index_left, col] = right[col]
        for col, value in zip(("group", "subgroup", "species"), classes):
            self.data.loc[index_left, col] = value
        self._copy_predicted_box(index_right, index_left, "R")
        self.remove_row(index_right)
        self.update_row(index_left)
        return index_left

    def split_match(self, index: int) -> int:
        """Split a matched row back into a left-only row (kept) and a new right-only row. Returns the new row's index."""
        row = self.data.loc[index].to_dict()
        right_row = dict(row)
        for col in ("LX1", "LY1", "LX2", "LY2", "LX3", "LY3", "LX4", "LY4"):
            right_row[col] = NO_VALUE
        right_row["length"] = right_row["height"] = NO_VALUE
        for col in ("RX1", "RY1", "RX2", "RY2", "RX3", "RY3", "RX4", "RY4", "length", "height"):
            self.data.loc[index, col] = NO_VALUE
        self.update_row(index)
        new_index = self.add_row(right_row)
        self._copy_predicted_box(index, new_index, "R")
        return new_index

    @staticmethod
    def _fill_defaults(row: Mapping[str, object]) -> dict:
        full = {col: NO_VALUE for col in COLUMNS}
        full.update({
            "file_id": "", "object_remarks": "", "group": "", "subgroup": "", "species": "",
            "image_remarks": "", "status": "checked", "manually_corrected": True,
            "experiment_id": "", "user_id": "",
        })
        full.update(row)
        return full

    def _stamp_ids(self, fields: dict) -> None:
        if self.experiment_id:
            fields["experiment_id"] = self.experiment_id
        if self.user_id:
            fields["user_id"] = self.user_id
