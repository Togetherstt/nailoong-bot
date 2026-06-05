from __future__ import annotations

from typing import Optional

import httpx
from nonebot import get_driver, logger, on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageEvent, MessageSegment
from nonebot.rule import to_me
from src.plugins.utils.group_scope import is_extra_plugin_enabled

from .store import (
    NailoongStore,
    extract_command_name,
    find_image_url,
    find_segment_image_url,
    find_storable_segment,
    format_record_line,
    guess_extension_from_url,
)


store = NailoongStore()
nailoong_message = on_message(rule=to_me(), priority=10, block=False)
nailoong_help_message = on_message(priority=10, block=False)


@nailoong_message.handle()
async def handle_nailoong_message(bot: Bot, event: MessageEvent) -> None:
    command, argument = extract_command_name(event.message)
    if command not in {
        "/添加奶龙",
        "/随机奶龙",
        "/奶龙列表",
        "/删除奶龙",
        "/撤销删除奶龙",
        "/编号查找奶龙",
        "/模糊查找奶龙",
        "/奶龙套皮",
        "/奶龙去皮",
    }:
        return

    if not is_extra_plugin_enabled(event):
        return

    if command == "/添加奶龙":
        await _handle_add_nailoong(bot, event, argument)
        return

    if command == "/随机奶龙":
        await _handle_random_nailoong(bot, event)
        return

    if command == "/编号查找奶龙":
        await _handle_find_nailoong_by_index(bot, event, argument)
        return

    if command == "/模糊查找奶龙":
        await _handle_fuzzy_find_nailoong(bot, event, argument)
        return

    if not _is_admin_user(event):
        return

    if command == "/奶龙套皮":
        await _handle_toggle_forward_mode(bot, event, enabled=True)
        return

    if command == "/奶龙去皮":
        await _handle_toggle_forward_mode(bot, event, enabled=False)
        return

    if command == "/奶龙列表":
        await _handle_list_nailoong(bot, event)
        return

    if command == "/撤销删除奶龙":
        await _handle_undo_delete(bot, event)
        return

    await _handle_delete_nailoong(bot, event, argument)


@nailoong_help_message.handle()
async def handle_nailoong_help_message(bot: Bot, event: MessageEvent) -> None:
    command, argument = extract_command_name(event.message)
    if command != "/奶龙":
        return
    if not is_extra_plugin_enabled(event):
        return
    await _handle_help(bot, event, argument)


async def _handle_add_nailoong(
    bot: Bot,
    event: MessageEvent,
    argument: Optional[str],
) -> None:
    if event.reply is None:
        await bot.send(
            event,
            "请先引用一张奶龙表情包，再发送 `/添加奶龙 奶龙名称`。",
            reply_message=True,
        )
        return

    segment = find_storable_segment(event.reply.message)
    if segment is None:
        await bot.send(
            event,
            "引用消息里没有可保存的图片或表情包，请确认你引用的是奶龙图片或 QQ 表情包。",
            reply_message=True,
        )
        return

    try:
        if segment.type == "image":
            image_url = find_image_url(event.reply.message)
            if image_url is None:
                await bot.send(
                    event,
                    "这条引用消息是图片，但当前拿不到可下载地址。请优先引用原图消息后重试。",
                    reply_message=True,
                )
                return
            image_bytes = await _download_image(image_url)
            record, created = await store.add_record(
                image_bytes,
                added_by=str(event.user_id),
                original_name=argument,
                file_extension=guess_extension_from_url(image_url),
            )
        else:
            segment_image_bytes: Optional[bytes] = None
            segment_image_url = find_segment_image_url(segment)
            if segment_image_url is not None:
                try:
                    segment_image_bytes = await _download_image(segment_image_url)
                except httpx.HTTPError:
                    logger.warning("Failed to download comparable bytes for segment dedup.")
            record, created = await store.add_segment_record(
                segment,
                added_by=str(event.user_id),
                original_name=argument,
                image_bytes=segment_image_bytes,
            )
    except httpx.HTTPError:
        logger.exception("Failed to download nailoong image from reply message")
        await bot.send(
            event,
            "奶龙表情包下载失败，请稍后重试或重新发送原图。",
            reply_message=True,
        )
        return
    except Exception:
        logger.exception("Failed to persist nailoong image")
        await bot.send(
            event,
            "添加奶龙表情包时发生异常，请查看日志后重试。",
            reply_message=True,
        )
        return

    total = await store.count()
    if created:
        await bot.send(
            event,
            (
                f"已添加奶龙表情包：{record.display_name}\n"
                f"添加者 QQ：{record.added_by}\n"
                f"添加日期：{record.added_at}\n"
                f"当前库存：{total}"
            ),
            reply_message=True,
        )
        return

    await bot.send(
        event,
        (
            f"这张奶龙已经存在，无需重复添加。\n"
            f"奶龙名称：{record.display_name}\n"
            f"添加者 QQ：{record.added_by}\n"
            f"添加日期：{record.added_at}\n"
            f"当前库存：{total}"
        ),
        reply_message=True,
    )


