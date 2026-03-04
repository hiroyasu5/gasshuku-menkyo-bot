"""マイコム合宿免許 (gasyukumenkyo.com) スクレイパー

戦略:
1. /school/ から全教習所リストを取得（静的HTML）
2. /school/{ID}/price/ から料金テーブルを取得
3. POST /ajax_price_list.php でカレンダー取得（入校可能日）
4. 入校日 × 部屋タイプ × 該当期間の価格 → PlanInfo組立
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from datetime import date

import httpx
from bs4 import BeautifulSoup

from ..config import (
    HTTP_TIMEOUT,
    MAX_RETRIES,
    RETRY_BACKOFF_BASE,
    TARGET_END_DATE,
    TARGET_START_DATE,
)
from ..models import PlanInfo
from .base import BaseScraper

logger = logging.getLogger(__name__)

BASE_URL = "https://gasyukumenkyo.com"
SCHOOL_LIST_URL = f"{BASE_URL}/school/"
AJAX_URL = f"{BASE_URL}/ajax_price_list.php"

TARGET_DATE = date.fromisoformat(TARGET_START_DATE)
TARGET_END = date.fromisoformat(TARGET_END_DATE)
TARGET_MONTHS = [
    f"{TARGET_DATE.year}-{m:02d}"
    for m in range(TARGET_DATE.month, TARGET_END.month + 1)
]

# 期間パターン: "7/1～9/30" or "7/1〜9/30"
PERIOD_RE = re.compile(r"(\d{1,2})/(\d{1,2})[～〜](\d{1,2})/(\d{1,2})")

# 税込価格パターン
PRICE_RE = re.compile(r"税込[：:]\s*([\d,]+)\s*円")
# フォールバック価格パターン
PRICE_FALLBACK_RE = re.compile(r"([\d,]+)\s*円\s*[（(]税込[)）]")
# さらにシンプルな価格パターン
PRICE_SIMPLE_RE = re.compile(r"([\d,]+)\s*円")


@dataclass
class PriceEntry:
    """料金テーブルの1行分"""
    period_start: date
    period_end: date
    room_type: str
    price: int | None


class MycomScraper(BaseScraper):
    source_name = "mycom"

    def scrape(self) -> list[PlanInfo]:
        plans: list[PlanInfo] = []

        try:
            schools = self._get_school_list()
        except Exception as e:
            logger.error("[mycom] 教習所一覧の取得失敗: %s", e)
            return []

        logger.info("[mycom] %d 校を検出", len(schools))

        for i, (school_id, name, location) in enumerate(schools):
            try:
                school_plans = self._scrape_school(school_id, name, location)
                plans.extend(school_plans)
                if school_plans:
                    logger.info("[mycom] %s: %d プラン取得", name, len(school_plans))
            except Exception as e:
                logger.warning("[mycom] %s (ID:%s) スキップ: %s", name, school_id, e)

            if (i + 1) % 5 == 0:
                time.sleep(1)

        logger.info("[mycom] 合計 %d プラン取得", len(plans))
        return plans

    def _get_school_list(self) -> list[tuple[str, str, str]]:
        """(school_id, school_name, location) のリストを返す"""
        resp = self._get_with_retry(SCHOOL_LIST_URL)
        soup = BeautifulSoup(resp.text, "lxml")

        schools: list[tuple[str, str, str]] = []
        current_pref = ""

        # h3 タグに都道府県、配下に学校リンクがある構造を想定
        for el in soup.find_all(["h3", "a"]):
            if el.name == "h3":
                pref_match = re.search(
                    r"(北海道|青森県|岩手県|宮城県|秋田県|山形県|福島県|"
                    r"茨城県|栃木県|群馬県|埼玉県|千葉県|東京都|神奈川県|"
                    r"新潟県|富山県|石川県|福井県|山梨県|長野県|岐阜県|"
                    r"静岡県|愛知県|三重県|滋賀県|京都府|大阪府|兵庫県|"
                    r"奈良県|和歌山県|鳥取県|島根県|岡山県|広島県|山口県|"
                    r"徳島県|香川県|愛媛県|高知県|福岡県|佐賀県|長崎県|"
                    r"熊本県|大分県|宮崎県|鹿児島県|沖縄県)",
                    el.get_text(),
                )
                if pref_match:
                    current_pref = pref_match.group(1)
                continue

            href = el.get("href", "")
            m = re.search(r"/school/(\d+)/?", href)
            if not m:
                continue

            school_id = m.group(1)
            name = el.get_text(strip=True)

            # リンクに都道府県が含まれていなければ直近のh3から取得
            location = current_pref
            if not location:
                parent = el.parent
                for _ in range(5):
                    if parent is None:
                        break
                    prev_h3 = parent.find_previous("h3")
                    if prev_h3:
                        pref_match = re.search(
                            r"(北海道|青森県|岩手県|宮城県|秋田県|山形県|福島県|"
                            r"茨城県|栃木県|群馬県|埼玉県|千葉県|東京都|神奈川県|"
                            r"新潟県|富山県|石川県|福井県|山梨県|長野県|岐阜県|"
                            r"静岡県|愛知県|三重県|滋賀県|京都府|大阪府|兵庫県|"
                            r"奈良県|和歌山県|鳥取県|島根県|岡山県|広島県|山口県|"
                            r"徳島県|香川県|愛媛県|高知県|福岡県|佐賀県|長崎県|"
                            r"熊本県|大分県|宮崎県|鹿児島県|沖縄県)",
                            prev_h3.get_text(),
                        )
                        if pref_match:
                            location = pref_match.group(1)
                            break
                    parent = parent.parent

            if school_id and name:
                schools.append((school_id, name, location))

        # 重複排除
        seen: set[str] = set()
        unique: list[tuple[str, str, str]] = []
        for s in schools:
            if s[0] not in seen:
                seen.add(s[0])
                unique.append(s)

        return unique

    def _scrape_school(
        self, school_id: str, school_name: str, location: str
    ) -> list[PlanInfo]:
        price_url = f"{BASE_URL}/school/{school_id}/price/"
        try:
            resp = self._get_with_retry(price_url)
        except Exception:
            return []

        soup = BeautifulSoup(resp.text, "lxml")
        price_entries = self._parse_price_table(soup)

        if not price_entries:
            return []

        # 教習期間をページから取得
        duration = self._extract_duration(soup)

        # カレンダーから入校可能日を取得
        entry_dates = self._fetch_entry_dates(school_id)

        detail_url = f"{BASE_URL}/school/{school_id}/"
        return self._build_plans(
            school_name, location, price_entries, entry_dates, detail_url,
            duration,
        )

    def _parse_price_table(self, soup: BeautifulSoup) -> list[PriceEntry]:
        """料金テーブルから PriceEntry リストを返す"""
        entries: list[PriceEntry] = []
        year = TARGET_DATE.year

        # dl 要素（#term_list_dl1 ~ #term_list_dl8）を探す
        for i in range(1, 9):
            dl = soup.find(id=f"term_list_dl{i}")
            if not dl:
                continue

            # dt に期間、dd に部屋+価格
            dt = dl.find("dt")
            if not dt:
                continue

            period_text = dt.get_text(strip=True)
            m = PERIOD_RE.search(period_text)
            if not m:
                continue

            try:
                p_start = date(year, int(m.group(1)), int(m.group(2)))
                p_end = date(year, int(m.group(3)), int(m.group(4)))
            except ValueError:
                continue

            # 対象期間と重ならなければスキップ
            if p_start > TARGET_END or p_end < TARGET_DATE:
                continue

            for dd in dl.find_all("dd"):
                dd_text = dd.get_text(strip=True)

                # 部屋タイプの抽出
                room_type = self._extract_room_type(dd_text)

                # 価格の抽出（税込優先）
                price = self._extract_price(dd_text)

                entries.append(PriceEntry(
                    period_start=p_start,
                    period_end=p_end,
                    room_type=room_type,
                    price=price,
                ))

        # dl 構造が見つからない場合、テーブル要素で再探索
        if not entries:
            entries = self._parse_price_table_fallback(soup)

        return entries

    def _parse_price_table_fallback(self, soup: BeautifulSoup) -> list[PriceEntry]:
        """テーブル構造のフォールバック解析"""
        entries: list[PriceEntry] = []
        year = TARGET_DATE.year

        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            for row in rows:
                text = row.get_text(strip=True)
                m = PERIOD_RE.search(text)
                if not m:
                    continue

                try:
                    p_start = date(year, int(m.group(1)), int(m.group(2)))
                    p_end = date(year, int(m.group(3)), int(m.group(4)))
                except ValueError:
                    continue

                if p_start > TARGET_END or p_end < TARGET_DATE:
                    continue

                room_type = self._extract_room_type(text)
                price = self._extract_price(text)

                entries.append(PriceEntry(
                    period_start=p_start,
                    period_end=p_end,
                    room_type=room_type,
                    price=price,
                ))

        return entries

    def _extract_room_type(self, text: str) -> str:
        """テキストから部屋タイプを抽出"""
        room_patterns = [
            "シングル", "ツイン", "トリプル", "ダブル",
            "相部屋", "レギュラー", "ホテルシングル", "ホテルツイン",
            "自炊シングル", "自炊ツイン", "自炊",
        ]
        for pattern in room_patterns:
            if pattern in text:
                return pattern
        return "不明"

    def _extract_price(self, text: str) -> int | None:
        """テキストから税込価格を抽出"""
        # 税込：NNN,NNN円
        m = PRICE_RE.search(text)
        if m:
            return self._parse_price_value(m.group(1))

        # NNN,NNN円（税込）
        m = PRICE_FALLBACK_RE.search(text)
        if m:
            return self._parse_price_value(m.group(1))

        # シンプルなNNN,NNN円（合宿免許妥当範囲でフィルタ）
        m = PRICE_SIMPLE_RE.search(text)
        if m:
            price = self._parse_price_value(m.group(1))
            if price and 100_000 <= price <= 500_000:
                return price

        return None

    @staticmethod
    def _parse_price_value(price_str: str) -> int | None:
        try:
            return int(price_str.replace(",", ""))
        except ValueError:
            return None

    def _fetch_entry_dates(self, school_id: str) -> list[date]:
        """AJAXでカレンダーから入校可能日を取得。失敗時は空リスト。"""
        all_dates: list[date] = []

        for ym in TARGET_MONTHS:
            try:
                resp = self._post_with_retry(
                    AJAX_URL,
                    data={
                        "in_school_ym": ym,
                        "mt_at": "0",  # AT
                        "plan_code": "",
                        "school_id": school_id,
                    },
                )
                soup = BeautifulSoup(resp.text, "lxml")

                # .entry クラスの日付セルを探す
                for cell in soup.select(".entry"):
                    day_text = cell.get_text(strip=True)
                    day_match = re.search(r"(\d{1,2})", day_text)
                    if day_match:
                        year_month = ym.split("-")
                        try:
                            d = date(
                                int(year_month[0]),
                                int(year_month[1]),
                                int(day_match.group(1)),
                            )
                            if TARGET_DATE <= d <= TARGET_END:
                                all_dates.append(d)
                        except ValueError:
                            pass

                time.sleep(0.3)
            except Exception as e:
                logger.debug("[mycom] カレンダー取得失敗 school=%s ym=%s: %s", school_id, ym, e)

        return sorted(set(all_dates))

    def _post_with_retry(self, url: str, **kwargs) -> httpx.Response:
        """POST版リトライ付きリクエスト"""
        last_exc: Exception | None = None
        for attempt in range(MAX_RETRIES):
            try:
                resp = self.client.post(url, **kwargs)
                resp.raise_for_status()
                return resp
            except (httpx.HTTPStatusError, httpx.TransportError) as e:
                last_exc = e
                wait = RETRY_BACKOFF_BASE ** attempt
                logger.warning(
                    "[%s] POST失敗 (attempt %d/%d): %s - %s秒待機",
                    self.source_name, attempt + 1, MAX_RETRIES, e, wait,
                )
                time.sleep(wait)
        raise last_exc  # type: ignore[misc]

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

    def _build_plans(
        self,
        school_name: str,
        location: str,
        price_entries: list[PriceEntry],
        entry_dates: list[date],
        detail_url: str,
        duration: int | None = None,
    ) -> list[PlanInfo]:
        plans: list[PlanInfo] = []

        if entry_dates:
            # 入校日ごとに該当期間の価格をマッチ
            for entry_date in entry_dates:
                for entry in price_entries:
                    if entry.period_start <= entry_date <= entry.period_end:
                        plans.append(PlanInfo(
                            source=self.source_name,
                            school_name=school_name,
                            location=location,
                            start_date=entry_date.isoformat(),
                            duration_days=duration,
                            price_min=entry.price,
                            room_type=entry.room_type,
                            detail_url=detail_url,
                        ))
        else:
            # フォールバック: 料金テーブルの期間情報のみで生成（menkyo084パターン）
            seen: set[str] = set()
            for entry in price_entries:
                key = f"{entry.room_type}|{entry.price}"
                if key in seen:
                    continue
                seen.add(key)
                plans.append(PlanInfo(
                    source=self.source_name,
                    school_name=school_name,
                    location=location,
                    start_date=TARGET_START_DATE,
                    duration_days=duration,
                    price_min=entry.price,
                    room_type=entry.room_type,
                    detail_url=detail_url,
                ))

        return plans
