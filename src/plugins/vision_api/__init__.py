from nonebot import on_command


vision_ping = on_command("vision_ping", priority=10, block=True)


@vision_ping.handle()
async def handle_vision_ping() -> None:
    await vision_ping.finish("vision_api plugin loaded")
