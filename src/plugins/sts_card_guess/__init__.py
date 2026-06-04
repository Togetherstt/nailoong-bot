from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from nonebot import get_driver, logger, on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageEvent
from nonebot.rule import to_me

from .core import (
    CardRepository,
    GuessGameState,
    build_help_message,
    create_game_state,
    extract_command,
)


card_guess_message = on_message(rule=to_me(), priority=10, block=False)
card_guess_help_message = on_message(priority=10, block=False)
repository = CardRepository()
session_lock = asyncio.Lock()
active_sessions: dict[str, "CardGuessSession"] = {}
INITIAL_HINT_DELAY_SECONDS = 10.0
FOLLOWUP_HINT_DELAY_SECONDS = 60.0


@dataclass
class CardGuessSession:
    group_id: str
    bot: Bot
    state: GuessGameState
    timer_task: asyncio.Task[None] | None = None


@card_guess_message.handle()
async def handle_card_guess_message(bot: Bot, event: MessageEvent) -> None:
    command, argument = extract_command(event.get_message().extract_plain_text())

    if command == "/help":
        return
    if command == "/猜卡" and (argument or "").strip().lower() in {"help", "帮助"}:
        return

    if command in {"/猜卡", "/结束猜卡", "/提示", "/猜卡测试"}:
        if not isinstance(event, GroupMessageEvent):
            await bot.send(event, "猜卡功能仅支持群聊。", reply_message=True)
            return

        if command == "/猜卡":
            await _handle_start_game(bot, event)
            return
        if command == "/结束猜卡":
            await _handle_end_game(bot, event)
            return
        if command == "/提示":
            await _handle_manual_hint(bot, event)
            return
        if command == "/猜卡测试":
            await _handle_test_game(bot, event, argument)
            return

    if isinstance(event, GroupMessageEvent):
        plain_text = event.get_message().extract_plain_text().strip()
        if plain_text and not plain_text.startswith("/"):
            await _handle_guess_attempt(bot, event, plain_text)


@card_guess_help_message.handle()
async def handle_card_guess_help_message(bot: Bot, event: MessageEvent) -> None:
    command, argument = extract_command(event.get_message().extract_plain_text())
    if command != "/猜卡":
        return
    if (argument or "").strip().lower() not in {"help", "帮助"}:
        return
    await bot.send(event, build_help_message(), reply_message=True)


async def _handle_start_game(bot: Bot, event: GroupMessageEvent) -> None:
    data_dir = _get_card_data_dir()
    repository.set_data_dir(data_dir)
    try:
        card = repository.pick_random_card()
    except FileNotFoundError:
        await bot.send(
            event,
            f"猜卡牌数据目录不存在：`{data_dir}`，请先在 `.env` 中配置 `STS_CARD_DATA_DIR`。",
            reply_message=True,
        )
        return
    except ValueError:
        await bot.send(event, "猜卡牌卡池为空，暂时无法开始。", reply_message=True)
        return
    except Exception:
        logger.exception("Failed to load STS card data")
        await bot.send(event, "读取猜卡牌数据失败，请检查卡牌 JSON。", reply_message=True)
        return

    await _start_session(bot, event, card=card)


async def _handle_test_game(
    bot: Bot,
    event: GroupMessageEvent,
    argument: Optional[str],
) -> None:
    if not _is_admin_user(event):
        await bot.send(event, "您没有管理权限，无法开启测试局。", reply_message=True)
        return
    if not argument:
        await bot.send(
            event,
            "请使用 `@机器人 /猜卡测试 卡牌名或ID` 来指定测试卡牌。",
            reply_message=True,
        )
        return

    data_dir = _get_card_data_dir()
    repository.set_data_dir(data_dir)
    try:
        card = repository.find_card(argument)
    except FileNotFoundError:
        await bot.send(
            event,
            f"猜卡牌数据目录不存在：`{data_dir}`，请先在 `.env` 中配置 `STS_CARD_DATA_DIR`。",
            reply_message=True,
        )
        return
    except Exception:
        logger.exception("Failed to search STS card data")
        await bot.send(event, "读取猜卡牌数据失败，请检查卡牌 JSON。", reply_message=True)
        return

    if card is None:
        await bot.send(
            event,
            f"没有找到卡牌：`{argument}`。请使用完整中文卡名或 card id。",
            reply_message=True,
        )
        return

    await _start_session(bot, event, card=card, testing=True)


