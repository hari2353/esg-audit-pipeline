from esg_pipeline.incremental import dedup_by_key, incremental_filter


def test_incremental_filter():
    records = [
        {"id": 1, "updated_at": "2026-01-01"},
        {"id": 2, "updated_at": "2026-01-02"},
        {"id": 3, "updated_at": "2026-01-03"},
    ]
    new, wm = incremental_filter(records, "2026-01-01")
    assert [r["id"] for r in new] == [2, 3]
    assert wm == "2026-01-03"


def test_incremental_filter_none_new():
    records = [{"id": 1, "updated_at": "2026-01-01"}]
    new, wm = incremental_filter(records, "2026-01-05")
    assert new == []
    assert wm == "2026-01-05"


def test_dedup_by_key():
    records = [{"id": 1, "v": "a"}, {"id": 1, "v": "b"}, {"id": 2, "v": "c"}]
    out = dedup_by_key(records)
    assert len(out) == 2
    assert {r["v"] for r in out} == {"b", "c"}


def test_dedup_empty():
    assert dedup_by_key([]) == []
