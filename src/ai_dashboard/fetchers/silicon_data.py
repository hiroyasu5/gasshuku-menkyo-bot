"""Silicon Data の GPUレンタル価格指数を取得する。

各GPUの指数ページ (/products/silicon-index/<gpu>) のFAQに

  "The current B200 rental price is $5.45 per GPU-hour, based on the
   Silicon Data B200 Rental Price Index (ticker SDB200RT)."

という定型文がサーバーサイドレンダリングで含まれる (2026-08 実ページで確認)。
この文から現在値を抜く。取れたGPUだけ返し、全滅なら SiliconDataError。
"""
from __future__ import annotations

import logging
import re

import httpx

from ..config import HTTP_TIMEOUT, USER_AGENT

logger = logging.getLogger(__name__)

BASE = "https://www.silicondata.com/products/silicon-index"

# metric名 -> (ページslug, FAQ内のGPU表記)
INDEX_PAGES = {
    "sd_b200_rental": ("b200", "B200"),
    "sd_h200_rental": ("h200", "H200"),
    "sd_h100_rental": ("h100", "H100"),
}


class SiliconDataError(Exception):
    pass


def _faq_price(html: str, gpu_label: str) -> float | None:
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))
    m = re.search(
        rf"current\s+{re.escape(gpu_label)}\s+rental\s+price\s+is\s*"
        rf"\$\s*([0-9]+(?:\.[0-9]+)?)\s*per\s*GPU-hour",
        text,
        re.IGNORECASE,
    )
    if not m:
        return None
    v = float(m.group(1))
    return v if 0.1 <= v <= 100 else None


def fetch_gpu_rental_index() -> dict[str, float]:
    """{"sd_b200_rental": 5.45, ...} を返す (取得できたGPUのみ)。全滅なら例外"""
    found: dict[str, float] = {}
    last_error: Exception | None = None
    for metric, (slug, label) in INDEX_PAGES.items():
        url = f"{BASE}/{slug}"
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
            logger.info("[SiliconData] %s へのアクセス失敗: %s", url, e)
            continue

        price = _faq_price(resp.text, label)
        if price is not None:
            found[metric] = price
        else:
            logger.info("[SiliconData] %s にFAQ価格文が見つかりません (構造変更?)", url)

    if not found:
        raise SiliconDataError(
            f"Silicon Dataから価格を取得できませんでした (最後のエラー: {last_error})"
        )
    logger.info("[SiliconData] 取得: %s", found)
    return found
