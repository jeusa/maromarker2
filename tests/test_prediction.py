import os
import threading
import threading

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
import types
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QPointF
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QGraphicsItem, QMessageBox

from maromarker2.models.annotation_store import AnnotationStore
from maromarker2.models.image_set import scan_image_set
from maromarker2.models.taxonomy import Taxonomy
from maromarker2.predictors import KeypointPredictor, PredictedAnimal
from maromarker2.predictors.onnx_predictor import OnnxKeypointPredictor, validate_model_path
from maromarker2.ui.editor.editor_widget import EditorWidget, Mode
from maromarker2.workers.prediction_worker import PredictionWorker


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _isolated_editor_settings(tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings
    import maromarker2.ui.editor.editor_settings as editor_settings_module
    ini_path = str(tmp_path / "editor_settings.ini")
    monkeypatch.setattr(
        editor_settings_module, "QSettings", lambda *a, **k: QSettings(ini_path, QSettings.IniFormat)
    )


@pytest.fixture(autouse=True)
def _no_modal_dialogs(monkeypatch):
    # a modal message box would block forever in a headless run
    for name in ("information", "warning", "critical"):
        monkeypatch.setattr(QMessageBox, name, lambda *a, **k: None)


@pytest.fixture
def image_pairs(pytestconfig):
    return scan_image_set(pytestconfig.rootpath / "sample_data" / "2016_04")


class StubPredictor(KeypointPredictor):
    """Returns the same animal for every image, and records which images were asked for."""

    def __init__(self, animals=None):
        self.animals = animals if animals is not None else [
            PredictedAnimal(
                head=(100.0, 100.0), tail=(200.0, 150.0), group="fish", subgroup="pelagic",
                species="Gadus_Morhua", bbox=(90.0, 90.0, 210.0, 160.0), conf=0.9,
            )
        ]
        self.calls: list[str] = []

    def predict(self, image_path: str) -> list[PredictedAnimal]:
        self.calls.append(image_path)
        return list(self.animals)


@pytest.fixture
def editor(qapp, image_pairs):
    editor = EditorWidget(AnnotationStore(), Taxonomy.load_default(), image_pairs)
    editor.set_predictor(StubPredictor())
    return editor


def _predict(editor, start):
    start()
    for _ in range(1000):  # up to ~10 s
        if editor._prediction_thread is None:
            return
        QTest.qWait(10)
    pytest.fail("prediction did not finish")


# --- model path validation ------------------------------------------------------ #

def _write_model(directory: Path, onnx_name="best.onnx", args_onnx="best.onnx") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    model = directory / onnx_name
    model.write_bytes(b"")
    (directory / "inference_args.yaml").write_text(f"model:\n  onnx_file: {args_onnx}\n")
    return model


def test_validate_model_path_accepts_model_with_matching_args(tmp_path):
    assert validate_model_path(_write_model(tmp_path / "m")) is None


def test_validate_model_path_rejects_missing_file_args_and_mismatch(tmp_path):
    assert validate_model_path(tmp_path / "missing.onnx") is not None

    no_args = tmp_path / "no_args"
    no_args.mkdir()
    (no_args / "best.onnx").write_bytes(b"")
    assert "inference_args.yaml" in validate_model_path(no_args / "best.onnx")

    assert "other.onnx" in validate_model_path(_write_model(tmp_path / "m", "best.onnx", "other.onnx"))


def test_onnx_predictor_converts_detections(tmp_path, monkeypatch):
    class FakeDetection:
        path = ["fish", "fish_pelagic", "gadus_morhua"]
        keypoints = np.array([[10.0, 20.0, 1.0], [-5.0, 40.0, 1.0]])
        box = np.array([1.0, 2.0, 30.0, 50.0])
        conf = 0.8

    class FakeDetector:
        kpt_shape = (2, 3)

        def __init__(self, model_dir):
            self.model_dir = model_dir

        def predict(self, image):
            return [FakeDetection()]

    fake_module = types.ModuleType("maromarker2.workers.inference_standalone")
    fake_module.OnnxPoseDetector = FakeDetector
    monkeypatch.setitem(sys.modules, "maromarker2.workers.inference_standalone", fake_module)
    model = _write_model(tmp_path / "m")

    predictor = OnnxKeypointPredictor(model, Taxonomy.load_default())
    (animal,) = predictor.predict("image.jpg")

    assert (animal.group, animal.subgroup, animal.species) == ("fish", "pelagic", "gadus_morhua")
    assert animal.head == (10.0, 20.0)
    assert animal.tail == (1.0, 40.0)  # coordinates <= 0 would count as "no point" in the store
    assert animal.bbox == (1.0, 2.0, 30.0, 50.0)
    assert animal.conf == pytest.approx(0.8)


def test_onnx_predictor_rejects_invalid_model(tmp_path):
    with pytest.raises(ValueError):
        OnnxKeypointPredictor(tmp_path / "missing.onnx", Taxonomy.load_default())


# --- worker ------------------------------------------------------------------------ #

def test_prediction_worker_emits_results_per_pair_and_skips_missing_images(qapp):
    predictor = StubPredictor()
    results, progress, finished = [], [], []
    worker = PredictionWorker(predictor, [
        ("a", Path("a_L.jpg"), Path("a_R.jpg")), ("b", Path("b_L.jpg"), None),
    ])
    worker.pair_done.connect(lambda file_id, sides: results.append((file_id, sorted(sides))))
    worker.progress.connect(lambda done, total: progress.append((done, total)))
    worker.finished.connect(lambda: finished.append(True))

    worker.run()

    assert results == [("a", ["L", "R"]), ("b", ["L"])]
    assert progress == [(1, 2), (2, 2)]
    assert finished == [True]
    assert len(predictor.calls) == 3


def test_prediction_worker_stops_when_cancelled(qapp):
    predictor = StubPredictor()
    worker = PredictionWorker(predictor, [("a", Path("a_L.jpg"), None)])
    worker.cancel()

    worker.run()

    assert predictor.calls == []


def test_prediction_worker_reports_failure(qapp):
    class Failing(KeypointPredictor):
        def predict(self, image_path):
            raise RuntimeError("boom")

    errors, finished = [], []
    worker = PredictionWorker(Failing(), [("a", Path("a_L.jpg"), None)])
    worker.failed.connect(errors.append)
    worker.finished.connect(lambda: finished.append(True))

    worker.run()

    assert errors == ["boom"]
    assert finished == [True]


# --- AI mode in the editor --------------------------------------------------------- #

def test_ai_mode_shows_prediction_buttons_only_and_locks_editing(editor):
    editor._set_mode(Mode.AI)

    assert editor._mode == Mode.AI
    assert editor.action_predict_selected.isVisible() is True
    assert editor.action_predict_selected.isEnabled() is True
    assert editor.action_predict_all.isVisible() is True
    for action in (editor.action_edit, editor.action_add, editor.action_remove,
                   editor.action_split_match, editor.action_auto_match):
        assert action.isVisible() is False

    editor._set_mode(Mode.EDIT)
    assert editor.action_predict_selected.isVisible() is False
    editor._set_mode(Mode.AI)
    assert editor.action_predict_all.isEnabled() is True  # not left disabled by an earlier hide


def test_ai_mode_markers_cannot_be_moved_but_can_be_deleted(editor):
    row_index = editor.scene_l.add_animal(QPointF(10, 10), QPointF(20, 20))
    editor._selected_row = row_index

    editor._set_mode(Mode.AI)
    assert not editor.scene_l._markers[row_index].head.flags() & QGraphicsItem.ItemIsMovable
    editor._delete_selected()
    assert row_index not in editor.store.data.index


def test_predict_selected_adds_unchecked_animals_with_boxes_and_classes(editor, image_pairs):
    editor._set_mode(Mode.AI)

    _predict(editor, editor.action_predict_selected.trigger)

    file_id = image_pairs[0].file_id
    rows = editor.store.rows_for_file(file_id)
    sides = ["L", "R"] if image_pairs[0].is_stereo else ["L"]
    assert len(rows) == len(sides)  # one animal per image side
    assert set(rows["status"]) == {"unchecked"}
    assert set(rows["manually_corrected"]) == {False}
    assert set(rows["species"]) == {"gadus_morhua"}
    assert set(rows["subgroup"]) == {"pelagic"}
    left = rows[rows["LX1"] > 0].iloc[0]
    assert (left["LX1"], left["LY1"], left["LX2"], left["LY2"]) == (100.0, 100.0, 200.0, 150.0)
    for index, row in rows.iterrows():
        side = "L" if row["LX1"] > 0 else "R"
        assert editor.store.predicted_box(index, side) == (90.0, 90.0, 210.0, 160.0)
    assert editor.scene_l._markers[left.name].bbox.rect().width() == 120.0
    assert set(editor.store.data["file_id"]) == {file_id}  # other pairs untouched


def test_predicting_again_discards_duplicates_within_tolerance(editor):
    _predict(editor, editor._predict_selected)
    count = len(editor.store.data)
    editor.set_predictor(StubPredictor([
        PredictedAnimal(head=(100.4, 99.6), tail=(200.3, 150.2), bbox=(0, 0, 1, 1)),
        PredictedAnimal(head=(200.0, 150.0), tail=(100.0, 100.0)),  # same animal, swapped keypoints
    ]))

    _predict(editor, editor._predict_selected)

    assert len(editor.store.data) == count


def test_prediction_differing_from_existing_animal_is_added(editor):
    _predict(editor, editor._predict_selected)
    count = len(editor.store.data)
    editor.set_predictor(StubPredictor([PredictedAnimal(head=(400.0, 100.0), tail=(500.0, 150.0))]))

    _predict(editor, editor._predict_selected)

    assert len(editor.store.data) == count + count


def test_predict_all_covers_every_pair_and_is_undone_in_one_step(editor, image_pairs):
    _predict(editor, editor._predict_all)

    assert set(editor.store.data["file_id"]) == {pair.file_id for pair in image_pairs}
    assert len(editor.store.data) == sum(2 if pair.is_stereo else 1 for pair in image_pairs)

    editor._undo_stack.undo()

    assert editor.store.data.empty


def test_prediction_without_model_informs_user(qapp, image_pairs, monkeypatch):
    shown = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: shown.append(a[2]))
    editor = EditorWidget(AnnotationStore(), Taxonomy.load_default(), image_pairs)

    editor._predict_selected()

    assert editor._prediction_thread is None
    assert shown and "AI model" in shown[0]


