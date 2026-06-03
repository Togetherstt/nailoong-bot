from __future__ import annotations

import asyncio
import time
from typing import Optional

from nonebot import on_message
from nonebot.adapters.onebot.v11 import Bot, MessageEvent
from nonebot.rule import to_me

from .store import (
    InitialsStore,
    extract_chinese_text,
    find_homophone_results,
    is_valid_quote_text,
    normalize_initials,
    rotate_homophone_results,
)


store = InitialsStore()
result_rotation_state: dict[str, object] = {}
QUOTE_COOLDOWN_SECONDS = 5.0
MAX_CONCURRENT_BOX_JOBS = 4
MAX_PENDING_BOX_JOBS = 16
homophone_job_semaphore = asyncio.Semaphore(MAX_CONCURRENT_BOX_JOBS)
homophone_pending_jobs = 0
homophone_pending_lock = asyncio.Lock()
recent_quote_hits: dict[str, float] = {}
homophone_message = on_message(rule=to_me(), priority=10, block=False)
homophone_quick_message = on_message(priority=10, block=False)


@homophone_message.handle()
async def handle_homophone_message(bot: Bot, event: MessageEvent) -> None:
    command, argument = _extract_command(event.get_message().extract_plain_text())
    if command not in {"/添加首字母", "/首字母列表", "/删除首字母", "/谐音盒"}:
        return

    if command == "/添加首字母":
        await _handle_add_initials(bot, event, argument)
        return

    if command == "/首字母列表":
        await _handle_list_initials(bot, event)
        return

    if command == "/删除首字母":
        await _handle_delete_initials(bot, event, argument)
        return

    await _handle_homophone_box(bot, event)


@homophone_quick_message.handle()
async def handle_quick_homophone_message(bot: Bot, event: MessageEvent) -> None:
    command, _ = _extract_command(event.get_message().extract_plain_text())
    if command != "/盒":
        return

    await _handle_homophone_box(bot, event, command_label="/盒")


async def _handle_add_initials(
    bot: Bot,
    event: MessageEvent,
    argument: Optional[str],
) -> None:
    if argument is None:
        await bot.send(event, "请使用 `/添加首字母 yzh` 这种格式。", reply_message=True)
        return

    normalized = normalize_initials(argument)
    if normalized is None:
        await bot.send(
            event,
            "首字母格式无效。只支持 2 到 5 个英文字母，例如 `/添加首字母 yz`、`/添加首字母 yzh` 或 `/添加首字母 yzzhh`。",
            reply_message=True,
        )
        return

    inserted = await store.add_initials(normalized)
    total = await store.count()
    if inserted:
        await bot.send(
            event,
            f"已加入首字母：{normalized}\n当前姓名库数量：{total}",
            reply_message=True,
        )
        return

    await bot.send(
        event,
        f"首字母 `{normalized}` 已存在，无需重复添加。\n当前姓名库数量：{total}",
        reply_message=True,
    )


async def _handle_list_initials(bot: Bot, event: MessageEvent) -> None:
    initials_list = await store.list_initials()
    if not initials_list:
        await bot.send(event, "当前姓名库为空。", reply_message=True)
        return

    message = "姓名库首字母列表：\n" + "\n".join(
        f"{index}. {item}" for index, item in enumerate(initials_list, start=1)
    )
    await bot.send(event, message, reply_message=True)


async def _handle_delete_initials(
    bot: Bot,
    event: MessageEvent,
    argument: Optional[str],
) -> None:
    if argument is None:
        await bot.send(
            event,
            "请使用 `/删除首字母 yzh` 这种格式。",
            reply_message=True,
        )
        return

    normalized = normalize_initials(argument)
    if normalized is None:
        await bot.send(
            event,
            "首字母格式无效。只支持 2 到 5 个英文字母，例如 `/删除首字母 yz`、`/删除首字母 yzh` 或 `/删除首字母 yzzhh`。",
            reply_message=True,
        )
        return

    deleted = await store.delete_initials(normalized)
    if not deleted:
        await bot.send(event, f"首字母 `{normalized}` 不存在。", reply_message=True)
        return

    total = await store.count()
    await bot.send(
        event,
        f"已删除首字母：{normalized}\n当前姓名库数量：{total}",
        reply_message=True,
    )


