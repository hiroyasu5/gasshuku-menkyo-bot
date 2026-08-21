"""FRED からOAS系列を取得する。

2系統を持つ:
1. FRED_API_KEY が設定されていれば公式API (api.stlouisfed.org) を使う。
   キーは https://fred.stlouisfed.org/docs/api/api_key.html で無料発行できる。
   GitHub ActionsのIPはfredgraph.csv側でbot対策により遮断されることがある
   (実測: HTTP/2 INTERNAL_ERROR / read timeout) ため、APIキー方式を推奨。
2. キーが無ければ fredgraph.csv (キー不要) をリトライ付きで試す。
   注意: ブラウザ風User-Agentを付けるとbot検知でtarpitされる (実測60s無応答)。
   素のHTTPクライアントUAだと即応答するため、あえてブラウザUAを送らない。

値は % 表記 (2.94 = 294bp) なので bp に変換して返す。欠損日は "." でスキップ。
"""
from __future__ import annotations

import csv
import io
import logging
import os
import time

import httpx

from ..config import USER_AGENT

logger = logging.getLogger(__name__)

FREDGRAPH_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
API_URL = "https://api.stlouisfed.org/fred/series/observations"
FETCH_TIMEOUT = 60
RETRIES = 3
BACKOFF_BASE = 5  # seconds


def fetch_series_bps(series_id: str, start_date: str) -> list[tuple[str, float]]:
    """(date, value_bps) のリストを日付昇順で返す"""
    api_key = os.getenv("FRED_API_KEY", "")
    if api_key:
        out = _fetch_api(series_id, start_date, api_key)
    else:
        out = _fetch_fredgraph(series_id, start_date)
    out.sort(key=lambda x: x[0])
    logger.info("[FRED %s] %d observations (since %s)", series_id, len(out), start_date)
    return out


def _to_bps(rows: list[tuple[str, str]]) -> list[tuple[str, float]]:
    out: list[tuple[str, float]] = []
    for date_str, raw in rows:
        raw = raw.strip()
        if raw in ("", "."):
            continue
        try:
            out.append((date_str, float(raw) * 100.0))  # % -> bp
        except ValueError:
            continue
    return out


def _fetch_api(series_id: str, start_date: str, api_key: str) -> list[tuple[str, float]]:
    last_error: Exception | None = None
    for attempt in range(RETRIES):
        try:
            resp = httpx.get(
                API_URL,
                params={
                    "series_id": series_id,
                    "observation_start": start_date,
                    "api_key": api_key,
                    "file_type": "json",
                    "limit": 100000,
                },
                headers={"User-Agent": USER_AGENT},
                timeout=FETCH_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
            return _to_bps(
                [(o["date"], str(o.get("value", "."))) for o in data.get("observations", [])]
            )
        except Exception as e:
            last_error = e
            logger.info("[FRED API %s] attempt %d 失敗: %s", series_id, attempt + 1, e)
            time.sleep(BACKOFF_BASE * (attempt + 1))
    raise RuntimeError(f"FRED API取得失敗 ({series_id}): {last_error}")


def _fetch_fredgraph(series_id: str, start_date: str) -> list[tuple[str, float]]:
    last_error: Exception | None = None
    # ブラウザUAはbot検知に引っかかるため使わない (docstring参照)
    headers = {"User-Agent": "curl/8.5.0", "Accept": "text/csv,text/plain,*/*"}
    for attempt in range(RETRIES):
        try:
            resp = httpx.get(
                FREDGRAPH_URL,
                params={"id": series_id, "cosd": start_date},
                headers=headers,
                timeout=FETCH_TIMEOUT,
                follow_redirects=True,
            )
            resp.raise_for_status()
            reader = csv.reader(io.StringIO(resp.text))
            header = next(reader, None)
            if not header or len(header) < 2:
                raise ValueError(f"FRED CSVの形式が想定外です: header={header!r}")
            return _to_bps([(r[0].strip(), r[1]) for r in reader if len(r) >= 2])
        except Exception as e:
            last_error = e
            logger.info("[fredgraph %s] attempt %d 失敗: %s", series_id, attempt + 1, e)
            time.sleep(BACKOFF_BASE * (attempt + 1))
    raise RuntimeError(
        f"fredgraph.csv取得失敗 ({series_id}): {last_error}。"
        "FRED_API_KEY (無料) の設定を推奨します"
    )
