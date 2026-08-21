from datetime import date, timedelta

from src.ai_dashboard import signals
from src.ai_dashboard.models import (
    GROUP_COMPUTE,
    GROUP_CREDIT,
    GROUP_DATACENTER,
    GROUP_DEMAND,
    GROUP_POWER,
    IndicatorResult,
    Level,
    worst_level,
)


def _daily_history(metric: str, values: list[tuple[int, float]]) -> dict:
    """values: (days_ago, value) のリストからhistoryを作る"""
    daily = {}
    today = date.today()
    for days_ago, v in values:
        d = (today - timedelta(days=days_ago)).isoformat()
        daily.setdefault(d, {})[metric] = v
    return {"daily": daily, "levels": {}, "reminders_sent": {}}


def test_worst_level():
    assert worst_level([Level.GREEN, Level.ORANGE, Level.YELLOW]) is Level.ORANGE
    assert worst_level([Level.UNKNOWN, Level.GREEN]) is Level.GREEN
    assert worst_level([Level.UNKNOWN]) is Level.UNKNOWN
    assert worst_level([]) is Level.UNKNOWN


# --- 四半期QoQ判定 ---

def _crwv_manual(*backlogs, canceled=False):
    return {
        "quarterly": {
            "crwv": [
                {"quarter": f"2026Q{i+1}", "backlog_busd": b,
                 "canceled": canceled if i == len(backlogs) - 1 else False}
                for i, b in enumerate(backlogs)
            ]
        }
    }


def test_backlog_qoq_up_is_green():
    r = signals.eval_crwv_backlog(_crwv_manual(80, 104.2))
    assert r.level is Level.GREEN
    assert "104.2" in r.value_text


def test_backlog_qoq_flat_is_yellow():
    r = signals.eval_crwv_backlog(_crwv_manual(100, 100.5))
    assert r.level is Level.YELLOW


def test_backlog_qoq_down_is_orange():
    r = signals.eval_crwv_backlog(_crwv_manual(100, 95))
    assert r.level is Level.ORANGE


def test_backlog_big_drop_is_red():
    r = signals.eval_crwv_backlog(_crwv_manual(100, 80))
    assert r.level is Level.RED


def test_backlog_cancel_is_red():
    r = signals.eval_crwv_backlog(_crwv_manual(100, 110, canceled=True))
    assert r.level is Level.RED


def test_backlog_single_quarter_uses_hint():
    manual = {"quarterly": {"crwv": [
        {"quarter": "2026Q2", "backlog_busd": 104.2, "level_hint": "green"}
    ]}}
    r = signals.eval_crwv_backlog(manual)
    assert r.level is Level.GREEN


# --- HY OAS ---

def test_hy_oas_stable_green():
    h = _daily_history("hy_oas_bps", [(90, 300), (0, 320)])
    assert signals.eval_hy_oas(h).level is Level.GREEN


def test_hy_oas_100bp_widening_orange():
    h = _daily_history("hy_oas_bps", [(90, 300), (0, 410)])
    assert signals.eval_hy_oas(h).level is Level.ORANGE


def test_hy_oas_200bp_widening_red():
    h = _daily_history("hy_oas_bps", [(90, 300), (0, 510)])
    assert signals.eval_hy_oas(h).level is Level.RED


# --- GPU価格 ---

def test_gpu_price_stable_green():
    h = _daily_history("sd_b200_rental", [(90, 5.74), (0, 5.60)])
    r = signals.eval_gpu_price(h, {})
    assert r.level is Level.GREEN
    assert "B200" in r.name


def test_gpu_price_25pct_drop_orange():
    h = _daily_history("sd_b200_rental", [(90, 6.0), (0, 4.5)])
    assert signals.eval_gpu_price(h, {}).level is Level.ORANGE


def test_gpu_price_35pct_drop_red():
    h = _daily_history("sd_b200_rental", [(90, 6.0), (0, 3.8)])
    assert signals.eval_gpu_price(h, {}).level is Level.RED


def test_gpu_price_falls_back_to_manual_is_unknown():
    # データ不足時に🟢を出さない (v2): 手動値は表示するが判定は⚪
    manual = {"gpu_manual_fallback": {"sd_b200_rental": 5.74, "as_of": "2026-07-07"}}
    r = signals.eval_gpu_price({"daily": {}}, manual)
    assert r.level is Level.UNKNOWN
    assert "5.74" in r.value_text


