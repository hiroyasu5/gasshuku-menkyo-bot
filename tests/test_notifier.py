from unittest.mock import patch

from src.models import PlanInfo, DiffResult
from src.notifier import (
    notify_diff,
    notify_error,
    _build_table,
    _plan_to_row,
    _price_change_to_row,
    _display_width,
    _pad,
    _truncate,
)


def _make_plan(**kwargs) -> PlanInfo:
    defaults = {
        "source": "test",
        "school_name": "テスト自動車学校",
        "location": "新潟県",
        "start_date": "2026-07-26",
        "duration_days": 15,
        "price_min": 220000,
        "room_type": "シングル",
    }
    defaults.update(kwargs)
    return PlanInfo(**defaults)


# ── ユーティリティのテスト ──


def test_display_width():
    assert _display_width("abc") == 3
    assert _display_width("あいう") == 6
    assert _display_width("ab あ") == 5  # a(1)+b(1)+空白(1)+あ(2)


def test_pad():
    s = _pad("abc", 6)
    assert s == "abc   "
    s = _pad("あ", 4)
    assert s == "あ  "


def test_truncate():
    assert _truncate("テスト自動車学校", 10) == "テスト自…"
    assert _truncate("abc", 10) == "abc"


def test_build_table():
    headers = ["名前", "値"]
    rows = [["A校", "100"], ["B校", "200"]]
    table = _build_table(headers, rows)
    assert "```" in table
    assert "A校" in table
    assert "B校" in table
    assert " | " in table


# ── 行データ作成のテスト ──


def test_plan_to_row():
    plan = _make_plan()
    row = _plan_to_row(plan)
    assert len(row) == 6
    assert "7/26" in row[2]
    assert "¥220,000" in row[4]
    assert "test(シングル)" in row[5]


def test_plan_to_row_no_room():
    plan = _make_plan(room_type="")
    row = _plan_to_row(plan)
    assert row[5] == "test"


def test_price_change_to_row():
    plan = _make_plan(price_min=200000)
    row = _price_change_to_row(plan, 250000)
    assert len(row) == 5
    assert "↓" in row[3]
    assert "250,000" in row[3]
    assert "200,000" in row[3]


# ── notify_diff のテスト ──


@patch("src.notifier._send_webhook")
def test_notify_diff_with_new_plans(mock_send):
    diff = DiffResult(
        new_plans=[_make_plan()],
        total_active=1,
    )
    notify_diff(diff)
    mock_send.assert_called_once()
    embeds = mock_send.call_args[0][0]
    # summary + 1 table embed
    assert len(embeds) == 2
    assert "```" in embeds[1].description


@patch("src.notifier._send_webhook")
def test_notify_diff_multiple_plans_one_table(mock_send):
    """複数プランが1つの表にまとまる"""
    diff = DiffResult(
        new_plans=[
            _make_plan(source="site_a", school_name="A校"),
            _make_plan(source="site_b", school_name="B校"),
        ],
        total_active=2,
    )
    notify_diff(diff)
    mock_send.assert_called_once()
    embeds = mock_send.call_args[0][0]
    # summary + 1 table embed (全部1つの表)
    assert len(embeds) == 2
    assert "A校" in embeds[1].description
    assert "B校" in embeds[1].description


@patch("src.notifier._send_webhook")
def test_notify_diff_no_changes(mock_send):
    diff = DiffResult(total_active=5)
    notify_diff(diff)
    mock_send.assert_called_once()
    embeds = mock_send.call_args[0][0]
    assert len(embeds) == 1
    assert "新着なし" in embeds[0].title


@patch("src.notifier._send_webhook")
def test_notify_diff_with_price_changes(mock_send):
    plan = _make_plan(price_min=200000)
    diff = DiffResult(
        price_changes=[(plan, 250000)],
        total_active=1,
    )
    notify_diff(diff)
    mock_send.assert_called_once()
    embeds = mock_send.call_args[0][0]
    # summary + 1 price change table
    assert len(embeds) == 2
    assert "```" in embeds[1].description


@patch("src.notifier._send_webhook")
def test_notify_diff_new_and_price_changes(mock_send):
    """新着と価格変動がそれぞれ別テーブルになる"""
    new_plan = _make_plan(source="site_a")
    changed_plan = _make_plan(source="site_b", price_min=200000)
    diff = DiffResult(
        new_plans=[new_plan],
        price_changes=[(changed_plan, 250000)],
        total_active=2,
    )
    notify_diff(diff)
    mock_send.assert_called_once()
    embeds = mock_send.call_args[0][0]
    # summary + new table + change table
    assert len(embeds) == 3


@patch("src.notifier._send_webhook")
def test_notify_error(mock_send):
    notify_error("テストエラー")
    mock_send.assert_called_once()
    embeds = mock_send.call_args[0][0]
    assert "エラー" in embeds[0].title


@patch("src.notifier.DISCORD_WEBHOOK_URL", "")
def test_send_webhook_no_url(caplog):
    import logging
    with caplog.at_level(logging.WARNING):
        diff = DiffResult(new_plans=[_make_plan()], total_active=1)
        notify_diff(diff)
        assert "未設定" in caplog.text
