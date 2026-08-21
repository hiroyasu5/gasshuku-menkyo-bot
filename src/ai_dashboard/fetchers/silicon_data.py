"""Silicon Data の GPUレンタル価格指数を取得する (実験的)。

公式APIは有償のため、公開ページに掲載されている指数値をベストエフォートで
拾う。サイト構造は不明・変更されうるので、以下の戦略を順に試す:

1. 候補URLを順にフェッチ
2. ページ内の JSON (__NEXT_DATA__ 等) と可視テキストの両方を対象に、
   "H100" / "H200" / "B200" / "B300" の近傍にある $X.XX パターンを探す

1つも取れなければ SiliconDataError を送出し、呼び出し側は
manual_inputs.yaml のフォールバック値を使う。
"""
from __future__ import annotations

import logging
import re

import httpx

from ..config import HTTP_TIMEOUT, USER_AGENT

logger = logging.getLogger(__name__)

CANDIDATE_URLS = [
    "https://silicondata.com/",
    "https://www.silicondata.com/",
    "https://silicondata.com/indices",
    "https://www.silicondata.com/indices",
    "https://silicondata.com/gpu-rental-index",
]

# metric名 -> ページ内で探すGPU名パターン
GPU_PATTERNS = {
    "sd_b300_rental": r"(?:GB300|B300)",
    "sd_b200_rental": r"(?:GB200|B200)",
    "sd_h200_rental": r"H200",
    "sd_h100_rental": r"H100",
}

# GPU名の近傍 (前後200文字) に現れる $x.xx を価格候補とみなす
PRICE_NEAR = r"\$\s*([0-9]{1,2}(?:\.[0-9]{1,3})?)"


class SiliconDataError(Exception):
    pass


def _extract_prices(text: str) -> dict[str, float]:
    found: dict[str, float] = {}
    for metric, gpu_pat in GPU_PATTERNS.items():
        prices: list[float] = []
        for m in re.finditer(gpu_pat, text, re.IGNORECASE):
            window = text[m.end(): m.end() + 200] + " " + text[max(0, m.start() - 200): m.start()]
            for pm in re.finditer(PRICE_NEAR, window):
                v = float(pm.group(1))
                # GPU-hour単価として妥当な範囲のみ (誤検出の排除)
                if 0.2 <= v <= 30:
                    prices.append(v)
        if prices:
            # 同一GPUで複数出た場合は最小値 (Neo-cloud側の指数を優先する意図)
            found[metric] = min(prices)
    return found


def fetch_gpu_rental_index() -> dict[str, float]:
    """{"sd_b200_rental": 5.74, ...} を返す。全滅なら例外"""
    last_error: Exception | None = None
    for url in CANDIDATE_URLS:
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

        html = resp.text
        # 可視テキスト + script内JSONの両方を対象にするため、タグだけ除去した版と生HTML版の両方を見る
        stripped = re.sub(r"<[^>]+>", " ", html)
        found = _extract_prices(stripped)
        if not found:
            found = _extract_prices(html)
        if found:
            logger.info("[SiliconData] %s から取得: %s", url, found)
            return found
        logger.info("[SiliconData] %s に価格パターンが見つかりません", url)

    raise SiliconDataError(
        f"Silicon Dataから価格を取得できませんでした (最後のエラー: {last_error})"
    )
