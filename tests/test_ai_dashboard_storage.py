from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from src.ai_dashboard import storage


def test_load_history_empty(tmp_path: Path):
    with patch("src.ai_dashboard.storage.HISTORY_FILE", tmp_path / "none.json"):
        h = storage.load_history()
    assert h["daily"] == {}
    assert h["levels"] == {}


def test_save_and_load_roundtrip(tmp_path: Path):
    f = tmp_path / "history.json"
    with patch("src.ai_dashboard.storage.HISTORY_FILE", f), \
         patch("src.ai_dashboard.storage.DATA_DIR", tmp_path):
        h = storage.load_history()
        storage.merge_daily(h, "2026-08-20", {"hy_oas_bps": 294.0, "skip": None})
        storage.save_history(h)
        h2 = storage.load_history()
    assert h2["daily"]["2026-08-20"]["hy_oas_bps"] == 294.0
    assert "skip" not in h2["daily"]["2026-08-20"]
    assert h2["last_updated"] != ""


def test_get_series_sorted():
    h = {"daily": {
        "2026-08-20": {"m": 2.0},
        "2026-08-18": {"m": 1.0},
        "2026-08-19": {"other": 9.9},
    }}
    assert storage.get_series(h, "m") == [("2026-08-18", 1.0), ("2026-08-20", 2.0)]
    assert storage.latest_value(h, "m") == ("2026-08-20", 2.0)


def test_value_near_days_ago():
    today = date.today()
    h = {"daily": {
        (today - timedelta(days=92)).isoformat(): {"m": 100.0},
        today.isoformat(): {"m": 200.0},
    }}
    near = storage.value_near_days_ago(h, "m", 90, tolerance=21)
    assert near is not None
    assert near[1] == 100.0
    # toleranceを超えて離れていればNone
    assert storage.value_near_days_ago(h, "m", 45, tolerance=21) is None


def test_trim_daily(tmp_path: Path):
    old_date = (date.today() - timedelta(days=900)).isoformat()
    with patch("src.ai_dashboard.storage.HISTORY_FILE", tmp_path / "h.json"), \
         patch("src.ai_dashboard.storage.DATA_DIR", tmp_path):
        h = storage.load_history()
        storage.merge_daily(h, old_date, {"m": 1.0})
        storage.merge_daily(h, date.today().isoformat(), {"m": 2.0})
        storage.save_history(h)
    assert old_date not in h["daily"]
    assert date.today().isoformat() in h["daily"]
