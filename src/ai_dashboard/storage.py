"""AI Bubble Dashboard - 日次履歴の永続化 (data/ai_dashboard/history.json)"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from .config import DATA_DIR, HISTORY_FILE, HISTORY_MAX_DAYS

JST = timezone(timedelta(hours=9))

EMPTY_HISTORY = {
    "last_updated": "",
    "daily": {},           # {"YYYY-MM-DD": {"hy_oas_bps": 294.0, ...}}
    "levels": {},          # 前回実行時の各指標レベル {"crwv_backlog": "green", ...}
    "composite_level": "",
    "reminders_sent": {},  # {"CRWV-2026-11-10": "2026-11-04"}
}


def now_jst() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def today_jst() -> str:
    return datetime.now(JST).strftime("%Y-%m-%d")


def load_history() -> dict:
    if not HISTORY_FILE.exists():
        return json.loads(json.dumps(EMPTY_HISTORY))
    with open(HISTORY_FILE, "r", encoding="utf-8") as f:
        history = json.load(f)
    for key, default in EMPTY_HISTORY.items():
        history.setdefault(key, json.loads(json.dumps(default)))
    return history


def save_history(history: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    history["last_updated"] = now_jst()
    _trim_daily(history)
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2, sort_keys=True)


def merge_daily(history: dict, date_str: str, metrics: dict[str, float]) -> None:
    """指定日のエントリに数値をマージする。Noneは書き込まない"""
    clean = {k: v for k, v in metrics.items() if v is not None}
    if not clean:
        return
    day = history["daily"].setdefault(date_str, {})
    day.update(clean)


def get_series(history: dict, metric: str) -> list[tuple[str, float]]:
    """metric の (date, value) 時系列を日付昇順で返す"""
    out = [
        (d, vals[metric])
        for d, vals in history["daily"].items()
        if metric in vals and vals[metric] is not None
    ]
    out.sort(key=lambda x: x[0])
    return out


def latest_value(history: dict, metric: str) -> tuple[str, float] | None:
    series = get_series(history, metric)
    return series[-1] if series else None


def value_near_days_ago(
    history: dict, metric: str, days: int, tolerance: int = 21
) -> tuple[str, float] | None:
    """およそ days 日前の値を返す。tolerance 日以内に観測がなければ None"""
    series = get_series(history, metric)
    if not series:
        return None
    target = datetime.now(JST).date() - timedelta(days=days)
    best = None
    best_gap = tolerance + 1
    for d, v in series:
        gap = abs((datetime.strptime(d, "%Y-%m-%d").date() - target).days)
        if gap < best_gap:
            best_gap = gap
            best = (d, v)
    return best


def _trim_daily(history: dict) -> None:
    cutoff = (datetime.now(JST).date() - timedelta(days=HISTORY_MAX_DAYS)).strftime(
        "%Y-%m-%d"
    )
    history["daily"] = {d: v for d, v in history["daily"].items() if d >= cutoff}
