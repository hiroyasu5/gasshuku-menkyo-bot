"""合宿免許受付センター (drivers-license.jp) スクレイパー

注: 計画時のドメイン menkyo084.net は存在しないため、
実際のサイト drivers-license.jp を使用。

戦略:
1. /list/ から全教習所スラッグを取得
2. 各 /school/{slug}/ からAT料金・入校日カレンダーを取得
"""
from __future__ import annotations

import logging
import re
import time
from datetime import date

from bs4 import BeautifulSoup

from ..config import TARGET_START_DATE
from ..models import PlanInfo
from .base import BaseScraper

logger = logging.getLogger(__name__)

BASE_URL = "https://drivers-license.jp"
LIST_URL = f"{BASE_URL}/list/"

TARGET_DATE = date.fromisoformat(TARGET_START_DATE)

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


class Menkyo084Scraper(BaseScraper):
    source_name = "drivers_license"

    def scrape(self) -> list[PlanInfo]:
        plans: list[PlanInfo] = []

        try:
            slugs = self._get_school_slugs()
        except Exception as e:
            logger.error("[drivers_license] 教習所一覧の取得失敗: %s", e)
            return []

        logger.info("[drivers_license] %d 校を検出", len(slugs))

        for i, (slug, name, location) in enumerate(slugs):
            try:
                school_plans = self._scrape_school(slug, name, location)
                plans.extend(school_plans)
                if school_plans:
                    logger.info(
                        "[drivers_license] %s: %d プラン取得", name, len(school_plans)
                    )
            except Exception as e:
                logger.warning("[drivers_license] %s スキップ: %s", slug, e)

            if (i + 1) % 10 == 0:
                time.sleep(1)

        logger.info("[drivers_license] 合計 %d プラン取得", len(plans))
        return plans

    def _get_school_slugs(self) -> list[tuple[str, str, str]]:
        """(slug, school_name, location) のリストを返す"""
        resp = self._get_with_retry(LIST_URL)
        soup = BeautifulSoup(resp.text, "lxml")

        schools: list[tuple[str, str, str]] = []

        # /school/{slug}/ パターンのリンクを検索
        for a_tag in soup.find_all("a", href=re.compile(r"/school/[^/]+/?$")):
            href = a_tag.get("href", "")
            m = re.search(r"/school/([^/]+)/?$", href)
            if not m:
                continue

            slug = m.group(1)
            name = a_tag.get_text(strip=True)

            # 親要素から都道府県を探す
            location = ""
            parent = a_tag.parent
            for _ in range(5):
                if parent is None:
                    break
                parent_text = parent.get_text()
                pref_match = PREFECTURE_RE.search(parent_text)
                if pref_match:
                    location = pref_match.group(1)
                    break
                parent = parent.parent

            if slug and name:
                schools.append((slug, name, location))

        # 重複排除
        seen: set[str] = set()
        unique: list[tuple[str, str, str]] = []
        for s in schools:
            if s[0] not in seen:
                seen.add(s[0])
                unique.append(s)

        return unique

    def _scrape_school(self, slug: str, school_name: str, location: str) -> list[PlanInfo]:
        url = f"{BASE_URL}/school/{slug}/"
        resp = self._get_with_retry(url)
        soup = BeautifulSoup(resp.text, "lxml")

        plans: list[PlanInfo] = []

        # 宿泊プラン名の取得
        room_types = self._extract_room_types(soup)

        # 料金テーブルの解析
        price_data = self._extract_prices(soup, room_types)

        # AT入校日カレンダーの解析
        entry_dates = self._extract_entry_dates(soup)

        if not entry_dates:
            # 入校日カレンダーがない場合、料金期間からプランを作成
            for room_type, seasons in price_data.items():
                for season_period, price in seasons:
                    # 対象期間に7月以降が含まれるかチェック
                    if self._period_includes_target(season_period):
                        plans.append(PlanInfo(
                            source=self.source_name,
                            school_name=school_name,
                            location=location,
                            start_date=TARGET_START_DATE,
                            price_min=price,
                            room_type=room_type,
                            detail_url=url,
                        ))
        else:
            # 入校日ごとにプランを作成
            for entry_date_str in entry_dates:
                try:
                    entry_date = date.fromisoformat(entry_date_str)
                except ValueError:
                    continue

                if entry_date < TARGET_DATE:
                    continue

                for room_type, seasons in price_data.items():
                    price = self._get_price_for_date(entry_date, seasons)
                    plans.append(PlanInfo(
                        source=self.source_name,
                        school_name=school_name,
                        location=location,
                        start_date=entry_date_str,
                        price_min=price,
                        room_type=room_type,
                        detail_url=url,
                    ))

        return plans

    def _extract_room_types(self, soup: BeautifulSoup) -> list[str]:
        rooms: list[str] = []
        # hotelPlanTitle3_{N} クラスから部屋タイプ名を取得
        for i in range(1, 10):
            el = soup.select_one(f"div[class*='hotelPlanTitle3_{i}']")
            if el:
                text = el.get_text(strip=True)
                if text:
                    rooms.append(text)
        if not rooms:
            rooms = ["不明"]
        return rooms

    def _extract_prices(
        self, soup: BeautifulSoup, room_types: list[str]
    ) -> dict[str, list[tuple[str, int | None]]]:
        """部屋タイプ → [(期間文字列, 税込価格)] のマッピングを返す"""
        result: dict[str, list[tuple[str, int | None]]] = {}

        for room_idx, room_type in enumerate(room_types, start=1):
            seasons: list[tuple[str, int | None]] = []

            for season_idx in range(1, 12):
                # 期間
                period_el = soup.select_one(f"div[class*='hotelPlan{season_idx}_0']")
                if not period_el:
                    continue
                period = period_el.get_text(strip=True)

                # 価格
                price_el = soup.select_one(
                    f"div[class*='hotelPlanPrice{season_idx}_{room_idx}']"
                )
                price: int | None = None
                if price_el:
                    # 税込価格を優先
                    tax_inc = price_el.select_one(".taxInc")
                    price_text = tax_inc.get_text() if tax_inc else price_el.get_text()
                    price_match = re.search(r"([\d,]+)円", price_text)
                    if price_match:
                        try:
                            price = int(price_match.group(1).replace(",", ""))
                        except ValueError:
                            pass

                seasons.append((period, price))

            result[room_type] = seasons

        return result

    def _extract_entry_dates(self, soup: BeautifulSoup) -> list[str]:
        """AT入校日のリストを YYYY-MM-DD 形式で返す"""
        dates: list[str] = []
        year = TARGET_DATE.year

        # atEntryDate クラスの要素を検索
        for el in soup.select("div[class*='atEntryDate']"):
            text = el.get_text(strip=True)
            # 日付パターン: "7/26", "8/1" など
            for m in re.finditer(r"(\d{1,2})/(\d{1,2})", text):
                month = int(m.group(1))
                day = int(m.group(2))
                try:
                    d = date(year, month, day)
                    dates.append(d.isoformat())
                except ValueError:
                    pass

        # 申込リンクからも日付を抽出
        for a_tag in soup.find_all("a", href=re.compile(r"desired_date1=")):
            href = a_tag.get("href", "")
            m = re.search(r"desired_date1=(\d{4})/(\d{2})/(\d{2})", href)
            if m:
                date_str = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
                if date_str not in dates:
                    dates.append(date_str)

        return sorted(set(dates))

    def _period_includes_target(self, period: str) -> bool:
        """期間文字列が対象日（7/26以降）を含むか"""
        # "7/1～9/30" のようなパターン
        m = re.search(r"(\d{1,2})/(\d{1,2})～(\d{1,2})/(\d{1,2})", period)
        if not m:
            return False
        start_month = int(m.group(1))
        end_month = int(m.group(3))
        end_day = int(m.group(4))

        try:
            end_date = date(TARGET_DATE.year, end_month, end_day)
        except ValueError:
            return False

        return end_date >= TARGET_DATE and start_month <= 9

    def _get_price_for_date(
        self, target: date, seasons: list[tuple[str, int | None]]
    ) -> int | None:
        """指定日に該当するシーズンの価格を返す"""
        for period, price in seasons:
            # "M/D～M/D" パターン
            m = re.search(r"(\d{1,2})/(\d{1,2})～(\d{1,2})/(\d{1,2})", period)
            if not m:
                continue

            try:
                start = date(target.year, int(m.group(1)), int(m.group(2)))
                end = date(target.year, int(m.group(3)), int(m.group(4)))
            except ValueError:
                continue

            if start <= target <= end:
                return price

        return None
