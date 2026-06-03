from nonebot import on_command
from nonebot.adapters.onebot.v11 import MessageSegment


emoji_ping = on_command("emoji_ping", priority=10, block=True)


@emoji_ping.handle()
async def handle_emoji_ping() -> None:
    await emoji_ping.finish(MessageSegment.text("basic_reply plugin loaded"))
