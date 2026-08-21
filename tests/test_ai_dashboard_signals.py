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


def test_gpu_price_falls_back_to_manual():
    manual = {"gpu_manual_fallback": {"sd_b200_rental": 5.74, "as_of": "2026-07-07"}}
    r = signals.eval_gpu_price({"daily": {}}, manual)
    assert r.level is Level.GREEN
    assert "5.74" in r.value_text


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


# --- CRWV債券 ---

def test_crwv_bond_levels():
    assert signals.eval_crwv_bond({"crwv_bond": {"yield_pct": 9.6}}).level is Level.GREEN
    assert signals.eval_crwv_bond({"crwv_bond": {"yield_pct": 12}}).level is Level.YELLOW
    assert signals.eval_crwv_bond({"crwv_bond": {"yield_pct": 15}}).level is Level.ORANGE
    assert signals.eval_crwv_bond({"crwv_bond": {"yield_pct": 20}}).level is Level.RED


# --- Financing ---

def test_financing_failed_is_red():
    manual = {"financing": [
        {"date": "2026-06", "issuer": "CRWV", "coupon_pct": 9.6, "failed": False},
        {"date": "2026-09", "issuer": "APLD", "failed": True},
    ]}
    assert signals.eval_financing(manual).level is Level.RED


def test_financing_coupon_jump_is_orange():
    manual = {"financing": [
        {"date": "2026-06", "issuer": "CRWV", "coupon_pct": 7.0, "failed": False},
        {"date": "2026-09", "issuer": "CRWV", "coupon_pct": 9.0, "failed": False},
    ]}
    assert signals.eval_financing(manual).level is Level.ORANGE


def test_financing_improving_is_green():
    manual = {"financing": [
        {"date": "2026-06", "issuer": "APLD", "coupon_pct": 9.25, "failed": False},
        {"date": "2026-07", "issuer": "APLD", "coupon_pct": 7.0, "failed": False},
    ]}
    assert signals.eval_financing(manual).level is Level.GREEN


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
