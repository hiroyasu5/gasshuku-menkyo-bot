"""Stooq から日次株価 (終値) を取得する。無料・APIキー不要。

https://stooq.com/q/d/l/?s=nvda.us&d1=YYYYMMDD&d2=YYYYMMDD&i=d
が Date,Open,High,Low,Close,Volume のCSVを返す。

200DMA計算のため約1.2年分を取得する。銘柄単位の失敗は許容し、
呼び出し側でカバレッジ (取得できた銘柄割合) を判定に使う。
"""
from __future__ import annotations

import csv
import io
import logging
import time
from datetime import date, timedelta

import httpx

from ..config import HTTP_TIMEOUT

logger = logging.getLogger(__name__)

STOOQ_URL = "https://stooq.com/q/d/l/"
LOOKBACK_DAYS = 440  # 200DMA + 90日リターンに十分な期間
REQUEST_INTERVAL_SEC = 1.0  # 連続アクセスの間隔


def fetch_close_series(stooq_symbol: str) -> list[tuple[str, float]]:
    """(date, close) を日付昇順で返す。データがなければ空リスト"""
    d2 = date.today()
    d1 = d2 - timedelta(days=LOOKBACK_DAYS)
    resp = httpx.get(
        STOOQ_URL,
        params={
            "s": stooq_symbol,
            "d1": d1.strftime("%Y%m%d"),
            "d2": d2.strftime("%Y%m%d"),
            "i": "d",
        },
        headers={"User-Agent": "curl/8.5.0"},
        timeout=HTTP_TIMEOUT,
        follow_redirects=True,
    )
    resp.raise_for_status()
    out: list[tuple[str, float]] = []
    reader = csv.DictReader(io.StringIO(resp.text))
    for row in reader:
        d = (row.get("Date") or "").strip()
        c = (row.get("Close") or "").strip()
        if not d or not c:
            continue
        try:
            out.append((d, float(c)))
        except ValueError:
            continue
    out.sort(key=lambda x: x[0])
    return out


def fetch_basket_prices(
    basket: dict[str, tuple[str, str]],
) -> dict[str, list[tuple[str, float]]]:
    """バスケット全銘柄の終値系列。取得できた銘柄のみ返す"""
    prices: dict[str, list[tuple[str, float]]] = {}
    for ticker, (symbol, _sector) in basket.items():
        try:
            series = fetch_close_series(symbol)
            if len(series) >= 30:
                prices[ticker] = series
            else:
                logger.info("[Stooq] %s: データ不足 (%d日分)", ticker, len(series))
        except Exception as e:
            logger.info("[Stooq] %s 取得失敗: %s", ticker, e)
        time.sleep(REQUEST_INTERVAL_SEC)
    logger.info("[Stooq] %d/%d 銘柄取得", len(prices), len(basket))
    return prices
