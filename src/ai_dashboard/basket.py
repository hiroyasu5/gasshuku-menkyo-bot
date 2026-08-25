"""AI Basket 定義。

重要: 後から都合よく銘柄を入れ替えないこと。構成を変える時は
AI_BASKET_V2 を新設し、historyのbreadth系列も別メトリクス名にする。
"""
from __future__ import annotations

BASKET_VERSION = "v1"

# ticker -> (stooqシンボル, セクター)
AI_BASKET_V1: dict[str, tuple[str, str]] = {
    # Hyperscaler
    "MSFT": ("msft.us", "hyperscaler"),
    "AMZN": ("amzn.us", "hyperscaler"),
    "GOOGL": ("googl.us", "hyperscaler"),
    "META": ("meta.us", "hyperscaler"),
    "ORCL": ("orcl.us", "hyperscaler"),
    # Semiconductor
    "NVDA": ("nvda.us", "semi"),
    "AMD": ("amd.us", "semi"),
    "AVGO": ("avgo.us", "semi"),
    "MU": ("mu.us", "semi"),
    "MRVL": ("mrvl.us", "semi"),
    "ARM": ("arm.us", "semi"),
    # Network / Server
    "ANET": ("anet.us", "network"),
    "VRT": ("vrt.us", "network"),
    "DELL": ("dell.us", "network"),
    "HPE": ("hpe.us", "network"),
    "SMCI": ("smci.us", "network"),
    # NeoCloud / DC
    "CRWV": ("crwv.us", "neocloud"),
    "NBIS": ("nbis.us", "neocloud"),
    "APLD": ("apld.us", "neocloud"),
    "DLR": ("dlr.us", "neocloud"),
    "EQIX": ("eqix.us", "neocloud"),
    # Power
    "CEG": ("ceg.us", "power"),
    "VST": ("vst.us", "power"),
    "NRG": ("nrg.us", "power"),
}

# ⑩⑪でEPS consensusを追うTier1 (Alpha Vantage 無料枠25req/日に収める)
TIER1_ESTIMATES = [
    "NVDA", "AVGO", "AMD",
    "MSFT", "AMZN", "GOOGL", "META", "ORCL",
    "CRWV", "NBIS", "APLD", "VRT",
]
