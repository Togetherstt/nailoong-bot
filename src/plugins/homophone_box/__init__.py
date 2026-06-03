from __future__ import annotations

import asyncio
import time
from typing import Optional

from nonebot import get_driver, on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageEvent
from nonebot.rule import to_me

from .store import (
    HomophoneMatch,
    InitialsEntry,
    InitialsStore,
    extract_chinese_text,
    find_homophone_matches,
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
    if command not in {
        "/添加首字母",
        "/绑定群友",
        "/解绑群友",
        "/首字母列表",
        "/删除首字母",
        "/谐音盒",
        "/盒",
    }:
        return

    if command in {"/谐音盒", "/盒"} and (argument or "").strip().lower() in {"help", "帮助"}:
        await _handle_help(bot, event)
        return

    if command == "/添加首字母":
        await _handle_add_initials(bot, event, argument)
        return

    if command == "/绑定群友":
        await _handle_bind_member(bot, event, argument)
        return

    if command == "/解绑群友":
        await _handle_unbind_member(bot, event, argument)
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
    command, argument = _extract_command(event.get_message().extract_plain_text())
    if command != "/盒":
        return

    if (argument or "").strip().lower() in {"help", "帮助"}:
        await _handle_help(bot, event)
        return

    await _handle_homophone_box(bot, event, command_label="/盒")


async def _handle_add_initials(
    bot: Bot,
    event: MessageEvent,
    argument: Optional[str],
) -> None:
    if argument is None:
        await bot.send(
            event,
            "请使用 `/添加首字母 yzh` 或 `/添加首字母 yzh @群友` 这种格式。",
            reply_message=True,
        )
        return

    normalized, member_qq = _parse_initials_binding_argument(event, argument)
    if normalized is None:
        await bot.send(
            event,
            "首字母格式无效。只支持 2 到 5 个英文字母，例如 `/添加首字母 yz`、`/添加首字母 yzh` 或 `/添加首字母 yzzhh`。",
            reply_message=True,
        )
        return

    inserted = await store.add_initials(normalized, member_qq=member_qq)
    total = await store.count()
    if inserted:
        suffix = ""
        if member_qq is not None:
            display_name = await _resolve_member_display_name(bot, event, member_qq)
            suffix = f"\n绑定群友：{display_name}"
        await bot.send(
            event,
            f"已加入首字母：{normalized}{suffix}\n当前姓名库数量：{total}",
            reply_message=True,
        )
        return

    await bot.send(
        event,
        f"首字母 `{normalized}` 已存在，无需重复添加。\n当前姓名库数量：{total}",
        reply_message=True,
    )


async def _handle_bind_member(
    bot: Bot,
    event: MessageEvent,
    argument: Optional[str],
) -> None:
    if argument is None:
        await bot.send(
            event,
            "请使用 `/绑定群友 yzh @群友` 这种格式。",
            reply_message=True,
        )
        return

    normalized, member_qq = _parse_initials_binding_argument(event, argument)
    if normalized is None or member_qq is None:
        await bot.send(
            event,
            "绑定格式无效。请使用 `/绑定群友 yzh @群友`，其中首字母只支持 2 到 5 个英文字母。",
            reply_message=True,
        )
        return

    updated = await store.bind_member(normalized, member_qq)
    if not updated:
        await bot.send(
            event,
            f"首字母 `{normalized}` 不存在，请先使用 `/添加首字母 {normalized}`。",
            reply_message=True,
        )
        return

    display_name = await _resolve_member_display_name(bot, event, member_qq)
    await bot.send(
        event,
        f"已为首字母 `{normalized}` 绑定群友：{display_name}",
        reply_message=True,
    )


async def _handle_unbind_member(
    bot: Bot,
    event: MessageEvent,
    argument: Optional[str],
) -> None:
    if argument is None:
        await bot.send(
            event,
            "请使用 `/解绑群友 yzh` 这种格式。",
            reply_message=True,
        )
        return

    normalized = normalize_initials(argument)
    if normalized is None:
        await bot.send(
            event,
            "解绑格式无效。请使用 `/解绑群友 yzh`，其中首字母只支持 2 到 5 个英文字母。",
            reply_message=True,
        )
        return

    updated = await store.unbind_member(normalized)
    if not updated:
        await bot.send(
            event,
            f"首字母 `{normalized}` 不存在，或当前没有绑定任何群友。",
            reply_message=True,
        )
        return

    await bot.send(
        event,
        f"已解除首字母 `{normalized}` 的群友绑定。",
        reply_message=True,
    )


async def _handle_list_initials(bot: Bot, event: MessageEvent) -> None:
    entries = await store.list_entries()
    if not entries:
        await bot.send(event, "当前姓名库为空。", reply_message=True)
        return

    lines = ["姓名库首字母列表："]
    for index, entry in enumerate(entries, start=1):
        if entry.member_qq is None:
            lines.append(f"{index}. {entry.initials}")
            continue
        display_name = await _resolve_member_display_name(bot, event, entry.member_qq)
        lines.append(f"{index}. {entry.initials} -> {display_name}")
    await bot.send(event, "\n".join(lines), reply_message=True)


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

    if not _is_homophone_group_allowed(event):
        return

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

    entries = await store.list_entries()
    if not entries:
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

            matches = find_homophone_matches(quoted_text, entries)
            rotated_candidates = rotate_homophone_results(
                key=_build_rotation_key(quoted_text, [entry.initials for entry in entries]),
                results=[match.candidate for match in matches],
                state_map=result_rotation_state,
            )
            if not rotated_candidates:
                await bot.send(event, "没盒出来。", reply_message=True)
                return

            selected_matches = _select_matches_by_candidates(matches, rotated_candidates)
            formatted_results = []
            for match in selected_matches:
                suffix = await _format_bound_member_suffix(bot, event, match)
                formatted_results.append(f"<{match.candidate}>{suffix}")

            message = "盒出了：\n" + "\n".join(formatted_results)
            await bot.send(event, message, reply_message=True)
    finally:
        async with homophone_pending_lock:
            homophone_pending_jobs -= 1


async def _handle_help(bot: Bot, event: MessageEvent) -> None:
    await bot.send(
        event,
        (
            "盒功能用法：\n"
            "1. 发送 `@机器人 /添加首字母 yz~yzzhh` 可新增 2 到 5 位首字母模式。\n"
            "2. 发送 `@机器人 /添加首字母 首字母 @群友` 可在新增时直接绑定群友。\n"
            "3. 发送 `@机器人 /绑定群友 首字母 @群友`、`@机器人 /解绑群友 首字母` 管理绑定关系。\n"
            "4. 发送 `@机器人 /首字母列表` 查看当前首字母库。\n"
            "5. 发送 `@机器人 /删除首字母 首字母` 删除指定模式。\n"
            "6. 引用文本后发送 `@机器人 /谐音盒` 或直接发送 `/盒` 触发匹配。\n"
            "7. 发送 `/盒 help` 查看本帮助。\n"
            "说明：只有 `/盒` 和 `/盒 help` 不需要 @机器人，其余管理命令仍然需要。"
        ),
        reply_message=True,
    )


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
        group_id = getattr(event, "group_id", None)
        return f"{group_id}:{reply_id}"
    quoted_text = event.reply.message.extract_plain_text().strip()
    if not quoted_text:
        return None
    group_id = getattr(event, "group_id", None)
    return f"{group_id}:{quoted_text}"


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


def _parse_initials_binding_argument(
    event: MessageEvent,
    argument: str,
) -> tuple[Optional[str], Optional[str]]:
    raw_initials = argument.split(maxsplit=1)[0].strip()
    normalized = normalize_initials(raw_initials)
    if normalized is None:
        return None, None
    member_qq = _extract_mentioned_member_qq(event)
    return normalized, member_qq


def _extract_mentioned_member_qq(event: MessageEvent) -> Optional[str]:
    for segment in event.message:
        if getattr(segment, "type", "") != "at":
            continue
        qq = segment.data.get("qq")
        if qq in {None, "all"}:
            continue
        return str(qq)
    return None


def _is_homophone_group_allowed(event: MessageEvent) -> bool:
    allowed_group_ids = _get_allowed_homophone_group_ids()
    if not allowed_group_ids:
        return False
    if not isinstance(event, GroupMessageEvent):
        return False
    return str(event.group_id) in allowed_group_ids


def _get_allowed_homophone_group_ids() -> set[str]:
    config = get_driver().config
    raw_value = getattr(config, "homophone_group_ids", "")
    if isinstance(raw_value, (list, tuple, set)):
        return {str(item).strip() for item in raw_value if str(item).strip()}
    text = str(raw_value).strip()
    if not text:
        return set()
    return {item.strip() for item in text.split(",") if item.strip()}


async def _resolve_member_display_name(
    bot: Bot,
    event: MessageEvent,
    member_qq: str,
) -> str:
    if not isinstance(event, GroupMessageEvent):
        return member_qq
    try:
        info = await bot.get_group_member_info(
            group_id=event.group_id,
            user_id=int(member_qq),
            no_cache=False,
        )
    except Exception:
        return member_qq
    card = str(info.get("card", "")).strip()
    nickname = str(info.get("nickname", "")).strip()
    return card or nickname or member_qq


async def _format_bound_member_suffix(
    bot: Bot,
    event: MessageEvent,
    match: HomophoneMatch,
) -> str:
    if match.member_qq is None:
        return ""
    display_name = await _resolve_member_display_name(bot, event, match.member_qq)
    return f"（{display_name}）"


def _select_matches_by_candidates(
    matches: list[HomophoneMatch],
    rotated_candidates: list[str],
) -> list[HomophoneMatch]:
    selected: list[HomophoneMatch] = []
    used_indexes: set[int] = set()
    for candidate in rotated_candidates:
        for index, match in enumerate(matches):
            if index in used_indexes:
                continue
            if match.candidate != candidate:
                continue
            selected.append(match)
            used_indexes.add(index)
            break
    return selected
