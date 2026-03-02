from unittest.mock import patch, MagicMock

from src.models import PlanInfo, DiffResult
from src.notifier import notify_diff, notify_error, _make_plan_embed, _make_price_change_embed


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


def test_make_plan_embed():
    plan = _make_plan()
    embed = _make_plan_embed(plan)
    assert "テスト自動車学校" in embed.title
    assert "新潟県" in embed.title


def test_make_price_change_embed():
    plan = _make_plan(price_min=220000)
    embed = _make_price_change_embed(plan, old_price=250000)
    assert "価格変動" in embed.title


@patch("src.notifier._send_webhook")
def test_notify_diff_with_new_plans(mock_send):
    diff = DiffResult(
        new_plans=[_make_plan()],
        total_active=1,
    )
    notify_diff(diff)
    mock_send.assert_called_once()
    embeds = mock_send.call_args[0][0]
    assert len(embeds) == 2  # summary + 1 plan


@patch("src.notifier._send_webhook")
def test_notify_diff_no_changes(mock_send):
    diff = DiffResult(total_active=5)
    notify_diff(diff)
    mock_send.assert_called_once()
    embeds = mock_send.call_args[0][0]
    assert len(embeds) == 1  # summary only
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
    assert len(embeds) == 2  # summary + 1 price change


@patch("src.notifier._send_webhook")
def test_notify_error(mock_send):
    notify_error("テストエラー")
    mock_send.assert_called_once()
    embeds = mock_send.call_args[0][0]
    assert "エラー" in embeds[0].title


@patch("src.notifier.DISCORD_WEBHOOK_URL", "")
def test_send_webhook_no_url(caplog):
    """Webhook URLが未設定の場合はスキップ"""
    import logging
    with caplog.at_level(logging.WARNING):
        diff = DiffResult(new_plans=[_make_plan()], total_active=1)
        notify_diff(diff)
        assert "未設定" in caplog.text
