from __future__ import annotations

from nonebot import on_message
from nonebot.adapters.onebot.v11 import Bot, MessageEvent
from nonebot.rule import to_me
from src.plugins.utils.group_scope import is_extra_plugin_enabled


help_message = on_message(rule=to_me(), priority=10, block=False)


@help_message.handle()
async def handle_help_message(bot: Bot, event: MessageEvent) -> None:
    plain_text = event.get_message().extract_plain_text().strip()
    if plain_text != "/help":
        return

    if is_extra_plugin_enabled(event):
        message = (
            "功能总帮助：\n"
            "`/奶龙 help`：查看奶龙功能帮助。\n"
            "`/盒 help`：查看盒功能帮助。\n"
            "`/昔涟 help`：查看昔涟功能帮助。\n"
            "`/笑话 help`：查看笑话功能帮助。\n"
            "`/猜卡 help`：查看杀戮尖塔猜卡帮助。\n"
            "说明：总 `/help` 需要 @机器人；其余分支 help 仍按各插件自己的触发规则使用。"
        )
    else:
        message = (
            "功能总帮助：\n"
            "`/猜卡 help`：查看杀戮尖塔猜卡帮助。\n"
            "说明：当前群默认仅开放杀戮尖塔插件。"
        )

    await bot.send(event, message, reply_message=True)
