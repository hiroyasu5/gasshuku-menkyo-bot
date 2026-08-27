"""AI Bubble Dashboard - Discord通知。

通知ポリシー (毎日スパムしない):
- 指標レベルの変化があった時のみ変化通知
- 複合判定レベルの変化は必ず通知 (EXITシグナルは@here相当の強調)
- 取得エラーは通知 (スクレイパーが壊れたことに気づけるように)
- 決算リマインダー
- 月曜 (JST) は全12指標のサマリーを送る
"""
from __future__ import annotations

import logging

from discord_webhook import DiscordEmbed, DiscordWebhook

from .config import DISCORD_WEBHOOK_URL
from .models import (
    GROUP_LABEL_JA,
    GROUP_ORDER,
    CompositeResult,
    IndicatorResult,
    Level,
    LEVEL_EMOJI,
    LEVEL_LABEL_JA,
)

logger = logging.getLogger(__name__)

LEVEL_COLOR = {
    Level.GREEN: "2ecc71",
    Level.YELLOW: "f1c40f",
    Level.ORANGE: "e67e22",
    Level.RED: "e74c3c",
    Level.UNKNOWN: "95a5a6",
}

MAX_DESCRIPTION_LEN = 3800


def _send(embeds: list[DiscordEmbed]) -> None:
    if not embeds:
        return
    if not DISCORD_WEBHOOK_URL:
        logger.warning("Discord webhook未設定。通知をスキップします")
        for e in embeds:
            logger.info("[Discord skip] %s", e.title)
        return
    for embed in embeds:
        webhook = DiscordWebhook(url=DISCORD_WEBHOOK_URL)
        webhook.add_embed(embed)
        try:
            resp = webhook.execute()
            if resp is not None and hasattr(resp, "status_code"):
                logger.info("Discord送信完了 (status=%s)", resp.status_code)
        except Exception as e:
            logger.error("Discord送信エラー: %s", e)


def _fmt_indicator(r: IndicatorResult) -> str:
    line = f"{LEVEL_EMOJI[r.level]} **{r.name}**: {r.value_text}"
    if r.detail:
        line += f"\n　└ {r.detail}"
    return line


def _badge(value: str) -> str:
    """"green"等はemoji、"Stage 3"等はそのまま"""
    if value in Level._value2member_map_:
        return LEVEL_EMOJI[Level(value)]
    return value


def notify_events(events: list[dict], composite: CompositeResult) -> None:
    """イベントログに記録された色変化をそのままDiscordへ通知する。

    イベントログと通知は同じ detect_events() を源にする (片方だけ鳴らない
    ケースを作らない)。⚪との往復 = データ取得開始/停止も通知される。
    """
    if not events:
        return
    worst = Level.UNKNOWN
    for ev in events:
        to = ev.get("to", "")
        if to in Level._value2member_map_:
            lv = Level(to)
            order = ["unknown", "green", "yellow", "orange", "red"]
            if order.index(lv.value) > order.index(worst.value):
                worst = lv
    embed = DiscordEmbed(
        title="📊 AI Bubble Dashboard: シグナル変化",
        color=LEVEL_COLOR[worst],
    )
    lines = []
    for ev in events:
        line = f"{_badge(str(ev['from']))}→{_badge(str(ev['to']))} **{ev['name']}**"
        if ev.get("value"):
            line += f"\n　{ev['value']}"
        lines.append(line)
    embed.description = "\n".join(lines)[:MAX_DESCRIPTION_LEN]
    embed.set_footer(text=f"複合判定: {composite.summary} / {composite.stage_label}")
    _send([embed])


def notify_composite_change(composite: CompositeResult, old_level: str) -> None:
    title = "📊 AI Bubble Dashboard: 複合判定が変化"
    if composite.exit_signal:
        title = "🚨🚨 AI Bubble Dashboard: EXIT検討シグナル 🚨🚨"
    embed = DiscordEmbed(title=title, color=LEVEL_COLOR[composite.level])
    old = Level(old_level) if old_level in Level._value2member_map_ else Level.UNKNOWN
    lines = [
        f"{LEVEL_EMOJI[old]} {LEVEL_LABEL_JA[old]} → "
        f"{LEVEL_EMOJI[composite.level]} {LEVEL_LABEL_JA[composite.level]}",
        "",
        composite.summary,
        "",
    ]
    for g in GROUP_ORDER:
        lv = composite.group_levels.get(g, Level.UNKNOWN)
        lines.append(f"{LEVEL_EMOJI[lv]} {GROUP_LABEL_JA[g]}")
    embed.description = "\n".join(lines)[:MAX_DESCRIPTION_LEN]
    _send([embed])


def notify_weekly_summary(
    results: list[IndicatorResult], composite: CompositeResult, dashboard_url: str = ""
) -> None:
    embed = DiscordEmbed(
        title=f"{LEVEL_EMOJI[composite.level]} AI Bubble Dashboard 週次サマリー",
        color=LEVEL_COLOR[composite.level],
    )
    lines = [
        f"**複合判定: {composite.summary}**",
        f"**{composite.stage_label}**",
        composite.market_summary,
        f"Data confidence: {composite.confidence_pct}% "
        f"({composite.confirmed_count}/{composite.total_count}指標を実データで確認)",
        "",
    ]
    for g in GROUP_ORDER:
        lines.append(f"__{GROUP_LABEL_JA[g]}__")
        for r in results:
            if r.group == g:
                lines.append(_fmt_indicator(r))
        lines.append("")
    if dashboard_url:
        lines.append(f"📈 ダッシュボード: {dashboard_url}")
    embed.description = "\n".join(lines)[:MAX_DESCRIPTION_LEN]
    _send([embed])


def notify_fetch_errors(errors: list[str]) -> None:
    if not errors:
        return
    embed = DiscordEmbed(
        title="⚠️ AI Bubble Dashboard: データ取得エラー",
        color=LEVEL_COLOR[Level.ORANGE],
        description=(
            "\n".join(f"- {e}" for e in errors)[:MAX_DESCRIPTION_LEN]
            + "\n\nスクレイパー対象サイトの構造変更の可能性。"
            "manual_inputs.yaml のフォールバック値を使用中。"
        ),
    )
    _send([embed])


def notify_reminders(events: list[dict]) -> None:
    if not events:
        return
    embed = DiscordEmbed(
        title="📅 AI Bubble Dashboard: 決算・データ更新リマインダー",
        color=LEVEL_COLOR[Level.UNKNOWN],
    )
    lines = []
    for ev in events:
        when = "今日" if ev["days_until"] == 0 else f"{ev['days_until']}日後"
        stage = f" [{ev['stage']}]" if ev.get("stage") else ""
        lines.append(
            f"**{ev.get('ticker', '?')}** {ev['date']} ({when}){stage}: {ev.get('note', '')}"
        )
    lines.append("\n決算後に `data/ai_dashboard/manual_inputs.yaml` を更新してください。")
    embed.description = "\n".join(lines)[:MAX_DESCRIPTION_LEN]
    _send([embed])
