"""AI Bubble Dashboard - 設定"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Discord。専用チャンネルを分けたい場合は AI_DASHBOARD_DISCORD_WEBHOOK_URL を設定。
# 未設定なら既存Botと同じ DISCORD_WEBHOOK_URL に送る。
DISCORD_WEBHOOK_URL = os.getenv("AI_DASHBOARD_DISCORD_WEBHOOK_URL", "") or os.getenv(
    "DISCORD_WEBHOOK_URL", ""
)

# Paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data" / "ai_dashboard"
HISTORY_FILE = DATA_DIR / "history.json"
MANUAL_INPUTS_FILE = DATA_DIR / "manual_inputs.yaml"
DASHBOARD_DIR = PROJECT_ROOT / "docs" / "ai-dashboard"
DASHBOARD_FILE = DASHBOARD_DIR / "index.html"

# HTTP
HTTP_TIMEOUT = 30
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

# FRED系列ID
FRED_SERIES = {
    "hy_oas_bps": "BAMLH0A0HYM2",       # ICE BofA US High Yield OAS
    "single_b_oas_bps": "BAMLH0A2HYB",  # ICE BofA Single-B US High Yield OAS
    "ig_oas_bps": "BAMLC0A0CM",         # ICE BofA US Corporate (IG) OAS
}

# 初回実行時にFREDを何日分バックフィルするか
FRED_BACKFILL_DAYS = 730

# historyに保持する日次データの最大日数
HISTORY_MAX_DAYS = 750

# 決算リマインダーを何日前から出すか
REMINDER_LOOKAHEAD_DAYS = 7

# 週次サマリーを送る曜日 (0=月曜, JST基準)
WEEKLY_SUMMARY_WEEKDAY = 0
