from __future__ import annotations

import logging
from typing import Optional

from discord_webhook import DiscordWebhook, DiscordEmbed

from .config import DISCORD_WEBHOOK_URL
from .models import PlanInfo, DiffResult

logger = logging.getLogger(__name__)

COLOR_GREEN = "2ecc71"
COLOR_ORANGE = "e67e22"
COLOR_RED = "e74c3c"
COLOR_GREY = "95a5a6"
COLOR_BLUE = "3498db"

MAX_EMBEDS_PER_MESSAGE = 10


def _send_webhook(embeds: list[DiscordEmbed]) -> None:
    if not DISCORD_WEBHOOK_URL:
        logger.warning("DISCORD_WEBHOOK_URL が未設定です。通知をスキップします。")
        for e in embeds:
            logger.info("[Discord skip] %s", e.title)
        return

    for i in range(0, len(embeds), MAX_EMBEDS_PER_MESSAGE):
        chunk = embeds[i : i + MAX_EMBEDS_PER_MESSAGE]
        webhook = DiscordWebhook(url=DISCORD_WEBHOOK_URL)
        for embed in chunk:
            webhook.add_embed(embed)
        try:
            resp = webhook.execute()
            if resp and hasattr(resp, "status_code"):
                logger.info("Discord送信完了 (status=%s)", resp.status_code)
        except Exception as e:
            logger.error("Discord送信エラー: %s", e)


def _make_plan_embed(plan: PlanInfo, color: str = COLOR_GREEN) -> DiscordEmbed:
    embed = DiscordEmbed(
        title=f"🏫 {plan.school_name}（{plan.location}）",
        color=color,
    )
    embed.add_embed_field(name="入校日", value=plan.format_date(), inline=True)
    embed.add_embed_field(name="期間", value=plan.format_duration(), inline=True)
    embed.add_embed_field(name="費用", value=plan.format_price(), inline=True)
    if plan.room_type:
        embed.add_embed_field(name="部屋", value=plan.room_type, inline=True)
    embed.add_embed_field(name="出典", value=plan.source, inline=True)
    if plan.detail_url:
        embed.add_embed_field(name="詳細", value=f"[リンク]({plan.detail_url})", inline=True)
    return embed


def _make_price_change_embed(plan: PlanInfo, old_price: int) -> DiscordEmbed:
    diff = plan.price_min - old_price
    arrow = "↑" if diff > 0 else "↓"
    embed = DiscordEmbed(
        title=f"💰 {plan.school_name}（{plan.location}）- 価格変動",
        color=COLOR_ORANGE,
    )
    embed.add_embed_field(
        name="価格変動",
        value=f"¥{old_price:,} → ¥{plan.price_min:,} ({arrow}¥{abs(diff):,})",
        inline=False,
    )
    embed.add_embed_field(name="入校日", value=plan.format_date(), inline=True)
    if plan.room_type:
        embed.add_embed_field(name="部屋", value=plan.room_type, inline=True)
    return embed


def _make_removed_embed(plan: PlanInfo) -> DiscordEmbed:
    embed = DiscordEmbed(
        title=f"❌ {plan.school_name}（{plan.location}）- 掲載終了",
        color=COLOR_RED,
    )
    embed.add_embed_field(name="入校日", value=plan.format_date(), inline=True)
    embed.add_embed_field(name="費用", value=plan.format_price(), inline=True)
    if plan.room_type:
        embed.add_embed_field(name="部屋", value=plan.room_type, inline=True)
    return embed


def notify_diff(diff: DiffResult) -> None:
    embeds: list[DiscordEmbed] = []

    has_updates = diff.new_plans or diff.price_changes or diff.removed_plans

    if has_updates:
        parts = []
        if diff.new_plans:
            parts.append(f"新着 {len(diff.new_plans)}件")
        if diff.price_changes:
            parts.append(f"価格変動 {len(diff.price_changes)}件")
        if diff.removed_plans:
            parts.append(f"掲載終了 {len(diff.removed_plans)}件")

        summary = DiscordEmbed(
            title="🚗 合宿免許情報",
            description=f"{'／'.join(parts)}（監視中: {diff.total_active}件）",
            color=COLOR_GREEN,
        )
        embeds.append(summary)

        for plan in diff.new_plans:
            embeds.append(_make_plan_embed(plan))

        for plan, old_price in diff.price_changes:
            embeds.append(_make_price_change_embed(plan, old_price))

        for plan in diff.removed_plans:
            embeds.append(_make_removed_embed(plan))
    else:
        summary = DiscordEmbed(
            title="🚗 合宿免許情報 - 新着なし",
            description=f"現在 {diff.total_active}件のプランを監視中",
            color=COLOR_GREY,
        )
        embeds.append(summary)

    _send_webhook(embeds)


def notify_error(message: str) -> None:
    embed = DiscordEmbed(
        title="⚠️ 合宿免許Bot エラー",
        description=message,
        color=COLOR_RED,
    )
    _send_webhook([embed])