def test_reclassifying_a_predicted_animal_marks_it_checked(editor, image_pairs):
    _predict(editor, editor._predict_selected)
    row_index = editor.store.rows_for_file(image_pairs[0].file_id).index[0]
    editor._selected_row = row_index

    editor._update_selected(species="boreogadus_saida")

    row = editor.store.data.loc[row_index]
    assert (row["status"], row["manually_corrected"]) == ("checked", True)


def test_remark_edit_keeps_predicted_animal_unchecked(editor, image_pairs):
    _predict(editor, editor._predict_selected)
    row_index = editor.store.rows_for_file(image_pairs[0].file_id).index[0]
    editor._selected_row = row_index

    editor._update_selected(object_remarks="blurry")

    assert editor.store.data.loc[row_index, "status"] == "unchecked"


class GatedPredictor(StubPredictor):
    """Blocks inside ``predict`` until released, so the state during a prediction can be observed."""

    def __init__(self):
        super().__init__()
        self.entered = threading.Event()
        self.gate = threading.Event()

    def predict(self, image_path: str) -> list[PredictedAnimal]:
        self.entered.set()
        assert self.gate.wait(10)
        return super().predict(image_path)


def _wait_until(condition) -> None:
    for _ in range(1000):  # up to ~10 s
        if condition():
            return
        QTest.qWait(10)
    pytest.fail("condition not reached")