def test_gpu_price_short_history_is_unknown():
    h = _daily_history("sd_b200_rental", [(5, 5.74), (0, 5.70)])
    r = signals.eval_gpu_price(h, {})
    assert r.level is Level.UNKNOWN


# --- Spot比率 ---

def test_spot_ratio_healthy():
    h = _daily_history("cw_b200_od_node", [(0, 68.80)])
    h["daily"][list(h["daily"])[0]]["cw_b200_spot_node"] = 34.87
    r = signals.eval_spot_ratio(h, {})
    assert r.level is Level.GREEN


def test_spot_ratio_collapse_red():
    h = _daily_history("cw_b200_od_node", [(0, 80.0)])
    h["daily"][list(h["daily"])[0]]["cw_b200_spot_node"] = 8.0
    r = signals.eval_spot_ratio(h, {})
    assert r.level is Level.RED


# --- CRWVスプレッド ---

def _spread_setup(yield_pct: float, ust_bps: float, hy_bps: float | None = None):
    h = _daily_history("ust7y_bps", [(0, ust_bps)])
    if hy_bps is not None:
        h["daily"][list(h["daily"])[0]]["hy_oas_bps"] = hy_bps
    manual = {"crwv_bond": {"yield_pct": yield_pct, "as_of": "2026-08-20"}}
    return h, manual


def test_crwv_spread_levels():
    # 9.6% - 4.2% = 540bp → 🟢
    h, manual = _spread_setup(9.6, 420)
    r = signals.eval_crwv_spread(h, manual)
    assert r.level is Level.GREEN
    assert "540" in r.value_text
    # 12.5% - 4.2% = 830bp → 🟡
    h, manual = _spread_setup(12.5, 420)
    assert signals.eval_crwv_spread(h, manual).level is Level.YELLOW
    # 14.5% - 4.2% = 1030bp → 🟠
    h, manual = _spread_setup(14.5, 420)
    assert signals.eval_crwv_spread(h, manual).level is Level.ORANGE
    # 20% - 4.2% = 1580bp → 🔴
    h, manual = _spread_setup(20.0, 420)
    assert signals.eval_crwv_spread(h, manual).level is Level.RED


def test_crwv_spread_shows_ai_premium_vs_hy():
    h, manual = _spread_setup(9.6, 420, hy_bps=300)
    r = signals.eval_crwv_spread(h, manual)
    assert "+240bp" in r.detail  # 540 - 300


def test_crwv_spread_without_treasury_is_provisional():
    manual = {"crwv_bond": {"yield_pct": 9.6, "as_of": "2026-08-20"}}
    r = signals.eval_crwv_spread({"daily": {}}, manual)
    assert r.confidence == "provisional"


# --- Financing (同issuer + 同categoryのみ比較) ---

def test_financing_failed_is_red():
    manual = {"financing": [
        {"date": "2026-06", "issuer": "CRWV", "category": "a", "coupon_pct": 9.6},
        {"date": "2026-09", "issuer": "APLD", "category": "b", "failed": True},
    ]}
    assert signals.eval_financing(manual).level is Level.RED


def test_financing_cross_company_not_compared():
    # 会社もcategoryも違う → 比較せず「蓄積待ち」の暫定green
    manual = {"financing": [
        {"date": "2026-06", "issuer": "CRWV", "category": "crwv_unsecured", "coupon_pct": 9.625},
        {"date": "2026-07", "issuer": "APLD", "category": "apld_spv", "coupon_pct": 7.0},
    ]}
    r = signals.eval_financing(manual)
    assert r.level is Level.GREEN
    assert r.confidence == "provisional"
    assert "蓄積待ち" in r.detail


def test_financing_same_category_coupon_jump_is_orange():
    manual = {"financing": [
        {"date": "2026-06", "issuer": "CRWV", "category": "crwv_unsecured", "coupon_pct": 7.0},
        {"date": "2026-09", "issuer": "CRWV", "category": "crwv_unsecured", "coupon_pct": 9.0},
    ]}
    r = signals.eval_financing(manual)
    assert r.level is Level.ORANGE
    assert r.confidence == "confirmed"


