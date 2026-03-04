"""合宿免許情報 Discord通知Bot - エントリーポイント"""
from __future__ import annotations

import logging
import sys

from .config import DISCORD_WEBHOOK_URL
from .models import PlanInfo
from .notifier import notify_diff, notify_error
from .scrapers.dream_licence import DreamLicenceScraper
from .scrapers.island import IslandScraper
from .scrapers.menkyo_live import MenkyoLiveScraper
from .scrapers.menkyo084 import Menkyo084Scraper
from .scrapers.mycom import MycomScraper
from .storage import compute_diff, load_history, save_history, update_history

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

SCRAPERS = [
    MenkyoLiveScraper,
    DreamLicenceScraper,
    Menkyo084Scraper,
    MycomScraper,
    IslandScraper,
]


def run() -> None:
    logger.info("=== 合宿免許Bot 実行開始 ===")

    if not DISCORD_WEBHOOK_URL:
        logger.warning("DISCORD_WEBHOOK_URL が未設定です。通知はスキップされます。")

    all_plans: list[PlanInfo] = []
    errors: list[str] = []

    for scraper_cls in SCRAPERS:
        name = scraper_cls.source_name
        logger.info("[%s] スクレイピング開始...", name)
        try:
            with scraper_cls() as scraper:
                plans = scraper.scrape()
                all_plans.extend(plans)
                logger.info("[%s] %d プラン取得完了", name, len(plans))
        except Exception as e:
            msg = f"[{name}] スクレイピング失敗: {e}"
            logger.error(msg)
            errors.append(msg)

    logger.info("全サイト合計: %d プラン取得", len(all_plans))

    # 差分検出
    old_history = load_history()
    diff = compute_diff(old_history, all_plans)

    logger.info(
        "差分: 新着=%d, 価格変動=%d, 掲載終了=%d, 監視中=%d",
        len(diff.new_plans),
        len(diff.price_changes),
        len(diff.removed_plans),
        diff.total_active,
    )

    # Discord通知
    notify_diff(diff)

    # エラーがあれば通知
    if errors:
        notify_error("\n".join(errors))

    # 履歴更新
    if all_plans:
        new_history = update_history(old_history, all_plans)
        save_history(new_history)
        logger.info("履歴ファイルを更新しました")
    else:
        logger.warning("プランが取得できなかったため、履歴は更新しません")

    logger.info("=== 合宿免許Bot 実行完了 ===")


def main() -> None:
    try:
        run()
    except Exception as e:
        logger.exception("予期せぬエラー: %s", e)
        try:
            notify_error(f"予期せぬエラー: {e}")
        except Exception:
            pass
        # GitHub Actionsを失敗にしないためexit 0
        sys.exit(0)


if __name__ == "__main__":
    main()
