import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from src.models import PlanInfo
from src.storage import compute_diff, load_history, save_history, update_history


def _make_plan(**kwargs) -> PlanInfo:
    defaults = {
        "source": "test",
        "school_name": "テスト校",
        "location": "新潟県",
        "start_date": "2026-07-26",
        "price_min": 220000,
        "room_type": "シングル",
    }
    defaults.update(kwargs)
    return PlanInfo(**defaults)


def test_load_history_empty(tmp_path: Path):
    with patch("src.storage.HISTORY_FILE", tmp_path / "nonexistent.json"):
        h = load_history()
        assert h == {"last_updated": "", "plans": {}}


def test_save_and_load_history(tmp_path: Path):
    filepath = tmp_path / "history.json"
    with patch("src.storage.HISTORY_FILE", filepath), \
         patch("src.storage.DATA_DIR", tmp_path):
        data = {"last_updated": "2026-03-01", "plans": {"abc": {"source": "test"}}}
        save_history(data)

        loaded = load_history()
        assert loaded["plans"]["abc"]["source"] == "test"


def test_compute_diff_new_plans():
    old = {"plans": {}}
    plan = _make_plan()

    diff = compute_diff(old, [plan])
    assert len(diff.new_plans) == 1
    assert diff.new_plans[0].school_name == "テスト校"
    assert diff.total_active == 1
    assert len(diff.removed_plans) == 0
    assert len(diff.price_changes) == 0


def test_compute_diff_price_change():
    plan = _make_plan(price_min=220000)
    pid = plan.plan_id

    old = {
        "plans": {
            pid: plan.to_dict() | {"price_min": 250000},
        }
    }

    diff = compute_diff(old, [plan])
    assert len(diff.new_plans) == 0
    assert len(diff.price_changes) == 1
    changed_plan, old_price = diff.price_changes[0]
    assert old_price == 250000
    assert changed_plan.price_min == 220000


def test_compute_diff_removed():
    plan = _make_plan()
    pid = plan.plan_id

    old = {"plans": {pid: plan.to_dict()}}

    diff = compute_diff(old, [])  # 今回はプランなし
    assert len(diff.removed_plans) == 1
    assert diff.total_active == 0


def test_compute_diff_no_change():
    plan = _make_plan()
    pid = plan.plan_id

    old = {"plans": {pid: plan.to_dict()}}

    diff = compute_diff(old, [plan])
    assert len(diff.new_plans) == 0
    assert len(diff.price_changes) == 0
    assert len(diff.removed_plans) == 0
    assert diff.total_active == 1


def test_update_history():
    plan = _make_plan()
    old = {"plans": {}}

    new_history = update_history(old, [plan])
    assert plan.plan_id in new_history["plans"]
    assert new_history["last_updated"] != ""
    assert new_history["plans"][plan.plan_id]["first_seen"] != ""
