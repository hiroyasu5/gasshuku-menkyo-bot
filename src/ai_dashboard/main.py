"""AI Bubble Escape Dashboard - エントリーポイント。

1日1回 GitHub Actions から実行される:
1. FRED (HY/Single-B/IG OAS) を取得しhistoryへマージ (初回は2年分バックフィル)
2. Silicon Data / CoreWeave のGPU価格をスクレイピング (失敗はエラー通知)
3. manual_inputs.yaml (四半期指標・債券利回り) を読み込み
4. 12指標 + 複合判定を評価
5. レベル変化・複合変化・取得エラー・決算リマインダー・週次サマリーをDiscord通知
6. history.json 保存とダッシュボードHTML生成 (Actionsがコミット)
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime, timedelta, timezone

from . import manual as manual_mod
from . import market, notifier, reminders, signals, storage
from .basket import AI_BASKET_V1, TIER1_ESTIMATES
from .config import FRED_BACKFILL_DAYS, FRED_SERIES, WEEKLY_SUMMARY_WEEKDAY
from .dashboard import generate_dashboard
from .fetchers import alphavantage, coreweave, fred, silicon_data, stooq
from .models import Level

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

JST = timezone(timedelta(hours=9))


def _fetch_fred(history: dict, errors: list[str]) -> None:
    for metric, series_id in FRED_SERIES.items():
        existing = storage.get_series(history, metric)
        if existing:
            # 直近30日分だけ取り直す (改定値の反映も兼ねる)
            start = (datetime.now(JST).date() - timedelta(days=30)).isoformat()
        else:
            start = (
                datetime.now(JST).date() - timedelta(days=FRED_BACKFILL_DAYS)
            ).isoformat()
        try:
            for date_str, value in fred.fetch_series_bps(series_id, start):
                storage.merge_daily(history, date_str, {metric: value})
        except Exception as e:
            logger.error("[FRED %s] 取得失敗: %s", series_id, e)
            errors.append(f"FRED {series_id} ({metric}): {e}")


def _fetch_gpu_prices(history: dict, errors: list[str]) -> None:
    today = storage.today_jst()
    try:
        prices = silicon_data.fetch_gpu_rental_index()
        storage.merge_daily(history, today, prices)
    except Exception as e:
        logger.error("[SiliconData] 取得失敗: %s", e)
        errors.append(f"Silicon Data GPU指数: {e}")
    try:
        prices = coreweave.fetch_b200_pricing()
        storage.merge_daily(history, today, prices)
    except Exception as e:
        logger.error("[CoreWeave] 取得失敗: %s", e)
        errors.append(f"CoreWeave B200価格: {e}")


def _fetch_market(history: dict, errors: list[str]) -> None:
    """⑨⑩⑪ Market Early Warning 用のデータ取得とメトリクス保存"""
    today_str = storage.today_jst()
    today = datetime.now(JST).date()

    prices: dict = {}
    try:
        prices = stooq.fetch_basket_prices(AI_BASKET_V1)
        if len(prices) < len(AI_BASKET_V1) * 0.5:
            errors.append(
                f"Stooq株価: {len(prices)}/{len(AI_BASKET_V1)}銘柄しか取得できず"
            )
    except Exception as e:
        logger.error("[Stooq] basket取得失敗: %s", e)
        errors.append(f"Stooq株価basket: {e}")

    try:
        estimates = alphavantage.fetch_estimates(TIER1_ESTIMATES)
        if estimates:
            snap = history.setdefault("estimates", {}).setdefault(today_str, {})
            for ticker, data in estimates.items():
                if data.get("fy1_eps") is not None:
                    snap[ticker] = data["fy1_eps"]
    except Exception as e:
        logger.error("[AlphaVantage] 取得失敗: %s", e)
        errors.append(f"Alpha Vantage EPS consensus: {e}")

    try:
        metrics = market.collect_market_metrics(
            prices, history.get("estimates", {}), len(AI_BASKET_V1), today
        )
        storage.merge_daily(history, today_str, metrics)
        logger.info("[Market] metrics: %s", metrics)
    except Exception as e:
        logger.error("[Market] メトリクス計算失敗: %s", e)
        errors.append(f"Marketメトリクス計算: {e}")


def run() -> None:
    logger.info("=== AI Bubble Dashboard 実行開始 ===")
    history = storage.load_history()
    try:
        manual = manual_mod.load_manual_inputs()
    except Exception as e:
        logger.error("manual_inputs.yaml の読み込みに失敗: %s", e)
        notifier.notify_fetch_errors([f"manual_inputs.yaml 読み込み失敗: {e}"])
        raise

    errors: list[str] = []
    _fetch_fred(history, errors)
    _fetch_gpu_prices(history, errors)
    _fetch_market(history, errors)

    results, composite = signals.evaluate_all(history, manual)

    # レベル変化の検出
    old_levels: dict = history.get("levels", {})
    changes = [
        (r, old_levels.get(r.key, ""))
        for r in results
        if old_levels.get(r.key) not in ("", None)
        and old_levels.get(r.key) != r.level.value
        and r.level is not Level.UNKNOWN
    ]
    old_composite = history.get("composite_level", "")
    composite_changed = old_composite not in ("", None) and old_composite != composite.level.value

    due = reminders.due_reminders(manual, history)

    # --- 通知 ---
    notifier.notify_fetch_errors(errors)
    notifier.notify_level_changes(changes, composite)
    if composite_changed:
        notifier.notify_composite_change(composite, old_composite)
    notifier.notify_reminders(due)
    is_first_run = not old_levels
    if datetime.now(JST).weekday() == WEEKLY_SUMMARY_WEEKDAY or is_first_run:
        notifier.notify_weekly_summary(results, composite)

    # --- 保存・ダッシュボード生成 ---
    history["levels"] = {r.key: r.level.value for r in results}
    history["composite_level"] = composite.level.value
    storage.save_history(history)
    generate_dashboard(history, results, composite)

    logger.info("複合判定: %s", composite.summary)
    logger.info("%s / %s", composite.market_summary, composite.stage_label)
    for r in results:
        logger.info("  [%s] %s: %s (%s)", r.level.value, r.name, r.value_text, r.detail)
    logger.info("=== AI Bubble Dashboard 実行完了 (取得エラー%d件) ===", len(errors))


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        logger.exception("実行失敗: %s", e)
        sys.exit(1)
