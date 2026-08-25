"""AI Bubble Dashboard - データモデルとシグナルレベル定義"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Level(str, Enum):
    """指標の警戒レベル(信号色)"""

    GREEN = "green"
    YELLOW = "yellow"
    ORANGE = "orange"
    RED = "red"
    UNKNOWN = "unknown"


LEVEL_EMOJI = {
    Level.GREEN: "🟢",
    Level.YELLOW: "🟡",
    Level.ORANGE: "🟠",
    Level.RED: "🔴",
    Level.UNKNOWN: "⚪",
}

LEVEL_LABEL_JA = {
    Level.GREEN: "正常",
    Level.YELLOW: "注意",
    Level.ORANGE: "警戒",
    Level.RED: "危険",
    Level.UNKNOWN: "データ不足",
}

# 深刻度の順序。compositeの集計に使う
LEVEL_RANK = {
    Level.UNKNOWN: -1,
    Level.GREEN: 0,
    Level.YELLOW: 1,
    Level.ORANGE: 2,
    Level.RED: 3,
}


def worst_level(levels: list[Level]) -> Level:
    """UNKNOWNを除いた最悪レベル。全てUNKNOWNならUNKNOWN"""
    known = [lv for lv in levels if lv is not Level.UNKNOWN]
    if not known:
        return Level.UNKNOWN
    return max(known, key=lambda lv: LEVEL_RANK[lv])


# 指標グループ(バブル崩壊の因果連鎖に対応する6分類 + Market overlay)
GROUP_DEMAND = "demand"            # Hyperscaler / Backlog / RPO (未来の需要)
GROUP_COMPUTE = "compute"          # GPUレンタル価格 / spot比率
GROUP_UTILIZATION = "utilization"  # Revenue/Active GW 等の稼働率proxy
GROUP_DATACENTER = "datacenter"    # APLD契約MW / DLR bookings・賃料
GROUP_POWER = "power"              # AEP契約GW / PJM需要予測
GROUP_CREDIT = "credit"            # HY OAS / CRWVスプレッド / 借入条件 / 流動性
GROUP_MARKET = "market"            # Breadth / Revisions / Multiple Expansion (先行警報)

GROUP_LABEL_JA = {
    GROUP_MARKET: "Market Early Warning (株式市場の先行指標)",
    GROUP_DEMAND: "需要 (Hyperscaler / Backlog / RPO)",
    GROUP_COMPUTE: "Compute価格",
    GROUP_UTILIZATION: "稼働率 (Utilization Proxy)",
    GROUP_DATACENTER: "データセンター需給",
    GROUP_POWER: "電力需要",
    GROUP_CREDIT: "信用市場",
}

# 表示順 (Marketを先頭に置く: 先行指標なので)
GROUP_ORDER = [
    GROUP_MARKET,
    GROUP_DEMAND, GROUP_COMPUTE, GROUP_UTILIZATION,
    GROUP_DATACENTER, GROUP_POWER, GROUP_CREDIT,
]

# 複合判定 (悪化グループ数・EXIT) の対象。Marketはノイズが多いため
# 意図的に含めない — 別のMarket警報ラインとクロスシグナルにのみ使う
ALERT_GROUPS = [
    GROUP_DEMAND, GROUP_COMPUTE, GROUP_UTILIZATION,
    GROUP_DATACENTER, GROUP_POWER, GROUP_CREDIT,
]

# EXIT判定で「需要側」とみなすグループ (credit以外)
DEMAND_SIDE_GROUPS = [
    GROUP_DEMAND, GROUP_COMPUTE, GROUP_UTILIZATION, GROUP_DATACENTER, GROUP_POWER,
]

# データの確からしさ。🟢は「データで正常を確認」した時のみ
CONF_CONFIRMED = "confirmed"      # 実データで判定した
CONF_PROVISIONAL = "provisional"  # level_hint / 手動フォールバック / stale
CONF_NONE = "none"                # データ不足 (level=UNKNOWN)


@dataclass
class IndicatorResult:
    """1指標の評価結果"""

    key: str            # 例: "crwv_backlog"
    name: str           # 表示名
    group: str          # GROUP_* のいずれか
    level: Level
    value_text: str     # 現在値の表示文字列 (例: "$104.2B")
    detail: str         # 判定理由・変化の説明
    as_of: str = ""     # データ基準日 (例: "2026Q2", "2026-08-20")
    source: str = ""    # データ源の説明
    confidence: str = CONF_CONFIRMED  # CONF_* のいずれか
    stale: bool = False               # データが古い (🕐表示)
    detail_rows: list[list[str]] | None = None  # カード内に表として出す行 (任意)

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "name": self.name,
            "group": self.group,
            "level": self.level.value,
            "value_text": self.value_text,
            "detail": self.detail,
            "as_of": self.as_of,
            "source": self.source,
            "confidence": self.confidence,
            "stale": self.stale,
        }


@dataclass
class CompositeResult:
    """複合判定(何グループ同時に悪化しているか)"""

    level: Level
    alert_groups: list[str] = field(default_factory=list)  # 🟠以上のグループ
    group_levels: dict[str, Level] = field(default_factory=dict)
    exit_signal: bool = False  # 需要側3グループ以上 + credit悪化
    summary: str = ""
    confidence_pct: int = 0     # confirmed指標の割合 (%)
    confirmed_count: int = 0
    total_count: int = 0
    # Market Early Warning (EXIT判定には使わない別ライン)
    market_level: Level = Level.UNKNOWN
    market_summary: str = ""
    # AI Bubble State (Market / Fundamentals / Infrastructure / Credit)
    state: dict[str, Level] = field(default_factory=dict)
    # Stage 1-6
    stage: int = 1
    stage_label: str = ""
