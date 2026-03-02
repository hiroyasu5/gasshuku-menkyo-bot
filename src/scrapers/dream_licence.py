"""合宿免許ドリーム (dream-licence.jp) スクレイパー

戦略:
1. school-sitemap.xml からスクール slug 一覧を取得
2. 各スクールの /school/{slug}/futsusha/ からホテル・部屋IDを取得
3. AJAXエンドポイントを直接呼び出して料金カレンダーを取得
"""
from __future__ import annotations

import logging
import re
import time
from datetime import date
from urllib.parse import parse_qs, urlparse
from xml.etree import ElementTree

from bs4 import BeautifulSoup

from ..config import TARGET_START_DATE
from ..models import PlanInfo
from .base import BaseScraper

logger = logging.getLogger(__name__)

SITEMAP_URL = "https://www.dream-licence.jp/school-sitemap.xml"
SCHOOL_BASE = "https://dream-licence.jp/school"
AJAX_URL = "https://manage.dream-licence.jp/public/ajax/calendarselect"

TARGET_DATE = date.fromisoformat(TARGET_START_DATE)
# 7月〜9月をスキャン
TARGET_MONTHS = [(TARGET_DATE.year, m) for m in (7, 8, 9)]


class DreamLicenceScraper(BaseScraper):
    source_name = "dream_licence"

    def scrape(self) -> list[PlanInfo]:
        plans: list[PlanInfo] = []
        slugs = self._get_school_slugs()
        logger.info("[dream_licence] %d 校を検出", len(slugs))

        for i, slug in enumerate(slugs):
            try:
                school_plans = self._scrape_school(slug)
                plans.extend(school_plans)
                if school_plans:
                    logger.info(
                        "[dream_licence] %s: %d プラン取得", slug, len(school_plans)
                    )
            except Exception as e:
                logger.warning("[dream_licence] %s スキップ: %s", slug, e)
            # レート制限対策
            if (i + 1) % 10 == 0:
                time.sleep(1)

        logger.info("[dream_licence] 合計 %d プラン取得", len(plans))
        return plans

    def _get_school_slugs(self) -> list[str]:
        resp = self._get_with_retry(SITEMAP_URL)
        root = ElementTree.fromstring(resp.content)
        ns = {"ns": "http://www.sitemaps.org/schemas/sitemap/0.9"}

        slugs: set[str] = set()
        for loc in root.findall(".//ns:loc", ns):
            url = loc.text or ""
            # /school/{slug}/futsusha/ パターンを抽出
            m = re.search(r"/school/([^/]+)/futsusha/?$", url)
            if m:
                slugs.add(m.group(1))
            # /school/{slug}/ パターンからも（futsushaが個別にない場合）
            m2 = re.search(r"/school/([^/]+)/?$", url)
            if m2:
                slugs.add(m2.group(1))

        return sorted(slugs)

    def _scrape_school(self, slug: str) -> list[PlanInfo]:
        """1校分の情報を取得"""
        url = f"{SCHOOL_BASE}/{slug}/futsusha/"
        try:
            resp = self._get_with_retry(url)
        except Exception:
            # futsusha ページがない場合はスキップ
            return []

        soup = BeautifulSoup(resp.text, "lxml")

        # 学校名取得
        title_tag = soup.find("h1") or soup.find("title")
        school_name = ""
        if title_tag:
            school_name = title_tag.get_text(strip=True)
            # "○○自動車学校 | 合宿免許ドリーム" のようなパターン
            school_name = school_name.split("|")[0].split("｜")[0].strip()
            school_name = re.sub(r"\s*の合宿免許.*$", "", school_name)

        # 所在地取得
        location = self._extract_location(soup)

        # ホテル・部屋の選択肢を取得
        hotels = self._extract_select_options(soup, ".hotelSelect", "select.hotelSelect", "hotel")
        rooms = self._extract_select_options(soup, ".roomSelect", "select.roomSelect", "room")

        if not hotels or not rooms:
            # ドロップダウンがない場合、デフォルト値で試す
            hotels = [("", "")]
            rooms = [("", "")]

        plans: list[PlanInfo] = []

        for hotel_id, hotel_name in hotels:
            for room_id, room_name in rooms:
                for year, month in TARGET_MONTHS:
                    new_plans = self._fetch_calendar(
                        slug, school_name, location,
                        hotel_id, room_id, room_name or hotel_name,
                        year, month, url,
                    )
                    plans.extend(new_plans)
                    time.sleep(0.3)  # リクエスト間隔

        return plans

    def _extract_location(self, soup: BeautifulSoup) -> str:
        # パンくずリストや住所から都道府県を抽出
        breadcrumb = soup.select_one(".breadcrumb, .c-breadcrumb, nav[aria-label='breadcrumb']")
        if breadcrumb:
            text = breadcrumb.get_text()
            m = re.search(r"(北海道|青森県|岩手県|宮城県|秋田県|山形県|福島県|"
                          r"茨城県|栃木県|群馬県|埼玉県|千葉県|東京都|神奈川県|"
                          r"新潟県|富山県|石川県|福井県|山梨県|長野県|岐阜県|"
                          r"静岡県|愛知県|三重県|滋賀県|京都府|大阪府|兵庫県|"
                          r"奈良県|和歌山県|鳥取県|島根県|岡山県|広島県|山口県|"
                          r"徳島県|香川県|愛媛県|高知県|福岡県|佐賀県|長崎県|"
                          r"熊本県|大分県|宮崎県|鹿児島県|沖縄県)", text)
            if m:
                return m.group(1)

        # ページ全体から都道府県を探す
        full_text = soup.get_text()
        m = re.search(r"所在地[：:]?\s*(.*?[都道府県])", full_text)
        if m:
            return m.group(1).strip()
        return ""

    def _extract_select_options(
        self, soup: BeautifulSoup, css_class: str, selector: str, label: str
    ) -> list[tuple[str, str]]:
        select = soup.select_one(selector)
        if not select:
            # クラス名で再検索
            select = soup.select_one(f"select[class*='{css_class.lstrip('.')}']")
        if not select:
            return []

        options: list[tuple[str, str]] = []
        for opt in select.find_all("option"):
            val = opt.get("value", "")
            if val and val != "--":
                options.append((val, opt.get_text(strip=True)))
        return options

    def _fetch_calendar(
        self,
        slug: str,
        school_name: str,
        location: str,
        hotel_id: str,
        room_id: str,
        room_name: str,
        year: int,
        month: int,
        detail_url: str,
    ) -> list[PlanInfo]:
        params = {
            "school": slug,
            "type": "1",  # AT
            "gender": "",
            "hotel": hotel_id,
            "hotelroom": room_id,
            "year": str(year),
            "month": str(month),
            "plan": "",
        }

        try:
            resp = self._get_with_retry(AJAX_URL, params=params)
        except Exception as e:
            logger.debug("[dream_licence] AJAX失敗 %s/%d-%02d: %s", slug, year, month, e)
            return []

        return self._parse_calendar_html(
            resp.text, school_name, location, room_name, detail_url, year
        )

    def _parse_calendar_html(
        self,
        html: str,
        school_name: str,
        location: str,
        room_name: str,
        detail_url: str,
        year: int,
    ) -> list[PlanInfo]:
        soup = BeautifulSoup(html, "lxml")
        plans: list[PlanInfo] = []

        # エントリーリンクから情報抽出
        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"]
            if "entry" not in href and "date=" not in href:
                continue

            parsed = urlparse(href)
            qs = parse_qs(parsed.query)

            date_str = qs.get("date", [""])[0]
            price_str = qs.get("price", [""])[0]

            if not date_str:
                continue

            try:
                entry_date = date.fromisoformat(date_str)
            except ValueError:
                continue

            if entry_date < TARGET_DATE:
                continue

            # 満室チェック
            link_text = a_tag.get_text(strip=True)
            if "×" in link_text:
                continue

            price: int | None = None
            if price_str:
                try:
                    price = int(price_str)
                except ValueError:
                    pass

            # 卒業日から期間計算
            duration: int | None = None
            # テーブル行から卒業日を探す
            parent_row = a_tag.find_parent("tr")
            if parent_row:
                cells = parent_row.find_all("td")
                for cell in cells:
                    cell_text = cell.get_text(strip=True)
                    m = re.match(r"(\d{2})/(\d{2})", cell_text)
                    if m:
                        grad_month = int(m.group(1))
                        grad_day = int(m.group(2))
                        try:
                            grad_date = date(year, grad_month, grad_day)
                            if grad_date > entry_date:
                                duration = (grad_date - entry_date).days
                        except ValueError:
                            pass

            plans.append(PlanInfo(
                source=self.source_name,
                school_name=school_name or "不明",
                location=location,
                start_date=date_str,
                duration_days=duration,
                price_min=price,
                room_type=room_name,
                detail_url=detail_url,
            ))

        return plans