async def _handle_random_nailoong(bot: Bot, event: MessageEvent) -> None:
    record = await store.random_record()
    if record is None:
        await bot.send(
            event,
            "当前还没有已保存的奶龙表情包。先引用图片并发送 `/添加奶龙 奶龙名称`。",
            reply_message=True,
        )
        return

    if isinstance(event, GroupMessageEvent) and await store.is_forward_mode_enabled(str(event.group_id)):
        await _send_forward_nailoong(bot, event, record)
        return

    await bot.send(event, store.message_for_record(record), reply_message=True)


async def _handle_find_nailoong_by_index(
    bot: Bot,
    event: MessageEvent,
    argument: Optional[str],
) -> None:
    if argument is None or not argument.isdigit():
        await bot.send(event, "请使用 `@机器人 /编号查找奶龙 序号`。", reply_message=True)
        return

    record = await store.find_by_index(int(argument))
    if record is None:
        await bot.send(event, "没有找到对应序号的奶龙。", reply_message=True)
        return

    await bot.send(event, store.message_for_record(record), reply_message=True)


async def _handle_fuzzy_find_nailoong(
    bot: Bot,
    event: MessageEvent,
    argument: Optional[str],
) -> None:
    if argument is None or not argument.strip():
        await bot.send(event, "请使用 `@机器人 /模糊查找奶龙 名称关键字`。", reply_message=True)
        return

    matched = await store.fuzzy_find_by_name(argument)
    if matched is None:
        await bot.send(event, "当前没有可供匹配的奶龙名称。", reply_message=True)
        return

    index, record = matched
    message = store.message_for_record(record)
    message.append(MessageSegment.text(f"\n库存序号：{index}"))
    await bot.send(event, message, reply_message=True)


async def _handle_list_nailoong(bot: Bot, event: MessageEvent) -> None:
    records = await store.list_records()
    if not records:
        await bot.send(event, "当前没有已保存的奶龙表情包。", reply_message=True)
        return

    lines = ["奶龙库存列表："]
    for index, record in enumerate(records, start=1):
        lines.append(format_record_line(index, record))

    await bot.send(event, "\n".join(lines), reply_message=True)


async def _handle_delete_nailoong(
    bot: Bot,
    event: MessageEvent,
    argument: Optional[str],
) -> None:
    if argument is None:
        await bot.send(
            event,
            "请使用 `/删除奶龙 序号` 或 `/删除奶龙 名称`。",
            reply_message=True,
        )
        return

    if argument == "最近一个":
        deleted = await store.delete_latest()
        if deleted is None:
            await bot.send(event, "删除失败：当前库存为空。", reply_message=True)
            return
        await bot.send(
            event,
            f"已删除最近添加的奶龙：{deleted.display_name}",
            reply_message=True,
        )
        return

    if argument.isdigit():
        deleted = await store.delete_by_index(int(argument))
        if deleted is None:
            await bot.send(event, "删除失败：序号不存在。", reply_message=True)
            return
        await bot.send(
            event,
            f"已删除奶龙：{deleted.display_name}",
            reply_message=True,
        )
        return

    deleted_records = await store.delete_by_name(argument)
    if not deleted_records:
        await bot.send(event, "删除失败：没有找到同名奶龙。", reply_message=True)
        return

    await bot.send(
        event,
        f"已删除 {len(deleted_records)} 条名为“{argument}”的奶龙记录。",
        reply_message=True,
    )


