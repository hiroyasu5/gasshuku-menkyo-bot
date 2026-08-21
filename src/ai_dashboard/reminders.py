"""AI Bubble Dashboard - 決算・データ更新リマインダー。

manual_inputs.yaml の earnings_calendar を見て、7日前〜当日のイベントを
1回だけDiscordに通知する。送信済みは history.json の reminders_sent に記録。
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from .config import REMINDER_LOOKAHEAD_DAYS
from .manual import earnings_calendar

logger = logging.getLogger(__name__)

JST = timezone(timedelta(hours=9))


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
        key = f"{event.get('ticker', '?')}-{event['date']}"
        days_until = (event_date - today).days
        if 0 <= days_until <= REMINDER_LOOKAHEAD_DAYS and key not in sent:
            due.append({**event, "days_until": days_until})
            sent[key] = today.isoformat()
    return due