async def _start_session(
    bot: Bot,
    event: GroupMessageEvent,
    card,
    testing: bool = False,
) -> None:
    group_key = str(event.group_id)

    async with session_lock:
        existing = active_sessions.get(group_key)
        if existing is not None:
            await bot.send(
                event,
                "本群已经有一局猜卡进行中，请先猜完，或使用 `@机器人 /结束猜卡` 结束当前游戏。",
                reply_message=True,
            )
            return

        state = create_game_state(card, rng=random.Random())
        session = CardGuessSession(group_id=group_key, bot=bot, state=state)
        active_sessions[group_key] = session
        session.timer_task = asyncio.create_task(
            _hint_after_delay(group_key, INITIAL_HINT_DELAY_SECONDS)
        )

    prefix = "猜卡测试开始：\n" if testing else ""
    await bot.send(event, prefix + state.build_start_message(), reply_message=True)


async def _handle_end_game(bot: Bot, event: GroupMessageEvent) -> None:
    session = await _remove_session(str(event.group_id))
    if session is None:
        await bot.send(event, "本群当前没有正在进行的猜卡游戏。", reply_message=True)
        return

    await bot.send(
        event,
        f"已结束本局猜卡。这张卡是 `{session.state.card.name}`。",
        reply_message=True,
    )


async def _handle_manual_hint(bot: Bot, event: GroupMessageEvent) -> None:
    result = await _advance_group_session(
        str(event.group_id),
        next_delay=FOLLOWUP_HINT_DELAY_SECONDS,
    )
    if result is None:
        await bot.send(event, "本群当前没有正在进行的猜卡游戏。", reply_message=True)
        return

    await bot.send(event, result.message, reply_message=True)


async def _handle_guess_attempt(bot: Bot, event: GroupMessageEvent, guess_text: str) -> None:
    group_key = str(event.group_id)
    async with session_lock:
        session = active_sessions.get(group_key)
        if session is None:
            return
        if session.state.check_guess(guess_text):
            active_sessions.pop(group_key, None)
            timer_task = session.timer_task
        else:
            timer_task = None
    if session is None:
        return
    if timer_task is not None:
        timer_task.cancel()
        await bot.send(
            event,
            f"猜对了，答案就是 `{session.state.card.name}`，本局游戏结束。",
            reply_message=True,
        )
        return

    await bot.send(event, "猜错了，游戏继续。", reply_message=True)


async def _hint_after_delay(group_key: str, delay: float) -> None:
    try:
        await asyncio.sleep(delay)
        result = await _advance_group_session(
            group_key,
            next_delay=FOLLOWUP_HINT_DELAY_SECONDS,
        )
        if result is None:
            return
        await _send_group_text(result.bot, result.group_id, result.message)
    except asyncio.CancelledError:
        return
    except Exception:
        logger.exception("Unexpected card guess timer failure")


async def _advance_group_session(
    group_key: str,
    next_delay: float,
) -> Optional["AdvanceEnvelope"]:
    async with session_lock:
        session = active_sessions.get(group_key)
        if session is None:
            return None

        result = session.state.advance()
        if result.finished:
            active_sessions.pop(group_key, None)
            current_task = asyncio.current_task()
            if session.timer_task is not None and session.timer_task is not current_task:
                session.timer_task.cancel()
            return AdvanceEnvelope(
                message=result.message,
                bot=session.bot,
                group_id=session.group_id,
            )

        if session.timer_task is not None:
            session.timer_task.cancel()
        session.timer_task = asyncio.create_task(_hint_after_delay(group_key, next_delay))
        return AdvanceEnvelope(
            message=result.message,
            bot=session.bot,
            group_id=session.group_id,
        )


@dataclass
class AdvanceEnvelope:
    message: str
    bot: Bot
    group_id: str


async def _remove_session(group_key: str) -> Optional[CardGuessSession]:
    async with session_lock:
        session = active_sessions.pop(group_key, None)
        if session is not None and session.timer_task is not None:
            session.timer_task.cancel()
        return session


async def _get_session(group_key: str) -> Optional[CardGuessSession]:
    async with session_lock:
        return active_sessions.get(group_key)


async def _send_group_text(bot: Bot, group_id: str, message: str) -> None:
    await bot.send_group_msg(group_id=int(group_id), message=message)


def _get_card_data_dir() -> Path:
    config = get_driver().config
    configured = getattr(config, "sts_card_data_dir", None)
    if configured is None:
        return repository.get_data_dir()
    value = str(configured).strip()
    if not value:
        return repository.get_data_dir()
    return Path(value)


def _is_admin_user(event: MessageEvent) -> bool:
    config = get_driver().config
    admin_qq = getattr(config, "admin_qq", None)
    if admin_qq is None:
        return False
    return str(event.user_id) == str(admin_qq).strip()
