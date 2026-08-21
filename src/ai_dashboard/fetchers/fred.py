"""FRED (fredgraph.csv) からOAS系列を取得する。APIキー不要。

https://fred.stlouisfed.org/graph/fredgraph.csv?id=BAMLH0A0HYM2&cosd=2026-01-01
の形式で、1列目が日付・2列目が値のCSVが返る。欠損日は "." になる。
値は % 表記 (2.94 = 294bp) なので bp に変換して返す。
"""
from __future__ import annotations

import csv
import io
import logging

import httpx

from ..config import HTTP_TIMEOUT, USER_AGENT

logger = logging.getLogger(__name__)

FREDGRAPH_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"


def fetch_series_bps(series_id: str, start_date: str) -> list[tuple[str, float]]:
    """(date, value_bps) のリストを日付昇順で返す"""
    resp = httpx.get(
        FREDGRAPH_URL,
        params={"id": series_id, "cosd": start_date},
        headers={"User-Agent": USER_AGENT},
        timeout=HTTP_TIMEOUT,
        follow_redirects=True,
    )
    resp.raise_for_status()

    out: list[tuple[str, float]] = []
    reader = csv.reader(io.StringIO(resp.text))
    header = next(reader, None)
    if not header or len(header) < 2:
        raise ValueError(f"FRED CSVの形式が想定外です: header={header!r}")
    for row in reader:
        if len(row) < 2:
            continue
        date_str, raw = row[0].strip(), row[1].strip()
        if raw in ("", "."):
            continue
        try:
            out.append((date_str, float(raw) * 100.0))  # % -> bp
        except ValueError:
            continue
    out.sort(key=lambda x: x[0])
    logger.info("[FRED %s] %d observations (since %s)", series_id, len(out), start_date)
    return out