async def _handle_undo_delete(bot: Bot, event: MessageEvent) -> None:
    restored = await store.undo_last_delete()
    if restored is None:
        await bot.send(event, "撤销失败：当前没有可恢复的删除记录。", reply_message=True)
        return

    await bot.send(
        event,
        f"已恢复奶龙：{restored.display_name}",
        reply_message=True,
    )


async def _handle_toggle_forward_mode(
    bot: Bot,
    event: MessageEvent,
    *,
    enabled: bool,
) -> None:
    if not isinstance(event, GroupMessageEvent):
        await bot.send(event, "奶龙套皮功能仅支持群聊使用。", reply_message=True)
        return

    changed = await store.set_forward_mode(str(event.group_id), enabled)
    if enabled:
        if changed:
            await bot.send(event, "本群已开启奶龙套皮。之后 `/随机奶龙` 会以合并转发消息形式发送。", reply_message=True)
            return
        await bot.send(event, "本群已经处于奶龙套皮状态。", reply_message=True)
        return

    if changed:
        await bot.send(event, "本群已关闭奶龙套皮。之后 `/随机奶龙` 会恢复为直接发送。", reply_message=True)
        return
    await bot.send(event, "本群当前未开启奶龙套皮。", reply_message=True)


async def _handle_help(bot: Bot, event: MessageEvent, argument: Optional[str]) -> None:
    help_arg = (argument or "").strip().lower()
    if help_arg not in {"help", "帮助"}:
        return

    await bot.send(
        event,
        (
            "奶龙功能用法：\n"
            "1. 先引用一张奶龙图片或 QQ 表情，再发送 `@机器人 /添加奶龙 名称可选`。\n"
            "2. 发送 `@机器人 /随机奶龙`，随机抽取一条奶龙记录。\n"
            "3. 发送 `@机器人 /编号查找奶龙 序号`，按库存序号查找奶龙。\n"
            "4. 发送 `@机器人 /模糊查找奶龙 名称关键字`，按奶龙名称模糊查找。\n"
            "5. 管理员可发送 `@机器人 /奶龙套皮`，让本群 `/随机奶龙` 改为合并转发形式发送。\n"
            "6. 管理员可发送 `@机器人 /奶龙去皮`，关闭合并转发形式发送。\n"
            "7. 管理员可发送 `@机器人 /奶龙列表` 查看库存。\n"
            "8. 管理员可发送 `@机器人 /删除奶龙 序号`、`@机器人 /删除奶龙 名称` 或 `@机器人 /删除奶龙 最近一个` 删除记录。\n"
            "9. 管理员可发送 `@机器人 /撤销删除奶龙` 恢复最近一次删除。\n"
            "10. 发送 `/奶龙 help` 查看本帮助。\n"
            "说明：奶龙上传已启用 `sha256 + dHash` 双层去重；只有 `/奶龙 help` 不需要 @机器人，其他奶龙命令仍然需要。"
        ),
        reply_message=True,
    )


async def _download_image(url: str) -> bytes:
    timeout = httpx.Timeout(20.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.content


def _is_admin_user(event: MessageEvent) -> bool:
    admin_qq = _get_admin_qq()
    return admin_qq is not None and str(event.user_id) == admin_qq


def _get_admin_qq() -> Optional[str]:
    config = get_driver().config
    admin_qq = getattr(config, "admin_qq", None)
    if admin_qq is None:
        return None
    value = str(admin_qq).strip()
    return value or None


async def _send_forward_nailoong(bot: Bot, event: GroupMessageEvent, record) -> None:
    payload = store.message_for_record(record)
    content_lines = []
    message = Message()

    for segment in payload:
        if segment.type == "text":
            text = str(segment.data.get("text", ""))
            if text:
                content_lines.append(text)
            continue
        message.append(segment)

    if content_lines:
        message.append(MessageSegment.text("".join(content_lines)))

    await bot.call_api(
        "send_group_forward_msg",
        group_id=event.group_id,
        messages=[
            {
                "type": "node",
                "data": {
                    "name": "奶龙库存",
                    "uin": str(bot.self_id),
                    "content": message,
                },
            }
        ],
    )
