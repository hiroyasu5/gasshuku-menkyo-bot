"""AI Bubble Dashboard - シグナル判定ロジック。

12指標それぞれを Level (🟢🟡🟠🔴) に落とし、5グループの複合判定を行う。
閾値はユーザー定義の警戒ラインに基づく:

- GPU価格(最新世代): 3か月変化 -10%🟢 / -20%🟡境界 / -30%🔴
- HY OAS: 3か月で +50bp🟢 / +100bp🟡 / +200bp🟠 / それ以上🔴
- CRWV債券: 利回り <11%🟢 / 11-13%🟡 / 13-16%🟠 / 16%+🔴
- 四半期指標: QoQ増加🟢 / 横ばい🟡 / 減少🟠 / キャンセル発生🔴
- 複合: 🟠以上のグループが 1=ノイズ / 2=警戒 / 3+=AIサイクル変調
        需要側3グループ以上 + credit悪化 = EXIT検討シグナル
"""
from __future__ import annotations

import logging
from datetime import date

from . import manual as m
from . import storage
from .models import (
    GROUP_COMPUTE,
    GROUP_CREDIT,
    GROUP_DATACENTER,
    GROUP_DEMAND,
    GROUP_LABEL_JA,
    GROUP_ORDER,
    GROUP_POWER,
    CompositeResult,
    IndicatorResult,
    Level,
    LEVEL_RANK,
    worst_level,
)

logger = logging.getLogger(__name__)

# QoQ変化率の閾値 (増加/横ばい/減少)
QOQ_UP = 0.02      # +2%以上で増加
QOQ_DOWN = -0.02   # -2%以下で減少


def _hint_level(entry: dict | None) -> Level:
    if not entry:
        return Level.UNKNOWN
    hint = str(entry.get("level_hint") or "").lower()
    try:
        return Level(hint)
    except ValueError:
        return Level.UNKNOWN


def _qoq_level(
    latest: dict | None,
    prev: dict | None,
    field: str,
    *,
    red_drop: float = -0.15,
) -> tuple[Level, str]:
    """四半期系列の共通判定。(level, 説明) を返す"""
    if not latest:
        return Level.UNKNOWN, "データ未入力"
    if latest.get("canceled"):
        return Level.RED, "契約キャンセル/解約が発生"

    cur = m.as_float(latest.get(field))
    prv = m.as_float(prev.get(field)) if prev else None
    if cur is None:
        return _hint_level(latest), "数値未入力 (level_hint使用)"
    if prv is None or prv == 0:
        hint = _hint_level(latest)
        if hint is not Level.UNKNOWN:
            return hint, "前四半期データ待ち (level_hint使用)"
        return Level.UNKNOWN, "前四半期データ待ち"

    chg = (cur - prv) / abs(prv)
    pct = f"{chg:+.1%}"
    if chg <= red_drop:
        return Level.RED, f"QoQ {pct} (大幅減少)"
    if chg <= QOQ_DOWN:
        return Level.ORANGE, f"QoQ {pct} (減少)"
    if chg < QOQ_UP:
        return Level.YELLOW, f"QoQ {pct} (横ばい)"
    return Level.GREEN, f"QoQ {pct} (増加)"


# ---------------------------------------------------------------
# Compute価格
# ---------------------------------------------------------------

