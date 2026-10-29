"""KeypointPredictor backed by an exported ONNX YOLO-pose model (see workers/inference_standalone.py)."""

from __future__ import annotations

from pathlib import Path

import yaml

from maromarker2.models.taxonomy import Taxonomy
from maromarker2.predictors import KeypointPredictor, PredictedAnimal

INFERENCE_ARGS_FILENAME = "inference_args.yaml"

# the annotation store treats coordinates <= 0 as "no point"
_MIN_COORDINATE = 1.0


def validate_model_path(path: str | Path) -> str | None:
    """An error message if ``path`` is not a usable model file, else None.

    The model needs an ``inference_args.yaml`` next to it that names this very file."""
    path = Path(path)
    if not path.is_file():
        return "The AI model file does not exist."
    args_path = path.parent / INFERENCE_ARGS_FILENAME
    if not args_path.is_file():
        return f"{INFERENCE_ARGS_FILENAME} was not found next to the AI model."
    try:
        with open(args_path) as fh:
            onnx_file = yaml.safe_load(fh)["model"]["onnx_file"]
    except Exception as exc:
        return f"{INFERENCE_ARGS_FILENAME} could not be read: {exc}"
    if onnx_file != path.name:
        return f"{INFERENCE_ARGS_FILENAME} refers to the model file {onnx_file!r}, not {path.name!r}."
    return None


class OnnxKeypointPredictor(KeypointPredictor):
    """Predicts head/tail keypoints, boxes and classes; keypoint 0 is the head, keypoint 1 the tail."""

    def __init__(self, model_path: str | Path, taxonomy: Taxonomy):
        error = validate_model_path(model_path)
        if error is not None:
            raise ValueError(error)
        # imported here so the app runs without onnxruntime until a model is actually used
        from maromarker2.workers.inference_standalone import OnnxPoseDetector

        self._detector = OnnxPoseDetector(Path(model_path).parent)
        if self._detector.kpt_shape[0] < 2:
            raise ValueError("The AI model must predict at least two keypoints (head and tail).")
        self._taxonomy = taxonomy

    def predict(self, image_path: str | Path) -> list[PredictedAnimal]:
        return [self._to_animal(detection) for detection in self._detector.predict(image_path)]

    def _to_animal(self, detection) -> PredictedAnimal:
        group, subgroup, species = self._taxonomy.resolve_label_path(detection.path)
        head, tail = (
            (max(float(x), _MIN_COORDINATE), max(float(y), _MIN_COORDINATE))
            for x, y, _ in detection.keypoints[:2]
        )
        x1, y1, x2, y2 = (float(v) for v in detection.box)
        return PredictedAnimal(
            head=head, tail=tail, group=group, subgroup=subgroup, species=species,
            bbox=(x1, y1, x2, y2), conf=float(detection.conf),
        )
