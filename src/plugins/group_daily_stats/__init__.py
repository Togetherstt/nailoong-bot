from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Optional

from nonebot import get_driver, logger, on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageEvent
from nonebot.rule import to_me
from src.plugins.utils.group_scope import is_homophone_group_enabled

from .service import (
    disable_stats,
    enable_stats,
    flush_daily_stats,
    get_group_stats_snapshot,
    is_stats_enabled,
    record_message_interaction,
)
from .store import GroupStatsSnapshot, InteractionStats, extract_interaction_stats


stats_command = on_message(rule=to_me(), priority=10, block=False)
stats_listener = on_message(priority=50, block=False)
try:
    driver = get_driver()
except ValueError:
    driver = None
_scheduler_task: asyncio.Task[None] | None = None
EXCLUDED_STATS_MEMBER_NAMES = {"ARClass", "Yurisaki"}


@stats_command.handle()
async def handle_stats_command(bot: Bot, event: MessageEvent) -> None:
    command = event.get_message().extract_plain_text().strip()
    if command not in {"/开启统计", "/结束统计", "/查看当前统计"}:
        return

    if not isinstance(event, GroupMessageEvent):
        await bot.send(event, "统计功能仅支持群聊使用。", reply_message=True)
        return

    if command == "/开启统计":
        if not _is_admin_user(event):
            await bot.send(event, "您没有管理权限，无法开启", reply_message=True)
            return
        enabled = await enable_stats(str(event.group_id))
        if enabled:
            await bot.send(
                event,
                "本群统计已开启。之后会持续统计，直到管理员执行 `@机器人 /结束统计`。",
                reply_message=True,
            )
            return
        await bot.send(event, "本群统计已经处于开启状态。", reply_message=True)
        return

    if command == "/结束统计":
        if not _is_admin_user(event):
            await bot.send(event, "您没有管理权限，无法结束", reply_message=True)
            return
        disabled = await disable_stats(str(event.group_id))
        if disabled:
            await bot.send(event, "本群统计已结束并清空当前统计数据。", reply_message=True)
            return
        await bot.send(event, "本群当前未开启统计。", reply_message=True)
        return

    if not await is_stats_enabled(str(event.group_id)):
        await bot.send(event, "本群当前未开启统计。", reply_message=True)
        return

    snapshot = await get_group_stats_snapshot(str(event.group_id))
    message = await _render_stats_message(
        bot=bot,
        group_id=str(event.group_id),
        snapshot=snapshot,
        title="今日当前统计",
    )
    await bot.send(event, message, reply_message=True)


@stats_listener.handle()
async def handle_stats_listener(bot: Bot, event: MessageEvent) -> None:
    if not isinstance(event, GroupMessageEvent):
        return
    if not await is_stats_enabled(str(event.group_id)):
        return

    interaction = extract_interaction_stats(event.message, self_id=str(bot.self_id))
    interaction = await _filter_excluded_member_interaction(
        bot=bot,
        group_id=str(event.group_id),
        sender_id=str(event.user_id),
        interaction=interaction,
    )
    if interaction.is_empty():
        return

    await record_message_interaction(
        group_id=str(event.group_id),
        sender_id=str(event.user_id),
        interaction=interaction,
    )


if driver is not None:
    @driver.on_startup
    async def _start_stats_scheduler() -> None:
        global _scheduler_task
        if _scheduler_task is None or _scheduler_task.done():
            _scheduler_task = asyncio.create_task(_stats_scheduler_loop())


    @driver.on_shutdown
    async def _stop_stats_scheduler() -> None:
        global _scheduler_task
        if _scheduler_task is None:
            return
        _scheduler_task.cancel()
        try:
            await _scheduler_task
        except asyncio.CancelledError:
            pass
        _scheduler_task = None


async def _stats_scheduler_loop() -> None:
    while True:
        try:
            await asyncio.sleep(_seconds_until_next_daily_flush())
            await _flush_and_broadcast_daily_stats()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Daily group stats scheduler failed")


async def _flush_and_broadcast_daily_stats() -> None:
    if driver is None:
        return

    flush_result = await flush_daily_stats()
    if not flush_result.snapshots:
        return

    bots = list(driver.bots.values())
    if not bots:
        return
    bot = bots[0]

    for group_id, snapshot in flush_result.snapshots.items():
        try:
            message = await _render_stats_message(
                bot=bot,
                group_id=group_id,
                snapshot=snapshot,
                title=f"{snapshot.date} 统计",
            )
            await bot.send_group_msg(group_id=int(group_id), message=message)
        except Exception:
            logger.exception("Failed to broadcast daily group stats summary")