def eval_gpu_price(history: dict, manual: dict) -> IndicatorResult:
    """①③ 最新世代GPU (B200/B300優先、なければH100) レンタル価格の3か月変化"""
    fallback = m.gpu_fallback(manual)
    metric, label = None, ""
    for cand, lb in [
        ("sd_b300_rental", "B300"),
        ("sd_b200_rental", "B200"),
        ("sd_h200_rental", "H200"),
        ("sd_h100_rental", "H100"),
    ]:
        if storage.get_series(history, cand):
            metric, label = cand, lb
            break

    if metric is None:
        # スクレイピング実績なし → 手動フォールバック値を表示のみ
        for cand, lb in [("sd_b200_rental", "B200"), ("sd_h100_rental", "H100")]:
            v = m.as_float(fallback.get(cand))
            if v is not None:
                return IndicatorResult(
                    key="gpu_price", name=f"GPUレンタル価格 ({lb})",
                    group=GROUP_COMPUTE, level=Level.GREEN,
                    value_text=f"${v:.2f}/GPU-h",
                    detail="自動取得未成功のため手動値。時系列が貯まるまで変化率は未計算",
                    as_of=str(fallback.get("as_of", "")), source="手動入力 (Silicon Data)",
                )
        return IndicatorResult(
            key="gpu_price", name="GPUレンタル価格", group=GROUP_COMPUTE,
            level=Level.UNKNOWN, value_text="-", detail="データなし",
            source="Silicon Data",
        )

    latest = storage.latest_value(history, metric)
    assert latest is not None
    date_str, cur = latest
    base = storage.value_near_days_ago(history, metric, 90, tolerance=30)
    series = storage.get_series(history, metric)
    oldest_date = series[0][0]

    if base is None or base[0] == date_str:
        # 90日前の値がまだない → 最古値と比較 (30日以上の蓄積があれば参考判定)
        span_days = (
            date.fromisoformat(date_str) - date.fromisoformat(oldest_date)
        ).days
        if span_days < 30:
            return IndicatorResult(
                key="gpu_price", name=f"GPUレンタル価格 ({label})",
                group=GROUP_COMPUTE, level=Level.GREEN,
                value_text=f"${cur:.2f}/GPU-h",
                detail=f"時系列蓄積中 ({span_days}日分)。3か月変化は90日蓄積後に判定",
                as_of=date_str, source="Silicon Data (自動)",
            )
        base = series[0]

    chg = (cur - base[1]) / base[1] if base[1] else 0.0
    pct = f"{chg:+.1%}"
    if chg <= -0.30:
        level, note = Level.RED, "最新世代まで価格崩壊の兆候"
    elif chg <= -0.20:
        level, note = Level.ORANGE, "警戒ライン超え"
    elif chg <= -0.10:
        level, note = Level.YELLOW, "下落進行"
    else:
        level, note = Level.GREEN, "正常範囲"
    return IndicatorResult(
        key="gpu_price", name=f"GPUレンタル価格 ({label})",
        group=GROUP_COMPUTE, level=level,
        value_text=f"${cur:.2f}/GPU-h",
        detail=f"3か月変化 {pct} ({base[0]}: ${base[1]:.2f} 比) - {note}",
        as_of=date_str, source="Silicon Data (自動)",
    )


def eval_spot_ratio(history: dict, manual: dict) -> IndicatorResult:
    """① CoreWeave B200 spot/on-demand比率 (低下=GPU余剰シグナル)"""
    od = storage.latest_value(history, "cw_b200_od_node")
    spot = storage.latest_value(history, "cw_b200_spot_node")
    source = "CoreWeave料金ページ (自動)"
    as_of = od[0] if od else ""

    if od is None or spot is None:
        fb = m.gpu_fallback(manual)
        od_v = m.as_float(fb.get("cw_b200_od_node"))
        spot_v = m.as_float(fb.get("cw_b200_spot_node"))
        if od_v is None or spot_v is None:
            return IndicatorResult(
                key="spot_ratio", name="Spot/On-demand比率 (CW B200)",
                group=GROUP_COMPUTE, level=Level.UNKNOWN, value_text="-",
                detail="データなし", source=source,
            )
        source = "手動入力 (CoreWeave)"
        as_of = str(fb.get("as_of", ""))
    else:
        od_v, spot_v = od[1], spot[1]

    ratio = spot_v / od_v if od_v else 0.0
    if ratio >= 0.40:
        level, note = Level.GREEN, "需要は堅調"
    elif ratio >= 0.25:
        level, note = Level.YELLOW, "spot価格が緩みつつある"
    elif ratio >= 0.15:
        level, note = Level.ORANGE, "GPU余剰の可能性"
    else:
        level, note = Level.RED, "spot価格崩壊 = 深刻な余剰"
    return IndicatorResult(
        key="spot_ratio", name="Spot/On-demand比率 (CW B200)",
        group=GROUP_COMPUTE, level=level,
        value_text=f"{ratio:.2f} (spot ${spot_v:.2f} / OD ${od_v:.2f})",
        detail=note, as_of=as_of, source=source,
    )


# ---------------------------------------------------------------
# 需要 (backlog / commitments / hyperscaler)
# ---------------------------------------------------------------

