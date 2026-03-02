from src.models import PlanInfo, DiffResult


def test_plan_id_deterministic():
    plan = PlanInfo(
        source="test",
        school_name="テスト自動車学校",
        location="東京都",
        start_date="2026-07-26",
        room_type="シングル",
    )
    assert len(plan.plan_id) == 16
    assert plan.plan_id == plan.plan_id  # 同一入力なら同一ID


def test_plan_id_unique():
    plan_a = PlanInfo(
        source="test", school_name="A校", location="", start_date="2026-07-26", room_type="シングル"
    )
    plan_b = PlanInfo(
        source="test", school_name="B校", location="", start_date="2026-07-26", room_type="シングル"
    )
    assert plan_a.plan_id != plan_b.plan_id


def test_to_dict_and_from_dict():
    plan = PlanInfo(
        source="dream_licence",
        school_name="テスト校",
        location="新潟県",
        start_date="2026-07-26",
        duration_days=15,
        price_min=220000,
        room_type="シングル",
        first_seen="2026-03-01",
        last_seen="2026-03-02",
    )
    d = plan.to_dict()
    assert d["plan_id"] == plan.plan_id
    assert d["source"] == "dream_licence"
    assert d["price_min"] == 220000

    restored = PlanInfo.from_dict(d)
    assert restored.school_name == "テスト校"
    assert restored.price_min == 220000
    assert restored.plan_id == plan.plan_id


def test_format_price():
    plan = PlanInfo(source="t", school_name="t", location="", start_date="2026-07-26")
    assert plan.format_price() == "要問合せ"

    plan.price_min = 220000
    assert plan.format_price() == "¥220,000"


def test_format_date():
    plan = PlanInfo(source="t", school_name="t", location="", start_date="2026-07-26")
    assert plan.format_date() == "7/26"


def test_format_duration():
    plan = PlanInfo(source="t", school_name="t", location="", start_date="2026-07-26")
    assert plan.format_duration() == "不明"

    plan.duration_days = 15
    assert plan.format_duration() == "15日間"
