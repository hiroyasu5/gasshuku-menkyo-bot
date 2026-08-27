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
    basket = {"A": "semi", "B": "power", "C": "semi", "D": "power"}
    out = market.compute_breadth({"A": up, "B": down}, basket)
    assert out["breadth_200_pct"] == 50.0
    assert out["breadth_50_pct"] == 50.0
    assert out["breadth_coverage"] == 50.0
    # セクター別 (Aのみsemi=100%上、Bのみpower=0%上)
    assert out["breadth200_semi"] == 100.0
    assert out["breadth200_power"] == 0.0


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
    def b(p200, p50=None):
        m = {"breadth_200_pct": [(0, p200)], "breadth_coverage": [(0, 95)]}
        if p50 is not None:
            m["breadth_50_pct"] = [(0, p50)]
        return signals.eval_market_breadth(_daily_history(m)).level

    assert b(72, 70) is Level.GREEN     # 長期・短期とも健全
    assert b(67, 50) is Level.YELLOW    # 長期Bull維持 / 短期軟化 (現況ケース)
    assert b(72, 35) is Level.ORANGE    # 長期維持だが短期崩壊
    assert b(55) is Level.YELLOW
    assert b(40) is Level.ORANGE
    assert b(25) is Level.RED


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

def test_revision_breadth_net_bands():
    def rb(up, down, total=12):
        h = _daily_history({"rev_total_n": [(0, total)], "rev_up_n": [(0, up)],
                            "rev_down_n": [(0, down)]})
        return signals.eval_revision_breadth(h)

    assert rb(8, 2).level is Level.GREEN    # net +50%
    assert rb(5, 4).level is Level.YELLOW   # net +8%
    assert rb(3, 7).level is Level.ORANGE   # net -33%
    assert rb(1, 9).level is Level.RED      # net -67%
    # 上方1・下方0・変化なし11 → 旧方式なら🔴だったが、netでは+8%で🟡
    r = rb(1, 0)
    assert r.level is Level.YELLOW
    assert "Net +8%" in r.value_text
    assert "変化なし 11" in r.value_text


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


def test_multiple_expansion_excludes_negative_eps():
    # NVDA: EPS>0で株価+40%/EPS+10% → ME=+30。CRWVは赤字なので除外される
    today = date.today()
    prices = {}
    for ticker, ret in [("NVDA", 40.0), ("MSFT", 40.0), ("GOOGL", 40.0),
                        ("META", 40.0), ("AMZN", 40.0), ("CRWV", 100.0)]:
        s = _series(120, 100.0)
        s[-1] = (s[-1][0], 100.0 + ret)
        prices[ticker] = s
    est_hist = {
        (today - timedelta(days=90)).isoformat(): {
            "NVDA": 10.0, "MSFT": 10.0, "GOOGL": 10.0, "META": 10.0,
            "AMZN": 10.0, "CRWV": -4.0,
        },
        today.isoformat(): {
            "NVDA": 11.0, "MSFT": 11.0, "GOOGL": 11.0, "META": 11.0,
            "AMZN": 11.0, "CRWV": -1.0,  # 赤字縮小だが変化率-75%相当で発散する例
        },
    }
    out = market.compute_multiple_expansion(prices, est_hist, today)
    # CRWV除外でEPS>0の5社: ME = 40 - 10 = +30pt
    assert out["me_90d_pt"] == 30.0
    assert out["me_n"] == 5


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


# --- Stage confidence連動 ---

def test_stage_label_confidence_prefix():
    from src.ai_dashboard.models import CONF_CONFIRMED, CONF_NONE

    def mk(n_confirmed, n_unknown):
        rs = []
        for i in range(n_confirmed):
            r = _result(f"c{i}", GROUP_DEMAND, Level.GREEN)
            r.confidence = CONF_CONFIRMED
            rs.append(r)
        for i in range(n_unknown):
            r = _result(f"u{i}", GROUP_CREDIT, Level.UNKNOWN)
            r.confidence = CONF_NONE
            rs.append(r)
        return rs

    # 8/10 = 80% → 断定
    assert signals.compute_composite(mk(8, 2)).stage_label.startswith("Stage 1")
    # 6/10 = 60% → Likely
    assert signals.compute_composite(mk(6, 4)).stage_label.startswith("Likely Stage 1")
    # 4/10 = 40% → Leaning
    assert signals.compute_composite(mk(4, 6)).stage_label.startswith("Leaning Stage 1")
    # 2/10 = 20% → uncertain
    assert signals.compute_composite(mk(2, 8)).stage_label.startswith("Stage uncertain")