def eval_crwv_backlog(manual: dict) -> IndicatorResult:
    latest, prev = m.latest_and_previous(manual, "crwv")
    level, detail = _qoq_level(latest, prev, "backlog_busd")
    value = "-"
    if latest and m.as_float(latest.get("backlog_busd")) is not None:
        value = f"${m.as_float(latest['backlog_busd']):.1f}B"
    return IndicatorResult(
        key="crwv_backlog", name="CRWV Revenue Backlog", group=GROUP_DEMAND,
        level=level, value_text=value, detail=detail,
        as_of=str(latest.get("quarter", "")) if latest else "",
        source="CRWV決算 (手動)",
    )


def eval_nbis_commitments(manual: dict) -> IndicatorResult:
    latest, prev = m.latest_and_previous(manual, "nbis")
    level, detail = _qoq_level(latest, prev, "commitments_busd")
    value = "-"
    if latest and m.as_float(latest.get("commitments_busd")) is not None:
        value = f"${m.as_float(latest['commitments_busd']):.0f}B+"
    return IndicatorResult(
        key="nbis_commitments", name="NBIS Customer Commitments", group=GROUP_DEMAND,
        level=level, value_text=value, detail=detail,
        as_of=str(latest.get("quarter", "")) if latest else "",
        source="NBIS決算 (手動)",
    )


def eval_hyperscalers(manual: dict) -> IndicatorResult:
    latest, _ = m.latest_and_previous(manual, "hyperscalers")
    if not latest:
        return IndicatorResult(
            key="hyperscalers", name="Hyperscaler CapEx/Cloud", group=GROUP_DEMAND,
            level=Level.UNKNOWN, value_text="-", detail="データ未入力",
            source="決算 (手動)",
        )
    capex = str(latest.get("capex_trend", "")).lower()
    cloud = str(latest.get("cloud_growth_trend", "")).lower()
    fcf_bad = bool(latest.get("fcf_deteriorating"))

    if capex == "up" and cloud == "up":
        level = Level.GREEN
        note = "CapEx↑ + Cloud成長↑ (需要が投資を正当化)"
    elif capex == "up" and cloud == "flat":
        level = Level.YELLOW
        note = "CapEx↑ だがCloud成長は横ばい"
    elif cloud == "down" and fcf_bad:
        level = Level.RED
        note = "CapEx継続 + Cloud減速 + FCF悪化"
    elif cloud == "down" or capex == "down":
        level = Level.ORANGE
        note = "Cloud減速またはCapEx削減の動き"
    else:
        level = Level.YELLOW
        note = "トレンド不明瞭"
    if fcf_bad and level is Level.GREEN:
        note += "。ただしFCF悪化 (cash burn) は進行中"
    return IndicatorResult(
        key="hyperscalers", name="Hyperscaler CapEx/Cloud", group=GROUP_DEMAND,
        level=level,
        value_text=f"CapEx:{capex or '?'} / Cloud:{cloud or '?'}",
        detail=note + (f" - {latest.get('note')}" if latest.get("note") else ""),
        as_of=str(latest.get("quarter", "")),
        source="GOOGL/MSFT/AMZN/META/ORCL決算 (手動)",
    )


# ---------------------------------------------------------------
# データセンター需給
# ---------------------------------------------------------------

def eval_apld(manual: dict) -> IndicatorResult:
    latest, prev = m.latest_and_previous(manual, "apld")
    level, detail = _qoq_level(latest, prev, "contracted_mw", red_drop=-0.10)
    value = "-"
    if latest:
        cmw = m.as_float(latest.get("contracted_mw"))
        lmw = m.as_float(latest.get("live_mw"))
        if cmw is not None:
            value = f"{cmw:,.0f}MW契約"
            if lmw is not None:
                value += f" / {lmw:,.0f}MW稼働"
    return IndicatorResult(
        key="apld_mw", name="APLD Contracted/Live MW", group=GROUP_DATACENTER,
        level=level, value_text=value, detail=detail,
        as_of=str(latest.get("quarter", "")) if latest else "",
        source="APLD決算 (手動)",
    )


