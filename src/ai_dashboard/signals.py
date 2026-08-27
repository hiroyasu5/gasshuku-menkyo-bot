"""AI Bubble Dashboard - シグナル判定ロジック (v2)。

15指標を Level (🟢🟡🟠🔴⚪) + 信頼度 (confirmed/provisional/none) に落とし、
6グループの複合判定を行う。

v2の原則:
- 🟢 = データで正常を「確認」した時のみ。データ不足は⚪(UNKNOWN)、
  level_hint・手動フォールバック・staleデータによる判定は「暫定」扱い
- Refinancingの比較は 同issuer + 同category (担保/シニオリティ区分) のみ。
  異なる会社・担保の債券のクーポンは比較しない
- CRWVは利回りの絶対値ではなく 対米国債スプレッド (+ HY OASとの差) で判定
- 複合判定に Data confidence (confirmed指標の割合) を出す

閾値 (ユーザー定義の警戒ライン):
- GPU価格(最新世代): 3か月変化 -10%🟢 / -20%🟡境界 / -30%🔴
- HY OAS: 3か月で +50bp🟢 / +100bp🟡 / +200bp🟠 / それ以上🔴
- CRWVスプレッド: <700bp🟢 / <900bp🟡 / <1200bp🟠 / 1200bp+🔴
- 四半期指標: QoQ増加🟢 / 横ばい🟡 / 減少🟠 / キャンセル発生🔴
- Liquidity coverage: ≥2x🟢 / ≥1x🟡 / ≥0.7x🟠 / <0.7x🔴
- 複合: 🟠以上のグループが 1=ノイズ / 2=警戒 / 3+=AIサイクル変調
        需要側3グループ以上 + credit悪化 = EXIT検討シグナル
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from . import manual as m
from . import storage
from .config import BOND_STALE_DAYS, DAILY_STALE_DAYS
from .models import (
    ALERT_GROUPS,
    CONF_CONFIRMED,
    CONF_NONE,
    CONF_PROVISIONAL,
    DEMAND_SIDE_GROUPS,
    GROUP_COMPUTE,
    GROUP_CREDIT,
    GROUP_DATACENTER,
    GROUP_DEMAND,
    GROUP_LABEL_JA,
    GROUP_MARKET,
    GROUP_ORDER,
    GROUP_POWER,
    GROUP_UTILIZATION,
    CompositeResult,
    IndicatorResult,
    Level,
    LEVEL_RANK,
    worst_level,
)

logger = logging.getLogger(__name__)

JST = timezone(timedelta(hours=9))

# QoQ変化率の閾値 (増加/横ばい/減少)
QOQ_UP = 0.02      # +2%以上で増加
QOQ_DOWN = -0.02   # -2%以下で減少


def _today() -> date:
    return datetime.now(JST).date()


def _days_since(date_str: str) -> int | None:
    """"YYYY-MM-DD" からの経過日数。パースできなければ None"""
    try:
        return (_today() - date.fromisoformat(str(date_str))).days
    except (ValueError, TypeError):
        return None


def _is_stale_daily(date_str: str) -> bool:
    d = _days_since(date_str)
    return d is not None and d > DAILY_STALE_DAYS


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
) -> tuple[Level, str, str]:
    """四半期系列の共通判定。(level, 説明, confidence) を返す"""
    if not latest:
        return Level.UNKNOWN, "データ未入力", CONF_NONE
    if latest.get("canceled"):
        return Level.RED, "契約キャンセル/解約が発生", CONF_CONFIRMED

    cur = m.as_float(latest.get(field))
    prv = m.as_float(prev.get(field)) if prev else None
    if cur is None:
        hint = _hint_level(latest)
        if hint is not Level.UNKNOWN:
            return hint, "数値未入力 (level_hintによる暫定)", CONF_PROVISIONAL
        return Level.UNKNOWN, "数値未入力", CONF_NONE
    if prv is None or prv == 0:
        hint = _hint_level(latest)
        if hint is not Level.UNKNOWN:
            return hint, "前四半期データ待ち (level_hintによる暫定)", CONF_PROVISIONAL
        return Level.UNKNOWN, "前四半期データ待ち (QoQ比較不能)", CONF_NONE

    chg = (cur - prv) / abs(prv)
    pct = f"{chg:+.1%}"
    if chg <= red_drop:
        return Level.RED, f"QoQ {pct} (大幅減少)", CONF_CONFIRMED
    if chg <= QOQ_DOWN:
        return Level.ORANGE, f"QoQ {pct} (減少)", CONF_CONFIRMED
    if chg < QOQ_UP:
        return Level.YELLOW, f"QoQ {pct} (横ばい)", CONF_CONFIRMED
    return Level.GREEN, f"QoQ {pct} (増加)", CONF_CONFIRMED


# ---------------------------------------------------------------
# Compute価格
# ---------------------------------------------------------------

def eval_gpu_price(history: dict, manual: dict) -> IndicatorResult:
    """最新世代GPU (B200/B300優先) レンタル価格の3か月変化。

    時系列が90日分貯まるまでは⚪ (現在値は表示するが正常判定はしない)。
    """
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
        for cand, lb in [("sd_b200_rental", "B200"), ("sd_h100_rental", "H100")]:
            v = m.as_float(fallback.get(cand))
            if v is not None:
                return IndicatorResult(
                    key="gpu_price", name=f"GPUレンタル価格 ({lb})",
                    group=GROUP_COMPUTE, level=Level.UNKNOWN,
                    value_text=f"${v:.2f}/GPU-h",
                    detail="自動取得未成功 (手動値を表示中)。時系列がないため判定不能",
                    as_of=str(fallback.get("as_of", "")),
                    source="手動入力 (Silicon Data)", confidence=CONF_NONE,
                )
        return IndicatorResult(
            key="gpu_price", name="GPUレンタル価格", group=GROUP_COMPUTE,
            level=Level.UNKNOWN, value_text="-", detail="データなし",
            source="Silicon Data", confidence=CONF_NONE,
        )

    latest = storage.latest_value(history, metric)
    assert latest is not None
    date_str, cur = latest
    stale = _is_stale_daily(date_str)
    base = storage.value_near_days_ago(history, metric, 90, tolerance=30)
    series = storage.get_series(history, metric)
    oldest_date = series[0][0]

    if base is None or base[0] == date_str:
        span_days = (
            date.fromisoformat(date_str) - date.fromisoformat(oldest_date)
        ).days
        if span_days < 30:
            return IndicatorResult(
                key="gpu_price", name=f"GPUレンタル価格 ({label})",
                group=GROUP_COMPUTE, level=Level.UNKNOWN,
                value_text=f"${cur:.2f}/GPU-h",
                detail=f"時系列蓄積中 ({span_days}日分)。3か月変化の判定には最低30日必要",
                as_of=date_str, source="Silicon Data (自動)",
                confidence=CONF_NONE, stale=stale,
            )
        base = series[0]

    chg = (cur - base[1]) / base[1] if base[1] else 0.0
    span = (date.fromisoformat(date_str) - date.fromisoformat(base[0])).days
    pct = f"{chg:+.1%}"
    if chg <= -0.30:
        level, note = Level.RED, "最新世代まで価格崩壊の兆候"
    elif chg <= -0.20:
        level, note = Level.ORANGE, "警戒ライン超え"
    elif chg <= -0.10:
        level, note = Level.YELLOW, "下落進行"
    else:
        level, note = Level.GREEN, "正常範囲"
    conf = CONF_CONFIRMED if span >= 60 and not stale else CONF_PROVISIONAL
    period = "3か月変化" if span >= 60 else f"{span}日変化 (蓄積中・暫定)"
    return IndicatorResult(
        key="gpu_price", name=f"GPUレンタル価格 ({label})",
        group=GROUP_COMPUTE, level=level,
        value_text=f"${cur:.2f}/GPU-h",
        detail=f"{period} {pct} ({base[0]}: ${base[1]:.2f} 比) - {note}",
        as_of=date_str, source="Silicon Data (自動)",
        confidence=conf, stale=stale,
    )


def eval_spot_ratio(history: dict, manual: dict) -> IndicatorResult:
    """CoreWeave B200 spot/on-demand比率。絶対値より30日トレンドを重視"""
    od = storage.latest_value(history, "cw_b200_od_node")
    spot = storage.latest_value(history, "cw_b200_spot_node")
    source = "CoreWeave料金ページ (自動)"
    stale = False
    as_of = ""

    if od is None or spot is None:
        fb = m.gpu_fallback(manual)
        od_v = m.as_float(fb.get("cw_b200_od_node"))
        spot_v = m.as_float(fb.get("cw_b200_spot_node"))
        if od_v is None or spot_v is None:
            return IndicatorResult(
                key="spot_ratio", name="Spot/On-demand比率 (CW B200)",
                group=GROUP_COMPUTE, level=Level.UNKNOWN, value_text="-",
                detail="データなし", source=source, confidence=CONF_NONE,
            )
        ratio = spot_v / od_v if od_v else 0.0
        return IndicatorResult(
            key="spot_ratio", name="Spot/On-demand比率 (CW B200)",
            group=GROUP_COMPUTE, level=Level.UNKNOWN,
            value_text=f"{ratio:.2f} (手動値)",
            detail="自動取得未成功。時系列がないため判定不能",
            as_of=str(fb.get("as_of", "")), source="手動入力 (CoreWeave)",
            confidence=CONF_NONE,
        )

    as_of = od[0]
    stale = _is_stale_daily(as_of)
    od_v, spot_v = od[1], spot[1]
    ratio = spot_v / od_v if od_v else 0.0

    # 30日前の比率と比較 (spotは元々ディスカウント商品なので絶対値は参考)
    trend_note = ""
    trend_level: Level | None = None
    od30 = storage.value_near_days_ago(history, "cw_b200_od_node", 30, tolerance=10)
    spot30 = storage.value_near_days_ago(history, "cw_b200_spot_node", 30, tolerance=10)
    if od30 and spot30 and od30[1] and od30[0] != as_of:
        ratio30 = spot30[1] / od30[1]
        d = ratio - ratio30
        trend_note = f" / 30日変化 {d:+.2f} ({ratio30:.2f}→{ratio:.2f})"
        if d <= -0.20:
            trend_level = Level.RED
        elif d <= -0.10:
            trend_level = Level.ORANGE
        elif d <= -0.05:
            trend_level = Level.YELLOW

    # 絶対値バンド (参考・緩め)
    if ratio >= 0.30:
        abs_level, note = Level.GREEN, "需要は堅調"
    elif ratio >= 0.20:
        abs_level, note = Level.YELLOW, "spot価格が緩みつつある"
    elif ratio >= 0.12:
        abs_level, note = Level.ORANGE, "GPU余剰の可能性"
    else:
        abs_level, note = Level.RED, "spot価格崩壊 = 深刻な余剰"

    level = trend_level if trend_level and LEVEL_RANK[trend_level] > LEVEL_RANK[abs_level] else abs_level
    conf = CONF_PROVISIONAL if stale else CONF_CONFIRMED
    return IndicatorResult(
        key="spot_ratio", name="Spot/On-demand比率 (CW B200)",
        group=GROUP_COMPUTE, level=level,
        value_text=f"{ratio:.2f} (spot ${spot_v:.2f} / OD ${od_v:.2f})",
        detail=note + trend_note, as_of=as_of, source=source,
        confidence=conf, stale=stale,
    )


# ---------------------------------------------------------------
# 需要 (Hyperscaler / Backlog / RPO)
# ---------------------------------------------------------------

def eval_crwv_backlog(manual: dict) -> IndicatorResult:
    """CRWV: backlogとnew commitmentsの両方をQoQで見る"""
    latest, prev = m.latest_and_previous(manual, "crwv")
    b_level, b_detail, b_conf = _qoq_level(latest, prev, "backlog_busd")
    c_level, c_detail, c_conf = _qoq_level(latest, prev, "new_commitments_busd")

    value_parts = []
    if latest:
        b = m.as_float(latest.get("backlog_busd"))
        c = m.as_float(latest.get("new_commitments_busd"))
        r = m.as_float(latest.get("revenue_musd"))
        if b is not None:
            value_parts.append(f"Backlog ${b:.1f}B")
        if c is not None:
            value_parts.append(f"新規 ${c:.0f}B+")
        if r is not None:
            value_parts.append(f"売上 ${r/1000:.2f}B/q")
    value = " / ".join(value_parts) or "-"

    level = worst_level([b_level, c_level])
    conf_rank = {CONF_CONFIRMED: 2, CONF_PROVISIONAL: 1, CONF_NONE: 0}
    conf = min([b_conf, c_conf], key=lambda c: conf_rank[c])
    detail = f"Backlog: {b_detail} / 新規commitments: {c_detail}"
    return IndicatorResult(
        key="crwv_backlog", name="CRWV Backlog / Commitments", group=GROUP_DEMAND,
        level=level, value_text=value, detail=detail,
        as_of=str(latest.get("quarter", "")) if latest else "",
        source="CRWV決算 (手動)", confidence=conf,
    )


def eval_nbis_commitments(manual: dict) -> IndicatorResult:
    latest, prev = m.latest_and_previous(manual, "nbis")
    level, detail, conf = _qoq_level(latest, prev, "commitments_busd")
    value = "-"
    if latest and m.as_float(latest.get("commitments_busd")) is not None:
        value = f"${m.as_float(latest['commitments_busd']):.0f}B+"
    return IndicatorResult(
        key="nbis_commitments", name="NBIS Customer Commitments", group=GROUP_DEMAND,
        level=level, value_text=value, detail=detail,
        as_of=str(latest.get("quarter", "")) if latest else "",
        source="NBIS決算 (手動)", confidence=conf,
    )


def eval_orcl_rpo(manual: dict) -> IndicatorResult:
    """Oracle RPO (未来の需要のproxy)"""
    latest, prev = m.latest_and_previous(manual, "orcl")
    level, detail, conf = _qoq_level(latest, prev, "rpo_busd")
    value = "-"
    if latest and m.as_float(latest.get("rpo_busd")) is not None:
        value = f"RPO ${m.as_float(latest['rpo_busd']):.0f}B"
    return IndicatorResult(
        key="orcl_rpo", name="ORCL RPO", group=GROUP_DEMAND,
        level=level, value_text=value, detail=detail,
        as_of=str(latest.get("quarter", "")) if latest else "",
        source="ORCL決算 (手動)", confidence=conf,
    )


TREND_WORDS = {"up": "↑", "flat": "→", "down": "↓"}


def eval_hyperscalers(manual: dict) -> IndicatorResult:
    """Hyperscaler 5社を個別に評価し、breadth (何社が悪化したか) で判定する。

    新形式: companies: {MSFT: {cloud_yoy_pct, prev_cloud_yoy_pct, capex_guide,
    fcf_negative, capacity_constrained}, ...}
    旧形式 (capex_trend/cloud_growth_trend) にもフォールバックする。
    """
    latest, _ = m.latest_and_previous(manual, "hyperscalers")
    if not latest:
        return IndicatorResult(
            key="hyperscalers", name="Hyperscaler CapEx/Cloud", group=GROUP_DEMAND,
            level=Level.UNKNOWN, value_text="-", detail="データ未入力",
            source="決算 (手動)", confidence=CONF_NONE,
        )

    companies = latest.get("companies")
    if not isinstance(companies, dict) or not companies:
        return _eval_hyperscalers_legacy(latest)

    guide_down: list[str] = []
    decelerating: list[str] = []
    cloud_negative: list[str] = []
    constrained: list[str] = []
    # 指標ごとの「観測できた社数」= 分母。データがない会社を分母に入れない
    guide_n = accel_n = cloud_n = constrained_n = 0
    rows: list[list[str]] = [["社", "Cloud YoY", "加速度", "CapEx guide", "制約発言"]]
    for name in ["MSFT", "AMZN", "GOOGL", "META", "ORCL"]:
        c = companies.get(name)
        if not isinstance(c, dict):
            continue
        yoy = m.as_float(c.get("cloud_yoy_pct"))
        prev_yoy = m.as_float(c.get("prev_cloud_yoy_pct"))
        guide_raw = c.get("capex_guide")
        guide = str(guide_raw).lower() if guide_raw is not None else ""
        constrained_raw = c.get("capacity_constrained")
        accel = None
        if yoy is not None and prev_yoy is not None:
            accel = yoy - prev_yoy
            accel_n += 1
            if accel <= -5:
                decelerating.append(name)
        if yoy is not None:
            cloud_n += 1
            if yoy < 0:
                cloud_negative.append(name)
        if guide in ("up", "flat", "down"):
            guide_n += 1
            if guide == "down":
                guide_down.append(name)
        if constrained_raw is not None:
            constrained_n += 1
            if constrained_raw:
                constrained.append(name)
        rows.append([
            name,
            f"{yoy:+.0f}%" if yoy is not None else "—",
            f"{accel:+.0f}pt" if accel is not None else "—",
            TREND_WORDS.get(guide, "—"),
            ("Yes" if constrained_raw else "No") if constrained_raw is not None else "—",
        ])

    n = len(rows) - 1
    if n == 0 or (guide_n == 0 and cloud_n == 0):
        return _eval_hyperscalers_legacy(latest)

    if len(guide_down) >= 2 or cloud_negative:
        level = Level.RED
        note = f"CapEx下方修正 {len(guide_down)}社 / Cloud縮小 {len(cloud_negative)}社"
    elif len(guide_down) == 1 or len(decelerating) >= 3:
        level = Level.ORANGE
        note = f"CapEx下方修正 {len(guide_down)}社 / 減速(≥5pt) {len(decelerating)}社"
    elif len(decelerating) >= 2:
        level = Level.YELLOW
        note = f"減速(≥5pt) {len(decelerating)}社 ({'/'.join(decelerating)})"
    else:
        level = Level.GREEN
        note = "観測範囲ではCapEx維持・Cloud成長継続"
    breadth = (
        f"CapEx下方修正 {len(guide_down)}/{guide_n}観測 ・ "
        f"減速 {len(decelerating)}/{accel_n}観測 ・ "
        f"constrained {len(constrained)}/{constrained_n}観測"
    )
    # 5社中3社以上でCapEx guideとCloud YoYの両方が観測できて初めて「確認済み」
    well_observed = guide_n >= 3 and cloud_n >= 3
    if not well_observed:
        note += f" (観測 {max(guide_n, cloud_n)}/5社のみ・暫定)"
    return IndicatorResult(
        key="hyperscalers", name="Hyperscaler 5社 (breadth)", group=GROUP_DEMAND,
        level=level, value_text=breadth,
        detail=note + (f" - {latest.get('note')}" if latest.get("note") else ""),
        as_of=str(latest.get("quarter", "")),
        source="MSFT/AMZN/GOOGL/META/ORCL決算 (手動)",
        confidence=CONF_CONFIRMED if well_observed else CONF_PROVISIONAL,
        detail_rows=rows,
    )


def _eval_hyperscalers_legacy(latest: dict) -> IndicatorResult:
    """旧形式 (5社まとめ) のフォールバック。暫定扱い"""
    capex = str(latest.get("capex_trend", "")).lower()
    cloud = str(latest.get("cloud_growth_trend", "")).lower()
    fcf_bad = bool(latest.get("fcf_deteriorating"))
    if capex == "up" and cloud == "up":
        level, note = Level.GREEN, "CapEx↑ + Cloud成長↑"
    elif capex == "up" and cloud == "flat":
        level, note = Level.YELLOW, "CapEx↑ だがCloud成長は横ばい"
    elif cloud == "down" and fcf_bad:
        level, note = Level.RED, "CapEx継続 + Cloud減速 + FCF悪化"
    elif cloud == "down" or capex == "down":
        level, note = Level.ORANGE, "Cloud減速またはCapEx削減の動き"
    else:
        level, note = Level.YELLOW, "トレンド不明瞭"
    if fcf_bad and level is Level.GREEN:
        note += "。ただしFCF悪化は進行中"
    return IndicatorResult(
        key="hyperscalers", name="Hyperscaler CapEx/Cloud", group=GROUP_DEMAND,
        level=level, value_text=f"CapEx:{capex or '?'} / Cloud:{cloud or '?'}",
        detail=note + " (5社一括の暫定評価。companies形式への移行推奨)"
        + (f" - {latest.get('note')}" if latest.get("note") else ""),
        as_of=str(latest.get("quarter", "")),
        source="GOOGL/MSFT/AMZN/META/ORCL決算 (手動)",
        confidence=CONF_PROVISIONAL,
    )


# ---------------------------------------------------------------
# 稼働率 (Utilization Proxy)
# ---------------------------------------------------------------

def eval_crwv_utilization(manual: dict) -> IndicatorResult:
    """CRWV Utilization Proxy:
    A. Revenue(年換算) / Active GW
    B. Contracted / Active power倍率
    C. Revenue成長率 − Active power成長率 (判定の主軸)
    """
    latest, prev = m.latest_and_previous(manual, "crwv")
    if not latest:
        return IndicatorResult(
            key="crwv_utilization", name="CRWV Utilization Proxy",
            group=GROUP_UTILIZATION, level=Level.UNKNOWN, value_text="-",
            detail="データ未入力", source="CRWV決算 (手動)", confidence=CONF_NONE,
        )

    rev = m.as_float(latest.get("revenue_musd"))
    active = m.as_float(latest.get("active_power_gw"))
    contracted = m.as_float(latest.get("contracted_power_gw"))

    parts = []
    if rev is not None and active:
        rev_per_gw = (rev * 4 / 1000) / active  # 年換算$B / GW
        parts.append(f"${rev_per_gw:.1f}B/GW (年換算)")
    if contracted is not None and active:
        parts.append(f"Contracted/Active {contracted/active:.1f}x")
    value = " / ".join(parts) or "-"

    if active is None:
        return IndicatorResult(
            key="crwv_utilization", name="CRWV Utilization Proxy",
            group=GROUP_UTILIZATION, level=Level.UNKNOWN,
            value_text=value or "-",
            detail="active_power_gw が未入力。決算開示があれば manual_inputs.yaml に記入",
            as_of=str(latest.get("quarter", "")),
            source="CRWV決算 (手動)", confidence=CONF_NONE,
        )

    # C: 成長率差 (前四半期の revenue と active_power が両方必要)
    prev_rev = m.as_float(prev.get("revenue_musd")) if prev else None
    prev_active = m.as_float(prev.get("active_power_gw")) if prev else None
    if rev is not None and prev_rev and prev_active:
        rev_g = (rev - prev_rev) / prev_rev
        pow_g = (active - prev_active) / prev_active
        gap = (rev_g - pow_g) * 100  # pt
        detail = (
            f"売上QoQ {rev_g:+.0%} vs 電力QoQ {pow_g:+.0%} (差 {gap:+.0f}pt)"
        )
        if gap >= 0:
            level, note = Level.GREEN, "需要の伸びが設備増を上回る"
        elif gap >= -10:
            level, note = Level.YELLOW, "設備増にやや遅れ"
        elif gap >= -25:
            level, note = Level.ORANGE, "増設分が埋まっていない可能性"
        else:
            level, note = Level.RED, "設備増に需要が大きく未達"
        return IndicatorResult(
            key="crwv_utilization", name="CRWV Utilization Proxy",
            group=GROUP_UTILIZATION, level=level, value_text=value,
            detail=f"{detail} - {note}",
            as_of=str(latest.get("quarter", "")),
            source="CRWV決算 (手動)", confidence=CONF_CONFIRMED,
        )

    return IndicatorResult(
        key="crwv_utilization", name="CRWV Utilization Proxy",
        group=GROUP_UTILIZATION, level=Level.UNKNOWN, value_text=value,
        detail="成長率比較には前四半期の revenue / active_power が必要 (蓄積待ち)",
        as_of=str(latest.get("quarter", "")),
        source="CRWV決算 (手動)", confidence=CONF_NONE,
    )


# ---------------------------------------------------------------
# データセンター需給
# ---------------------------------------------------------------

def eval_apld(manual: dict) -> IndicatorResult:
    latest, prev = m.latest_and_previous(manual, "apld")
    level, detail, conf = _qoq_level(latest, prev, "contracted_mw", red_drop=-0.10)
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
        source="APLD決算 (手動)", confidence=conf,
    )


def eval_dlr(manual: dict) -> IndicatorResult:
    """DLR DC Demand。四半期bookingsは大型契約でブレるためLTM (直近4四半期合計) で
    見る。LTM↓ + backlog↓ + 更新賃料マイナス が同時なら🟠以上に引き上げる。"""
    entries = m.series_entries(manual, "dlr")
    if not entries:
        return IndicatorResult(
            key="dlr_rent", name="DLR DC Demand", group=GROUP_DATACENTER,
            level=Level.UNKNOWN, value_text="-", detail="データ未入力",
            source="DLR決算 (手動)", confidence=CONF_NONE,
        )
    latest = entries[-1]
    prev = entries[-2] if len(entries) >= 2 else None
    rent = m.as_float(latest.get("renewal_rent_cash_pct"))
    bookings = m.as_float(latest.get("bookings_annualized_musd"))
    conf = CONF_CONFIRMED
    if rent is None:
        level = _hint_level(latest)
        detail = "更新賃料未入力 (level_hintによる暫定)"
        conf = CONF_PROVISIONAL if level is not Level.UNKNOWN else CONF_NONE
    elif rent >= 3:
        level, detail = Level.GREEN, f"更新賃料 +{rent:.1f}% (供給不足〜均衡)"
    elif rent >= 0:
        level, detail = Level.YELLOW, f"更新賃料 +{rent:.1f}% (均衡へ軟化)"
    elif rent > -5:
        level, detail = Level.ORANGE, f"更新賃料 {rent:.1f}% (過剰供給の兆候)"
    else:
        level, detail = Level.RED, f"更新賃料 {rent:.1f}% (過剰供給)"

    # LTM bookings (5四半期以上あればLTM同士のQoQ比較)
    def _ltm(idx_end: int) -> float | None:
        window = entries[max(0, idx_end - 4): idx_end]
        vals = [m.as_float(e.get("bookings_annualized_musd")) for e in window]
        vals = [v for v in vals if v is not None]
        return sum(vals) if len(vals) == 4 else None

    ltm = _ltm(len(entries))
    prev_ltm = _ltm(len(entries) - 1)
    ltm_declining = False
    if ltm is not None and prev_ltm:
        chg = (ltm - prev_ltm) / prev_ltm
        detail += f" / LTM bookings ${ltm:,.0f}M ({chg:+.1%})"
        ltm_declining = chg < -0.05
    elif bookings is not None:
        detail += f" / bookings ${bookings:,.0f}M (LTMは4四半期蓄積後)"

    backlog_declining = False
    if prev is not None:
        b_cur = m.as_float(latest.get("backlog_busd"))
        b_prev = m.as_float(prev.get("backlog_busd"))
        if b_cur is not None and b_prev:
            b_chg = (b_cur - b_prev) / b_prev
            detail += f" / backlog QoQ {b_chg:+.0%}"
            backlog_declining = b_chg < 0

    rent_negative = rent is not None and rent < 0
    if ltm_declining and backlog_declining and rent_negative:
        if LEVEL_RANK[level] < LEVEL_RANK[Level.ORANGE]:
            level = Level.ORANGE
        detail += " ⚠ LTM bookings・backlog・賃料が同時悪化"

    value_parts = []
    if ltm is not None:
        value_parts.append(f"LTM ${ltm:,.0f}M")
    elif bookings is not None:
        value_parts.append(f"${bookings:,.0f}M bookings")
    if rent is not None:
        value_parts.append(f"賃料{rent:+.0f}%")
    mw = m.as_float(latest.get("mw_leased"))
    if mw is not None:
        value_parts.append(f"{mw:,.0f}MW leased")
    return IndicatorResult(
        key="dlr_rent", name="DLR DC Demand", group=GROUP_DATACENTER,
        level=level, value_text=" / ".join(value_parts) or "-", detail=detail,
        as_of=str(latest.get("quarter", "")), source="DLR決算 (手動)",
        confidence=conf,
    )


# ---------------------------------------------------------------
# 電力
# ---------------------------------------------------------------

def eval_aep(manual: dict) -> IndicatorResult:
    latest, prev = m.latest_and_previous(manual, "aep")
    level, detail, conf = _qoq_level(latest, prev, "contracted_load_gw", red_drop=-0.05)
    value = "-"
    if latest and m.as_float(latest.get("contracted_load_gw")) is not None:
        value = f"{m.as_float(latest['contracted_load_gw']):.0f}GW (2030年まで)"
    return IndicatorResult(
        key="aep_load", name="AEP Contracted Large-Load", group=GROUP_POWER,
        level=level, value_text=value, detail=detail,
        as_of=str(latest.get("quarter", "")) if latest else "",
        source="AEP決算 (手動)", confidence=conf,
    )


def eval_power_forecast(manual: dict) -> IndicatorResult:
    entries = m.series_entries(manual, "power_forecast")
    if not entries:
        return IndicatorResult(
            key="power_forecast", name="PJM需要予測 (Dominion)", group=GROUP_POWER,
            level=Level.UNKNOWN, value_text="-", detail="データ未入力",
            source="PJM Load Forecast (手動)", confidence=CONF_NONE,
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
            confidence=CONF_NONE,
        )
    if prev is None or m.as_float(prev.get("pjm_dominion_2030_peak_gw")) is None:
        return IndicatorResult(
            key="power_forecast", name="PJM需要予測 (Dominion)", group=GROUP_POWER,
            level=Level.UNKNOWN, value_text=value,
            detail="前年forecastとの比較待ち (年次改定は毎年1月頃)",
            as_of=str(latest.get("year", "")), source="PJM Load Forecast (手動)",
            confidence=CONF_NONE,
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
        confidence=CONF_CONFIRMED,
    )


# ---------------------------------------------------------------
# Market Early Warning (⑨⑩⑪)
# 注意: このグループは複合判定 (悪化グループ数・EXIT) に含めない。
# 別のMarket警報ラインとクロスシグナルにのみ使う。
# ---------------------------------------------------------------

_ESCALATE = {
    Level.GREEN: Level.YELLOW,
    Level.YELLOW: Level.ORANGE,
    Level.ORANGE: Level.RED,
    Level.RED: Level.RED,
}


SECTOR_LABELS_JA = {
    "hyperscaler": "Hyperscaler",
    "semi": "半導体",
    "network": "Network/Server",
    "neocloud": "NeoCloud/DC",
    "power": "電力",
}


def eval_market_breadth(history: dict) -> IndicatorResult:
    """⑨ AI Basket (固定v1・24銘柄) の50/200DMA上回り率 + 20日変化。

    長期(200DMA)と短期(50DMA)の両方を見る早期警報:
    200>65 & 50>55 🟢 / 200>65 & 50が40-55 🟡 / 200>65 & 50<40 🟠
    200が50-65 🟡 / 30-50 🟠 / <30 🔴。20日で-20pt以上なら1段階悪化。
    """
    b200 = storage.latest_value(history, "breadth_200_pct")
    if b200 is None:
        return IndicatorResult(
            key="market_breadth", name="AI Market Breadth", group=GROUP_MARKET,
            level=Level.UNKNOWN, value_text="-",
            detail="株価データ未取得 (次回実行で自動取得)",
            source="Yahoo / AI_BASKET_V1 (自動)", confidence=CONF_NONE,
        )
    date_str, pct200 = b200
    stale = _is_stale_daily(date_str)
    b50 = storage.latest_value(history, "breadth_50_pct")
    pct50 = b50[1] if b50 else None
    cov = storage.latest_value(history, "breadth_coverage")
    coverage = cov[1] if cov else 100.0

    if pct200 > 65:
        if pct50 is None or pct50 > 55:
            level, note = Level.GREEN, "内部は健全 (長期・短期とも)"
        elif pct50 >= 40:
            level, note = Level.YELLOW, "長期Bull維持 / 短期Breadthが軟化"
        else:
            level, note = Level.ORANGE, "長期は維持だが短期内部が崩れている"
    elif pct200 > 50:
        level, note = Level.YELLOW, "長期Breadthがやや軟化"
    elif pct200 > 30:
        level, note = Level.ORANGE, "指数の内部が壊れつつある"
    else:
        level, note = Level.RED, "内部崩壊 (少数銘柄だけの相場)"

    trend_note = ""
    b200_20d = storage.value_near_days_ago(history, "breadth_200_pct", 20, tolerance=7)
    if b200_20d and b200_20d[0] != date_str:
        d = pct200 - b200_20d[1]
        trend_note = f" / 20日変化 {d:+.0f}pt"
        if d <= -20:
            level = _ESCALATE[level]
            trend_note += " (急落→1段階悪化)"

    # セクター別200DMA breadth (収集できていれば表で出す)
    sector_rows: list[list[str]] | None = None
    rows = [["セクター", "200DMA上"]]
    for sector, label in SECTOR_LABELS_JA.items():
        v = storage.latest_value(history, f"breadth200_{sector}")
        if v and v[0] == date_str:
            rows.append([label, f"{v[1]:.0f}%"])
    if len(rows) > 1:
        sector_rows = rows

    value = f"200DMA上 {pct200:.0f}%"
    if pct50 is not None:
        value += f" / 50DMA上 {pct50:.0f}%"
    conf = CONF_CONFIRMED
    if coverage < 60:
        conf = CONF_PROVISIONAL
        note += f" (カバレッジ{coverage:.0f}%と低め)"
    if stale:
        conf = CONF_PROVISIONAL
    return IndicatorResult(
        key="market_breadth", name="AI Market Breadth", group=GROUP_MARKET,
        level=level, value_text=value,
        detail=f"{note}{trend_note} / basket {coverage:.0f}%取得",
        as_of=date_str, source="Yahoo / AI_BASKET_V1 24銘柄 (自動)",
        confidence=conf, stale=stale, detail_rows=sector_rows,
    )


def eval_revision_breadth(history: dict) -> IndicatorResult:
    """⑩ Tier1 12社のFY1 EPS consensus 30日前比の上方修正率"""
    total = storage.latest_value(history, "rev_total_n")
    if total is None or total[1] == 0:
        has_snapshots = bool(history.get("estimates"))
        detail = (
            "EPSスナップショット蓄積中 (30日分貯まると判定開始)"
            if has_snapshots
            else "ALPHAVANTAGE_API_KEY 未設定または未取得"
        )
        return IndicatorResult(
            key="revision_breadth", name="EPS Revision Breadth", group=GROUP_MARKET,
            level=Level.UNKNOWN, value_text="-", detail=detail,
            source="Alpha Vantage / Tier1 12社 (自動)", confidence=CONF_NONE,
        )
    date_str, n = total
    stale = _is_stale_daily(date_str)
    up = storage.latest_value(history, "rev_up_n")
    down = storage.latest_value(history, "rev_down_n")
    up_n = up[1] if up else 0
    down_n = down[1] if down else 0
    flat_n = n - up_n - down_n
    # Net Revision Breadth = (上方 − 下方) / 有効社数。
    # 「全社変化なし」を🔴にしないため、上方率ではなくnetで判定する
    net = (up_n - down_n) / n * 100
    if net >= 25:
        level, note = Level.GREEN, "利益予想は広く上方修正中"
    elif net >= -10:
        level, note = Level.YELLOW, "修正は中立圏 (方向感なし)"
    elif net >= -35:
        level, note = Level.ORANGE, "下方修正が優勢"
    else:
        level, note = Level.RED, "利益期待の広範な悪化"
    return IndicatorResult(
        key="revision_breadth", name="EPS Revision Breadth", group=GROUP_MARKET,
        level=level,
        value_text=(
            f"Net {net:+.0f}% / 上方 {up_n:.0f}・変化なし {flat_n:.0f}・"
            f"下方 {down_n:.0f} ({n:.0f}社)"
        ),
        detail=f"{note} (FY1 EPS consensus 30日前比)",
        as_of=date_str, source="Alpha Vantage / Tier1 12社 (自動)",
        confidence=CONF_PROVISIONAL if stale else CONF_CONFIRMED, stale=stale,
    )


def eval_multiple_expansion(history: dict) -> IndicatorResult:
    """⑪ Multiple Expansion: 90日株価リターン − 90日FY1 EPS修正 (Tier1中央値)。

    「利益予想の上昇で説明できない株価上昇」がどれだけあるか。
    """
    me = storage.latest_value(history, "me_90d_pt")
    if me is None:
        px = storage.latest_value(history, "px_ret90_med")
        value = f"株価90日 {px[1]:+.0f}% (中央値)" if px else "-"
        return IndicatorResult(
            key="multiple_expansion", name="Multiple Expansion", group=GROUP_MARKET,
            level=Level.UNKNOWN, value_text=value,
            detail="EPSスナップショットが90日分貯まると判定開始 (それまで株価側のみ表示)",
            as_of=px[0] if px else "",
            source="Stooq + Alpha Vantage (自動)", confidence=CONF_NONE,
        )
    date_str, pt = me
    stale = _is_stale_daily(date_str)
    me_n = storage.latest_value(history, "me_n")
    n_note = f"EPS>0の{me_n[1]:.0f}社中央値" if me_n else "Tier1中央値"
    if pt <= 10:
        level, note = Level.GREEN, "株価上昇は利益予想で概ね説明できる"
    elif pt <= 25:
        level, note = Level.YELLOW, "倍率拡大が進行"
    elif pt <= 45:
        level, note = Level.ORANGE, "利益予想を大きく超えた株価上昇"
    else:
        level, note = Level.RED, "利益期待と無関係な倍率拡大 (バブル的)"
    return IndicatorResult(
        key="multiple_expansion", name="Multiple Expansion", group=GROUP_MARKET,
        level=level, value_text=f"{pt:+.0f}pt (90日, {n_note})",
        detail=f"{note} (株価リターン − EPS修正。赤字企業は除外)",
        as_of=date_str, source="Yahoo + Alpha Vantage (自動)",
        confidence=CONF_PROVISIONAL if stale else CONF_CONFIRMED, stale=stale,
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
            source="FRED BAMLH0A0HYM2 (自動)", confidence=CONF_NONE,
        )
    date_str, cur = latest
    stale = _is_stale_daily(date_str)
    base = storage.value_near_days_ago(history, "hy_oas_bps", 90, tolerance=30)
    if base is None:
        return IndicatorResult(
            key="hy_oas", name="HY OAS", group=GROUP_CREDIT,
            level=Level.UNKNOWN, value_text=f"{cur:.0f}bp",
            detail="時系列蓄積中 (3か月変化は蓄積後に判定)",
            as_of=date_str, source="FRED BAMLH0A0HYM2 (自動)",
            confidence=CONF_NONE, stale=stale,
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
        confidence=CONF_PROVISIONAL if stale else CONF_CONFIRMED, stale=stale,
    )


def eval_crwv_spread(history: dict, manual: dict) -> IndicatorResult:
    """CRWV 2032債の対米国債スプレッド (+ HY OASとの差でAI固有ストレスを分離)。

    spread = yield − UST7y (FRED DGS7)。発行時spread ≈ 540bp が基準。
    """
    bond = m.crwv_bond(manual)
    y = m.as_float(bond.get("yield_pct"))
    if y is None:
        return IndicatorResult(
            key="crwv_spread", name="CRWVスプレッド (2032)", group=GROUP_CREDIT,
            level=Level.UNKNOWN, value_text="-", detail="利回り未入力",
            source="FINRA TRACE等 (手動) + FRED DGS7", confidence=CONF_NONE,
        )
    as_of = str(bond.get("as_of", ""))
    days = _days_since(as_of)
    stale = days is not None and days > BOND_STALE_DAYS

    ust = storage.latest_value(history, "ust7y_bps")
    if ust is None:
        # 国債利回りが取れない間は利回り絶対値で暫定判定
        if y < 11:
            level, note = Level.GREEN, "利回り絶対値で暫定判定 (UST7y未取得)"
        elif y < 13:
            level, note = Level.YELLOW, "利回り上昇・要観察 (暫定)"
        elif y < 16:
            level, note = Level.ORANGE, "AI creditストレスの警報 (暫定)"
        else:
            level, note = Level.RED, "重大警報 (暫定)"
        return IndicatorResult(
            key="crwv_spread", name="CRWVスプレッド (2032)", group=GROUP_CREDIT,
            level=level, value_text=f"利回り {y:.2f}%", detail=note,
            as_of=as_of, source="FINRA TRACE等 (手動) + FRED DGS7",
            confidence=CONF_PROVISIONAL, stale=stale,
        )

    spread = y * 100 - ust[1]
    if spread < 700:
        level, note = Level.GREEN, "AI credit安定 (発行時 ≈540bp 圏)"
    elif spread < 900:
        level, note = Level.YELLOW, "スプレッド拡大・要観察"
    elif spread < 1200:
        level, note = Level.ORANGE, "AI creditストレスの警報"
    else:
        level, note = Level.RED, "株価より重要な警報レベル"

    hy = storage.latest_value(history, "hy_oas_bps")
    rel_note = ""
    if hy:
        rel = spread - hy[1]
        rel_note = f" / vs HY OAS {rel:+.0f}bp (AI固有プレミアム)"
    detail = f"yield {y:.2f}% − UST7y {ust[1]/100:.2f}% - {note}{rel_note}"
    return IndicatorResult(
        key="crwv_spread", name="CRWVスプレッド (2032)", group=GROUP_CREDIT,
        level=level, value_text=f"{spread:.0f}bp",
        detail=detail, as_of=as_of,
        source="FINRA TRACE等 (手動) + FRED DGS7 (自動)",
        confidence=CONF_PROVISIONAL if stale else CONF_CONFIRMED, stale=stale,
    )


def eval_financing(manual: dict) -> IndicatorResult:
    """新規借入条件。比較は 同issuer + 同category (担保/シニオリティ区分) のみ。

    異なる会社・担保構造のクーポンを並べて「改善/悪化」とは言わない。
    比較はspread_bps優先、なければcoupon。発行失敗 (failed) は常に🔴。
    """
    entries = m.financing_entries(manual)
    if not entries:
        return IndicatorResult(
            key="financing", name="AI企業の新規借入条件", group=GROUP_CREDIT,
            level=Level.UNKNOWN, value_text="-", detail="データ未入力",
            source="起債・借入発表 (手動)", confidence=CONF_NONE,
        )
    if any(e.get("failed") for e in entries[-6:]):
        failed = [e for e in entries[-6:] if e.get("failed")][-1]
        return IndicatorResult(
            key="financing", name="AI企業の新規借入条件", group=GROUP_CREDIT,
            level=Level.RED,
            value_text=f"{failed.get('issuer')} 発行失敗",
            detail="債券発行失敗/撤回が発生 = credit market閉鎖の兆候",
            as_of=str(failed.get("date", "")), source="起債・借入発表 (手動)",
            confidence=CONF_CONFIRMED,
        )

    latest = entries[-1]
    value = f"{latest.get('issuer')} {latest.get('instrument', '')}".strip()
    coupon = m.as_float(latest.get("coupon_pct"))
    spread = m.as_float(latest.get("spread_bps"))
    if coupon is not None:
        value += f" @{coupon:.2f}%"
    elif spread is not None:
        value += f" @SOFR+{spread:.0f}bp"

    # 同issuer + 同category の直近を比較対象として探す
    issuer = latest.get("issuer")
    category = latest.get("category")
    comparable = None
    if category:
        for e in reversed(entries[:-1]):
            if e.get("issuer") == issuer and e.get("category") == category:
                comparable = e
                break

    if comparable is None:
        return IndicatorResult(
            key="financing", name="AI企業の新規借入条件", group=GROUP_CREDIT,
            level=Level.GREEN, value_text=value,
            detail="市場は開いている (直近調達が成立)。同issuer・同条件の比較対象は蓄積待ち",
            as_of=str(latest.get("date", "")), source="起債・借入発表 (手動)",
            confidence=CONF_PROVISIONAL,
        )

    prev_spread = m.as_float(comparable.get("spread_bps"))
    prev_coupon = m.as_float(comparable.get("coupon_pct"))
    if spread is not None and prev_spread is not None:
        d = spread - prev_spread
        desc = f"spread {prev_spread:.0f}→{spread:.0f}bp ({d:+.0f}bp, 同{issuer}/{category})"
        if d <= 25:
            level, note = Level.GREEN, "調達条件は安定〜改善"
        elif d <= 100:
            level, note = Level.YELLOW, "調達コスト上昇"
        elif d <= 300:
            level, note = Level.ORANGE, "調達コスト急上昇"
        else:
            level, note = Level.RED, "調達条件が大幅悪化"
    elif coupon is not None and prev_coupon is not None:
        d = coupon - prev_coupon
        desc = f"coupon {prev_coupon:.2f}→{coupon:.2f}% ({d:+.2f}pt, 同{issuer}/{category})"
        if d <= 0.25:
            level, note = Level.GREEN, "調達条件は安定〜改善"
        elif d <= 1.0:
            level, note = Level.YELLOW, "調達コスト上昇"
        elif d <= 3.0:
            level, note = Level.ORANGE, "調達コスト急上昇"
        else:
            level, note = Level.RED, "調達条件が大幅悪化"
    else:
        return IndicatorResult(
            key="financing", name="AI企業の新規借入条件", group=GROUP_CREDIT,
            level=Level.GREEN, value_text=value,
            detail="市場は開いている。比較可能なspread/couponがなく暫定",
            as_of=str(latest.get("date", "")), source="起債・借入発表 (手動)",
            confidence=CONF_PROVISIONAL,
        )
    return IndicatorResult(
        key="financing", name="AI企業の新規借入条件", group=GROUP_CREDIT,
        level=level, value_text=value, detail=f"{note} - {desc}",
        as_of=str(latest.get("date", "")), source="起債・借入発表 (手動)",
        confidence=CONF_CONFIRMED,
    )


def eval_liquidity(manual: dict) -> IndicatorResult:
    """Refinancing wall: (Cash + undrawn revolver) / 24か月以内満期debt。

    CRWV / APLD / ORCL 等、balance_sheets に入力された会社を横断して最悪値で判定。
    """
    companies = list((manual.get("balance_sheets") or {}).keys())
    if not companies:
        return IndicatorResult(
            key="liquidity", name="Liquidity Coverage (24m)", group=GROUP_CREDIT,
            level=Level.UNKNOWN, value_text="-",
            detail="balance_sheets 未入力 (10-Q/10-Kから記入)",
            source="各社決算 (手動)", confidence=CONF_NONE,
        )

    rows: list[list[str]] = [["社", "流動性", "24m満期", "Coverage"]]
    worst: tuple[str, float] | None = None
    as_of = ""
    for company in companies:
        entries = m.balance_sheet_entries(manual, company)
        if not entries:
            continue
        e = entries[-1]
        cash = m.as_float(e.get("cash_busd"))
        revolver = m.as_float(e.get("undrawn_revolver_busd")) or 0.0
        due24 = m.as_float(e.get("debt_due_24m_busd"))
        if cash is None or due24 is None:
            rows.append([company.upper(), "—", "—", "未入力"])
            continue
        liquidity = cash + revolver
        if due24 <= 0:
            cov_text = "満期なし"
            cov = float("inf")
        else:
            cov = liquidity / due24
            cov_text = f"{cov:.1f}x"
        rows.append([company.upper(), f"${liquidity:.1f}B", f"${due24:.1f}B", cov_text])
        as_of = str(e.get("quarter", as_of))
        if worst is None or cov < worst[1]:
            worst = (company.upper(), cov)

    if worst is None:
        return IndicatorResult(
            key="liquidity", name="Liquidity Coverage (24m)", group=GROUP_CREDIT,
            level=Level.UNKNOWN, value_text="-",
            detail="数値未入力 (cash / debt_due_24m が必要)",
            source="各社決算 (手動)", confidence=CONF_NONE, detail_rows=rows,
        )

    name_, cov = worst
    if cov >= 2:
        level, note = Level.GREEN, "満期の壁まで余裕"
    elif cov >= 1:
        level, note = Level.YELLOW, "リファイナンス前提の水準"
    elif cov >= 0.7:
        level, note = Level.ORANGE, "market閉鎖に脆弱"
    else:
        level, note = Level.RED, "refinancing wall接触の危険"
    cov_text = "∞" if cov == float("inf") else f"{cov:.1f}x"
    return IndicatorResult(
        key="liquidity", name="Liquidity Coverage (24m)", group=GROUP_CREDIT,
        level=level, value_text=f"最弱 {name_} {cov_text}",
        detail=f"{note} ((Cash+未使用枠) ÷ 24か月以内満期debt)",
        as_of=as_of, source="各社10-Q/10-K (手動)",
        confidence=CONF_CONFIRMED, detail_rows=rows,
    )


# ---------------------------------------------------------------
# 全体評価
# ---------------------------------------------------------------

def evaluate_all(history: dict, manual: dict) -> tuple[list[IndicatorResult], CompositeResult]:
    results = [
        eval_market_breadth(history),
        eval_revision_breadth(history),
        eval_multiple_expansion(history),
        eval_hyperscalers(manual),
        eval_crwv_backlog(manual),
        eval_nbis_commitments(manual),
        eval_orcl_rpo(manual),
        eval_gpu_price(history, manual),
        eval_spot_ratio(history, manual),
        eval_crwv_utilization(manual),
        eval_apld(manual),
        eval_dlr(manual),
        eval_aep(manual),
        eval_power_forecast(manual),
        eval_hy_oas(history),
        eval_crwv_spread(history, manual),
        eval_financing(manual),
        eval_liquidity(manual),
    ]
    composite = compute_composite(results)
    return results, composite


def _market_line(results: list[IndicatorResult]) -> tuple[Level, str]:
    """Market Early Warning: 3指標中いくつ🟠以上か (EXITには使わない)"""
    market = [r for r in results if r.group == GROUP_MARKET]
    known = [r for r in market if r.level is not Level.UNKNOWN]
    if not known:
        return Level.UNKNOWN, "Market: データ蓄積中"
    bad = sum(1 for r in known if LEVEL_RANK[r.level] >= LEVEL_RANK[Level.ORANGE])
    yellow = sum(1 for r in known if r.level is Level.YELLOW)
    if bad == 0:
        if yellow:
            return Level.GREEN, f"Market: 🟢〜🟡 概ねbull (注意🟡 {yellow}指標)"
        return Level.GREEN, "Market: 🟢 bull確認 (先行指標に悪化なし)"
    if bad == 1:
        return Level.YELLOW, "Market: 🟡 Watch (先行指標1つが悪化)"
    if bad == 2:
        return Level.ORANGE, "Market: 🟠 Early warning (先行指標2つが同時悪化)"
    return Level.RED, "Market: 🔴 Market regime deterioration"


STAGE_LABELS = {
    1: "Stage 1 — Expansion (需要・利益・CapEx すべて拡大)",
    2: "Stage 2 — Exuberance (Valuation先行、実需はまだ強い)",
    3: "Stage 3 — Divergence (Breadth/Revisions悪化、実需はまだ強い)",
    4: "Stage 4 — Fundamental rollover (Bookings/Utilization悪化)",
    5: "Stage 5 — Credit stress (Spread拡大・調達条件悪化)",
    6: "Stage 6 — Bust",
}


def _compute_stage(
    state: dict[str, Level], market_level: Level, me_level: Level
) -> int:
    warn = lambda lv: LEVEL_RANK[lv] >= LEVEL_RANK[Level.ORANGE]  # noqa: E731
    credit = state.get("credit", Level.UNKNOWN)
    fundamentals = state.get("fundamentals", Level.UNKNOWN)
    infra = state.get("infrastructure", Level.UNKNOWN)
    if credit is Level.RED and (warn(fundamentals) or warn(infra)):
        return 6
    if warn(credit):
        return 5
    if warn(fundamentals) or warn(infra):
        return 4
    if warn(market_level):
        return 3
    if warn(me_level):
        return 2
    return 1


def compute_composite(results: list[IndicatorResult]) -> CompositeResult:
    group_levels: dict[str, Level] = {}
    for g in GROUP_ORDER:
        group_levels[g] = worst_level([r.level for r in results if r.group == g])

    # Marketは悪化グループ数・EXIT判定に含めない (ALERT_GROUPSのみ対象)
    alert_groups = [
        g for g in ALERT_GROUPS
        if LEVEL_RANK[group_levels.get(g, Level.UNKNOWN)] >= LEVEL_RANK[Level.ORANGE]
    ]
    n = len(alert_groups)

    demand_alerts = [g for g in alert_groups if g in DEMAND_SIDE_GROUPS]
    credit_alert = GROUP_CREDIT in alert_groups
    exit_signal = len(demand_alerts) >= 3 and credit_alert

    confirmed = sum(1 for r in results if r.confidence == CONF_CONFIRMED)
    total = len(results)
    confidence_pct = round(confirmed / total * 100) if total else 0

    market_level, market_summary = _market_line(results)

    # AI Bubble State (4行)
    state = {
        "market": market_level,
        "fundamentals": worst_level(
            [group_levels[GROUP_DEMAND], group_levels[GROUP_UTILIZATION]]
        ),
        "infrastructure": worst_level([
            group_levels[GROUP_COMPUTE],
            group_levels[GROUP_DATACENTER],
            group_levels[GROUP_POWER],
        ]),
        "credit": group_levels[GROUP_CREDIT],
    }
    me_level = next(
        (r.level for r in results if r.key == "multiple_expansion"), Level.UNKNOWN
    )
    stage = _compute_stage(state, market_level, me_level)
    # Stage表示はconfidenceに応じて弱める:
    # 「正常っぽい」と「正常を十分確認した」を区別する
    base_label = STAGE_LABELS[stage]
    if confidence_pct >= 70:
        stage_label = base_label
    elif confidence_pct >= 50:
        stage_label = f"Likely {base_label}"
    elif confidence_pct >= 30:
        stage_label = f"Leaning {base_label}"
    else:
        stage_label = f"Stage uncertain (confidence {confidence_pct}%) — 参考: {base_label}"

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
        core_levels = [group_levels[g] for g in ALERT_GROUPS]
        yellow_n = sum(1 for lv in core_levels if lv is Level.YELLOW)
        unknown_n = sum(1 for lv in core_levels if lv is Level.UNKNOWN)
        level = Level.GREEN
        summary = "悪化グループなし"
        extras = []
        if yellow_n:
            extras.append(f"注意🟡 {yellow_n}グループ")
        if unknown_n:
            extras.append(f"データ不足⚪ {unknown_n}グループ")
        if extras:
            summary += " (" + " / ".join(extras) + ")"

    return CompositeResult(
        level=level,
        alert_groups=alert_groups,
        group_levels=group_levels,
        exit_signal=exit_signal,
        summary=summary,
        confidence_pct=confidence_pct,
        confirmed_count=confirmed,
        total_count=total,
        market_level=market_level,
        market_summary=market_summary,
        state=state,
        stage=stage,
        stage_label=stage_label,
    )
