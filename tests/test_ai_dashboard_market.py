from datetime import date, timedelta

from src.ai_dashboard import market, signals
from src.ai_dashboard.models import (
    GROUP_COMPUTE,
    GROUP_CREDIT,
    GROUP_DATACENTER,
    GROUP_DEMAND,
    GROUP_MARKET,
    GROUP_UTILIZATION,
    IndicatorResult,
    Level,
)


def _daily_history(values_by_metric: dict[str, list[tuple[int, float]]]) -> dict:
    daily: dict = {}
    today = date.today()
    for metric, values in values_by_metric.items():
        for days_ago, v in values:
            d = (today - timedelta(days=days_ago)).isoformat()
            daily.setdefault(d, {})[metric] = v
    return {"daily": daily, "estimates": {}, "levels": {}, "reminders_sent": {}}


# --- market.py 計算 ---

def _series(days: int, price: float) -> list[tuple[str, float]]:
    today = date.today()
    return [
        ((today - timedelta(days=days - i)).isoformat(), price)
        for i in range(days)
    ]


def test_compute_breadth():
    # A: 直近が200DMAより上 (上昇後), B: 下 (下落後)
    up = _series(250, 100.0)
    up[-1] = (up[-1][0], 150.0)
    down = _series(250, 100.0)
    down[-1] = (down[-1][0], 50.0)
    out = market.compute_breadth({"A": up, "B": down}, basket_size=4)
    assert out["breadth_200_pct"] == 50.0
    assert out["breadth_50_pct"] == 50.0
    assert out["breadth_coverage"] == 50.0


def test_compute_revision_metrics():
    today = date.today()
    hist = {
        (today - timedelta(days=30)).isoformat(): {"NVDA": 4.0, "AMD": 2.0, "MSFT": 10.0},
        today.isoformat(): {"NVDA": 4.4, "AMD": 1.8, "MSFT": 10.01},
    }
    out = market.compute_revision_metrics(hist, today)
    assert out["rev_total_n"] == 3
    assert out["rev_up_n"] == 1     # NVDA +10%
    assert out["rev_down_n"] == 1   # AMD -10% (MSFTは±0.5%以内で変化なし)


def test_compute_revision_metrics_needs_baseline():
    today = date.today()
    hist = {today.isoformat(): {"NVDA": 4.0}}
    assert market.compute_revision_metrics(hist, today) == {}


# --- eval_market_breadth ---

def test_breadth_bands():
    h = _daily_history({"breadth_200_pct": [(0, 72)], "breadth_50_pct": [(0, 70)],
                        "breadth_coverage": [(0, 95)]})
    assert signals.eval_market_breadth(h).level is Level.GREEN
    h = _daily_history({"breadth_200_pct": [(0, 55)], "breadth_coverage": [(0, 95)]})
    assert signals.eval_market_breadth(h).level is Level.YELLOW
    h = _daily_history({"breadth_200_pct": [(0, 40)], "breadth_coverage": [(0, 95)]})
    assert signals.eval_market_breadth(h).level is Level.ORANGE
    h = _daily_history({"breadth_200_pct": [(0, 25)], "breadth_coverage": [(0, 95)]})
    assert signals.eval_market_breadth(h).level is Level.RED


def test_breadth_rapid_drop_escalates():
    # 絶対値72%は🟢だが20日で-22ptなので🟡へ1段階悪化
    h = _daily_history({
        "breadth_200_pct": [(20, 94), (0, 72)],
        "breadth_coverage": [(0, 95)],
    })
    r = signals.eval_market_breadth(h)
    assert r.level is Level.YELLOW
    assert "急落" in r.detail


def test_breadth_no_data_is_unknown():
    r = signals.eval_market_breadth({"daily": {}, "estimates": {}})
    assert r.level is Level.UNKNOWN


# --- eval_revision_breadth ---

def test_revision_breadth_bands():
    h = _daily_history({"rev_total_n": [(0, 12)], "rev_up_n": [(0, 8)], "rev_down_n": [(0, 2)]})
    assert signals.eval_revision_breadth(h).level is Level.GREEN   # 67%
    h = _daily_history({"rev_total_n": [(0, 12)], "rev_up_n": [(0, 5)], "rev_down_n": [(0, 4)]})
    assert signals.eval_revision_breadth(h).level is Level.YELLOW  # 42%
    h = _daily_history({"rev_total_n": [(0, 12)], "rev_up_n": [(0, 3)], "rev_down_n": [(0, 7)]})
    assert signals.eval_revision_breadth(h).level is Level.ORANGE  # 25%
    h = _daily_history({"rev_total_n": [(0, 12)], "rev_up_n": [(0, 1)], "rev_down_n": [(0, 9)]})
    assert signals.eval_revision_breadth(h).level is Level.RED     # 8%


# --- eval_multiple_expansion ---

def test_multiple_expansion_bands():
    h = _daily_history({"me_90d_pt": [(0, 5)]})
    assert signals.eval_multiple_expansion(h).level is Level.GREEN
    h = _daily_history({"me_90d_pt": [(0, 20)]})
    assert signals.eval_multiple_expansion(h).level is Level.YELLOW
    h = _daily_history({"me_90d_pt": [(0, 35)]})
    assert signals.eval_multiple_expansion(h).level is Level.ORANGE
    h = _daily_history({"me_90d_pt": [(0, 60)]})
    assert signals.eval_multiple_expansion(h).level is Level.RED