def eval_dlr(manual: dict) -> IndicatorResult:
    latest, prev = m.latest_and_previous(manual, "dlr")
    if not latest:
        return IndicatorResult(
            key="dlr_rent", name="DLR Bookings/更新賃料", group=GROUP_DATACENTER,
            level=Level.UNKNOWN, value_text="-", detail="データ未入力",
            source="DLR決算 (手動)",
        )
    rent = m.as_float(latest.get("renewal_rent_cash_pct"))
    bookings = m.as_float(latest.get("bookings_annualized_musd"))
    if rent is None:
        level, detail = _hint_level(latest), "更新賃料未入力 (level_hint使用)"
    elif rent >= 3:
        level, detail = Level.GREEN, f"更新賃料 +{rent:.1f}% (供給不足〜均衡)"
    elif rent >= 0:
        level, detail = Level.YELLOW, f"更新賃料 +{rent:.1f}% (均衡へ軟化)"
    elif rent > -5:
        level, detail = Level.ORANGE, f"更新賃料 {rent:.1f}% (過剰供給の兆候)"
    else:
        level, detail = Level.RED, f"更新賃料 {rent:.1f}% (過剰供給)"
    # bookingsの急減も加味
    if bookings is not None and prev is not None:
        pb = m.as_float(prev.get("bookings_annualized_musd"))
        if pb:
            chg = (bookings - pb) / pb
            detail += f" / bookings QoQ {chg:+.0%}"
            if chg <= -0.30 and LEVEL_RANK[level] < LEVEL_RANK[Level.ORANGE]:
                level = Level.ORANGE
    value = f"${bookings:,.0f}M bookings" if bookings is not None else "-"
    if rent is not None:
        value += f" / 賃料{rent:+.0f}%"
    return IndicatorResult(
        key="dlr_rent", name="DLR Bookings/更新賃料", group=GROUP_DATACENTER,
        level=level, value_text=value, detail=detail,
        as_of=str(latest.get("quarter", "")), source="DLR決算 (手動)",
    )


# ---------------------------------------------------------------
# 電力
# ---------------------------------------------------------------

def eval_aep(manual: dict) -> IndicatorResult:
    latest, prev = m.latest_and_previous(manual, "aep")
    level, detail = _qoq_level(latest, prev, "contracted_load_gw", red_drop=-0.05)
    value = "-"
    if latest and m.as_float(latest.get("contracted_load_gw")) is not None:
        value = f"{m.as_float(latest['contracted_load_gw']):.0f}GW (2030年まで)"
    return IndicatorResult(
        key="aep_load", name="AEP Contracted Large-Load", group=GROUP_POWER,
        level=level, value_text=value, detail=detail,
        as_of=str(latest.get("quarter", "")) if latest else "",
        source="AEP決算 (手動)",
    )


def eval_power_forecast(manual: dict) -> IndicatorResult:
    entries = m.series_entries(manual, "power_forecast")
    if not entries:
        return IndicatorResult(
            key="power_forecast", name="PJM需要予測 (Dominion)", group=GROUP_POWER,
            level=Level.UNKNOWN, value_text="-", detail="データ未入力",
            source="PJM Load Forecast (手動)",
        )
    latest = entries[-1]
    prev = entries[-2] if len(entries) >= 2 else None
    cur = m.as_float(latest.get("pjm_dominion_2030_peak_gw"))
    value = f"2030予測 {cur:.0f}GW" if cur is not None else "-"
    if cur is None:
        return IndicatorResult(
            key="power_forecast", name="PJM需要予測 (Dominion)", group=GROUP_POWER,
            level=Level.UNKNOWN, value_text="-", detail="数値未入力",
            as_of=str(latest.get("year", "")), source="PJM Load Forecast (手動)",
        )
    if prev is None or m.as_float(prev.get("pjm_dominion_2030_peak_gw")) is None:
        return IndicatorResult(
            key="power_forecast", name="PJM需要予測 (Dominion)", group=GROUP_POWER,
            level=Level.GREEN, value_text=value,
            detail="年次改定待ち (前年比較は次回forecastで判定)",
            as_of=str(latest.get("year", "")), source="PJM Load Forecast (手動)",
        )
    prv = m.as_float(prev.get("pjm_dominion_2030_peak_gw"))
    chg = (cur - prv) / prv
    pct = f"{chg:+.1%}"
    if chg <= -0.15:
        level, note = Level.RED, "forecast大幅下方修正"
    elif chg <= -0.05:
        level, note = Level.ORANGE, "forecast下方修正"
    elif chg < 0.02:
        level, note = Level.YELLOW, "横ばい"
    else:
        level, note = Level.GREEN, "上方修正継続"
    return IndicatorResult(
        key="power_forecast", name="PJM需要予測 (Dominion)", group=GROUP_POWER,
        level=level, value_text=value, detail=f"前年forecast比 {pct} - {note}",
        as_of=str(latest.get("year", "")), source="PJM Load Forecast (手動)",
    )