def test_financing_same_category_spread_preferred():
    manual = {"financing": [
        {"date": "2026-03", "issuer": "CRWV", "category": "gpu", "spread_bps": 225, "coupon_pct": 5.9},
        {"date": "2026-09", "issuer": "CRWV", "category": "gpu", "spread_bps": 250, "coupon_pct": 9.0},
    ]}
    # spread +25bp → 🟢 (couponの+3.1ptではなくspreadで判定)
    assert signals.eval_financing(manual).level is Level.GREEN


# --- Utilization Proxy ---

def _crwv_util(entries):
    return {"quarterly": {"crwv": entries}}


def test_utilization_missing_power_is_unknown():
    manual = _crwv_util([{"quarter": "2026Q2", "revenue_musd": 2580}])
    r = signals.eval_crwv_utilization(manual)
    assert r.level is Level.UNKNOWN


def test_utilization_revenue_outpacing_power_is_green():
    manual = _crwv_util([
        {"quarter": "2026Q2", "revenue_musd": 2000, "active_power_gw": 1.0},
        {"quarter": "2026Q3", "revenue_musd": 3100, "active_power_gw": 1.5},
    ])
    r = signals.eval_crwv_utilization(manual)
    assert r.level is Level.GREEN  # +55% vs +50%


def test_utilization_power_outpacing_revenue_is_orange():
    # 売上+30% vs 電力+50% = 差-20pt → 🟠
    manual = _crwv_util([
        {"quarter": "2026Q2", "revenue_musd": 2000, "active_power_gw": 1.0},
        {"quarter": "2026Q3", "revenue_musd": 2600, "active_power_gw": 1.5},
    ])
    assert signals.eval_crwv_utilization(manual).level is Level.ORANGE


def test_utilization_big_gap_is_red():
    manual = _crwv_util([
        {"quarter": "2026Q2", "revenue_musd": 2000, "active_power_gw": 1.0},
        {"quarter": "2026Q3", "revenue_musd": 2100, "active_power_gw": 1.6},
    ])
    assert signals.eval_crwv_utilization(manual).level is Level.RED


# --- Liquidity Coverage ---

def _bs(cash, revolver, due24):
    return {"balance_sheets": {"crwv": [{
        "quarter": "2026Q2", "cash_busd": cash,
        "undrawn_revolver_busd": revolver, "debt_due_24m_busd": due24,
    }]}}


def test_liquidity_levels():
    assert signals.eval_liquidity(_bs(8, 2, 2)).level is Level.GREEN    # 5.0x
    assert signals.eval_liquidity(_bs(3, 0, 2)).level is Level.YELLOW   # 1.5x
    assert signals.eval_liquidity(_bs(4, 0, 5)).level is Level.ORANGE   # 0.8x
    assert signals.eval_liquidity(_bs(5, 0, 10)).level is Level.RED     # 0.5x


def test_liquidity_empty_is_unknown():
    assert signals.eval_liquidity({}).level is Level.UNKNOWN
    assert signals.eval_liquidity(_bs(None, None, None)).level is Level.UNKNOWN


# --- Hyperscaler 5社 breadth ---

def _hyper_companies(companies):
    return {"quarterly": {"hyperscalers": [{"quarter": "2026Q3", "companies": companies}]}}


def test_hyperscaler_breadth_all_good_is_green():
    manual = _hyper_companies({
        "MSFT": {"cloud_yoy_pct": 31, "prev_cloud_yoy_pct": 33, "capex_guide": "up", "capacity_constrained": True},
        "GOOGL": {"cloud_yoy_pct": 40, "prev_cloud_yoy_pct": 38, "capex_guide": "up", "capacity_constrained": True},
    })
    r = signals.eval_hyperscalers(manual)
    assert r.level is Level.GREEN
    assert r.detail_rows is not None


def test_hyperscaler_two_decelerating_is_yellow():
    manual = _hyper_companies({
        "MSFT": {"cloud_yoy_pct": 31, "prev_cloud_yoy_pct": 38, "capex_guide": "up"},
        "AMZN": {"cloud_yoy_pct": 15, "prev_cloud_yoy_pct": 22, "capex_guide": "up"},
        "GOOGL": {"cloud_yoy_pct": 40, "prev_cloud_yoy_pct": 39, "capex_guide": "up"},
    })
    assert signals.eval_hyperscalers(manual).level is Level.YELLOW


