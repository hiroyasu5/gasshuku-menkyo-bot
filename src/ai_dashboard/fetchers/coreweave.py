"""CoreWeave 料金ページから B200 の on-demand / spot 掲載価格を取得する (実験的)。

掲載価格は 8-GPUノード単位 (例: on-demand $68.80/h, spot $34.87/h)。
ページはJSレンダリングの可能性があるため、生HTML内のJSON
(__NEXT_DATA__ 等) も含めて "B200" 近傍の $ 金額を探す。

spot / on-demand 比率は需給シグナルとして使う:
比率が高い = 需要が強い、急低下 = GPU余剰の可能性。
"""
from __future__ import annotations

import logging
import re

import httpx

from ..config import HTTP_TIMEOUT, USER_AGENT

logger = logging.getLogger(__name__)

PRICING_URLS = [
    "https://www.coreweave.com/pricing",
    "https://coreweave.com/pricing",
]

# ノード時間単価として妥当な範囲 ($/hr, 8-GPUノード)
PRICE_PAT = r"\$\s*([0-9]{1,3}(?:\.[0-9]{1,3})?)"


class CoreWeaveError(Exception):
    pass


def _prices_near(text: str, anchor_pat: str, window: int = 400) -> list[tuple[float, str]]:
    """anchor近傍の (価格, 周辺テキスト小文字) リスト"""
    out: list[tuple[float, str]] = []
    for m in re.finditer(anchor_pat, text, re.IGNORECASE):
        ctx = text[max(0, m.start() - window): m.end() + window]
        for pm in re.finditer(PRICE_PAT, ctx):
            v = float(pm.group(1))
            if 5 <= v <= 300:
                local = ctx[max(0, pm.start() - 80): pm.end() + 80].lower()
                out.append((v, local))
    return out


def fetch_b200_pricing() -> dict[str, float]:
    """{"cw_b200_od_node": 68.80, "cw_b200_spot_node": 34.87} を返す (取れた分のみ)"""
    last_error: Exception | None = None
    for url in PRICING_URLS:
        try:
            resp = httpx.get(
                url,
                headers={"User-Agent": USER_AGENT},
                timeout=HTTP_TIMEOUT,
                follow_redirects=True,
            )
            resp.raise_for_status()
        except Exception as e:
            last_error = e
            logger.info("[CoreWeave] %s へのアクセス失敗: %s", url, e)
            continue

        html = resp.text
        stripped = re.sub(r"<[^>]+>", " ", html)

        found: dict[str, float] = {}
        for text in (stripped, html):
            candidates = _prices_near(text, r"(?:HGX\s*)?B200")
            if not candidates:
                continue
            spot = [v for v, ctx in candidates if "spot" in ctx]
            ondemand = [
                v for v, ctx in candidates
                if "spot" not in ctx
            ]
            if ondemand:
                found["cw_b200_od_node"] = max(ondemand)
            if spot:
                found["cw_b200_spot_node"] = min(spot)
            if found:
                break

        if found:
            logger.info("[CoreWeave] %s から取得: %s", url, found)
            return found
        logger.info("[CoreWeave] %s にB200価格が見つかりません", url)

    raise CoreWeaveError(
        f"CoreWeave料金ページからB200価格を取得できませんでした (最後のエラー: {last_error})"
    )