# ---------------------------------------------------------------
# 信用
# ---------------------------------------------------------------

def eval_hy_oas(history: dict) -> IndicatorResult:
    latest = storage.latest_value(history, "hy_oas_bps")
    if latest is None:
        return IndicatorResult(
            key="hy_oas", name="HY OAS", group=GROUP_CREDIT,
            level=Level.UNKNOWN, value_text="-", detail="FRED未取得",
            source="FRED BAMLH0A0HYM2 (自動)",
        )
    date_str, cur = latest
    base = storage.value_near_days_ago(history, "hy_oas_bps", 90, tolerance=30)
    if base is None:
        return IndicatorResult(
            key="hy_oas", name="HY OAS", group=GROUP_CREDIT,
            level=Level.GREEN, value_text=f"{cur:.0f}bp",
            detail="時系列蓄積中 (3か月変化は蓄積後に判定)",
            as_of=date_str, source="FRED BAMLH0A0HYM2 (自動)",
        )
    delta = cur - base[1]
    if delta >= 200:
        level, note = Level.RED, "信用ストレス (状況次第で信用危機を疑う)"
    elif delta >= 100:
        level, note = Level.ORANGE, "スプレッド急拡大・警戒"
    elif delta >= 50:
        level, note = Level.YELLOW, "拡大進行"
    else:
        level, note = Level.GREEN, "安定"
    single_b = storage.latest_value(history, "single_b_oas_bps")
    ig = storage.latest_value(history, "ig_oas_bps")
    extra = []
    if single_b:
        extra.append(f"Single-B {single_b[1]:.0f}bp")
    if ig:
        extra.append(f"IG {ig[1]:.0f}bp")
    detail = f"3か月変化 {delta:+.0f}bp - {note}"
    if extra:
        detail += " / " + " ・ ".join(extra)
    return IndicatorResult(
        key="hy_oas", name="HY OAS", group=GROUP_CREDIT,
        level=level, value_text=f"{cur:.0f}bp", detail=detail,
        as_of=date_str, source="FRED BAMLH0A0HYM2 (自動)",
    )


def eval_crwv_bond(manual: dict) -> IndicatorResult:
    bond = m.crwv_bond(manual)
    y = m.as_float(bond.get("yield_pct"))
    if y is None:
        return IndicatorResult(
            key="crwv_bond", name="CRWV債券利回り (2032)", group=GROUP_CREDIT,
            level=Level.UNKNOWN, value_text="-", detail="利回り未入力",
            source="FINRA TRACE等 (手動)",
        )
    if y < 11:
        level, note = Level.GREEN, "AI credit安定 (発行時9.625%比で正常圏)"
    elif y < 13:
        level, note = Level.YELLOW, "利回り上昇・要観察"
    elif y < 16:
        level, note = Level.ORANGE, "AI creditストレスの警報"
    else:
        level, note = Level.RED, "株価より重要な警報レベル"
    if y < 9:
        note = "市場はCRWVを安全側に再評価 (利回り低下)"
    return IndicatorResult(
        key="crwv_bond", name="CRWV債券利回り (2032)", group=GROUP_CREDIT,
        level=level, value_text=f"{y:.2f}%", detail=note,
        as_of=str(bond.get("as_of", "")), source="FINRA TRACE等 (手動)",
    )


