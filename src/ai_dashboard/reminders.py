"""AI Bubble Dashboard - 決算・データ更新リマインダー。

manual_inputs.yaml の earnings_calendar を見て、各イベントにつき2回
Discordへ通知する (アラーム):
- 7日前 (2〜7日前の最初の実行で1回)
- 前日〜当日 (0〜1日前の最初の実行で1回)

送信済みは history.json の reminders_sent にステージ別キーで記録する。
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from .config import REMINDER_LOOKAHEAD_DAYS
from .manual import earnings_calendar

logger = logging.getLogger(__name__)

JST = timezone(timedelta(hours=9))

# (ステージキー, 窓の下限日, 窓の上限日, 表示ラベル)
STAGES = [
    ("pre7", 2, REMINDER_LOOKAHEAD_DAYS, "1週間前"),
    ("pre1", 0, 1, "直前"),
]


def due_reminders(manual: dict, history: dict) -> list[dict]:
    """通知すべきイベント一覧。副作用として reminders_sent に記録する"""
    today = datetime.now(JST).date()
    sent: dict = history.setdefault("reminders_sent", {})
    due: list[dict] = []
    for event in earnings_calendar(manual):
        try:
            event_date = date.fromisoformat(str(event["date"]))
        except ValueError:
            logger.warning("earnings_calendarの日付が不正: %r", event)
            continue
        days_until = (event_date - today).days
        for stage, lo, hi, label in STAGES:
            key = f"{event.get('ticker', '?')}-{event['date']}-{stage}"
            if lo <= days_until <= hi and key not in sent:
                due.append({**event, "days_until": days_until, "stage": label})
                sent[key] = today.isoformat()
    return due
