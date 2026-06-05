from __future__ import annotations

from typing import Optional

import httpx
from nonebot import get_driver, logger, on_message
from nonebot.adapters.onebot.v11 import Bot, MessageEvent
from src.plugins.utils.group_scope import is_extra_plugin_enabled

from .store import (
    JokeStore,
    extract_command,
    is_valid_fuzzy_query,
    normalize_search_text,
    normalize_tag,
    parse_joke_id,
    prepare_joke_from_reply,
)


store = JokeStore()
joke_message = on_message(priority=10, block=False)


@joke_message.handle()
async def handle_joke_message(bot: Bot, event: MessageEvent) -> None:
    command, argument = extract_command(event.get_message().extract_plain_text())
    if command not in {
        "/上传笑话",
        "/随机笑话",
        "/模糊查找笑话",
        "/编号查找",
        "/删除笑话",
        "/笑话",
    }:
        return

    if not is_extra_plugin_enabled(event):
        return

    if command == "/上传笑话":
        await _handle_upload_joke(bot, event, argument)
        return

    if command == "/随机笑话":
        await _handle_random_joke(bot, event, argument)
        return

    if command == "/模糊查找笑话":
        await _handle_fuzzy_find(bot, event, argument)
        return

    if command == "/编号查找":
        await _handle_find_by_id(bot, event, argument)
        return

    if command == "/删除笑话":
        await _handle_delete_joke(bot, event, argument)
        return

    await _handle_help(bot, event, argument)


async def _handle_upload_joke(bot: Bot, event: MessageEvent, argument: Optional[str]) -> None:
    if event.reply is None:
        await bot.send(
            event,
            "请先引用一条聊天消息，再发送 `/上传笑话` 或 `/上传笑话 tag`。",
            reply_message=True,
        )
        return

    try:
        prepared = await prepare_joke_from_reply(event.reply.message, _download_image)
    except httpx.HTTPError:
        logger.exception("Failed to download joke image from reply message")
        await bot.send(
            event,
            "引用消息里的图片下载失败，请稍后重试或换一条消息上传。",
            reply_message=True,
        )
        return
    except ValueError:
        await bot.send(event, "这条引用消息是空的，无法上传为笑话。", reply_message=True)
        return
    except Exception:
        logger.exception("Failed to prepare joke content")
        await bot.send(event, "上传笑话时发生异常，请查看日志后重试。", reply_message=True)
        return

    try:
        record, created = await store.add_prepared_joke(
            prepared,
            provided_by=str(event.user_id),
            tag=argument,
        )
    except Exception:
        logger.exception("Failed to persist joke record")
        await bot.send(event, "保存笑话失败，请查看日志后重试。", reply_message=True)
        return

    if created:
        await bot.send(
            event,
            (
                f"上传成功。\n"
                f"笑话编号：{record.id}\n"
                f"归属标签：{record.display_tag}\n"
                f"提供人QQ：{record.provided_by}\n"
                f"提供时间：{record.provided_at}"
            ),
            reply_message=True,
        )
        return

    await bot.send(
        event,
        (
            f"这条笑话已经存在，无需重复上传。\n"
            f"笑话编号：{record.id}\n"
            f"归属标签：{record.display_tag}\n"
            f"提供人QQ：{record.provided_by}\n"
            f"提供时间：{record.provided_at}"
        ),
        reply_message=True,
    )


async def _handle_random_joke(bot: Bot, event: MessageEvent, argument: Optional[str]) -> None:
    tag = normalize_tag(argument)
    record = await store.random_record(tag=tag)
    if record is None:
        if tag is None:
            await bot.send(event, "当前笑话库为空。先引用消息并发送 `/上传笑话`。", reply_message=True)
            return
        await bot.send(event, f"当前没有归属到 `{tag}` 的笑话。", reply_message=True)
        return

    await bot.send(event, record.to_message(metadata_mode="id_only"), reply_message=True)


async def _handle_fuzzy_find(bot: Bot, event: MessageEvent, argument: Optional[str]) -> None:
    if argument is None or not is_valid_fuzzy_query(argument):
        await bot.send(
            event,
            "请使用 `/模糊查找笑话 关键字段`，关键字段不能为空且不能超过 10 个字。",
            reply_message=True,
        )
        return

    record = await store.fuzzy_find(argument)
    if record is None:
        await bot.send(event, "当前没有可供匹配的文字笑话。", reply_message=True)
        return

    await bot.send(event, record.to_message(), reply_message=True)


async def _handle_find_by_id(bot: Bot, event: MessageEvent, argument: Optional[str]) -> None:
    joke_id = parse_joke_id(argument)
    if joke_id is None:
        await bot.send(event, "请使用 `/编号查找 编号`，编号必须是正整数。", reply_message=True)
        return

    record = await store.find_by_id(joke_id)
    if record is None:
        await bot.send(event, f"没有找到编号为 {joke_id} 的笑话。", reply_message=True)
        return

    await bot.send(event, record.to_message(), reply_message=True)


async def _handle_delete_joke(bot: Bot, event: MessageEvent, argument: Optional[str]) -> None:
    if not _is_admin_user(event):
        await bot.send(event, "只有管理员可以删除笑话。", reply_message=True)
        return

    joke_id = parse_joke_id(argument)
    if joke_id is None:
        await bot.send(event, "请使用 `/删除笑话 编号`，编号必须是正整数。", reply_message=True)
        return

    deleted = await store.delete_by_id(joke_id)
    if deleted is None:
        await bot.send(event, f"没有找到编号为 {joke_id} 的笑话。", reply_message=True)
        return

    await bot.send(
        event,
        (
            f"已删除笑话。\n"
            f"笑话编号：{deleted.id}\n"
            f"归属标签：{deleted.display_tag}\n"
            f"提供人QQ：{deleted.provided_by}\n"
            f"提供时间：{deleted.provided_at}"
        ),
        reply_message=True,
    )


async def _handle_help(bot: Bot, event: MessageEvent, argument: Optional[str]) -> None:
    help_arg = normalize_search_text(argument or "").lower()
    if help_arg not in {"help", "帮助"}:
        return

    await bot.send(
        event,
        (
            "笑话功能用法：\n"
            "1. 引用一条聊天消息后发送 `/上传笑话`，上传到通用笑话库。\n"
            "2. 引用一条聊天消息后发送 `/上传笑话 tag`，上传到指定 tag 的笑话集合。\n"
            "3. 发送 `/随机笑话`，从全部笑话中随机抽一条。\n"
            "4. 发送 `/随机笑话 tag`，从指定 tag 中随机抽一条。\n"
            "5. 发送 `/模糊查找笑话 关键字段`，按不超过 10 个字的关键词查找最匹配的笑话。\n"
            "6. 发送 `/编号查找 编号`，按笑话编号查找。\n"
            "7. 发送 `/删除笑话 编号`，仅管理员可删除。\n"
            "8. 发送 `/笑话 help` 查看本帮助。\n"
            "说明：以上命令都不需要 @机器人；上传时会自动去重，重复笑话不会重复入库。"
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
