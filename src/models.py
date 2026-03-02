from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, asdict
from datetime import date
from typing import Optional


@dataclass
class PlanInfo:
    source: str
    school_name: str
    location: str
    start_date: str  # "YYYY-MM-DD"
    duration_days: Optional[int] = None
    price_min: Optional[int] = None
    room_type: str = ""
    detail_url: str = ""
    first_seen: str = ""
    last_seen: str = ""

    @property
    def plan_id(self) -> str:
        raw = f"{self.source}|{self.school_name}|{self.start_date}|{self.room_type}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["plan_id"] = self.plan_id
        return d

    @classmethod
    def from_dict(cls, data: dict) -> PlanInfo:
        data = {k: v for k, v in data.items() if k != "plan_id"}
        return cls(**data)

    def format_price(self) -> str:
        if self.price_min is None:
            return "要問合せ"
        return f"¥{self.price_min:,}"

    def format_date(self) -> str:
        try:
            d = date.fromisoformat(self.start_date)
            return f"{d.month}/{d.day}"
        except ValueError:
            return self.start_date

    def format_duration(self) -> str:
        if self.duration_days is None:
            return "不明"
        return f"{self.duration_days}日間"


@dataclass
class DiffResult:
    new_plans: list[PlanInfo] = field(default_factory=list)
    price_changes: list[tuple[PlanInfo, int]] = field(default_factory=list)  # (plan, old_price)
    removed_plans: list[PlanInfo] = field(default_factory=list)
    total_active: int = 0
