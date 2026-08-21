"""CoreWeave 料金ページから B200 の on-demand / spot 掲載価格を取得する。

料金表はタグ除去後に以下の形の連続テキストになる (2026-08 実ページで確認):

  NVIDIA HGX B200 On-Demand Price: $68.80 / Hour Spot Price: $34.11 / Hour
  Inference Single CPU Price: $8.60 / Hour ...

この "On-Demand Price / Spot Price" のペアを正確に抜く。
複数リージョンの表があるため最初のマッチ (NORTH AMERICA) を使う。
"Inference Single GPU Price" を spot と誤検出しないこと (過去に発生)。

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

B200_ROW_PAT = re.compile(
    r"NVIDIA\s+HGX\s+B200\s+"
    r"On-Demand\s+Price:\s*\$\s*([0-9][0-9.,]*)\s*/\s*Hour\s+"
    r"Spot\s+Price:\s*\$\s*([0-9][0-9.,]*)\s*/\s*Hour",
    re.IGNORECASE,
)


class CoreWeaveError(Exception):
    pass


def _parse(html: str) -> dict[str, float] | None:
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))
    m = B200_ROW_PAT.search(text)
    if not m:
        return None
    od = float(m.group(1).replace(",", ""))
    spot = float(m.group(2).replace(",", ""))
    # 8-GPUノード時間単価として妥当性チェック
    if not (5 <= od <= 500 and 1 <= spot <= 500):
        return None
    return {"cw_b200_od_node": od, "cw_b200_spot_node": spot}


def fetch_b200_pricing() -> dict[str, float]:
    """{"cw_b200_od_node": 68.80, "cw_b200_spot_node": 34.11} を返す"""
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

        found = _parse(resp.text)
        if found:
            logger.info("[CoreWeave] %s から取得: %s", url, found)
            return found
        logger.info("[CoreWeave] %s にB200価格行が見つかりません (ページ構造変更?)", url)

    raise CoreWeaveError(
        f"CoreWeave料金ページからB200価格を取得できませんでした (最後のエラー: {last_error})"
    )
