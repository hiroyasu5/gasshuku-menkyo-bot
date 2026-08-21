from datetime import date, timedelta

from src.ai_dashboard.reminders import due_reminders


def _manual(events):
    return {"earnings_calendar": events}


def test_reminder_within_window():
    ev_date = (date.today() + timedelta(days=3)).isoformat()
    manual = _manual([{"ticker": "CRWV", "date": ev_date, "note": "Q3"}])
    history = {"reminders_sent": {}}
    due = due_reminders(manual, history)
    assert len(due) == 1
    assert due[0]["ticker"] == "CRWV"
    assert f"CRWV-{ev_date}" in history["reminders_sent"]


def test_reminder_not_sent_twice():
    ev_date = (date.today() + timedelta(days=3)).isoformat()
    manual = _manual([{"ticker": "CRWV", "date": ev_date}])
    history = {"reminders_sent": {}}
    assert len(due_reminders(manual, history)) == 1
    assert len(due_reminders(manual, history)) == 0


def test_reminder_outside_window():
    far = (date.today() + timedelta(days=30)).isoformat()
    past = (date.today() - timedelta(days=2)).isoformat()
    manual = _manual([
        {"ticker": "FAR", "date": far},
        {"ticker": "PAST", "date": past},
    ])
    history = {"reminders_sent": {}}
    assert due_reminders(manual, history) == []
