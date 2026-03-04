"""アイランド (ai-menkyo.jp) スクレイパー

戦略:
1. /area/ から全教習所リストを取得（静的HTML、122校）
2. /area/{県slug}/{学校slug}/price/ から料金カレンダーを取得
3. 申込みリンクの date パラメータ + 近接テキストの価格を抽出
"""
from __future__ import annotations

import logging
import re
import time
from datetime import date

from bs4 import BeautifulSoup

from ..config import TARGET_END_DATE, TARGET_START_DATE
from ..models import PlanInfo
from .base import BaseScraper

logger = logging.getLogger(__name__)

BASE_URL = "https://ai-menkyo.jp"
AREA_URL = f"{BASE_URL}/area/"

TARGET_DATE = date.fromisoformat(TARGET_START_DATE)
TARGET_END = date.fromisoformat(TARGET_END_DATE)

# 県slug → 日本語名マッピング
PREFECTURE_SLUG_MAP: dict[str, str] = {
    "hokkaido": "北海道",
    "aomori": "青森県",
    "iwate": "岩手県",
    "miyagi": "宮城県",
    "akita": "秋田県",
    "yamagata": "山形県",
    "fukushima": "福島県",
    "ibaraki": "茨城県",
    "tochigi": "栃木県",
    "gunma": "群馬県",
    "saitama": "埼玉県",
    "chiba": "千葉県",
    "tokyo": "東京都",
    "kanagawa": "神奈川県",
    "niigata": "新潟県",
    "toyama": "富山県",
    "ishikawa": "石川県",
    "fukui": "福井県",
    "yamanashi": "山梨県",
    "nagano": "長野県",
    "gifu": "岐阜県",
    "shizuoka": "静岡県",
    "aichi": "愛知県",
    "mie": "三重県",
    "shiga": "滋賀県",
    "kyoto": "京都府",
    "osaka": "大阪府",
    "hyogo": "兵庫県",
    "nara": "奈良県",
    "wakayama": "和歌山県",
    "tottori": "鳥取県",
    "shimane": "島根県",
    "okayama": "岡山県",
    "hiroshima": "広島県",
    "yamaguchi": "山口県",
    "tokushima": "徳島県",
    "kagawa": "香川県",
    "ehime": "愛媛県",
    "kochi": "高知県",
    "fukuoka": "福岡県",
    "saga": "佐賀県",
    "nagasaki": "長崎県",
    "kumamoto": "熊本県",
    "oita": "大分県",
    "miyazaki": "宮崎県",
    "kagoshima": "鹿児島県",
    "okinawa": "沖縄県",
}

# 価格パターン
PRICE_RE = re.compile(r"[¥￥]?\s*([\d,]+)\s*円?")
PRICE_YEN_RE = re.compile(r"([\d,]+)\s*円")

# 日付パターン（申込みリンク内）
DATE_PARAM_RE = re.compile(r"date=(\d{4}-\d{2}-\d{2})")

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


