"""AI Bubble Dashboard - 手動入力データ (data/ai_dashboard/manual_inputs.yaml) の読み込み。

四半期決算ベースの指標・CRWV債券利回り・決算カレンダーはここから読む。
YAMLを編集してmainにpushすれば、Actionsが再評価してダッシュボードに反映する。
"""
from __future__ import annotations

import logging
from typing import Any

import yaml

from .config import MANUAL_INPUTS_FILE

logger = logging.getLogger(__name__)

# quarterly配下で扱う系列キー
QUARTERLY_KEYS = [
    "crwv", "nbis", "apld", "dlr", "aep", "power_forecast", "hyperscalers",
]


def load_manual_inputs() -> dict:
    if not MANUAL_INPUTS_FILE.exists():
        logger.warning("manual_inputs.yaml が見つかりません: %s", MANUAL_INPUTS_FILE)
        return {}
    with open(MANUAL_INPUTS_FILE, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError("manual_inputs.yaml のトップレベルはマッピングにしてください")
    return data


def _sort_key(entry: dict) -> str:
    # "2026Q2" / "2026-06" / 2026 のいずれでも文字列比較で時系列順になる形式を前提とする
    return str(entry.get("quarter") or entry.get("date") or entry.get("year") or "")


def series_entries(manual: dict, key: str) -> list[dict]:
    """quarterly.<key> のエントリ一覧を時系列昇順で返す"""
    entries = (manual.get("quarterly") or {}).get(key) or []
    entries = [e for e in entries if isinstance(e, dict)]
    return sorted(entries, key=_sort_key)


def latest_and_previous(manual: dict, key: str) -> tuple[dict | None, dict | None]:
    entries = series_entries(manual, key)
    latest = entries[-1] if entries else None
    prev = entries[-2] if len(entries) >= 2 else None
    return latest, prev


def financing_entries(manual: dict) -> list[dict]:
    entries = manual.get("financing") or []
    entries = [e for e in entries if isinstance(e, dict)]
    return sorted(entries, key=_sort_key)


def crwv_bond(manual: dict) -> dict:
    return manual.get("crwv_bond") or {}


def gpu_fallback(manual: dict) -> dict:
    """スクレイピング失敗時に使うGPU価格の手動値"""
    return manual.get("gpu_manual_fallback") or {}


def earnings_calendar(manual: dict) -> list[dict]:
    events = manual.get("earnings_calendar") or []
    return [e for e in events if isinstance(e, dict) and e.get("date")]


def as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