async def _handle_homophone_box(
    bot: Bot,
    event: MessageEvent,
    command_label: str = "/谐音盒",
) -> None:
    global homophone_pending_jobs

    if event.reply is None:
        await bot.send(
            event,
            f"请先引用一段文本，再发送 `{command_label}`。",
            reply_message=True,
        )
        return

    quoted_text = event.reply.message.extract_plain_text()
    if not is_valid_quote_text(quoted_text):
        chinese_text = extract_chinese_text(quoted_text)
        if not chinese_text:
            await bot.send(
                event,
                "引用内容里没有可用的中文字符或英文单词，谐音盒无法处理。",
                reply_message=True,
            )
            return

        await bot.send(
            event,
            "引用内容超过限制。谐音盒目前只处理不超过 40 个中文字符的文本。",
            reply_message=True,
        )
        return

    initials_list = await store.list_initials()
    if not initials_list:
        await bot.send(
            event,
            "当前姓名库为空，请先使用 `/添加首字母 yzh` 添加首字母。",
            reply_message=True,
        )
        return

    quote_key = _build_quote_key(event)
    if quote_key is None:
        await bot.send(event, "这条引用消息暂时无法识别。", reply_message=True)
        return

    async with homophone_pending_lock:
        if homophone_job_semaphore.locked() and homophone_pending_jobs >= MAX_PENDING_BOX_JOBS:
            await bot.send(
                event,
                "当前盒子太忙，请稍后再试。",
                reply_message=True,
            )
            return
        homophone_pending_jobs += 1

    try:
        async with homophone_job_semaphore:
            if _is_quote_in_cooldown(quote_key):
                await bot.send(event, "已经盒过了。", reply_message=True)
                return

            _prune_recent_quote_hits()
            recent_quote_hits[quote_key] = time.monotonic()

            results = rotate_homophone_results(
                key=_build_rotation_key(quoted_text, initials_list),
                results=find_homophone_results(quoted_text, initials_list),
                state_map=result_rotation_state,
            )
            if not results:
                await bot.send(event, "没盒出来。", reply_message=True)
                return

            message = "盒出了：\n" + "\n".join(f"<{result}>" for result in results)
            await bot.send(event, message, reply_message=True)
    finally:
        async with homophone_pending_lock:
            homophone_pending_jobs -= 1


def _extract_command(plain_text: str) -> tuple[str, Optional[str]]:
    content = plain_text.strip()
    if not content:
        return "", None

    parts = content.split(maxsplit=1)
    command = parts[0]
    argument = parts[1].strip() if len(parts) > 1 else None
    return command, argument or None


def _build_rotation_key(quoted_text: str, initials_list: list[str]) -> str:
    return f"{quoted_text}\n{'|'.join(sorted(initials_list))}"


def _build_quote_key(event: MessageEvent) -> Optional[str]:
    if event.reply is None:
        return None
    reply_id = getattr(event.reply, "message_id", None)
    if reply_id is not None:
        return str(reply_id)
    quoted_text = event.reply.message.extract_plain_text().strip()
    if not quoted_text:
        return None
    return quoted_text


def _is_quote_in_cooldown(quote_key: str, now: Optional[float] = None) -> bool:
    current = time.monotonic() if now is None else now
    last_hit_at = recent_quote_hits.get(quote_key)
    return last_hit_at is not None and current - last_hit_at < QUOTE_COOLDOWN_SECONDS


def _prune_recent_quote_hits(now: Optional[float] = None) -> None:
    current = time.monotonic() if now is None else now
    expired_keys = [
        key
        for key, last_hit_at in recent_quote_hits.items()
        if current - last_hit_at >= QUOTE_COOLDOWN_SECONDS
    ]
    for key in expired_keys:
        recent_quote_hits.pop(key, None)