class IslandScraper(BaseScraper):
    source_name = "island"

    def scrape(self) -> list[PlanInfo]:
        plans: list[PlanInfo] = []

        try:
            schools = self._get_school_list()
        except Exception as e:
            logger.error("[island] 教習所一覧の取得失敗: %s", e)
            return []

        logger.info("[island] %d 校を検出", len(schools))

        for i, (pref_slug, school_slug, name, location) in enumerate(schools):
            try:
                school_plans = self._scrape_school(
                    pref_slug, school_slug, name, location
                )
                plans.extend(school_plans)
                if school_plans:
                    logger.info(
                        "[island] %s: %d プラン取得", name, len(school_plans)
                    )
            except Exception as e:
                logger.warning(
                    "[island] %s/%s スキップ: %s", pref_slug, school_slug, e
                )

            if (i + 1) % 5 == 0:
                time.sleep(1)

        logger.info("[island] 合計 %d プラン取得", len(plans))
        return plans

    def _get_school_list(
        self,
    ) -> list[tuple[str, str, str, str]]:
        """(pref_slug, school_slug, school_name, location) のリストを返す"""
        resp = self._get_with_retry(AREA_URL)
        soup = BeautifulSoup(resp.text, "lxml")

        schools: list[tuple[str, str, str, str]] = []

        # /area/{pref_slug}/{school_slug}/ パターンのリンクを検索
        for a_tag in soup.find_all(
            "a", href=re.compile(r"/area/[^/]+/[^/]+/?$")
        ):
            href = a_tag.get("href", "")
            m = re.search(r"/area/([^/]+)/([^/]+)/?$", href)
            if not m:
                continue

            pref_slug = m.group(1)
            school_slug = m.group(2)

            # price, area 等のサブページは除外
            if school_slug in ("price", "area", "map", "info", "review"):
                continue

            name = a_tag.get_text(strip=True)
            if not name:
                continue

            # 都道府県名をslugから変換
            location = PREFECTURE_SLUG_MAP.get(pref_slug, "")

            # slugマッピングで解決できない場合、ページテキストから探す
            if not location:
                parent = a_tag.parent
                for _ in range(3):
                    if parent is None:
                        break
                    pref_match = PREFECTURE_RE.search(parent.get_text())
                    if pref_match:
                        location = pref_match.group(1)
                        break
                    parent = parent.parent

            schools.append((pref_slug, school_slug, name, location))

        # 重複排除
        seen: set[str] = set()
        unique: list[tuple[str, str, str, str]] = []
        for s in schools:
            key = f"{s[0]}/{s[1]}"
            if key not in seen:
                seen.add(key)
                unique.append(s)

        return unique

    def _scrape_school(
        self,
        pref_slug: str,
        school_slug: str,
        school_name: str,
        location: str,
    ) -> list[PlanInfo]:
        price_url = f"{BASE_URL}/area/{pref_slug}/{school_slug}/price/"
        try:
            resp = self._get_with_retry(price_url)
        except Exception:
            # priceページがない場合、学校トップページを試す
            try:
                resp = self._get_with_retry(
                    f"{BASE_URL}/area/{pref_slug}/{school_slug}/"
                )
            except Exception:
                return []

        soup = BeautifulSoup(resp.text, "lxml")
        detail_url = f"{BASE_URL}/area/{pref_slug}/{school_slug}/"

        # 教習期間を取得
        duration = self._extract_duration(soup)

        time.sleep(0.5)

        return self._parse_price_calendar(
            soup, school_name, location, detail_url, duration
        )

    @staticmethod
    def _extract_duration(soup: BeautifulSoup) -> int | None:
        """ページテキストから教習期間（日数）を抽出"""
        text = soup.get_text()
        for pattern in [
            r"(?:AT|ＡＴ).*?最短\s*(\d+)\s*日",
            r"最短\s*(\d+)\s*日",
            r"(\d+)\s*泊\s*(\d+)\s*日",
            r"教習期間[：:]\s*(\d+)\s*日",
            r"(\d+)\s*日間",
            r"(\d+)\s*日",
        ]:
            m = re.search(pattern, text)
            if m:
                days = int(m.group(2)) if m.lastindex and m.lastindex >= 2 else int(m.group(1))
                if 10 <= days <= 30:
                    return days
        return None

    def _parse_price_calendar(
        self,
        soup: BeautifulSoup,
        school_name: str,
        location: str,
        detail_url: str,
        duration: int | None = None,
    ) -> list[PlanInfo]:
        plans: list[PlanInfo] = []

        # 方法1: 申込みリンクの date= パラメータから日付+価格を抽出
        plans.extend(
            self._extract_from_links(soup, school_name, location, detail_url, duration)
        )

        # 方法2: カレンダーセルの日付+価格
        if not plans:
            plans.extend(
                self._extract_from_calendar_cells(
                    soup, school_name, location, detail_url, duration
                )
            )

        # 方法3: テキストベースで「M月D日」+近接価格
        if not plans:
            plans.extend(
                self._extract_from_text(
                    soup, school_name, location, detail_url, duration
                )
            )

        return plans

    def _extract_from_links(
        self,
        soup: BeautifulSoup,
        school_name: str,
        location: str,
        detail_url: str,
        duration: int | None = None,
    ) -> list[PlanInfo]:
        """申込みリンクの date パラメータ + 近接テキストから抽出"""
        plans: list[PlanInfo] = []

        for a_tag in soup.find_all("a", href=re.compile(r"date=")):
            href = a_tag.get("href", "")
            dm = DATE_PARAM_RE.search(href)
            if not dm:
                continue

            date_str = dm.group(1)
            try:
                entry_date = date.fromisoformat(date_str)
            except ValueError:
                continue

            if entry_date < TARGET_DATE or entry_date > TARGET_END:
                continue

            # 近接テキストから価格を取得
            price = self._find_nearby_price(a_tag)
            room_type = self._find_nearby_room_type(a_tag)

            plans.append(PlanInfo(
                source=self.source_name,
                school_name=school_name,
                location=location,
                start_date=date_str,
                duration_days=duration,
                price_min=price,
                room_type=room_type,
                detail_url=detail_url,
            ))

        return plans

    def _extract_from_calendar_cells(
        self,
        soup: BeautifulSoup,
        school_name: str,
        location: str,
        detail_url: str,
        duration: int | None = None,
    ) -> list[PlanInfo]:
        """カレンダーセル（td/div）から日付と価格を抽出"""
        plans: list[PlanInfo] = []
        year = TARGET_DATE.year

        # カレンダーのtdセルを探す
        for td in soup.select("td, div.calendar-cell, div.cal-cell"):
            text = td.get_text(strip=True)
            if not text:
                continue

            # 日付 + 価格が同一セルに入っているパターン
            day_match = re.match(r"^(\d{1,2})$", text.split("\n")[0].strip())
            if not day_match:
                continue

            day = int(day_match.group(1))

            # 価格
            price_match = PRICE_YEN_RE.search(text)
            price: int | None = None
            if price_match:
                price = self._parse_price_value(price_match.group(1))

            # 月の特定: カレンダーテーブルのヘッダから推定
            months = self._detect_calendar_months(td)
            for month in months:
                try:
                    entry_date = date(year, month, day)
                except ValueError:
                    continue

                if entry_date < TARGET_DATE or entry_date > TARGET_END:
                    continue

                plans.append(PlanInfo(
                    source=self.source_name,
                    school_name=school_name,
                    location=location,
                    start_date=entry_date.isoformat(),
                    duration_days=duration,
                    price_min=price,
                    room_type="",
                    detail_url=detail_url,
                ))

        return plans

    def _extract_from_text(
        self,
        soup: BeautifulSoup,
        school_name: str,
        location: str,
        detail_url: str,
        duration: int | None = None,
    ) -> list[PlanInfo]:
        """テキストベースで「M月D日」+近接価格を抽出"""
        plans: list[PlanInfo] = []
        year = TARGET_DATE.year
        text = soup.get_text(separator="\n")

        for m in re.finditer(r"(\d{1,2})月(\d{1,2})日", text):
            month = int(m.group(1))
            day = int(m.group(2))

            try:
                entry_date = date(year, month, day)
            except ValueError:
                continue

            if entry_date < TARGET_DATE or entry_date > TARGET_END:
                continue

            # 前後50文字から価格を探す
            start = max(0, m.start() - 50)
            end = min(len(text), m.end() + 50)
            context = text[start:end]

            price: int | None = None
            price_match = PRICE_YEN_RE.search(context)
            if price_match:
                price = self._parse_price_value(price_match.group(1))

            plans.append(PlanInfo(
                source=self.source_name,
                school_name=school_name,
                location=location,
                start_date=entry_date.isoformat(),
                duration_days=duration,
                price_min=price,
                room_type="",
                detail_url=detail_url,
            ))

        # 重複排除（同一日付）
        seen: set[str] = set()
        unique: list[PlanInfo] = []
        for p in plans:
            if p.start_date not in seen:
                seen.add(p.start_date)
                unique.append(p)

        return unique

    def _find_nearby_price(self, element) -> int | None:
        """要素の近接テキストから価格を探す"""
        # リンクテキスト自体
        link_text = element.get_text(strip=True)
        pm = PRICE_RE.search(link_text)
        if pm:
            price = self._parse_price_value(pm.group(1))
            if price and 100_000 <= price <= 500_000:
                return price

        # 親要素のテキスト
        parent = element.parent
        for _ in range(3):
            if parent is None:
                break
            parent_text = parent.get_text(strip=True)
            pm = PRICE_YEN_RE.search(parent_text)
            if pm:
                price = self._parse_price_value(pm.group(1))
                if price and 100_000 <= price <= 500_000:
                    return price
            parent = parent.parent

        return None

    def _find_nearby_room_type(self, element) -> str:
        """要素の近接テキストから部屋タイプを探す"""
        room_patterns = [
            "シングル", "ツイン", "トリプル", "ダブル",
            "相部屋", "レギュラー", "ホテルシングル", "ホテルツイン",
            "自炊",
        ]

        parent = element.parent
        for _ in range(3):
            if parent is None:
                break
            text = parent.get_text(strip=True)
            for pattern in room_patterns:
                if pattern in text:
                    return pattern
            parent = parent.parent

        return ""

    def _detect_calendar_months(self, cell_element) -> list[int]:
        """カレンダーセルが属する月を推定"""
        # テーブル or 親要素のヘッダからM月を探す
        parent = cell_element.find_parent("table")
        if not parent:
            parent = cell_element.find_parent("div")

        if parent:
            header = parent.find(["th", "caption", "h3", "h4"])
            if header:
                m = re.search(r"(\d{1,2})月", header.get_text())
                if m:
                    return [int(m.group(1))]

        # 対象期間の月をすべて返す（フォールバック）
        return list(range(TARGET_DATE.month, TARGET_END.month + 1))

    @staticmethod
    def _parse_price_value(price_str: str) -> int | None:
        try:
            return int(price_str.replace(",", ""))
        except ValueError:
            return None