def test_hyperscaler_guide_down_is_orange():
    manual = _hyper_companies({
        "MSFT": {"cloud_yoy_pct": 31, "prev_cloud_yoy_pct": 32, "capex_guide": "down"},
        "GOOGL": {"cloud_yoy_pct": 40, "prev_cloud_yoy_pct": 39, "capex_guide": "up"},
    })
    assert signals.eval_hyperscalers(manual).level is Level.ORANGE


def test_hyperscaler_two_guide_down_is_red():
    manual = _hyper_companies({
        "MSFT": {"capex_guide": "down"},
        "AMZN": {"capex_guide": "down"},
        "GOOGL": {"capex_guide": "up"},
    })
    assert signals.eval_hyperscalers(manual).level is Level.RED


def test_hyperscaler_legacy_format_is_provisional():
    manual = {"quarterly": {"hyperscalers": [
        {"quarter": "2026Q2", "capex_trend": "up", "cloud_growth_trend": "up",
         "fcf_deteriorating": False}
    ]}}
    r = signals.eval_hyperscalers(manual)
    assert r.level is Level.GREEN
    assert r.confidence == "provisional"


# --- Hyperscaler ---

def _hyper(capex, cloud, fcf_bad):
    return {"quarterly": {"hyperscalers": [
        {"quarter": "2026Q2", "capex_trend": capex,
         "cloud_growth_trend": cloud, "fcf_deteriorating": fcf_bad}
    ]}}


def test_hyperscaler_matrix():
    assert signals.eval_hyperscalers(_hyper("up", "up", False)).level is Level.GREEN
    assert signals.eval_hyperscalers(_hyper("up", "flat", False)).level is Level.YELLOW
    assert signals.eval_hyperscalers(_hyper("up", "down", True)).level is Level.RED
    assert signals.eval_hyperscalers(_hyper("down", "up", False)).level is Level.ORANGE


# --- 複合判定 ---

def _result(key: str, group: str, level: Level) -> IndicatorResult:
    return IndicatorResult(
        key=key, name=key, group=group, level=level, value_text="", detail=""
    )


def test_composite_all_green():
    results = [
        _result("a", GROUP_DEMAND, Level.GREEN),
        _result("b", GROUP_COMPUTE, Level.GREEN),
        _result("c", GROUP_CREDIT, Level.GREEN),
    ]
    c = signals.compute_composite(results)
    assert c.level is Level.GREEN
    assert not c.exit_signal


def test_composite_one_group_yellow_signal():
    results = [
        _result("a", GROUP_DEMAND, Level.ORANGE),
        _result("b", GROUP_COMPUTE, Level.GREEN),
    ]
    c = signals.compute_composite(results)
    assert c.level is Level.YELLOW  # 1グループ悪化はノイズの可能性


def test_composite_two_groups_orange():
    results = [
        _result("a", GROUP_DEMAND, Level.ORANGE),
        _result("b", GROUP_COMPUTE, Level.RED),
        _result("c", GROUP_CREDIT, Level.GREEN),
    ]
    c = signals.compute_composite(results)
    assert c.level is Level.ORANGE


def test_composite_three_groups_red():
    results = [
        _result("a", GROUP_DEMAND, Level.ORANGE),
        _result("b", GROUP_COMPUTE, Level.ORANGE),
        _result("c", GROUP_DATACENTER, Level.ORANGE),
    ]
    c = signals.compute_composite(results)
    assert c.level is Level.RED
    assert not c.exit_signal  # creditは無事なのでEXITではない


def test_composite_exit_signal():
    results = [
        _result("a", GROUP_DEMAND, Level.ORANGE),
        _result("b", GROUP_COMPUTE, Level.ORANGE),
        _result("c", GROUP_DATACENTER, Level.RED),
        _result("d", GROUP_POWER, Level.GREEN),
        _result("e", GROUP_CREDIT, Level.ORANGE),
    ]
    c = signals.compute_composite(results)
    assert c.exit_signal
    assert c.level is Level.RED
    assert "EXIT" in c.summary
