"""Market Early Warning のメトリクス計算。

取得した株価/EPS consensusから、historyのdailyに保存する数値を作る:
- breadth_50_pct / breadth_200_pct : 50/200DMAを上回る銘柄の割合 (%)
- breadth_coverage : 取得できた銘柄割合 (%)
- rev_up_n / rev_down_n / rev_total_n : 30日前比のFY1 EPS上方/下方修正社数
- me_90d_pt : Multiple Expansion (90日株価リターン% − 90日EPS修正%) の中央値
- px_ret90_med : Tier1の90日株価リターン中央値 (MEのEPS側が貯まるまでの表示用)

EPS consensusは history["estimates"][date][ticker] に日次スナップショットとして
保存し、30/90日前のスナップショットとの比較で自前計算する (v2の
「データで確認できるまで⚪」の原則に従い、蓄積前は該当指標を出さない)。
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from statistics import median

from .basket import TIER1_ESTIMATES

logger = logging.getLogger(__name__)

# EPS修正の「変化なし」とみなす閾値 (±0.5%)
REV_FLAT_THRESHOLD = 0.005


def _sma(values: list[float], window: int) -> float | None:
    if len(values) < window:
        return None
    return sum(values[-window:]) / window


def compute_breadth(
    prices: dict[str, list[tuple[str, float]]], basket_size: int
) -> dict[str, float]:
    """50/200DMA上回り率。200DMAを計算できた銘柄のみ分母に入れる"""
    above50 = above200 = n50 = n200 = 0
    for ticker, series in prices.items():
        closes = [c for _, c in series]
        if not closes:
            continue
        last = closes[-1]
        sma50 = _sma(closes, 50)
        sma200 = _sma(closes, 200)
        if sma50 is not None:
            n50 += 1
            if last > sma50:
                above50 += 1
        if sma200 is not None:
            n200 += 1
            if last > sma200:
                above200 += 1
    out: dict[str, float] = {
        "breadth_coverage": round(len(prices) / basket_size * 100, 1) if basket_size else 0.0,
    }
    if n50:
        out["breadth_50_pct"] = round(above50 / n50 * 100, 1)
    if n200:
        out["breadth_200_pct"] = round(above200 / n200 * 100, 1)
    return out


def _snapshot_near(
    estimates_hist: dict[str, dict], target: date, tolerance: int = 10
) -> dict | None:
    """estimates履歴から target に最も近い日のスナップショットを返す"""
    best = None
    best_gap = tolerance + 1
    for d_str, snap in estimates_hist.items():
        try:
            gap = abs((date.fromisoformat(d_str) - target).days)
        except ValueError:
            continue
        if gap < best_gap:
            best_gap = gap
            best = snap
    return best


def compute_revision_metrics(
    estimates_hist: dict[str, dict], today: date
) -> dict[str, float]:
    """30日前スナップショット比のFY1 EPS上方/下方修正社数"""
    today_snap = _snapshot_near(estimates_hist, today, tolerance=3)
    base_snap = _snapshot_near(estimates_hist, today - timedelta(days=30), tolerance=10)
    if not today_snap or not base_snap or base_snap is today_snap:
        return {}
    up = down = total = 0
    for ticker in TIER1_ESTIMATES:
        cur = today_snap.get(ticker)
        prev = base_snap.get(ticker)
        if cur is None or prev is None or prev == 0:
            continue
        total += 1
        chg = (cur - prev) / abs(prev)
        if chg > REV_FLAT_THRESHOLD:
            up += 1
        elif chg < -REV_FLAT_THRESHOLD:
            down += 1
    if total == 0:
        return {}
    return {"rev_up_n": up, "rev_down_n": down, "rev_total_n": total}


def _price_return(series: list[tuple[str, float]], days: int) -> float | None:
    """およそdays日前と比較したリターン (%)"""
    if not series:
        return None
    last_d, last_c = series[-1]
    target = date.fromisoformat(last_d) - timedelta(days=days)
    best = None
    best_gap = 15
    for d_str, c in series:
        gap = abs((date.fromisoformat(d_str) - target).days)
        if gap < best_gap:
            best_gap = gap
            best = c
    if best is None or best == 0:
        return None
    return (last_c - best) / best * 100


def compute_multiple_expansion(
    prices: dict[str, list[tuple[str, float]]],
    estimates_hist: dict[str, dict],
    today: date,
) -> dict[str, float]:
    """Tier1各社の (90日株価リターン% − 90日FY1 EPS修正%) の中央値。

    EPSスナップショットが90日分ない間は px_ret90_med のみ返す。
    """
    today_snap = _snapshot_near(estimates_hist, today, tolerance=3)
    base_snap = _snapshot_near(estimates_hist, today - timedelta(days=90), tolerance=15)

    px_returns: list[float] = []
    me_values: list[float] = []
    for ticker in TIER1_ESTIMATES:
        ret = _price_return(prices.get(ticker, []), 90)
        if ret is None:
            continue
        px_returns.append(ret)
        if today_snap and base_snap and base_snap is not today_snap:
            cur = today_snap.get(ticker)
            prev = base_snap.get(ticker)
            if cur is not None and prev not in (None, 0):
                eps_chg = (cur - prev) / abs(prev) * 100
                me_values.append(ret - eps_chg)

    out: dict[str, float] = {}
    if px_returns:
        out["px_ret90_med"] = round(median(px_returns), 1)
    if len(me_values) >= 6:  # Tier1の半数以上でMEを計算できた時のみ
        out["me_90d_pt"] = round(median(me_values), 1)
    return out


def collect_market_metrics(
    prices: dict[str, list[tuple[str, float]]],
    estimates_hist: dict[str, dict],
    basket_size: int,
    today: date,
) -> dict[str, float]:
    metrics: dict[str, float] = {}
    if prices:
        metrics.update(compute_breadth(prices, basket_size))
        metrics.update(compute_multiple_expansion(prices, estimates_hist, today))
    metrics.update(compute_revision_metrics(estimates_hist, today))
    return metrics
