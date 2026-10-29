from maromarker2.models.annotation_store import COLUMNS, AnnotationStore


def test_loads_real_legacy_results_csv(tmp_path, pytestconfig):
    repo_root = pytestconfig.rootpath
    path = repo_root / "sample_data" / "results_2022_06.csv"

    store = AnnotationStore.load(path)

    assert list(store.data.columns) == COLUMNS
    assert len(store.data) > 0
    # a known matched-pair-free fish detection row from this real file; group/species
    # are normalized to lowercase on load to match the (all-lowercase) taxonomy
    fish_rows = store.data[store.data["group"] == "fish"]
    assert len(fish_rows) >= 2
