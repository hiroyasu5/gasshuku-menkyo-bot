from __future__ import annotations

import logging
import unicodedata

from discord_webhook import DiscordWebhook, DiscordEmbed

from .config import DISCORD_WEBHOOK_URL
from .models import PlanInfo, DiffResult

logger = logging.getLogger(__name__)

COLOR_GREEN = "2ecc71"
COLOR_ORANGE = "e67e22"
COLOR_RED = "e74c3c"
COLOR_GREY = "95a5a6"

MAX_EMBEDS_PER_MESSAGE = 10
# Discord embed 全体の上限は6000文字。title等を考慮して余裕を持たせる
MAX_DESCRIPTION_LEN = 3800


def _send_webhook(embeds: list[DiscordEmbed]) -> None:
    if not DISCORD_WEBHOOK_URL:
        logger.warning("DISCORD_WEBHOOK_URL が未設定です。通知をスキップします。")
        for e in embeds:
            logger.info("[Discord skip] %s", e.title)
        return

    for embed in embeds:
        webhook = DiscordWebhook(url=DISCORD_WEBHOOK_URL)
        webhook.add_embed(embed)
        try:
            resp = webhook.execute()
            if resp and hasattr(resp, "status_code"):
                logger.info("Discord送信完了 (status=%s)", resp.status_code)
        except Exception as e:
            logger.error("Discord送信エラー: %s", e)


# ── 表組みユーティリティ ──────────────────────────────────


def _display_width(s: str) -> int:
    """全角=2, 半角=1 で表示幅を計算"""
    w = 0
    for ch in s:
        eaw = unicodedata.east_asian_width(ch)
        w += 2 if eaw in ("F", "W") else 1
    return w


def _pad(s: str, width: int) -> str:
    """表示幅 width になるよう半角スペースで右パディング"""
    return s + " " * (width - _display_width(s))


def _truncate(s: str, max_width: int) -> str:
    """表示幅 max_width に収まるよう切り詰める"""
    w = 0
    result: list[str] = []
    for ch in s:
        cw = 2 if unicodedata.east_asian_width(ch) in ("F", "W") else 1
        if w + cw > max_width - 1:
            result.append("…")
            break
        result.append(ch)
        w += cw
    return "".join(result)


def _build_table(headers: list[str], rows: list[list[str]]) -> str:
    """コードブロック付きのテーブル文字列を生成"""
    # 各列の最大表示幅を計算
    col_widths = [_display_width(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            col_widths[i] = max(col_widths[i], _display_width(cell))

    def _format_row(cells: list[str]) -> str:
        parts = [_pad(cell, col_widths[i]) for i, cell in enumerate(cells)]
        return " | ".join(parts)

    lines = [_format_row(headers)]
    lines.append("-+-".join("-" * w for w in col_widths))
    for row in rows:
        lines.append(_format_row(row))

    return "```\n" + "\n".join(lines) + "\n```"


# ── 表の行データ作成 ─────────────────────────────────────


def _plan_to_row(plan: PlanInfo) -> list[str]:
    """PlanInfo → テーブルの1行"""
    name = _truncate(plan.school_name, 20)
    loc = plan.location[:3] if plan.location else "-"  # 県名は3文字で十分
    source = plan.source
    if plan.room_type:
        source += f"({plan.room_type})"
    return [
        name,
        loc,
        plan.format_date(),
        plan.format_duration(),
        plan.format_price(),
        source,
    ]


def _price_change_to_row(plan: PlanInfo, old_price: int) -> list[str]:
    """価格変動 → テーブルの1行"""
    name = _truncate(plan.school_name, 20)
    loc = plan.location[:3] if plan.location else "-"
    diff = plan.price_min - old_price
    arrow = "↑" if diff > 0 else "↓"
    change = f"¥{old_price:,}→¥{plan.price_min:,}({arrow}¥{abs(diff):,})"
    source = plan.source
    if plan.room_type:
        source += f"({plan.room_type})"
    return [name, loc, plan.format_date(), change, source]


# ── テーブルを embed description に収まるよう分割 ────────


def _split_table_embeds(
    title: str,
    headers: list[str],
    rows: list[list[str]],
    color: str,
) -> list[DiscordEmbed]:
    """行が多い場合、description 上限に合わせて複数 embed に分割"""
    embeds: list[DiscordEmbed] = []
    chunk: list[list[str]] = []

    for row in rows:
        chunk.append(row)
        table_str = _build_table(headers, chunk)
        # コードブロック含め上限チェック
        if len(table_str) > MAX_DESCRIPTION_LEN - 100:
            # 最後の1行を除いて確定
            chunk.pop()
            embeds.append(DiscordEmbed(
                title=title,
                description=_build_table(headers, chunk),
                color=color,
            ))
            chunk = [row]

    if chunk:
        embeds.append(DiscordEmbed(
            title=title,
            description=_build_table(headers, chunk),
            color=color,
        ))

    return embeds


# ── メイン通知 ───────────────────────────────────────────


NEW_HEADERS = ["教習所", "県", "入校", "期間", "費用", "出典"]
CHANGE_HEADERS = ["教習所", "県", "入校", "価格変動", "出典"]


def notify_diff(diff: DiffResult) -> None:
    embeds: list[DiscordEmbed] = []

    has_updates = diff.new_plans or diff.price_changes

    if has_updates:
        parts = []
        if diff.new_plans:
            parts.append(f"新着 {len(diff.new_plans)}件")
        if diff.price_changes:
            parts.append(f"価格変動 {len(diff.price_changes)}件")

        summary = DiscordEmbed(
            title="🚗 合宿免許情報",
            description=f"{'／'.join(parts)}（監視中: {diff.total_active}件）",
            color=COLOR_GREEN,
        )
        embeds.append(summary)

        # 新着プランを入校日順にソートして表にまとめる
        if diff.new_plans:
            sorted_new = sorted(diff.new_plans, key=lambda p: p.start_date)
            rows = [_plan_to_row(p) for p in sorted_new]
            embeds.extend(_split_table_embeds(
                "🏫 新着プラン", NEW_HEADERS, rows, COLOR_GREEN,
            ))

        # 価格変動を入校日順にソートして表にまとめる
        if diff.price_changes:
            sorted_changes = sorted(diff.price_changes, key=lambda x: x[0].start_date)
            rows = [_price_change_to_row(p, old) for p, old in sorted_changes]
            embeds.extend(_split_table_embeds(
                "💰 価格変動", CHANGE_HEADERS, rows, COLOR_ORANGE,
            ))
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
