from __future__ import annotations

from nonebot import get_driver
from nonebot.adapters.onebot.v11 import GroupMessageEvent, MessageEvent


def is_extra_plugin_enabled(event: MessageEvent) -> bool:
    if not isinstance(event, GroupMessageEvent):
        return True
    allowed_group_ids = get_extra_plugin_group_ids()
    if not allowed_group_ids:
        return False
    return str(event.group_id) in allowed_group_ids


def get_extra_plugin_group_ids() -> set[str]:
    config = get_driver().config
    raw_value = getattr(config, "extra_plugin_group_ids", "")
    if raw_value is None:
        return set()

    if isinstance(raw_value, (list, tuple, set)):
        values = raw_value
    else:
        values = str(raw_value).split(",")

    result: set[str] = set()
    for value in values:
        text = str(value).strip()
        if text:
            result.add(text)
    return result
