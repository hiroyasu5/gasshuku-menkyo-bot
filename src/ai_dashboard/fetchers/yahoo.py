"""Yahoo Finance chart API から日次終値を取得する。キー不要。

https://query1.finance.yahoo.com/v8/finance/chart/NVDA?range=1y&interval=1d
が timestamp配列と close配列 を含むJSONを返す (Actionsランナーで動作確認済み。
Stooqはproof-of-work型bot対策のため使えない)。

200DMA計算のため1年分を取得する。銘柄単位の失敗は許容し、
呼び出し側でカバレッジ (取得できた銘柄割合) を判定に使う。
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

import httpx

from ..config import HTTP_TIMEOUT, USER_AGENT

logger = logging.getLogger(__name__)

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
REQUEST_INTERVAL_SEC = 0.7


def fetch_close_series(ticker: str) -> list[tuple[str, float]]:
    """(date, close) を日付昇順で返す。データがなければ空リスト"""
    resp = httpx.get(
        CHART_URL.format(ticker=ticker),
        params={"range": "1y", "interval": "1d"},
        headers={"User-Agent": USER_AGENT},
        timeout=HTTP_TIMEOUT,
        follow_redirects=True,
    )
    resp.raise_for_status()
    data = resp.json()
    results = (data.get("chart") or {}).get("result") or []
    if not results:
        err = (data.get("chart") or {}).get("error")
        raise ValueError(f"Yahoo chart error: {err}")
    res = results[0]
    timestamps = res.get("timestamp") or []
    quotes = (res.get("indicators") or {}).get("quote") or [{}]
    closes = quotes[0].get("close") or []
    out: list[tuple[str, float]] = []
    for ts, close in zip(timestamps, closes):
        if close is None:
            continue
        d = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
        out.append((d, float(close)))
    out.sort(key=lambda x: x[0])
    return out


def fetch_basket_prices(tickers: list[str]) -> dict[str, list[tuple[str, float]]]:
    """バスケット全銘柄の終値系列。取得できた銘柄のみ返す"""
    prices: dict[str, list[tuple[str, float]]] = {}
    for ticker in tickers:
        try:
            series = fetch_close_series(ticker)
            if len(series) >= 30:
                prices[ticker] = series
            else:
                logger.info("[Yahoo] %s: データ不足 (%d日分)", ticker, len(series))
        except Exception as e:
            logger.info("[Yahoo] %s 取得失敗: %s", ticker, e)
        time.sleep(REQUEST_INTERVAL_SEC)
    logger.info("[Yahoo] %d/%d 銘柄取得", len(prices), len(tickers))
    return prices
