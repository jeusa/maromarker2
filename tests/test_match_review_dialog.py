import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from maromarker2.geometry.matching import MatchResult
from maromarker2.ui.editor.match_review_dialog import MatchReviewDialog


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


def test_dialog_sorts_by_cost_and_defaults_all_checked(qapp):
    results = [MatchResult(0, 1, 30.0), MatchResult(2, 3, 5.0)]

    dialog = MatchReviewDialog(results)

    assert [r.cost for r in dialog.results] == [5.0, 30.0]
    assert dialog.accepted_results() == dialog.results


def test_unchecking_a_row_excludes_it_from_accepted_results(qapp):
    results = [MatchResult(0, 1, 5.0), MatchResult(2, 3, 10.0)]
    dialog = MatchReviewDialog(results)

    dialog._checkboxes[1].setChecked(False)

    accepted = dialog.accepted_results()
    assert len(accepted) == 1
    assert accepted[0].left_index == 0
