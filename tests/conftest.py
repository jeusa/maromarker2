import sys
from pathlib import Path


# Use .absolute() rather than .resolve() here: this file is reached via a symlink
# (tests/maromarker2 -> the standalone maromarker2 repo's tests/ dir), and
# .resolve() would follow that symlink out of this repo, breaking the parents[2]
# math below. .absolute() preserves the symlinked path as given.
SRC_DIR = Path(__file__).absolute().parents[2] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

REPO_ROOT = Path(__file__).absolute().parents[2]