def test_editing_is_locked_during_prediction_and_released_afterwards(editor, image_pairs):
    from maromarker2.ui.editor.editor_widget import EDITING_LOCKED_HINT

    _predict(editor, editor._predict_selected)  # one existing animal to try to edit
    row_index = editor.store.rows_for_file(image_pairs[0].file_id).index[0]
    editor._selected_row = row_index
    predictor = GatedPredictor()
    editor.set_predictor(predictor)
    events = []
    editor.prediction_started.connect(lambda total: events.append(("started", total)))
    editor.prediction_progress.connect(lambda done, total: events.append(("progress", done, total)))
    editor.prediction_finished.connect(lambda: events.append(("finished",)))

    editor._predict_selected()
    _wait_until(predictor.entered.is_set)

    assert editor.is_predicting
    assert editor.info_panel.text() == EDITING_LOCKED_HINT
    assert not editor.side_panel.isEnabled()
    assert not editor.image_list.edit_remark.isEnabled()
    for action in (editor.action_edit, editor.action_add, editor.action_remove, editor.action_undo):
        assert not action.isEnabled()
    count = len(editor.store.data)
    editor._delete_selected()
    editor._update_selected(object_remarks="blurry")
    assert len(editor.store.data) == count
    assert editor.store.data.loc[row_index, "object_remarks"] != "blurry"
    # selecting another image pair keeps the remark field locked
    editor.load_pair(0)
    assert not editor.image_list.edit_remark.isEnabled()

    predictor.gate.set()
    _wait_until(lambda: not editor.is_predicting)

    assert events[0] == ("started", 1) and events[-1] == ("finished",)
    assert editor.side_panel.isEnabled()
    assert editor.image_list.edit_remark.isEnabled()
    assert editor.action_edit.isEnabled() and editor.action_undo.isEnabled()
    assert editor.info_panel.text() != EDITING_LOCKED_HINT


def test_tool_is_forced_to_edit_during_prediction(editor):
    predictor = GatedPredictor()
    editor.set_predictor(predictor)
    editor._set_mode(Mode.EDIT)
    editor.action_add.trigger()
    editor._predict_selected()
    _wait_until(predictor.entered.is_set)

    assert editor.scene_l.tool.name == "EDIT"
    assert not editor.action_add.isEnabled()

    predictor.gate.set()
    _wait_until(lambda: not editor.is_predicting)
    assert editor.action_add.isEnabled()
