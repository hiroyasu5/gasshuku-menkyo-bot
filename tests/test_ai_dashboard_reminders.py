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
    assert due[0]["stage"] == "1週間前"
    assert f"CRWV-{ev_date}-pre7" in history["reminders_sent"]


def test_reminder_not_sent_twice():
    ev_date = (date.today() + timedelta(days=3)).isoformat()
    manual = _manual([{"ticker": "CRWV", "date": ev_date}])
    history = {"reminders_sent": {}}
    assert len(due_reminders(manual, history)) == 1
    assert len(due_reminders(manual, history)) == 0


def test_reminder_two_stages():
    # 7日前ステージ→前日ステージの2回鳴る
    ev_date = (date.today() + timedelta(days=6)).isoformat()
    manual = _manual([{"ticker": "ORCL", "date": ev_date}])
    history = {"reminders_sent": {}}
    due1 = due_reminders(manual, history)
    assert [d["stage"] for d in due1] == ["1週間前"]
    # 前日になったと仮定 (dateを付け替えて再判定)
    manual2 = _manual([{"ticker": "ORCL", "date": (date.today() + timedelta(days=1)).isoformat()}])
    due2 = due_reminders(manual2, history)
    assert [d["stage"] for d in due2] == ["直前"]


def test_reminder_day_of_event_fires_pre1():
    ev_date = date.today().isoformat()
    manual = _manual([{"ticker": "APLD", "date": ev_date}])
    history = {"reminders_sent": {}}
    due = due_reminders(manual, history)
    assert len(due) == 1
    assert due[0]["stage"] == "直前"


def test_reminder_outside_window():
    far = (date.today() + timedelta(days=30)).isoformat()
    past = (date.today() - timedelta(days=2)).isoformat()
    manual = _manual([
        {"ticker": "FAR", "date": far},
        {"ticker": "PAST", "date": past},
    ])
    history = {"reminders_sent": {}}
    assert due_reminders(manual, history) == []
