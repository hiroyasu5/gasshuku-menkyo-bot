"""免許合宿ライブ (menkyolive.net) スクレイパー

戦略:
1. application_list.php でAT検索（kibou_syasyu=2）
2. ページネーション対応で全件取得
3. 各リスティングから学校名・入校日・部屋タイプを抽出
"""
from __future__ import annotations

import logging
import re
import time
from datetime import date

from bs4 import BeautifulSoup, Tag

from ..config import TARGET_START_DATE
from ..models import PlanInfo
from .base import BaseScraper

logger = logging.getLogger(__name__)

BASE_URL = "https://menkyolive.net"
SEARCH_URL = f"{BASE_URL}/application_list.php"

TARGET_DATE = date.fromisoformat(TARGET_START_DATE)
# 7月〜9月をスキャン
TARGET_MONTHS = ["2026-07", "2026-08", "2026-09"]

# 都道府県パターン
PREFECTURE_RE = re.compile(
    r"(北海道|青森県|岩手県|宮城県|秋田県|山形県|福島県|"
    r"茨城県|栃木県|群馬県|埼玉県|千葉県|東京都|神奈川県|"
    r"新潟県|富山県|石川県|福井県|山梨県|長野県|岐阜県|"
    r"静岡県|愛知県|三重県|滋賀県|京都府|大阪府|兵庫県|"
    r"奈良県|和歌山県|鳥取県|島根県|岡山県|広島県|山口県|"
    r"徳島県|香川県|愛媛県|高知県|福岡県|佐賀県|長崎県|"
    r"熊本県|大分県|宮崎県|鹿児島県|沖縄県)"
)


class MenkyoLiveScraper(BaseScraper):
    source_name = "menkyo_live"

    def scrape(self) -> list[PlanInfo]:
        plans: list[PlanInfo] = []

        for month in TARGET_MONTHS:
            try:
                month_plans = self._scrape_month(month)
                plans.extend(month_plans)
                logger.info("[menkyo_live] %s: %d プラン取得", month, len(month_plans))
            except Exception as e:
                logger.warning("[menkyo_live] %s 取得失敗: %s", month, e)

        logger.info("[menkyo_live] 合計 %d プラン取得", len(plans))
        return plans

    def _scrape_month(self, month: str) -> list[PlanInfo]:
        plans: list[PlanInfo] = []
        page = 1

        while True:
            params = {
                "search": "1",
                "kibou_month": month,
                "kibou_syasyu": "2",  # AT
                "page": str(page),
            }

            try:
                resp = self._get_with_retry(SEARCH_URL, params=params)
            except Exception as e:
                logger.warning("[menkyo_live] ページ%d 取得失敗: %s", page, e)
                break

            # エンコーディング対応
            content = resp.content
            text = self._decode_content(content, resp.headers.get("content-type", ""))

            soup = BeautifulSoup(text, "lxml")
            page_plans = self._parse_search_results(soup, month)

            if not page_plans:
                break

            plans.extend(page_plans)

            # 次ページがあるか確認
            if not self._has_next_page(soup, page):
                break

            page += 1
            time.sleep(0.5)

        return plans

    def _decode_content(self, content: bytes, content_type: str) -> str:
        # Shift-JIS対応
        for encoding in ["utf-8", "shift_jis", "euc-jp", "cp932"]:
            try:
                return content.decode(encoding)
            except (UnicodeDecodeError, LookupError):
                continue
        return content.decode("utf-8", errors="replace")

    def _parse_search_results(self, soup: BeautifulSoup, month: str) -> list[PlanInfo]:
        plans: list[PlanInfo] = []

        # リスティング要素を検索（li要素のリスト）
        listings = soup.select("ul li")
        if not listings:
            listings = soup.find_all("li")

        for li in listings:
            plan = self._parse_listing(li, month)
            if plan:
                plans.append(plan)

        return plans

    def _parse_listing(self, li: Tag, month: str) -> PlanInfo | None:
        text = li.get_text(separator="\n", strip=True)

        # 学校詳細リンクがあるか確認（detail_{ID}.php）
        detail_link = li.find("a", href=re.compile(r"detail_\d+\.php"))
        if not detail_link:
            return None

        href = detail_link.get("href", "")
        school_id_match = re.search(r"detail_(\d+)\.php", href)
        school_id = school_id_match.group(1) if school_id_match else ""

        # 学校名
        school_name = detail_link.get_text(strip=True)
        if not school_name:
            return None

        # 都道府県
        location = ""
        pref_match = PREFECTURE_RE.search(text)
        if pref_match:
            location = pref_match.group(1)

        # 入校日を抽出（例: "7月26日", "8月1日"）
        date_match = re.search(r"(\d{1,2})月(\d{1,2})日", text)
        if not date_match:
            return None

        entry_month = int(date_match.group(1))
        entry_day = int(date_match.group(2))

        try:
            entry_date = date(TARGET_DATE.year, entry_month, entry_day)
        except ValueError:
            return None

        if entry_date < TARGET_DATE:
            return None

        start_date = entry_date.isoformat()

        # 宿泊日数
        duration: int | None = None
        duration_match = re.search(r"(\d+)日", text)
        if duration_match:
            d = int(duration_match.group(1))
            if 10 <= d <= 30:  # 合宿免許の妥当な日数範囲
                duration = d

        # 部屋タイプ
        room_type = ""
        room_patterns = ["シングル", "ツイン", "トリプル", "ダブル", "相部屋", "レギュラー", "ホテルシングル", "ホテルツイン", "自炊"]
        for pattern in room_patterns:
            if pattern in text:
                room_type = pattern
                break

        # 価格（リスティングに含まれない場合もある）
        price: int | None = None
        price_match = re.search(r"([\d,]+)\s*円", text)
        if price_match:
            try:
                p = int(price_match.group(1).replace(",", ""))
                if 100000 <= p <= 500000:  # 合宿免許の妥当な価格範囲
                    price = p
            except ValueError:
                pass

        detail_url = f"{BASE_URL}/detail_{school_id}.php" if school_id else ""

        return PlanInfo(
            source=self.source_name,
            school_name=school_name,
            location=location,
            start_date=start_date,
            duration_days=duration,
            price_min=price,
            room_type=room_type,
            detail_url=detail_url,
        )

    def _has_next_page(self, soup: BeautifulSoup, current_page: int) -> bool:
        # 「次へ」リンクまたは次のページ番号リンクを探す
        next_link = soup.find("a", string=re.compile(r"次へ|次の|Next"))
        if next_link:
            return True

        # ページ番号リンクで確認
        page_links = soup.find_all("a", href=re.compile(r"page=\d+"))
        for link in page_links:
            href = link.get("href", "")
            m = re.search(r"page=(\d+)", href)
            if m and int(m.group(1)) > current_page:
                return True

        return False
