"""Composes the L/R canvases + side panel + toolbar into the keypoint editor.

This is the main deliverable of Phase 5: navigate scanned image pairs, add/select/
remove/drag animal keypoints per side, and edit the selected animal's group/species/
remark through the SidePanel — all backed directly by the shared AnnotationStore.
"""

from __future__ import annotations

from contextlib import contextmanager
from enum import Enum, auto
from pathlib import Path

from PySide6.QtCore import Qt, QEvent, QPointF, QThread, Signal
from PySide6.QtGui import (
    QAction, QActionGroup, QIcon, QKeySequence, QPainter, QPixmap, QPolygonF, QUndoCommand, QUndoStack,
)
from PySide6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QMessageBox, QSizePolicy, QSplitter, QToolBar,
    QVBoxLayout, QWidget,
)

from maromarker2.geometry.matching import MatchCandidate, match_candidates
from maromarker2.models.annotation_store import AnnotationStore
from maromarker2.models.camera_calibration import CameraCalibration
from maromarker2.models.image_set import ImagePair
from maromarker2.models.taxonomy import Taxonomy
from maromarker2.predictors import KeypointPredictor, PredictedAnimal
from maromarker2.predictors.onnx_predictor import OnnxKeypointPredictor
from maromarker2.ui.editor.animal_list_panel import AnimalListPanel
from maromarker2.ui.editor.annotation_scene import AnnotationScene, Tool
from maromarker2.ui.editor.color_hints import CLASS_FIELDS, ColorHint
from maromarker2.ui.editor.editor_settings import EditorSettings
from maromarker2.ui.editor.editor_settings_dialog import EditorSettingsDialog
from maromarker2.ui.editor.image_canvas import ImageCanvas
from maromarker2.ui.editor.image_adjustments import ImageAdjustmentPanel, ImageAdjustments
from maromarker2.ui.editor.image_list_panel import ImageListPanel
from maromarker2.ui.editor.info_panel import InfoPanel
from maromarker2.ui.editor.match_review_dialog import MatchReviewDialog
from maromarker2.ui.editor.matched_list_panel import MatchedListPanel
from maromarker2.ui.editor.side_panel import SidePanel
from maromarker2.workers.prediction_worker import PredictionJob, PredictionWorker
from maromarker2.workers.triangulation_worker import TriangulationWorker

# each undo step stores two snapshots of one image pair's rows
UNDO_LIMIT = 100
SIDE_PANEL_TOP_SPACING = 10  # px between the toolbar and the side panel columns

MISMATCH_MESSAGE = "Matching not possible: Animals have differing classes"
MISMATCH_MESSAGE_TIMEOUT_MS = 5000
EDITING_LOCKED_HINT = "No editing or saving possible during prediction..."

# a predicted animal whose head and tail are all within this many pixels of an existing
# animal's (same side) is considered already annotated
DUPLICATE_TOLERANCE_PX = 20.0


class Mode(Enum):
    """The high-level interaction modes for the editor; each shows a different
    set of tool buttons in the main toolbar (see EditorWidget._set_mode)."""

    EDIT = auto()
    MATCH = auto()
    AI = auto()


class _SnapshotCommand(QUndoCommand):
    """Restores the rows of one image pair (``file_id``) to a before/after snapshot.

    The undo stack spans all image pairs; undoing/redoing navigates to the pair the
    change belongs to, unless ``navigate`` is False (then other pairs are changed in the background)."""

    def __init__(
        self, editor: "EditorWidget", file_id: str, before_rows, after_rows,
        before_selected, after_selected, text: str, navigate: bool = True,
    ):
        super().__init__(text)
        self._editor = editor
        self._file_id = file_id
        self._before_rows = before_rows
        self._after_rows = after_rows
        self._before_selected = before_selected
        self._after_selected = after_selected
        self._navigate = navigate
        self._applied = True

    def redo(self) -> None:
        # QUndoStack.push() calls redo() right away, but the "after" state has already been
        # applied (and the UI refreshed) by whoever recorded the change.
        if self._applied:
            self._applied = False
            return
        self._editor._restore_snapshot(self._file_id, self._after_rows, self._after_selected, self._navigate)

    def undo(self) -> None:
        self._editor._restore_snapshot(self._file_id, self._before_rows, self._before_selected, self._navigate)


