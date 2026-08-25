"""AI Basket 定義。

重要: 後から都合よく銘柄を入れ替えないこと。構成を変える時は
AI_BASKET_V2 を新設し、historyのbreadth系列も別メトリクス名にする。
"""
from __future__ import annotations

BASKET_VERSION = "v1"

# ticker -> セクター
AI_BASKET_V1: dict[str, str] = {
    # Hyperscaler
    "MSFT": "hyperscaler",
    "AMZN": "hyperscaler",
    "GOOGL": "hyperscaler",
    "META": "hyperscaler",
    "ORCL": "hyperscaler",
    # Semiconductor
    "NVDA": "semi",
    "AMD": "semi",
    "AVGO": "semi",
    "MU": "semi",
    "MRVL": "semi",
    "ARM": "semi",
    # Network / Server
    "ANET": "network",
    "VRT": "network",
    "DELL": "network",
    "HPE": "network",
    "SMCI": "network",
    # NeoCloud / DC
    "CRWV": "neocloud",
    "NBIS": "neocloud",
    "APLD": "neocloud",
    "DLR": "neocloud",
    "EQIX": "neocloud",
    # Power
    "CEG": "power",
    "VST": "power",
    "NRG": "power",
}

# ⑩⑪でEPS consensusを追うTier1 (Alpha Vantage 無料枠25req/日に収める)
TIER1_ESTIMATES = [
    "NVDA", "AVGO", "AMD",
    "MSFT", "AMZN", "GOOGL", "META", "ORCL",
    "CRWV", "NBIS", "APLD", "VRT",
]