def eval_financing(manual: dict) -> IndicatorResult:
    entries = m.financing_entries(manual)
    if not entries:
        return IndicatorResult(
            key="financing", name="AI企業の新規借入条件", group=GROUP_CREDIT,
            level=Level.UNKNOWN, value_text="-", detail="データ未入力",
            source="起債・借入発表 (手動)",
        )
    if any(e.get("failed") for e in entries[-4:]):
        failed = [e for e in entries[-4:] if e.get("failed")][-1]
        return IndicatorResult(
            key="financing", name="AI企業の新規借入条件", group=GROUP_CREDIT,
            level=Level.RED,
            value_text=f"{failed.get('issuer')} 発行失敗",
            detail="債券発行失敗/撤回が発生 = credit market閉鎖の兆候",
            as_of=str(failed.get("date", "")), source="起債・借入発表 (手動)",
        )
    latest = entries[-1]
    coupon = m.as_float(latest.get("coupon_pct"))
    value = f"{latest.get('issuer')} {latest.get('instrument')}"
    if coupon is not None:
        value += f" @{coupon:.2f}%"
    # 同一issuerの直近の比較対象を探す
    prev_coupon = None
    for e in reversed(entries[:-1]):
        c = m.as_float(e.get("coupon_pct"))
        if c is not None:
            prev_coupon = c
            break
    if coupon is None or prev_coupon is None:
        level, note = Level.GREEN, "市場は開いている (巨額調達が成立)"
    else:
        d = coupon - prev_coupon
        if d <= 0.25:
            level, note = Level.GREEN, f"調達条件は安定〜改善 (前回比 {d:+.2f}pt)"
        elif d <= 1.0:
            level, note = Level.YELLOW, f"調達コスト上昇 (前回比 {d:+.2f}pt)"
        elif d <= 3.0:
            level, note = Level.ORANGE, f"調達コスト急上昇 (前回比 {d:+.2f}pt)"
        else:
            level, note = Level.RED, f"調達条件が大幅悪化 (前回比 {d:+.2f}pt)"
    return IndicatorResult(
        key="financing", name="AI企業の新規借入条件", group=GROUP_CREDIT,
        level=level, value_text=value, detail=note,
        as_of=str(latest.get("date", "")), source="起債・借入発表 (手動)",
    )


# ---------------------------------------------------------------
# 全体評価
# ---------------------------------------------------------------

def evaluate_all(history: dict, manual: dict) -> tuple[list[IndicatorResult], CompositeResult]:
    results = [
        eval_hyperscalers(manual),
        eval_crwv_backlog(manual),
        eval_nbis_commitments(manual),
        eval_gpu_price(history, manual),
        eval_spot_ratio(history, manual),
        eval_apld(manual),
        eval_dlr(manual),
        eval_aep(manual),
        eval_power_forecast(manual),
        eval_hy_oas(history),
        eval_crwv_bond(manual),
        eval_financing(manual),
    ]
    composite = compute_composite(results)
    return results, composite


def compute_composite(results: list[IndicatorResult]) -> CompositeResult:
    group_levels: dict[str, Level] = {}
    for g in GROUP_ORDER:
        group_levels[g] = worst_level([r.level for r in results if r.group == g])

    alert_groups = [
        g for g, lv in group_levels.items() if LEVEL_RANK[lv] >= LEVEL_RANK[Level.ORANGE]
    ]
    n = len(alert_groups)

    demand_side = [GROUP_DEMAND, GROUP_COMPUTE, GROUP_DATACENTER, GROUP_POWER]
    demand_alerts = [g for g in alert_groups if g in demand_side]
    credit_alert = GROUP_CREDIT in alert_groups
    exit_signal = len(demand_alerts) >= 3 and credit_alert

    if exit_signal:
        level = Level.RED
        summary = (
            "🚨 EXIT検討シグナル: 需要側"
            f"{len(demand_alerts)}グループ + 信用市場が同時悪化"
        )
    elif n >= 3:
        level = Level.RED
        summary = f"AIサイクル変調の可能性: {n}グループが同時悪化"
    elif n == 2:
        level = Level.ORANGE
        summary = "警戒: 2グループが同時悪化"
    elif n == 1:
        level = Level.YELLOW
        summary = (
            "1グループのみ悪化 (企業固有要因/ノイズの可能性): "
            + GROUP_LABEL_JA[alert_groups[0]]
        )
    else:
        yellow_n = sum(
            1 for lv in group_levels.values() if lv is Level.YELLOW
        )
        level = Level.GREEN
        summary = "全グループ正常" + (f" (注意🟡: {yellow_n}グループ)" if yellow_n else "")

    return CompositeResult(
        level=level,
        alert_groups=alert_groups,
        group_levels=group_levels,
        exit_signal=exit_signal,
        summary=summary,
    )