def test_multiple_expansion_price_only_is_unknown():
    h = _daily_history({"px_ret90_med": [(0, 40)]})
    r = signals.eval_multiple_expansion(h)
    assert r.level is Level.UNKNOWN
    assert "+40%" in r.value_text


# --- 複合判定: MarketはEXITに入らない ---

def _result(key, group, level):
    return IndicatorResult(key=key, name=key, group=group, level=level,
                           value_text="", detail="")


def test_market_red_alone_does_not_trigger_composite():
    results = [
        _result("market_breadth", GROUP_MARKET, Level.RED),
        _result("revision_breadth", GROUP_MARKET, Level.RED),
        _result("multiple_expansion", GROUP_MARKET, Level.RED),
        _result("a", GROUP_DEMAND, Level.GREEN),
        _result("b", GROUP_CREDIT, Level.GREEN),
    ]
    c = signals.compute_composite(results)
    assert c.level is Level.GREEN          # 実体側は無傷なので複合は🟢のまま
    assert not c.exit_signal
    assert c.market_level is Level.RED     # Market警報ラインは🔴
    assert c.stage == 3                    # Divergence


def test_market_counts_line():
    results = [
        _result("market_breadth", GROUP_MARKET, Level.ORANGE),
        _result("revision_breadth", GROUP_MARKET, Level.GREEN),
        _result("multiple_expansion", GROUP_MARKET, Level.ORANGE),
        _result("a", GROUP_DEMAND, Level.GREEN),
    ]
    c = signals.compute_composite(results)
    assert c.market_level is Level.ORANGE  # 2/3悪化 = Early warning


# --- Stage ---

def test_stage_progression():
    def mk(market=Level.GREEN, me=Level.GREEN, demand=Level.GREEN,
           util=Level.GREEN, credit=Level.GREEN):
        return [
            _result("market_breadth", GROUP_MARKET, market),
            _result("multiple_expansion", GROUP_MARKET, me),
            _result("a", GROUP_DEMAND, demand),
            _result("u", GROUP_UTILIZATION, util),
            _result("c", GROUP_COMPUTE, Level.GREEN),
            _result("d", GROUP_DATACENTER, Level.GREEN),
            _result("cr", GROUP_CREDIT, credit),
        ]

    assert signals.compute_composite(mk()).stage == 1
    assert signals.compute_composite(mk(me=Level.ORANGE)).stage == 2
    assert signals.compute_composite(
        mk(market=Level.ORANGE, me=Level.ORANGE)
    ).stage == 3
    assert signals.compute_composite(mk(demand=Level.ORANGE)).stage == 4
    assert signals.compute_composite(mk(credit=Level.ORANGE)).stage == 5
    assert signals.compute_composite(
        mk(credit=Level.RED, demand=Level.ORANGE)
    ).stage == 6


# --- DLR LTM同時悪化ルール ---

def _dlr_manual(entries):
    return {"quarterly": {"dlr": entries}}


def test_dlr_triple_decline_escalates():
    entries = [
        {"quarter": f"2025Q{q}", "bookings_annualized_musd": 700,
         "backlog_busd": 2.0, "renewal_rent_cash_pct": 5.0}
        for q in range(1, 5)
    ]
    # 直近4四半期のLTMが低下し、backlog減・賃料マイナス
    entries += [
        {"quarter": "2026Q1", "bookings_annualized_musd": 400,
         "backlog_busd": 1.8, "renewal_rent_cash_pct": 1.0},
        {"quarter": "2026Q2", "bookings_annualized_musd": 300,
         "backlog_busd": 1.5, "renewal_rent_cash_pct": -1.0},
    ]
    r = signals.eval_dlr(_dlr_manual(entries))
    assert r.level in (Level.ORANGE, Level.RED)
    assert "同時悪化" in r.detail


def test_dlr_single_quarter_dip_not_escalated():
    # 賃料が健全なら四半期bookingsの落ち込みだけでは🟢のまま (LTMで平滑化)
    entries = [
        {"quarter": "2025Q1", "bookings_annualized_musd": 700, "renewal_rent_cash_pct": 5.0},
        {"quarter": "2025Q2", "bookings_annualized_musd": 650, "renewal_rent_cash_pct": 5.0},
        {"quarter": "2025Q3", "bookings_annualized_musd": 720, "renewal_rent_cash_pct": 5.0},
        {"quarter": "2025Q4", "bookings_annualized_musd": 680, "renewal_rent_cash_pct": 5.0},
        {"quarter": "2026Q1", "bookings_annualized_musd": 350, "renewal_rent_cash_pct": 5.0},
    ]
    r = signals.eval_dlr(_dlr_manual(entries))
    assert r.level is Level.GREEN


# --- 説明文の網羅性 ---

def test_every_indicator_has_explanation():
    from src.ai_dashboard.explanations import INDICATOR_EXPLANATIONS
    results, _ = signals.evaluate_all(
        {"daily": {}, "estimates": {}, "levels": {}, "reminders_sent": {}}, {}
    )
    for r in results:
        exp = INDICATOR_EXPLANATIONS.get(r.key)
        assert exp, f"説明がない指標: {r.key}"
        assert exp["why"] and exp["how"] and exp["terms"]
