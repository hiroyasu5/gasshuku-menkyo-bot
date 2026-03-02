from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

from .config import HISTORY_FILE, DATA_DIR
from .models import PlanInfo, DiffResult

JST = timezone(timedelta(hours=9))


def _now_jst() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def _today_jst() -> str:
    return datetime.now(JST).strftime("%Y-%m-%d")


def load_history() -> dict:
    if not HISTORY_FILE.exists():
        return {"last_updated": "", "plans": {}}
    with open(HISTORY_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_history(history: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def compute_diff(old_history: dict, new_plans: list[PlanInfo]) -> DiffResult:
    old_plans = old_history.get("plans", {})
    today = _today_jst()

    result = DiffResult()
    seen_ids: set[str] = set()

    for plan in new_plans:
        pid = plan.plan_id
        seen_ids.add(pid)
        plan.last_seen = today

        if pid not in old_plans:
            plan.first_seen = today
            result.new_plans.append(plan)
        else:
            old = old_plans[pid]
            plan.first_seen = old.get("first_seen", today)

            old_price = old.get("price_min")
            if (
                plan.price_min is not None
                and old_price is not None
                and plan.price_min != old_price
            ):
                result.price_changes.append((plan, old_price))

    for pid, old_data in old_plans.items():
        if pid not in seen_ids:
            removed = PlanInfo.from_dict(old_data)
            result.removed_plans.append(removed)

    result.total_active = len(seen_ids)
    return result


def update_history(old_history: dict, new_plans: list[PlanInfo]) -> dict:
    today = _today_jst()
    old_plans = old_history.get("plans", {})
    updated_plans: dict = {}

    for plan in new_plans:
        pid = plan.plan_id
        plan.last_seen = today
        if pid in old_plans:
            plan.first_seen = old_plans[pid].get("first_seen", today)
        else:
            plan.first_seen = today
        updated_plans[pid] = plan.to_dict()

    return {
        "last_updated": _now_jst(),
        "plans": updated_plans,
    }
