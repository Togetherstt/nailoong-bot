from __future__ import annotations

from typing import Optional

from nonebot import get_driver, logger, on_message
from nonebot.adapters.onebot.v11 import Bot, MessageEvent
from nonebot.rule import to_me

from .client import XiLianApiError, build_api_config, generate_xilian_text
from .store import XiLianModeStore, extract_command, is_valid_quoted_text


mode_store = XiLianModeStore()
xilian_message = on_message(rule=to_me(), priority=10, block=False)
xilian_help_message = on_message(priority=10, block=False)


@xilian_message.handle()
async def handle_xilian_message(bot: Bot, event: MessageEvent) -> None:
    command, argument = extract_command(event.get_message().extract_plain_text())
    if command not in {
        "/昔涟改写",
        "/昔涟回复",
        "/开启昔涟模式",
        "/关闭昔涟模式",
        "/昔涟",
    }:
        return

    if command == "/昔涟":
        await _handle_help(bot, event, argument)
        return

    if command == "/开启昔涟模式":
        await _handle_toggle(bot, event, enabled=True)
        return

    if command == "/关闭昔涟模式":
        await _handle_toggle(bot, event, enabled=False)
        return

    if not await mode_store.is_enabled():
        await bot.send(event, "昔涟模式当前未开启。", reply_message=True)
        return

    if event.reply is None:
        await bot.send(
            event,
            f"请先引用一段文本，再发送 `{command}`。",
            reply_message=True,
        )
        return

    quoted_text = event.reply.message.extract_plain_text()
    if not is_valid_quoted_text(quoted_text):
        await bot.send(
            event,
            "引用内容不能为空，且长度不能超过 200 个字。",
            reply_message=True,
        )
        return

    config = _get_api_config()
    if config is None:
        await bot.send(
            event,
            "昔涟 API 配置不完整，暂时无法调用。",
            reply_message=True,
        )
        return

    task = "rewrite" if command == "/昔涟改写" else "reply"
    try:
        result = await generate_xilian_text(config, task=task, quoted_text=quoted_text)
    except XiLianApiError as exc:
        logger.error(
            f"XiLian API call failed: code={exc.code} detail={exc.detail!r} "
            f"model={config.model} url={config.url}"
        )
        await bot.send(
            event,
            "昔涟改写服务暂时不可用，请稍后再试。",
            reply_message=True,
        )
        return
    except Exception:
        logger.exception("Unexpected XiLian plugin failure")
        await bot.send(
            event,
            "昔涟改写服务暂时不可用，请稍后再试。",
            reply_message=True,
        )
        return

    await bot.send(event, result, reply_message=True)


@xilian_help_message.handle()
async def handle_xilian_help_message(bot: Bot, event: MessageEvent) -> None:
    command, argument = extract_command(event.get_message().extract_plain_text())
    if command != "/昔涟":
        return
    await _handle_help(bot, event, argument)


async def _handle_toggle(bot: Bot, event: MessageEvent, enabled: bool) -> None:
    if not _is_admin_user(event):
        await bot.send(event, "您没有管理权限，无法开启", reply_message=True)
        return

    await mode_store.set_enabled(enabled)
    message = "昔涟模式已开启。" if enabled else "昔涟模式已关闭。"
    await bot.send(event, message, reply_message=True)


async def _handle_help(bot: Bot, event: MessageEvent, argument: Optional[str]) -> None:
    help_arg = (argument or "").strip().lower()
    if help_arg not in {"help", "帮助"}:
        return

    await bot.send(
        event,
        (
            "昔涟功能用法：\n"
            "1. 管理员发送 `@机器人 /开启昔涟模式` 开启功能。\n"
            "2. 管理员发送 `@机器人 /关闭昔涟模式` 关闭功能。\n"
            "3. 引用一段不超过 200 字的话后发送 `@机器人 /昔涟改写`，获取昔涟口吻改写。\n"
            "4. 引用一段不超过 200 字的话后发送 `@机器人 /昔涟回复`，获取昔涟口吻回复。\n"
            "5. 发送 `/昔涟 help` 查看本帮助。\n"
            "说明：只有 `/昔涟 help` 不需要 @机器人，其他昔涟命令仍然需要。"
        ),
        reply_message=True,
    )


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


def _get_api_config():
    config = get_driver().config
    return build_api_config(
        xilian_api_url=getattr(config, "xilian_api_url", ""),
        xilian_api_key=getattr(config, "xilian_api_key", ""),
        xilian_api_model=getattr(config, "xilian_api_model", ""),
        fallback_url=getattr(config, "vision_api_url", ""),
        fallback_key=getattr(config, "vision_api_key", ""),
        fallback_model=getattr(config, "vision_api_model", "") or "gpt-5-mini",
        timeout_seconds=getattr(config, "xilian_api_timeout", 20.0),
    )