async def _render_stats_message(
    *,
    bot: Bot,
    group_id: str,
    snapshot: GroupStatsSnapshot,
    title: str,
) -> str:
    lines = [title]
    lines.append(f"统计日期：{snapshot.date}")
    lines.append(
        await _format_rank_line(
            bot=bot,
            group_id=group_id,
            label="1. 被艾特次数最多的人",
            counter=snapshot.mentions_received,
        )
    )
    lines.append(
        await _format_rank_line(
            bot=bot,
            group_id=group_id,
            label="2. 贴表情最多的人",
            counter=snapshot.stickers_sent,
        )
    )
    lines.append(
        await _format_rank_line(
            bot=bot,
            group_id=group_id,
            label="3. 被贴表情次数最多的人",
            counter=snapshot.sticker_targets,
        )
    )

    if is_homophone_group_enabled(group_id):
        lines.append(
            await _format_rank_line(
                bot=bot,
                group_id=group_id,
                label="4. /盒 次数最多的人",
                counter=snapshot.homophone_used,
            )
        )
        lines.append(
            await _format_rank_line(
                bot=bot,
                group_id=group_id,
                label="5. 被 /盒 次数最多的人",
                counter=snapshot.homophone_boxed,
            )
        )

    return "\n".join(lines)


async def _format_rank_line(
    *,
    bot: Bot,
    group_id: str,
    label: str,
    counter: dict[str, int],
) -> str:
    if not counter:
        return f"{label}：暂无数据"

    top_count = max(counter.values())
    top_user_ids = sorted(
        [user_id for user_id, count in counter.items() if count == top_count],
        key=lambda user_id: (len(user_id), user_id),
    )
    names = [await _resolve_member_name(bot, group_id, user_id) for user_id in top_user_ids]
    return f"{label}：{'、'.join(names)}（{top_count} 次）"


async def _resolve_member_name(bot: Bot, group_id: str, user_id: str) -> str:
    try:
        info = await bot.get_group_member_info(
            group_id=int(group_id),
            user_id=int(user_id),
            no_cache=False,
        )
    except Exception:
        return user_id
    card = str(info.get("card", "")).strip()
    nickname = str(info.get("nickname", "")).strip()
    return card or nickname or user_id


def _seconds_until_next_daily_flush(now: Optional[datetime] = None) -> float:
    resolved_now = now.astimezone() if now is not None else datetime.now().astimezone()
    target = resolved_now.replace(hour=23, minute=59, second=59, microsecond=0)
    if resolved_now >= target:
        target = target + timedelta(days=1)
    return max((target - resolved_now).total_seconds(), 1.0)


def _is_admin_user(event: MessageEvent) -> bool:
    admin_qq = getattr(get_driver().config, "admin_qq", None)
    if admin_qq is None:
        return False
    return str(event.user_id) == str(admin_qq).strip()


async def _filter_excluded_member_interaction(
    *,
    bot: Bot,
    group_id: str,
    sender_id: str,
    interaction: InteractionStats,
) -> InteractionStats:
    if interaction.is_empty():
        return interaction

    sender_is_excluded = await _is_excluded_member(bot, group_id, sender_id)
    filtered_mentions = list(interaction.mention_targets)
    filtered_sticker_targets = list(interaction.sticker_targets)

    if sender_is_excluded:
        filtered_mentions = []
        filtered_sticker_count = 0
        filtered_sticker_targets = []
    else:
        filtered_sticker_count = interaction.sticker_count
        excluded_targets = {
            target
            for target in set(interaction.mention_targets)
            if await _is_excluded_member(bot, group_id, target)
        }
        if excluded_targets:
            filtered_mentions = [
                target for target in interaction.mention_targets if target not in excluded_targets
            ]
            filtered_sticker_targets = [
                target for target in interaction.sticker_targets if target not in excluded_targets
            ]

    return InteractionStats(
        mention_targets=filtered_mentions,
        sticker_count=filtered_sticker_count,
        sticker_targets=filtered_sticker_targets,
    )


async def _is_excluded_member(bot: Bot, group_id: str, user_id: str) -> bool:
    try:
        info = await bot.get_group_member_info(
            group_id=int(group_id),
            user_id=int(user_id),
            no_cache=False,
        )
    except Exception:
        return False

    card = str(info.get("card", "")).strip()
    nickname = str(info.get("nickname", "")).strip()
    return card in EXCLUDED_STATS_MEMBER_NAMES or nickname in EXCLUDED_STATS_MEMBER_NAMES