class EditorWidget(QWidget):
    prediction_started = Signal(int)  # number of image pairs to predict
    prediction_progress = Signal(int, int)  # image pairs done, total image pairs
    prediction_finished = Signal()

    def __init__(
        self,
        store: AnnotationStore,
        taxonomy: Taxonomy,
        image_pairs: list[ImagePair],
        calibration: CameraCalibration | None = None,
        editor_settings: EditorSettings | None = None,
        ai_model_path: str | Path | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.store = store
        self.taxonomy = taxonomy
        self.image_pairs = image_pairs
        self.calibration = calibration
        self.editor_settings = editor_settings if editor_settings is not None else EditorSettings()
        self.ai_model_path = Path(ai_model_path) if ai_model_path else None
        self._predictor: KeypointPredictor | None = None
        self._prediction_thread: QThread | None = None
        self._prediction_worker: PredictionWorker | None = None
        self._editing_locked = False
        self._prediction_cancelled = False
        self._prediction_macro_open = False
        self._prediction_added = 0
        self._prediction_description = ""
        self._pair_index = -1
        self._mode = Mode.EDIT
        self._selected_rows: set[int] = set()
        self._pending_match_pick: tuple[str, int] | None = None
        self._undo_stack = QUndoStack(self)
        self._undo_stack.setUndoLimit(UNDO_LIMIT)

        class_fields = CLASS_FIELDS if taxonomy.has_subgroup_level else ("group", "species")
        self.scene_l = AnnotationScene(store, "L", self.editor_settings, self, class_fields)
        self.scene_r = AnnotationScene(store, "R", self.editor_settings, self, class_fields)
        self.canvas_l = ImageCanvas(self.scene_l)
        self.canvas_r = ImageCanvas(self.scene_r)
        self.canvas_l.set_linked(self.canvas_r)
        self.canvas_r.set_linked(self.canvas_l)
        self.list_l = AnimalListPanel("L", show_subgroup=taxonomy.has_subgroup_level)
        self.list_r = AnimalListPanel("R", show_subgroup=taxonomy.has_subgroup_level)
        self.side_panel = SidePanel(taxonomy)
        self.matched_list = MatchedListPanel(show_subgroup=taxonomy.has_subgroup_level)
        self.adjustment_panel = ImageAdjustmentPanel()
        self.adjustment_panel.adjustments_changed.connect(self._on_adjustments_changed)
        self.adjustment_panel.markers_hidden_changed.connect(self._on_markers_hidden_changed)
        self.info_panel = InfoPanel()
        self.image_list = ImageListPanel()
        self.image_list.set_pairs(image_pairs, store)
        self.image_list.pair_selected.connect(self.load_pair)
        self.image_list.remark_changed.connect(self._on_image_remark_edited)
        self.image_list.checked_toggled.connect(self._on_pair_checked_toggled)
        self.scene_l.animal_selected.connect(self._on_selection_changed)
        self.scene_r.animal_selected.connect(self._on_selection_changed)
        for scene in (self.scene_l, self.scene_r):
            scene.animal_added.connect(self._on_store_changed)
            scene.animal_removed.connect(self._on_store_changed)
            scene.animal_moved.connect(self._on_animal_moved)
            scene.store_changed.connect(self._on_scene_store_changed)
            scene.edit_blocked.connect(
                lambda: self.info_panel.show_message("Annotation markers are hidden: show them to edit")
            )

        for panel in (self.list_l, self.list_r):
            panel.animal_selected.connect(self._on_list_row_clicked)
            panel.rows_selected.connect(self._on_rows_selected)
        self.matched_list.matches_selected.connect(self._on_matched_list_selected)

        self.side_panel.group_changed.connect(
            lambda g, sg, s: self._update_selected(group=g, subgroup=sg, species=s)
        )
        self.side_panel.subgroup_changed.connect(lambda sg, s: self._update_selected(subgroup=sg, species=s))
        self.side_panel.species_changed.connect(lambda sg, s: self._update_selected(subgroup=sg, species=s))
        self.side_panel.remark_changed.connect(lambda v: self._update_selected(object_remarks=v))
        self.side_panel.status_changed.connect(self._set_selected_status)

        self.label_pair = QLabel("No images loaded")
        self.label_counts = QLabel("")

        mode_bar = self._build_mode_bar()
        toolbar = self._build_toolbar()

        # one shared vertical splitter for BOTH sides at once (dragging it trades
        # canvas height for list height identically on L and R, rather than each
        # side having its own independent handle); the "Fit" action (see
        # ImageCanvas.fit_to_window) keeps both canvases at the same zoom level
        # regardless of how much space the canvases row ends up with.
        #
        # the L/R divide is ALSO shared between the canvas row and the list row
        # (a "cross" splitter): Qt has no native 2D grid splitter, so canvas_split
        # and lists_split are two independent QSplitters kept in sync via
        # _sync_horizontal_splitters, mirroring the same linking pattern ImageCanvas
        # uses for zoom/pan (see set_linked).
        self.canvas_split = QSplitter(Qt.Horizontal)
        self.canvas_split.addWidget(self.canvas_l)
        self.canvas_split.addWidget(self.canvas_r)

        self.lists_split = QSplitter(Qt.Horizontal)
        self.lists_split.addWidget(self.list_l)
        self.lists_split.addWidget(self.list_r)

        self._linking_h_splitters = False
        # dragging the L/R divider by hand means the sides are no longer "equal"
        self.canvas_split.splitterMoved.connect(lambda *_: self._highlight_view_action(None))
        self.lists_split.splitterMoved.connect(lambda *_: self._highlight_view_action(None))
        self.canvas_split.splitterMoved.connect(
            lambda *_: self._sync_horizontal_splitters(self.canvas_split, self.lists_split)
        )
        self.lists_split.splitterMoved.connect(
            lambda *_: self._sync_horizontal_splitters(self.lists_split, self.canvas_split)
        )

        self.canvas_list_splitter = QSplitter(Qt.Vertical)
        self.canvas_list_splitter.addWidget(self.canvas_split)
        self.canvas_list_splitter.addWidget(self.lists_split)
        self.canvas_list_splitter.setStretchFactor(0, 4)
        self.canvas_list_splitter.setStretchFactor(1, 1)
        self.canvas_list_splitter.setSizes([4000, 1000])

        # the matched list sits in its own vertical splitter so it stays at the same
        # height as list_l/list_r, kept in sync with canvas_list_splitter the same
        # way canvas_split/lists_split are kept in sync above.
        matched_container = QWidget()
        matched_layout = QVBoxLayout(matched_container)
        matched_layout.setContentsMargins(0, 0, 0, 0)
        matched_layout.addWidget(QLabel("Matched"), 0)
        matched_layout.addWidget(self.matched_list, 1)

        # the info panel sits right below the side panel, followed by the image
        # adjustments panel at the bottom of the upper pane, directly above the
        # splitter handle that separates it from the matched list.
        side_container = QWidget()
        side_layout = QVBoxLayout(side_container)
        side_layout.setContentsMargins(0, 0, 0, 0)
        side_layout.addWidget(self.side_panel, 1)
        side_layout.addWidget(self._frame_edge(), 0)
        side_layout.addWidget(self.info_panel, 0)
        side_layout.addWidget(self._frame_edge(), 0)
        side_layout.addWidget(self.adjustment_panel, 0)
        side_layout.addWidget(self._frame_edge(), 0)

        self.side_list_splitter = QSplitter(Qt.Vertical)
        self.side_list_splitter.addWidget(side_container)
        self.side_list_splitter.addWidget(matched_container)
        self.side_list_splitter.setStretchFactor(0, 4)
        self.side_list_splitter.setStretchFactor(1, 1)
        self.side_list_splitter.setSizes([4000, 1000])

        self._linking_v_splitters = False
        self.canvas_list_splitter.splitterMoved.connect(
            lambda *_: self._sync_horizontal_splitters(self.canvas_list_splitter, self.side_list_splitter, "_linking_v_splitters")
        )
        self.side_list_splitter.splitterMoved.connect(
            lambda *_: self._sync_horizontal_splitters(self.side_list_splitter, self.canvas_list_splitter, "_linking_v_splitters")
        )

        # the toolbar spans the full width above main_split, so the image list and
        # side panel columns both start below it.
        # the image list and side panel columns sit a little lower than the canvases,
        # so they don't crowd the toolbar above them; a line at the top of each column
        # continues the canvases' top frame edge to the left and right.
        def with_top_spacer(widget: QWidget) -> QWidget:
            column = QWidget()
            column_layout = QVBoxLayout(column)
            column_layout.setContentsMargins(0, 0, 0, 0)
            column_layout.setSpacing(0)
            column_layout.addWidget(self._frame_edge(), 0)
            column_layout.addSpacing(SIDE_PANEL_TOP_SPACING)
            column_layout.addWidget(widget, 1)
            return column

        self.main_split = QSplitter(Qt.Horizontal)
        self.main_split.addWidget(with_top_spacer(self.image_list))
        self.main_split.addWidget(self.canvas_list_splitter)
        self.main_split.addWidget(with_top_spacer(self.side_list_splitter))
        self.main_split.setStretchFactor(0, 1)
        self.main_split.setStretchFactor(1, 4)
        self.main_split.setStretchFactor(2, 1)

        status_row = QHBoxLayout()
        status_row.addWidget(self.label_pair)
        status_row.addStretch()
        status_row.addWidget(self.label_counts)

        layout = QVBoxLayout(self)
        layout.addWidget(mode_bar, 0)
        layout.addWidget(toolbar, 0)
        layout.addWidget(self.main_split, 1)
        layout.addLayout(status_row, 0)

        if self.image_pairs:
            self.load_pair(0)
        self._update_counts()
        self._refresh_animal_lists()
        self._set_mode(Mode.EDIT)
        self._reset_view()

    # --- toolbar / tools --------------------------------------------------- #
    @staticmethod
    def _frame_edge() -> QFrame:
        """A 1px line in the same style as the canvases' frame edge."""
        edge = QFrame()
        edge.setFrameStyle(QFrame.StyledPanel | QFrame.Sunken)
        edge.setFixedHeight(1)
        return edge

    @staticmethod
    def _expanding_spacer() -> QWidget:
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        return spacer

    def _sync_horizontal_splitters(self, source: QSplitter, target: QSplitter, guard_attr: str = "_linking_h_splitters") -> None:
        """Keeps two splitters' divides at the same position, guarded against the
        feedback loop of each one's own move re-triggering the other. Used both for
        canvas_split/lists_split's shared L/R divide and canvas_list_splitter/
        side_list_splitter's shared canvas-row/list-row divide."""
        if getattr(self, guard_attr):
            return
        setattr(self, guard_attr, True)
        target.setSizes(source.sizes())
        setattr(self, guard_attr, False)

    def _triangle_icon(self, point_right: bool) -> QIcon:
        size = self._nav_icon_size
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(self.palette().buttonText().color())
        margin = size * 0.2
        tip = QPointF(size - margin, size / 2) if point_right else QPointF(margin, size / 2)
        base = QPointF(margin, margin) if point_right else QPointF(size - margin, margin)
        base2 = QPointF(margin, size - margin) if point_right else QPointF(size - margin, size - margin)
        painter.drawPolygon(QPolygonF([base, base2, tip]))
        painter.end()
        return QIcon(pixmap)

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        # redraw nav arrows on theme change with correct color
        if event.type() == QEvent.PaletteChange:
            self.action_prev.setIcon(self._triangle_icon(False))
            self.action_next.setIcon(self._triangle_icon(True))

    def _build_mode_bar(self) -> QToolBar:
        """A small toolbar, shown above the main toolbar, that switches between
        Edit mode (add/remove/drag keypoints) and Match mode (pick pairs to match,
        split existing matches); also hosts pair navigation and view (zoom/fit)
        controls, which apply regardless of the current mode."""
        mode_bar = QToolBar("Mode")

        mode_group = QActionGroup(self)
        mode_group.setExclusive(True)
        self.action_mode_edit = QAction("Edit Mode", self, checkable=True, checked=True)
        self.action_mode_match = QAction("Match Mode", self, checkable=True)
        self.action_mode_ai = QAction("AI Mode", self, checkable=True)
        for action, mode in (
            (self.action_mode_edit, Mode.EDIT), (self.action_mode_match, Mode.MATCH), (self.action_mode_ai, Mode.AI),
        ):
            action.setActionGroup(mode_group)
            action.triggered.connect(lambda checked, m=mode: self._set_mode(m))
            mode_bar.addAction(action)

        # --- center: pair navigation -------------------------------------------- #
        mode_bar.addWidget(self._expanding_spacer())

        self._nav_icon_size = mode_bar.iconSize().width() or 16
        self.action_prev = mode_bar.addAction(
            self._triangle_icon(False), "Prev", lambda: self.load_pair(self._pair_index - 1)
        )
        self.action_prev.setShortcut(QKeySequence(Qt.Key_PageUp))
        self.action_prev.setToolTip("Move to previous image pair")
        self.action_next = mode_bar.addAction(
            self._triangle_icon(True), "Next", lambda: self.load_pair(self._pair_index + 1)
        )
        self.action_next.setShortcut(QKeySequence(Qt.Key_PageDown))
        self.action_next.setToolTip("Move to next image pair")
        nav_buttons = [mode_bar.widgetForAction(action) for action in (self.action_prev, self.action_next)]
        for button in nav_buttons:
            button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        nav_buttons[1].setLayoutDirection(Qt.RightToLeft)
        nav_metrics = nav_buttons[0].fontMetrics()
        nav_width = self._nav_icon_size + 6 + max(
            nav_metrics.horizontalAdvance(a.text()) for a in (self.action_prev, self.action_next)
        ) + 30
        for button in nav_buttons:
            button.setFixedWidth(nav_width)
        mode_bar.addWidget(self._expanding_spacer())

        # --- right: view controls ------------------------------------------------ #
        self._view_actions: dict[tuple[bool, bool], QAction] = {}
        for text, sides, tooltip in (
            ("L", (True, False), "Show only the left image and list"),
            ("R", (False, True), "Show only the right image and list"),
            ("L|R", (True, True), "Show both sides at equal width"),
        ):
            action = mode_bar.addAction(text)
            action.setCheckable(True)
            action.setToolTip(tooltip)
            action.triggered.connect(
                lambda checked, s=sides: self._set_view_sides(left=s[0], right=s[1], equalize=s == (True, True))
            )
            self._view_actions[sides] = action

        # all three side-view buttons share one width and show the active view highlighted
        buttons = [mode_bar.widgetForAction(action) for action in self._view_actions.values()]
        # sized from the text plus room for the stylesheet padding/border (see ui/theme.py),
        # so the width is right whether or not the app stylesheet is applied yet
        metrics = buttons[0].fontMetrics()
        button_width = max(metrics.horizontalAdvance(action.text()) for action in self._view_actions.values()) + 30
        for button in buttons:
            button.setFixedWidth(button_width)

        # QToolBar's native separator is nearly invisible in many styles, so use an
        # explicit thick bar with a wider margin to set the two button groups apart
        separator_container = QWidget()
        separator_layout = QHBoxLayout(separator_container)
        separator_layout.setContentsMargins(14, 2, 14, 2)
        separator_line = QFrame()
        separator_line.setFrameShape(QFrame.NoFrame)
        separator_line.setFixedWidth(4)
        separator_line.setObjectName("viewSeparator")  # styled in ui/theme.py
        separator_layout.addWidget(separator_line)
        mode_bar.addWidget(separator_container)
        mode_bar.addAction("Reset View", self._reset_view).setToolTip(
            "Restore the default layout: all panels visible at their startup sizes")
        mode_bar.addAction("Fit", self._fit_views)
        mode_bar.addAction("Zoom In", lambda: self._zoom(1.25))
        mode_bar.addAction("Zoom Out", lambda: self._zoom(0.8))

        return mode_bar

    def _build_toolbar(self) -> QToolBar:
        """The editing toolbar: spans the full width, directly above the
        image list, canvases and side panel."""
        toolbar = QToolBar("Editor")

        # --- left: mode-specific tool/action buttons --------------------------- #
        tool_group = QActionGroup(self)
        tool_group.setExclusive(True)
        self.action_edit = QAction("Edit", self, checkable=True, checked=True)
        self.action_add = QAction("Add", self, checkable=True)
        self.action_remove = QAction("Remove", self, checkable=True)
        for action, tool, key in (
            (self.action_edit, Tool.EDIT, Qt.Key_E),
            (self.action_add, Tool.ADD, Qt.Key_A),
            (self.action_remove, Tool.REMOVE, Qt.Key_R),
        ):
            action.setToolTip(f"{action.text()} ({QKeySequence(key).toString()})")
            action.setActionGroup(tool_group)
            action.triggered.connect(lambda checked, t=tool: self._set_tool(t))
            toolbar.addAction(action)
            # the toolbar actions are hidden outside Edit mode, which disables their shortcuts,
            # so the keys live on separate always-active actions that also switch to Edit mode
            self._add_hidden_shortcut(key, lambda t=tool: self._activate_tool(t))
        self._add_hidden_shortcut(Qt.Key_M, lambda: self._switch_mode(Mode.MATCH))
        self.action_mode_match.setToolTip(f"Match Mode ({QKeySequence(Qt.Key_M).toString()})")

        self.action_split_match = toolbar.addAction("Split Match", self._split_selected)
        self.action_split_match.setShortcut(QKeySequence(Qt.Key_S))
        self.action_split_match.setToolTip(f"Split Match ({QKeySequence(Qt.Key_S).toString()})")
        self.action_auto_match = toolbar.addAction("Auto Match", self._auto_match)
        self.action_auto_match.setEnabled(self.calibration is not None)
        self.action_predict_selected = toolbar.addAction("Detect for selected", self._predict_selected)
        self.action_predict_selected.setToolTip("Detect animals for the current image pair with U-YOLO Pose model")
        self.action_predict_all = toolbar.addAction("Detect for all", self._predict_all)
        self.action_predict_all.setToolTip("Detect animals for all image pairs with with U-YOLO Pose model")

        # --- right: undo/redo ---------------------------------------------------- #
        toolbar.addWidget(self._expanding_spacer())
        self.action_undo = QAction("Undo", self)
        self.action_undo.setShortcut(QKeySequence("Ctrl+Z"))
        self.action_undo.triggered.connect(self._undo)
        self.action_redo = QAction("Redo", self)
        self.action_redo.setShortcut(QKeySequence("Ctrl+Y"))
        self.action_redo.triggered.connect(self._redo)
        self._update_undo_actions()
        self._undo_stack.canUndoChanged.connect(self._update_undo_actions)
        self._undo_stack.canRedoChanged.connect(self._update_undo_actions)
        toolbar.addAction(self.action_undo)
        toolbar.addAction(self.action_redo)

        # hidden (toolbar-less) shortcuts, mirroring the Escape-cancel pattern below
        action_delete = QAction(self)
        action_delete.setShortcut(QKeySequence(Qt.Key_Delete))
        action_delete.triggered.connect(self._delete_selected)
        self.addAction(action_delete)

        action_cancel = QAction(self)
        action_cancel.setShortcut(QKeySequence(Qt.Key_Escape))
        action_cancel.triggered.connect(self._cancel_pending)
        self.addAction(action_cancel)

        return toolbar

    def _add_hidden_shortcut(self, key, callback) -> None:
        action = QAction(self)
        action.setShortcut(QKeySequence(key))
        action.triggered.connect(lambda checked=False: callback())
        self.addAction(action)

    def _switch_mode(self, mode: Mode) -> None:
        if self._mode != mode:
            self._set_mode(mode)

    def _activate_tool(self, tool: Tool) -> None:
        """Keyboard shortcut for the Edit/Add/Remove tools; switches to Edit mode first if needed."""
        if self._editing_locked:
            return
        self._switch_mode(Mode.EDIT)
        {Tool.EDIT: self.action_edit, Tool.ADD: self.action_add, Tool.REMOVE: self.action_remove}[tool].setChecked(True)
        self._set_tool(tool)

    def _set_mode(self, mode: Mode) -> None:
        self._mode = mode
        self._pending_match = None
        is_edit = mode == Mode.EDIT
        is_match = mode == Mode.MATCH
        is_ai = mode == Mode.AI
        hint = ColorHint.MATCH if is_match else ColorHint.CLASS
        for scene in (self.scene_l, self.scene_r):
            scene.set_color_hint(hint)
        for panel in (self.list_l, self.list_r):
            panel.set_color_hint(hint)
        self._refresh_animal_lists()
        self.action_edit.setVisible(is_edit)
        self.action_add.setVisible(is_edit)
        self.action_remove.setVisible(is_edit)
        self.action_split_match.setVisible(is_match)
        self.action_auto_match.setVisible(is_match)
        self.action_predict_selected.setVisible(is_ai)
        self.action_predict_all.setVisible(is_ai)
        # QAction.setVisible(False) also disables the action, so re-apply the
        # state-dependent enabled flags whenever an action becomes visible again.
        self._update_editing_actions()
        if is_ai:
            self._update_prediction_actions()
        if is_edit:
            self.action_mode_edit.setChecked(True)
            self.action_edit.setChecked(True)
        elif is_match:
            self.action_mode_match.setChecked(True)
        else:
            self.action_mode_ai.setChecked(True)
        self._update_markers_movable()
        self._set_tool(Tool.EDIT)
        if is_match and len(self._selected_rows) == 1:
            # a single unmatched animal already selected becomes the pending match pick
            self._register_pick_if_unmatched(self._selected_row)

    def _update_markers_movable(self) -> None:
        movable = self._mode == Mode.EDIT and not self._editing_locked
        for scene in (self.scene_l, self.scene_r):
            scene.set_markers_movable(movable)

    def _update_editing_actions(self) -> None:
        unlocked = not self._editing_locked
        for action in (self.action_edit, self.action_add, self.action_remove, self.action_split_match):
            action.setEnabled(unlocked)
        self.action_auto_match.setEnabled(unlocked and self.calibration is not None)
        self._update_undo_actions()

    def _update_undo_actions(self) -> None:
        self.action_undo.setEnabled(self._undo_stack.canUndo() and not self._editing_locked)
        self.action_redo.setEnabled(self._undo_stack.canRedo() and not self._editing_locked)

    def _set_editing_locked(self, locked: bool) -> None:
        """Read-only mode (navigation, zoom and selection stay possible), used while a prediction runs."""
        self._editing_locked = locked
        self.side_panel.setEnabled(not locked)
        self.image_list.set_editing_locked(locked)
        self._update_editing_actions()
        self._update_markers_movable()
        self.action_edit.setChecked(True)
        self._set_tool(Tool.EDIT)
        self.info_panel.clear()  # a transient message would otherwise hide the persistent hint
        self._update_match_hint()

    def _set_tool(self, tool: Tool) -> None:
        self._pending_match = None
        for scene in (self.scene_l, self.scene_r):
            scene.cancel_pending_add()
            scene.tool = tool

    def _cancel_pending(self) -> None:
        """Escape: abandons any half-placed keypoints and deselects everything."""
        for scene in (self.scene_l, self.scene_r):
            scene.cancel_pending_add()
        self._selected_rows = set()
        self._apply_selection()

    def _zoom(self, factor: float) -> None:
        # canvas_l/canvas_r are linked (ImageCanvas.set_linked), so zooming one mirrors to the other.
        self.canvas_l.zoom_by(factor)

    def _set_view_sides(self, left: bool, right: bool, equalize: bool = False) -> None:
        """Shows/hides the left and right image columns (canvas + list) entirely;
        hidden widgets take no space in their QSplitter, so the other side fills it."""
        for widget, visible in (
            (self.canvas_l, left), (self.list_l, left), (self.canvas_r, right), (self.list_r, right),
        ):
            widget.setVisible(visible)
        self._highlight_view_action((left, right))
        if equalize:
            self.canvas_split.setSizes([1, 1])
            self.lists_split.setSizes([1, 1])
        self._fit_views()

    def _highlight_view_action(self, active: tuple[bool, bool] | None) -> None:
        for sides, action in self._view_actions.items():
            action.setChecked(sides == active)

    def _reset_view(self) -> None:
        """Restores the default layout: every panel visible, all splitters at their
        initial proportions. Also applied once at startup so both always agree."""
        self._set_view_sides(left=True, right=True, equalize=True)
        self.canvas_list_splitter.setSizes([4000, 1000])
        self.side_list_splitter.setSizes([4000, 1000])
        self.main_split.setSizes([1200, 4000, 1000])

    def _fit_views(self) -> None:
        self.canvas_l.fit_to_window()

    def open_appearance_settings(self) -> None:
        dialog = EditorSettingsDialog(self.editor_settings, parent=self)
        if dialog.exec():
            for scene in (self.scene_l, self.scene_r):
                scene.refresh()

    # --- pair navigation ------------------------------------------------------ #
    def pair_count(self) -> int:
        return len(self.image_pairs)

    @property
    def current_pair_index(self) -> int:
        return self._pair_index

    def load_pair(self, index: int) -> None:
        if not self.image_pairs or not (0 <= index < len(self.image_pairs)):
            return
        # an image remark typed but not yet confirmed belongs to the pair being left
        self.commit_pending_edits()
        if index != self._pair_index:
            self.mark_current_pair_checked()
        self._pair_index = index
        pair = self.image_pairs[index]

        for scene, image_path in (
            (self.scene_l, pair.left), (self.scene_r, pair.right),
        ):
            if image_path is not None:
                scene.load_image(image_path)
                scene.set_file(pair.file_id)
            else:
                scene.reset_scene()
                scene.file_id = None
        self._fit_views()
        if self._check_image_sizes(pair.file_id):
            self._recalculate_matched_measurements(pair.file_id)

        self._selected_rows = set()
        self._pending_match = None
        self.side_panel.set_animal(None)
        self.label_pair.setText(f"{pair.file_id} ({index + 1}/{len(self.image_pairs)})")
        self._update_counts()
        self.image_list.select_pair(index, self.store.image_remark(pair.file_id))
        self._refresh_animal_lists()

    def _on_adjustments_changed(self, adjustments: ImageAdjustments) -> None:
        for scene in (self.scene_l, self.scene_r):
            scene.set_adjustments(adjustments)

    def _on_markers_hidden_changed(self, hidden: bool) -> None:
        for scene in (self.scene_l, self.scene_r):
            scene.set_markers_visible(not hidden)

    def _recalculate_matched_measurements(self, file_id: str) -> None:
        """Refreshes length/height of every matched animal of the pair from the current calibration."""
        if self.calibration is None:
            return
        for row_index in self.store.rows_for_file(file_id).index:
            if self._is_matched(row_index):
                # opening an image is not an edit, so the row keeps its user/experiment ids
                self._recalculate_triangulation(row_index, stamp=False)

    def _check_image_sizes(self, file_id: str) -> bool:
        """Shows an error if a loaded image's size differs from the camera config's image_size.
        Returns True if the sizes are fine (or there is no calibration to check against)."""
        if self.calibration is None:
            return False
        problems = []
        for side, scene in (("left", self.scene_l), ("right", self.scene_r)):
            size = scene.image_size()
            if size is None:
                continue
            message = self.calibration.image_size_mismatch(*size)
            if message is not None:
                problems.append(f"{side} image: {message}")
        if problems:
            QMessageBox.critical(self, "Image Size Mismatch", f"{file_id}\n\n" + "\n\n".join(problems))
        return not problems

    # --- selection / editing --------------------------------------------------- #
    @property
    def _pending_match(self) -> tuple[str, int] | None:
        """The first animal picked for a manual match (side, row_index), if any."""
        return self._pending_match_pick

    @_pending_match.setter
    def _pending_match(self, pick: tuple[str, int] | None) -> None:
        self._pending_match_pick = pick
        self._update_match_hint()

    def _update_match_hint(self) -> None:
        if self._editing_locked:
            hint = EDITING_LOCKED_HINT
        elif self._mode != Mode.MATCH:
            hint = ""
        elif self._pending_match_pick is not None:
            hint = "Matching: Select animal from other side"
        elif not self._selected_rows:
            hint = "Matching: Select animal from one side"
        else:
            hint = ""
        self.info_panel.set_hint(hint)

    @property
    def _selected_row(self) -> int | None:
        """The selected animal when exactly one is selected, otherwise None."""
        return next(iter(self._selected_rows)) if len(self._selected_rows) == 1 else None

    @_selected_row.setter
    def _selected_row(self, row_index: int | None) -> None:
        self._selected_rows = {row_index} if row_index is not None else set()

    def _has_points_on(self, row_index: int, side: str) -> bool:
        return row_index in self.store.data.index and self.store.data.loc[row_index, f"{side}X1"] > 0

    def _is_matched(self, row_index: int) -> bool:
        return self._has_points_on(row_index, "L") and self._has_points_on(row_index, "R")

    def _rows_for_side(self, side: str) -> set[int]:
        """Which rows should be highlighted on the given side: every selected animal
        that actually has points there (a matched animal therefore shows on both sides)."""
        return {row for row in self._selected_rows if self._has_points_on(row, side)}

    def _sync_matched_list_selection(self) -> None:
        matched = {row for row in self._selected_rows if self._is_matched(row)}
        self.matched_list.select_rows(matched)

    def _apply_selection(self, sync_matched_list: bool = True) -> None:
        """Pushes ``_selected_rows`` to the scenes, lists and side panel (silently,
        without re-emitting selection signals)."""
        self._selected_rows = {row for row in self._selected_rows if row in self.store.data.index}
        # a half-finished match pick is abandoned when nothing, or a matched animal, gets selected
        if self._pending_match is not None and (
            not self._selected_rows or any(self._is_matched(row) for row in self._selected_rows)
        ):
            self._pending_match = None
        for s, scene, panel in (("L", self.scene_l, self.list_l), ("R", self.scene_r, self.list_r)):
            apply_rows = self._rows_for_side(s)
            scene.blockSignals(True)
            scene.set_selected_rows(apply_rows)
            scene.blockSignals(False)
            panel.blockSignals(True)
            panel.select_rows(apply_rows)
            panel.blockSignals(False)
        self.side_panel.set_animals(self.store.data.loc[sorted(self._selected_rows)])
        if sync_matched_list:
            self._sync_matched_list_selection()
        self._update_match_hint()

    def _on_list_row_clicked(self, row_index: int | None, additive: bool) -> None:
        """With the Remove tool active, clicking an animal in a list removes it, just
        like clicking it on the canvas; otherwise it is a normal selection."""
        if self.scene_l.tool == Tool.REMOVE and row_index is not None:
            self._delete_row(row_index)
            return
        self._on_selection_changed(row_index, additive)

    def _on_selection_changed(self, row_index: int | None, additive: bool = False) -> None:
        rows = set(self._selected_rows)
        if row_index is None:
            rows = set()
        elif additive:
            rows ^= {row_index}
        else:
            rows = {row_index}

        self._selected_rows = rows
        self._apply_selection()

        if self._mode == Mode.MATCH and row_index is not None and not additive and len(rows) == 1:
            self._register_pick_if_unmatched(row_index)

    def _on_rows_selected(self, row_indices: set[int], additive: bool) -> None:
        """A whole set of animals selected at once (Shift+click range, Ctrl+A in a list)."""
        self._selected_rows = (self._selected_rows | row_indices) if additive else set(row_indices)
        self._apply_selection()

    def _on_matched_list_selected(self, row_indices: set[int]) -> None:
        """Selecting rows in the Matched list selects those animals on BOTH sides at
        once (a matched animal shares one row_index across L/R)."""
        self._selected_rows = set(row_indices)
        # the Matched list is the source of this change and already shows it
        self._apply_selection(sync_matched_list=False)

    def _register_pick_if_unmatched(self, row_index: int) -> None:
        if row_index not in self.store.data.index or self._is_matched(row_index):
            return
        self._register_match_pick("L" if self._has_points_on(row_index, "L") else "R", row_index)

    def _update_selected(self, **fields) -> None:
        """Applies the given field changes to every selected animal."""
        if self._editing_locked:
            return
        # None means "leave this field as it is"
        fields = {key: value for key, value in fields.items() if value is not None}
        if not self._selected_rows or not fields:
            return
        for key in ("group", "subgroup", "species"):
            if key in fields:
                fields[key] = str(fields[key]).lower()
        if fields.keys() & {"group", "subgroup", "species"}:
            # reclassifying an animal is a manual check (relevant for model predictions)
            fields.update(status="checked", manually_corrected=True)
        with self._undo_group("Edit animal" if len(self._selected_rows) == 1 else "Edit animals"):
            for row_index in sorted(self._selected_rows):
                self.store.update_row(row_index, **fields)
        # the remark is only shown in the side panel, so nothing else needs redrawing for it
        if fields.keys() - {"object_remarks"}:
            for scene in (self.scene_l, self.scene_r):
                scene.refresh()
            self._refresh_animal_lists()

    def _set_selected_status(self, status: str) -> None:
        """Sets the status of the selected animals; manually corrected ones cannot become "unchecked"."""
        if self._editing_locked or not self._selected_rows:
            return
        with self._undo_group("Set status"):
            for row_index in sorted(self._selected_rows):
                if row_index not in self.store.data.index:
                    continue
                if status.lower() == "unchecked" and bool(self.store.data.loc[row_index, "manually_corrected"]):
                    continue
                self.store.update_row(row_index, status=status)
        self.side_panel.set_animals(self.store.data.loc[sorted(self._selected_rows)])
        self._refresh_animal_lists()

    def _delete_selected(self) -> None:
        if not self._selected_rows:
            return
        # with a width keypoint explicitly selected, only the width keypoints go
        for scene in (self.scene_l, self.scene_r):
            width_row = scene.selected_width_row()
            if width_row is not None:
                if not self._editing_locked:
                    scene.remove_width(width_row)
                    self.info_panel.show_message("Removed width keypoints")
                return
        self._delete_rows(set(self._selected_rows))

    def _delete_row(self, row_index: int) -> None:
        self._delete_rows({row_index})

    def _delete_rows(self, row_indices: set[int]) -> None:
        if not row_indices or self._editing_locked:
            return
        if self._selected_rows & row_indices:
            self._selected_rows -= row_indices
            self._apply_selection()
        multi = len(row_indices) > 1
        if multi:
            self._undo_stack.beginMacro("Delete animals")
        for row_index in row_indices:
            for scene in (self.scene_l, self.scene_r):
                if scene.has_row(row_index):
                    scene.remove_row(row_index)
        if multi:
            self._undo_stack.endMacro()
        self._refresh_animal_lists()

    def _on_store_changed(self, _row_index: int) -> None:
        self._drop_stale_selection()
        self._update_counts()
        self._refresh_animal_lists()

    def _on_animal_moved(self, row_index: int) -> None:
        """A keypoint was dragged: a matched animal's length/height must follow the new position."""
        if self.calibration is not None and self._is_matched(row_index):
            self._recalculate_triangulation(row_index)
        self._on_store_changed(row_index)

    def _drop_stale_selection(self) -> None:
        """Forget selected rows that no longer exist (e.g. removed via the Remove tool)."""
        existing = {row for row in self._selected_rows if row in self.store.data.index}
        if existing == self._selected_rows:
            return
        self._selected_rows = existing
        self._apply_selection()

    def _refresh_animal_lists(self) -> None:
        if self._pair_index < 0:
            self.list_l.clear()
            self.list_r.clear()
            self.matched_list.clear()
            return
        file_id = self.image_pairs[self._pair_index].file_id
        self.image_list.refresh_pair(self.store, self._pair_index)
        self.list_l.refresh(self.store, file_id)
        self.list_r.refresh(self.store, file_id)
        self.matched_list.refresh(self.store, file_id)
        # L and R columns stay the same width as each other, sized off whichever
        # side's content/header is wider (plus margin), rather than each side
        # drifting independently or stretching to fill the panel's full width.
        shared_widths = [
            max(l_width, r_width)
            for l_width, r_width in zip(self.list_l.content_column_widths(), self.list_r.content_column_widths())
        ]
        self.list_l.apply_column_widths(shared_widths)
        self.list_r.apply_column_widths(shared_widths)
        for s, panel in (("L", self.list_l), ("R", self.list_r)):
            panel.blockSignals(True)
            panel.select_rows(self._rows_for_side(s))
            panel.blockSignals(False)
        self._sync_matched_list_selection()
        selected = [row for row in sorted(self._selected_rows) if row in self.store.data.index]
        self.side_panel.refresh_status(self.store.data.loc[selected])
    # --- remarks ------------------------------------------------------------------ #
    def set_remark_options(self, object_options: list[str], image_options: list[str]) -> None:
        """Sets the preset choices offered by the object remark and image remark fields."""
        self.side_panel.set_remark_options(object_options)
        self.image_list.set_remark_options(image_options)

    def set_status_options(self, options: list[str]) -> None:
        """Sets the statuses offered by the status dropdown of the side panel."""
        self.side_panel.set_status_options(options)

    def _on_image_remark_edited(self, remark: str) -> None:
        if self._editing_locked or self._pair_index < 0 or remark == self.store.image_remark(self._current_file_id()):
            return
        with self._undo_group("Edit image remark"):
            self.store.set_image_remark(self._current_file_id(), remark)
        self._refresh_animal_lists()

    def mark_current_pair_checked(self) -> None:
        """Sets all "unchecked" animals of the current image pair to "checked" (no-op while editing is locked)."""
        if self._editing_locked or self._pair_index < 0:
            return
        if self.store.mark_file_checked(self._current_file_id()):
            self._refresh_animal_lists()

    def _on_pair_checked_toggled(self, index: int, checked: bool) -> None:
        """The checked box of an image pair was clicked: "unchecked" animals become "checked", or "checked" ones
        not manually corrected become "unchecked"; other statuses stay."""
        if not 0 <= index < len(self.image_pairs):
            return
        if not self._editing_locked:
            file_id = self.image_pairs[index].file_id
            before_rows = self.store.rows_for_file(file_id).copy()
            if checked:
                self.store.mark_file_checked(file_id)
            else:
                self.store.mark_file_unchecked(file_id)
            after_rows = self.store.rows_for_file(file_id).copy()
            if not after_rows.equals(before_rows):
                selected = frozenset(self._selected_rows)
                self._undo_stack.push(_SnapshotCommand(
                    self, file_id, before_rows, after_rows, selected, selected,
                    "Check image pair" if checked else "Uncheck image pair", navigate=False,
                ))
        # also restores the box when nothing could change (locked, or all animals manually corrected)
        self.image_list.refresh_pair(self.store, index)
        if index == self._pair_index:
            self._refresh_animal_lists()

    def commit_pending_edits(self) -> None:
        """Applies an image remark that was typed but not yet confirmed (Enter / focus change)."""
        self._on_image_remark_edited(self.image_list.edit_remark.text())

    # --- undo/redo --------------------------------------------------------------- #
    def _undo(self) -> None:
        if self._editing_locked or not self._undo_stack.canUndo():
            return
        text = self._undo_stack.undoText()
        self._undo_stack.undo()
        self.info_panel.show_message(f"Undid: {text}")

    def _redo(self) -> None:
        if self._editing_locked or not self._undo_stack.canRedo():
            return
        text = self._undo_stack.redoText()
        self._undo_stack.redo()
        self.info_panel.show_message(f"Redid: {text}")

    def _current_file_id(self) -> str:
        return self.image_pairs[self._pair_index].file_id if self._pair_index >= 0 else ""

    @contextmanager
    def _undo_group(self, description: str):
        """Wraps a block of direct store mutations in a single undoable snapshot of the current pair's rows."""
        file_id = self._current_file_id()
        before_rows = self.store.rows_for_file(file_id).copy()
        before_selected = frozenset(self._selected_rows)
        yield
        after_rows = self.store.rows_for_file(file_id).copy()
        if after_rows.equals(before_rows):
            return
        self._undo_stack.push(
            _SnapshotCommand(
                self, file_id, before_rows, after_rows, before_selected, frozenset(self._selected_rows), description
            )
        )

    def _on_scene_store_changed(self, before_rows, description: str) -> None:
        file_id = self._current_file_id()
        after_rows = self.store.rows_for_file(file_id).copy()
        if after_rows.equals(before_rows):
            return
        selected = frozenset(self._selected_rows)
        self._undo_stack.push(
            _SnapshotCommand(self, file_id, before_rows, after_rows, selected, selected, description)
        )

    def _restore_snapshot(self, file_id: str, rows, selected_rows, navigate: bool = True) -> None:
        if file_id != self._current_file_id():
            target = next((i for i, pair in enumerate(self.image_pairs) if pair.file_id == file_id), None)
            if not navigate:
                self.store.replace_file_rows(file_id, rows.copy())
                if target is not None:
                    self.image_list.refresh_pair(self.store, target)
                return
            if target is not None:
                self.load_pair(target)
        self.store.replace_file_rows(file_id, rows.copy())
        self._selected_rows = {row for row in selected_rows if row in self.store.data.index}
        for scene in (self.scene_l, self.scene_r):
            scene.refresh()
        self._update_counts()
        self._refresh_animal_lists()
        self._apply_selection()

    # --- matching --------------------------------------------------------------- #
    def _register_match_pick(self, side: str, row_index: int) -> None:
        if self._editing_locked:
            return
        if self._pending_match is None or self._pending_match[0] == side:
            self._pending_match = (side, row_index)
            return
        pending_side, pending_index = self._pending_match
        self._pending_match = None
        left_index, right_index = (pending_index, row_index) if pending_side == "L" else (row_index, pending_index)
        if self.store.merged_classes(left_index, right_index) is None:
            self._selected_rows = set()
            self._apply_selection()
            self.info_panel.show_message(MISMATCH_MESSAGE, MISMATCH_MESSAGE_TIMEOUT_MS)
            return
        merged_index = self._perform_match(left_index, right_index)
        # A manually-completed match stays selected (on both sides) rather than
        # leaving the user with nothing highlighted after they just matched it.
        self._selected_rows = {merged_index}
        self._apply_selection()

    def _perform_match(self, left_index: int, right_index: int) -> int:
        with self._undo_group("Match animals"):
            merged_index = self.store.merge_as_match(left_index, right_index)
            self._selected_rows -= {left_index, right_index}
            if self.calibration is not None:
                self._recalculate_triangulation(merged_index)
        for scene in (self.scene_l, self.scene_r):
            scene.refresh()
        self._update_counts()
        self._refresh_animal_lists()
        self._apply_selection()
        return merged_index

    def _recalculate_triangulation(self, row_index: int, stamp: bool = True) -> None:
        row = self.store.data.loc[row_index]
        left = {"head": (row["LX1"], row["LY1"]), "tail": (row["LX2"], row["LY2"])}
        right = {"head": (row["RX1"], row["RY1"]), "tail": (row["RX2"], row["RY2"])}
        if row["LX3"] > 0 and row["LX4"] > 0 and row["RX3"] > 0 and row["RX4"] > 0:
            left["width_start"], left["width_end"] = (row["LX3"], row["LY3"]), (row["LX4"], row["LY4"])
            right["width_start"], right["width_end"] = (row["RX3"], row["RY3"]), (row["RX4"], row["RY4"])
        worker = TriangulationWorker(self.calibration, row_index, left, right)
        worker.finished.connect(
            lambda index, length, height: self._on_triangulation_finished(index, length, height, stamp)
        )
        worker.run()

    def _on_triangulation_finished(
        self, row_index: int, length: float | None, height: float | None, stamp: bool = True,
    ) -> None:
        fields = {}
        if length is not None:
            fields["length"] = length
        if height is not None:
            fields["height"] = height
        if fields:
            self.store.update_row(row_index, stamp=stamp, **fields)

    def _split_selected(self) -> None:
        """Splits every selected matched animal back into its left-only and right-only rows."""
        matched = sorted(row for row in self._selected_rows if self._is_matched(row))
        if not matched or self._editing_locked:
            return
        with self._undo_group("Split match" if len(matched) == 1 else "Split matches"):
            for row_index in matched:
                self.store.split_match(row_index)
        for scene in (self.scene_l, self.scene_r):
            scene.refresh()
        self._update_counts()
        self._refresh_animal_lists()
        self._apply_selection()

    def _auto_match(self) -> None:
        if self.calibration is None or self._pair_index < 0 or self._editing_locked:
            return
        file_id = self.image_pairs[self._pair_index].file_id
        left_candidates = self._candidates_from_rows(self.store.unmatched(file_id, "R"), "L")
        right_candidates = self._candidates_from_rows(self.store.unmatched(file_id, "L"), "R")
        if not left_candidates or not right_candidates:
            QMessageBox.information(self, "Auto Match", "No unmatched animals with both head and tail on each side.")
            return

        results = match_candidates(self.calibration, left_candidates, right_candidates)
        if not results:
            QMessageBox.information(self, "Auto Match", "No matches found.")
            return

        dialog = MatchReviewDialog(results, parent=self)
        if dialog.exec():
            accepted = dialog.accepted_results()
            if accepted:
                self._undo_stack.beginMacro("Auto match")
                for result in accepted:
                    self._perform_match(result.left_index, result.right_index)
                self._undo_stack.endMacro()

    @staticmethod
    def _candidates_from_rows(rows, side: str) -> list[MatchCandidate]:
        x1, y1, x2, y2 = f"{side}X1", f"{side}Y1", f"{side}X2", f"{side}Y2"
        candidates = []
        for row_index, row in rows.iterrows():
            if row[x1] > 0 and row[y1] > 0 and row[x2] > 0 and row[y2] > 0:
                candidates.append(MatchCandidate(
                    row_index, (row[x1], row[y1]), (row[x2], row[y2]),
                    group=str(row["group"] or ""), subgroup=str(row["subgroup"] or ""),
                    species=str(row["species"] or ""),
                ))
        return candidates

    # --- AI predictions ---------------------------------------------------------- #
    def set_ai_model_path(self, path: str | Path | None) -> None:
        self.ai_model_path = Path(path) if path else None
        self._predictor = None

    def set_predictor(self, predictor: KeypointPredictor | None) -> None:
        """Uses ``predictor`` instead of loading the model at ``ai_model_path`` (until the path changes)."""
        self._predictor = predictor

    def _update_prediction_actions(self) -> None:
        enabled = self._prediction_thread is None and bool(self.image_pairs)
        self.action_predict_selected.setEnabled(enabled)
        self.action_predict_all.setEnabled(enabled)

    def _ensure_predictor(self) -> KeypointPredictor | None:
        if self._predictor is not None:
            return self._predictor
        if self.ai_model_path is None:
            QMessageBox.information(self, "AI Mode", "No AI model is set. Choose one under Settings > AI model…")
            return None
        error = None
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self._predictor = OnnxKeypointPredictor(self.ai_model_path, self.taxonomy)
        except Exception as exc:
            error = str(exc)
        finally:
            QApplication.restoreOverrideCursor()
        if error is not None:
            QMessageBox.critical(self, "AI Model", f"The AI model could not be loaded:\n{error}")
        return self._predictor

    def _predict_selected(self) -> None:
        if self._pair_index >= 0:
            self._run_prediction([self._pair_index], "Predict animals")

    def _predict_all(self) -> None:
        self._run_prediction(list(range(len(self.image_pairs))), "Predict all")

    def _run_prediction(self, pair_indices: list[int], description: str) -> None:
        if self._prediction_thread is not None or not pair_indices:
            return
        predictor = self._ensure_predictor()
        if predictor is None:
            return
        self.commit_pending_edits()
        jobs: list[PredictionJob] = [
            (pair.file_id, pair.left, pair.right) for pair in (self.image_pairs[i] for i in pair_indices)
        ]
        self._prediction_cancelled = False
        self._prediction_added = 0
        self._prediction_description = description

        worker = PredictionWorker(predictor, jobs)
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.pair_done.connect(self._on_pair_predicted)
        worker.failed.connect(self._on_prediction_failed)
        worker.finished.connect(thread.quit)
        thread.finished.connect(self._on_prediction_finished)

        # progress and cancelling are shown by the main window (status bar)
        worker.progress.connect(self.prediction_progress)

        self._prediction_thread, self._prediction_worker = thread, worker
        self._update_prediction_actions()
        self._set_editing_locked(True)
        self.prediction_started.emit(len(jobs))
        thread.start()

    @property
    def is_predicting(self) -> bool:
        return self._prediction_thread is not None

    def cancel_prediction(self) -> None:
        self._prediction_cancelled = True
        if self._prediction_worker is not None:
            self._prediction_worker.cancel()

    def _on_prediction_failed(self, message: str) -> None:
        self._prediction_cancelled = True
        QMessageBox.critical(self, "AI Prediction", f"Prediction failed:\n{message}")

    def _on_prediction_finished(self) -> None:
        thread = self._prediction_thread
        self._prediction_thread = self._prediction_worker = None
        if thread is not None:
            thread.deleteLater()
        if self._prediction_macro_open:
            self._prediction_macro_open = False
            self._undo_stack.endMacro()
        self._set_editing_locked(False)
        self._update_prediction_actions()
        self.prediction_finished.emit()
        if not self._prediction_cancelled:
            self.info_panel.show_message(
                f"Predicted {self._prediction_added} new animal(s)" if self._prediction_added
                else "No new animals predicted"
            )

    def shutdown(self) -> None:
        """Stops a running prediction and waits for its thread; call before the editor is discarded."""
        if self._prediction_thread is None:
            return
        self._prediction_cancelled = True
        self._prediction_worker.cancel()
        self._prediction_thread.quit()
        self._prediction_thread.wait()

    def _on_pair_predicted(self, file_id: str, results: dict[str, list[PredictedAnimal]]) -> None:
        if self._prediction_cancelled:
            return
        before_rows = self.store.rows_for_file(file_id).copy()
        added = 0
        for side, animals in results.items():
            for animal in animals:
                if self._duplicates_existing(file_id, side, animal):
                    continue
                points = {"head": animal.head, "tail": animal.tail}
                row = self.store.build_row(
                    file_id, animal.group.lower(), animal.subgroup.lower(), animal.species.lower(),
                    left=points if side == "L" else None, right=points if side == "R" else None,
                    status="unchecked", manually_corrected=False,
                )
                row_index = self.store.add_row(row)
                if animal.bbox is not None:
                    self.store.set_predicted_box(row_index, side, animal.bbox)
                added += 1
        if not added:
            return
        self._prediction_added += added
        selected = frozenset(self._selected_rows)
        if not self._prediction_macro_open:
            self._prediction_macro_open = True
            self._undo_stack.beginMacro(self._prediction_description)
        self._undo_stack.push(_SnapshotCommand(
            self, file_id, before_rows, self.store.rows_for_file(file_id).copy(), selected, selected,
            "Predict animals",
        ))
        if file_id == self._current_file_id():
            for scene in (self.scene_l, self.scene_r):
                scene.refresh()
            self._update_counts()
            self._refresh_animal_lists()
        else:
            pair_index = next(i for i, pair in enumerate(self.image_pairs) if pair.file_id == file_id)
            self.image_list.refresh_pair(self.store, pair_index)

    def _duplicates_existing(self, file_id: str, side: str, animal: PredictedAnimal) -> bool:
        """True if an animal of the pair already has (about) the predicted head and tail on that side."""
        columns = [f"{side}X1", f"{side}Y1", f"{side}X2", f"{side}Y2"]
        predicted = (*animal.head, *animal.tail)
        swapped = (*animal.tail, *animal.head)  # the order carries no meaning for symmetric animals
        for existing in self.store.rows_for_file(file_id)[columns].itertuples(index=False):
            if min(existing) <= 0:
                continue
            for candidate in (predicted, swapped):
                if all(abs(a - b) <= DUPLICATE_TOLERANCE_PX for a, b in zip(existing, candidate)):
                    return True
        return False

    def _update_counts(self) -> None:
        if self._pair_index < 0:
            self.label_counts.setText("")
            return
        file_id = self.image_pairs[self._pair_index].file_id
        rows = self.store.rows_for_file(file_id)
        matched = ((rows["LX1"] > 0) & (rows["RX1"] > 0)).sum()
        self.label_counts.setText(f"{len(rows)} animal(s), {matched} matched")
