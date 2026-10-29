# MarOMarker v2

Rewrite of `fish_detection/MarOMarker` targeting Python 3.12 + PySide6 + OpenCV.

## Status

Core data/geometry layer implemented and tested:

- `models/annotation_store.py` — CSV schema compatible with the legacy app.
- `models/image_set.py` — folder scanning, L/R pairing, filename date/time parsing.
- `models/camera_calibration.py` — stereo calibration (`config.json`) + rectification.
- `models/taxonomy.py` — editable group/subgroup/species hierarchy (YAML with explicit `rank`/`children`, see `configs/hierarchy_full.yaml`)
- `geometry/triangulation.py` — stereo triangulation (length/height from matched points).
- `geometry/matching.py` — Hungarian-algorithm L/R animal matching with cost threshold.
- `predictors/__init__.py` — abstract `KeypointPredictor` interface; `predictors/onnx_predictor.py` implements it with an exported ONNX YOLO-pose model (needs `onnxruntime`).

UI shell implemented:

- `main.py` — QApplication bootstrap (`python -m maromarker2.main`): shows the project
  setup dialog, then the main window.
- `ui/project_setup_dialog.py` — image root/prefix, output CSV, camera config, hierarchy,
  user/experiment ID, with validation against the real data layer before accepting.
- `ui/app_settings.py` — persisted (QSettings) remembered values (last project setup fields, theme, remark options).
- `ui/remark_options_dialog.py` — Settings > Remark options…: the selectable presets (one per
  line) offered by the object remark field and the image remark field.
- `ui/editor/image_list_panel.py` — left panel listing all image pairs (click to navigate) with
  "Animals" (annotated animal count) and "Checked" columns (all animals of the pair have status
  `checked`; pairs without animals are unchecked) and the image remark; the selected pair's
  remark is edited above the list.
- `ui/main_window.py` — menu/status bar shell; loads the project's image pairs, annotation
  store, camera calibration and taxonomy.

## AI mode

The AI model path (an `.onnx` file) is optional; set it in the project setup dialog or later under Settings > AI model…. The `inference_args.yaml` written at export time must lie next to the model.

In AI mode keypoint predictions for the current image pair and for every pair can be generated. Predictions whose head/tail match an existing animal are discarded; new ones get status `unchecked` and `manually_corrected` False, with classes mapped onto the loaded hierarchy (unknown levels become `unidentified`). The predicted bounding boxes are only shown and never written to the CSV. Moving a keypoint or changing the class marks the animal `checked` and manually corrected.

Prediction runs in the background: a progress bar with a Cancel button appears in the status bar. While it runs, editing and saving are locked; navigating between image pairs, zooming and selecting animals still work.

## Running tests

```
QT_QPA_PLATFORM=offscreen /home/jeusa/anaconda3/envs/mom/bin/python -m pytest tests -v
```
