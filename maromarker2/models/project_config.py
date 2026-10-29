"""Session-level project configuration (not persisted as app defaults; see settings_store
for that). Produced by the project setup dialog and consumed by the main window."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class ProjectConfig:
    image_root: Path
    camera_config_path: Path
    hierarchy_path: Path
    user_id: str
    output_csv_path: Path | None = None
    image_prefix: str = ""
    experiment_id: str = ""
    ai_model_path: Path | None = None  # .onnx file with an inference_args.yaml next to it
