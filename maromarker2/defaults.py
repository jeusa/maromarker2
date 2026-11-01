"""Default camera configs, hierarchy and AI model, plus their persistent per-user copy.

The files shipped inside the application are read-only and, for an AppImage, live in a mount
point that changes on every launch. ``ensure_user_defaults`` therefore copies them to a stable
per-user folder that the setup dialog prefills and the file dialogs open in, so remembered paths
stay valid and users can find, edit or replace the files.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
from pathlib import Path

from PySide6.QtCore import QStandardPaths

logger = logging.getLogger(__name__)

USER_DATA_FOLDER_NAME = "MarOMarker2"

PACKAGE_DIR = Path(__file__).parent
CONFIGS_DIR = PACKAGE_DIR / "configs"
MODEL_DIR = PACKAGE_DIR / "resources" / "default_model"

CAMERA_CONFIG_SUBDIR = Path("camera_configs")
HIERARCHY_SUBDIR = Path("hierarchy")
AI_MODEL_SUBDIR = Path("ai_model")

# (bundled file, location relative to the user defaults folder)
DEFAULT_CAMERA_CONFIG = (CONFIGS_DIR / "config_remos1.json", CAMERA_CONFIG_SUBDIR / "config_remos1.json")
DEFAULT_HIERARCHY = (CONFIGS_DIR / "hierarchy_full.yaml", HIERARCHY_SUBDIR / "hierarchy_full.yaml")
DEFAULT_AI_MODEL = (MODEL_DIR / "best.onnx", AI_MODEL_SUBDIR / "best.onnx")

BUNDLED_FILES = (
    DEFAULT_CAMERA_CONFIG,
    (CONFIGS_DIR / "config_remos3.json", CAMERA_CONFIG_SUBDIR / "config_remos3.json"),
    DEFAULT_HIERARCHY,
    DEFAULT_AI_MODEL,
    (MODEL_DIR / "inference_args.yaml", AI_MODEL_SUBDIR / "inference_args.yaml"),
)

# hashes of the bundled files as last copied, to tell untouched copies from user-edited ones
_MANIFEST_NAME = ".bundled_hashes.json"


def user_data_dir() -> Path:
    base = QStandardPaths.writableLocation(QStandardPaths.GenericDataLocation)
    return Path(base) / USER_DATA_FOLDER_NAME


def user_defaults_dir() -> Path:
    return user_data_dir() / "defaults"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_manifest(path: Path) -> dict[str, str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def ensure_user_defaults() -> Path:
    """Copies the bundled defaults into the user defaults folder and returns that folder.

    Missing files are copied. A copy the user never touched is refreshed when the bundled file
    changed (new release); a copy the user edited is left alone. Per-file failures are logged
    and skipped, so a read-only home never prevents the app from starting.
    """
    root = user_defaults_dir()
    manifest_path = root / _MANIFEST_NAME
    manifest = _load_manifest(manifest_path)

    for source, relative in BUNDLED_FILES:
        key = relative.as_posix()
        target = root / relative
        try:
            source_hash = _sha256(source)
            if target.is_file():
                target_hash = _sha256(target)
                if target_hash == source_hash:
                    manifest[key] = source_hash
                    continue
                if manifest.get(key) != target_hash:
                    continue
            target.parent.mkdir(parents=True, exist_ok=True)
            partial = target.with_name(target.name + ".part")
            shutil.copyfile(source, partial)
            partial.replace(target)
            manifest[key] = source_hash
        except OSError:
            logger.warning("Could not copy default file %s to %s", source, target, exc_info=True)

    try:
        root.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    except OSError:
        logger.warning("Could not write %s", manifest_path, exc_info=True)
    return root


def _resolve(default: tuple[Path, Path]) -> Path | None:
    """The user copy if present, else the bundled file, else None."""
    source, relative = default
    user_copy = user_defaults_dir() / relative
    if user_copy.is_file():
        return user_copy
    return source if source.is_file() else None


def default_camera_config_path() -> Path | None:
    return _resolve(DEFAULT_CAMERA_CONFIG)


def default_hierarchy_path() -> Path | None:
    return _resolve(DEFAULT_HIERARCHY)


def default_ai_model_path() -> Path | None:
    return _resolve(DEFAULT_AI_MODEL)


def browse_dir(current: str, subdir: Path) -> str:
    """Start folder for a file dialog: where ``current`` lives, else the matching defaults subfolder."""
    if current:
        parent = Path(current).parent
        if parent.is_dir():
            return str(parent)
    return str(user_defaults_dir() / subdir)
