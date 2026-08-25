"""Alpha Vantage EARNINGS_ESTIMATES から FY1 EPS consensus を取得する。

無料APIキー (https://www.alphavantage.co/support/#api-key) を
ALPHAVANTAGE_API_KEY に設定すると有効になる。無料枠は25リクエスト/日のため
Tier1の12銘柄に絞る。レスポンスのフィールド名はプラン/時期で揺れる可能性が
あるため、キー名のあいまい一致で防御的にパースする。
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any

import httpx

from ..config import HTTP_TIMEOUT

logger = logging.getLogger(__name__)

AV_URL = "https://www.alphavantage.co/query"
REQUEST_INTERVAL_SEC = 15  # 旧無料枠の5req/分制限にも収まる間隔


def _as_float(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f


def _find_number(d: dict, *substrings: str) -> float | None:
    """キー名が substrings を全て含む最初の数値フィールドを返す"""
    for k, v in d.items():
        lk = str(k).lower()
        if all(s in lk for s in substrings):
            f = _as_float(v)
            if f is not None:
                return f
    return None


def _parse_estimates(data: dict) -> dict | None:
    """{"fy1_eps": float, "rev_up30": float|None, "rev_down30": float|None}"""
    # annual/horizon系の推定リストを探す
    rows = None
    for k, v in data.items():
        lk = str(k).lower()
        if isinstance(v, list) and v and isinstance(v[0], dict) and "estimate" in lk:
            rows = v
            break
    if rows is None:
        for v in data.values():
            if isinstance(v, list) and v and isinstance(v[0], dict):
                rows = v
                break
    if not rows:
        return None
    # horizon フィールドがあれば "current fiscal year" を優先
    fy1 = None
    for r in rows:
        horizon = str(r.get("horizon", "")).lower()
        if "current" in horizon and "year" in horizon:
            fy1 = r
            break
    if fy1 is None:
        fy1 = rows[0]
    eps = _find_number(fy1, "eps", "avg") or _find_number(fy1, "eps", "average") \
        or _find_number(fy1, "eps", "mean") or _find_number(fy1, "eps", "estimate")
    if eps is None:
        return None
    return {
        "fy1_eps": eps,
        "rev_up30": _find_number(fy1, "revision", "up"),
        "rev_down30": _find_number(fy1, "revision", "down"),
    }


def fetch_estimates(tickers: list[str]) -> dict[str, dict]:
    """{ticker: {"fy1_eps": ..., "rev_up30": ..., "rev_down30": ...}}。

    APIキー未設定なら空dict。銘柄単位の失敗は許容。
    """
    api_key = os.getenv("ALPHAVANTAGE_API_KEY", "")
    if not api_key:
        logger.info("[AlphaVantage] APIキー未設定のためEPS consensus取得をスキップ")
        return {}

    out: dict[str, dict] = {}
    for i, ticker in enumerate(tickers):
        if i:
            time.sleep(REQUEST_INTERVAL_SEC)
        try:
            resp = httpx.get(
                AV_URL,
                params={
                    "function": "EARNINGS_ESTIMATES",
                    "symbol": ticker,
                    "apikey": api_key,
                },
                headers={"User-Agent": "curl/8.5.0"},
                timeout=HTTP_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
            if not isinstance(data, dict) or "Note" in data or "Information" in data:
                logger.info("[AlphaVantage] %s: レート制限/情報応答: %s",
                            ticker, str(data)[:120])
                continue
            parsed = _parse_estimates(data)
            if parsed:
                out[ticker] = parsed
            else:
                logger.info("[AlphaVantage] %s: EPS estimateをパースできず", ticker)
        except Exception as e:
            logger.info("[AlphaVantage] %s 取得失敗: %s", ticker, e)
    logger.info("[AlphaVantage] %d/%d 銘柄取得", len(out), len(tickers))
    return out
