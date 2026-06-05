# Nailoong Bot

基于 `Python + NoneBot2 + OneBot V11 + NapCatQQ` 的 QQ 机器人项目。

当前版本包含这些功能：

- 奶龙表情包记忆与去重
- 谐音盒与首字母库
- 昔涟口吻改写
- 笑话梗库
- 杀戮尖塔猜卡
- 群每日统计
- 分插件帮助与总帮助

本仓库只负责机器人逻辑层。QQ 登录、收发消息和协议接入由 `NapCatQQ` 负责。

## 功能总览

### 奶龙

- `@机器人 /添加奶龙 [名称可选]`
- `@机器人 /随机奶龙`
- `@机器人 /编号查找奶龙 <序号>`
- `@机器人 /模糊查找奶龙 <名称关键字>`
- `@机器人 /奶龙套皮`
- `@机器人 /奶龙去皮`
- `@机器人 /奶龙列表`
- `@机器人 /删除奶龙 <序号|名称|最近一个>`
- `@机器人 /撤销删除奶龙`
- `/奶龙 help`

### 谐音盒

- `@机器人 /添加首字母 <2-5位字母> [@群友可选]`
- `@机器人 /绑定群友 <2-5位字母> @群友`
- `@机器人 /解绑群友 <2-5位字母>`
- `@机器人 /首字母列表`
- `@机器人 /删除首字母 <2-5位字母>`
- `@机器人 /谐音盒`
- `引用文本 + /盒`
- `/盒 help`

### 昔涟

- `@机器人 /开启昔涟模式`
- `@机器人 /关闭昔涟模式`
- `引用文本 + @机器人 /昔涟改写`
- `引用文本 + @机器人 /昔涟回复`
- `/昔涟 help`

### 笑话梗库

- `引用消息 + /上传笑话 [tag]`
- `/随机笑话 [tag]`
- `/模糊查找笑话 关键字段`
- `/编号查找 编号`
- `/删除笑话 编号`
- `/笑话 help`

### 杀戮尖塔猜卡

- `@机器人 /猜卡`
- `@机器人 /提示`
- `@机器人 /结束猜卡`
- `@机器人 /猜卡测试 <卡牌名或ID>`
- `/猜卡 help`
- `python scripts/test_sts_card_guess_local.py --card 暴走`

### 群每日统计

- `@机器人 /开启统计`
- `@机器人 /结束统计`
- `@机器人 /查看当前统计`

统计内容：

- 被艾特次数最多的人
- 贴表情最多的人
- 被贴表情次数最多的人
- 如果当前群能用 `/盒`，则额外统计 `/盒` 次数最多的人
- 如果当前群能用 `/盒` 且命中了已绑定群友，则额外统计被 `/盒` 次数最多的人
- 为避免群内其他 bot 干扰，若发消息的人或被 `@` 的对象其群昵称/昵称为 `ARClass` 或 `Yurisaki`，则相关艾特与表情统计不会计入。

### 总帮助

- `@机器人 /help`

## 群范围规则

- 所有群默认开放：
  - 杀戮尖塔猜卡
  - `/猜卡 help`
  - `@机器人 /help` 中的猜卡入口
  - 群每日统计功能
- 只有 `EXTRA_PLUGIN_GROUP_IDS` 指定的群额外开放：
  - 奶龙
  - 谐音盒
  - 昔涟
  - 笑话梗库
  - 对应分插件 help
- 谐音盒还必须额外命中 `HOMOPHONE_GROUP_IDS`
  - 也就是一个群要同时在 `EXTRA_PLUGIN_GROUP_IDS` 和 `HOMOPHONE_GROUP_IDS` 中，`/盒` 才可用

## 项目结构

```text
Nailoong Bot/
├─ AGENTS.MD
├─ README.md
├─ LICENSE
├─ bot.py
├─ pyproject.toml
├─ requirements.txt
├─ .env.example
├─ data/
│  ├─ group_daily_stats/
│  ├─ homophone_box/
│  ├─ joke_library/
│  ├─ nailoong/
│  ├─ sts_card_guess/
│  └─ xilian_style/
├─ scripts/
├─ src/
│  └─ plugins/
│     ├─ basic_reply/
│     ├─ group_daily_stats/
│     ├─ help_center/
│     ├─ homophone_box/
│     ├─ joke_library/
│     ├─ nailoong_memory/
│     ├─ sts_card_guess/
│     ├─ utils/
│     ├─ vision_api/
│     └─ xilian_style/
└─ tests/
```

## 环境准备

要求：

- `Python 3.10+`

安装依赖：

```powershell
pip install -r requirements.txt
```

如果使用 Conda：

```powershell
conda create -n nailoong-bot python=3.11 -y
conda activate nailoong-bot
pip install -r requirements.txt
```

## 配置文件

复制配置模板：

```powershell
Copy-Item .env.example .env
```

推荐最小配置：

```env
ENVIRONMENT=dev
LOG_LEVEL=DEBUG
HOST=127.0.0.1
PORT=8080
COMMAND_START=["/"]
ONEBOT_ACCESS_TOKEN=replace-with-your-token
ADMIN_QQ=123456789
EXTRA_PLUGIN_GROUP_IDS=123456789,987654321
HOMOPHONE_GROUP_IDS=123456789,987654321
SUPERUSERS=[]
VISION_API_URL=https://api.example.com/v1/chat/completions
VISION_API_KEY=sk-xxxxxx
XILIAN_API_URL=https://api.example.com/v1/responses
XILIAN_API_KEY=sk-xxxxxx
XILIAN_API_MODEL=gpt-5.4
XILIAN_API_TIMEOUT=20
STS_CARD_DATA_DIR=data/sts_card_guess/cards
```

关键字段：

- `ONEBOT_ACCESS_TOKEN`
  - NapCatQQ 与本项目之间的访问令牌，两边必须一致。
- `ADMIN_QQ`
  - 管理员 QQ。奶龙管理、笑话删除、猜卡测试、昔涟开关、群统计开关都依赖它。
- `EXTRA_PLUGIN_GROUP_IDS`
  - 允许开放“除猜卡外其他插件”的群号白名单。
- `HOMOPHONE_GROUP_IDS`
  - 允许触发谐音盒的群号白名单。
- `XILIAN_API_*`
  - 昔涟改写接口配置。
- `STS_CARD_DATA_DIR`
  - 猜卡卡池目录。

## 配置 NapCatQQ

1. 登录机器人小号。
2. 在 NapCatQQ 中启用 `OneBot V11`。
3. 连接方式选择 `正向 WebSocket`。
4. 地址填写：

```text
127.0.0.1:8080
```

5. Access Token 填写 `.env` 中的 `ONEBOT_ACCESS_TOKEN`。

如果你修改了 `.env` 中的 `HOST`、`PORT` 或 `ONEBOT_ACCESS_TOKEN`，NapCatQQ 里也要同步修改。

## 启动

直接启动：

```powershell
python bot.py
```

或热重载：

```powershell
nb run --reload
```

## 使用说明

### 奶龙表情包记忆

添加奶龙：

```text
@机器人 /添加奶龙 憨笑奶龙
```

可省略名字：

```text
@机器人 /添加奶龙
```

随机奶龙：

```text
@机器人 /随机奶龙
```

按编号查找奶龙：

```text
@机器人 /编号查找奶龙 12
```

按名称模糊查找奶龙：

```text
@机器人 /模糊查找奶龙 憨笑
```

开启本群奶龙套皮：

```text
@机器人 /奶龙套皮
```

关闭本群奶龙套皮：

```text
@机器人 /奶龙去皮
```

说明：

- 支持引用图片，也支持引用可解析的 QQ 表情。
- 数据持久化，重启不会丢失。
- 已启用 `sha256 + dHash` 双层去重。
- `/编号查找奶龙` 与 `/模糊查找奶龙` 只按奶龙名称和库存序号查找。
- 开启 `奶龙套皮` 后，本群里的 `/随机奶龙` 会改为合并转发消息形式发送，点进去后才能看到表情包和附带信息。
- 关闭后恢复为原本的直接发送形式。

### 谐音盒

添加首字母：

```text
@机器人 /添加首字母 yzh
@机器人 /添加首字母 yzh @某个群友
```

管理首字母：

```text
@机器人 /首字母列表
@机器人 /绑定群友 yzh @某个群友
@机器人 /解绑群友 yzh
@机器人 /删除首字母 yzh
```

触发盒：

```text
@机器人 /谐音盒
```

或：

```text
/盒
```

规则摘要：

- 只在同时命中 `EXTRA_PLUGIN_GROUP_IDS` 和 `HOMOPHONE_GROUP_IDS` 的群生效。
- 引用文本有效上限为 `40` 个中文字符。
- 支持中文单字、英文首字母、英文整词命中。
- 命中超过 `10` 条时只随机返回 `10` 条。
- 同一条引用消息有 `5` 秒冷却。

### 昔涟口吻改写

管理员开关：

```text
@机器人 /开启昔涟模式
@机器人 /关闭昔涟模式
```

改写与回复：

```text
@机器人 /昔涟改写
@机器人 /昔涟回复
```

要求：

- 必须先引用文本。
- 引用文本上限 `200` 字。
- 输出上限 `200` 字。
- 结尾必须为单个 `♪`。

### 笑话梗库

这些命令都不需要 `@机器人`。

上传：

```text
/上传笑话
/上传笑话 校园
```

查询：

```text
/随机笑话
/随机笑话 校园
/模糊查找笑话 晚风
/编号查找 12
```

管理：

```text
/删除笑话 12
```

说明：

- 上传时会自动去重。
- 若群内有人直接发送包含“张老师”“张师”“张雪峰”“雪峰”“巧乐兹”“雪碧”的消息，会被自动归档到 `张雪峰` tag，并正常提示入库结果。
- 若手动上传时引用内容包含这些关键词，也会自动归档到 `张雪峰` tag。
- 随机笑话只返回内容和编号。
- `/删除笑话` 仅管理员可用。

### 杀戮尖塔猜卡

开始游戏：

```text
@机器人 /猜卡
```

规则：

- 同群同一时间只允许一局。
- 开局随机给出 3 条基础提示。
- `10` 秒后补第 4 条基础提示。
- 之后每 `60` 秒揭示一次描述。
- 总共揭示 `4` 批描述字符。
- 第 `4` 批后仍未猜中则失败结束。

猜测格式：

```text
@机器人 暴走
```

辅助命令：

```text
@机器人 /提示
@机器人 /结束猜卡
@机器人 /猜卡测试 暴走
```

本地测试：

```powershell
python scripts/test_sts_card_guess_local.py --card 暴走
```

### 群每日统计

管理员开启：

```text
@机器人 /开启统计
```

管理员结束：

```text
@机器人 /结束统计
```

查看当前统计：

```text
@机器人 /查看当前统计
```

规则：

- 只有管理员能开启和结束。
- 开启后会持续生效，直到管理员手动结束。
- 每天 `23:59:59` 会自动发送当天统计。
- 自动发送后会清空当天数据，第二天重新开始统计。
- 统计按群独立计算，不会混群。
- `/盒` 相关两项只在该群实际开放 `/盒` 时展示。

## 数据持久化

当前项目使用本地文件持久化：

- 奶龙：`data/nailoong/`
- 谐音盒：`data/homophone_box/`
- 昔涟模式开关：`data/xilian_style/`
- 笑话库：`data/joke_library/`
- 猜卡卡池：`data/sts_card_guess/cards/`
- 群统计：`data/group_daily_stats/`

说明：

- 奶龙、谐音盒、笑话、昔涟模式开关、群统计开关都会在重启后保留。
- 奶龙套皮的群开关也会在重启后保留。
- 群统计的“当天计数”会在每日结算后自动清空。
- 猜卡对局本身不持久化，重启后正在进行的对局会结束。

## 测试

运行全部单元测试：

```powershell
python -m unittest discover -s tests -v
```

仅测试猜卡：

```powershell
python -m unittest tests.test_sts_card_guess -v
```

仅测试群统计：

```powershell
python -m unittest tests.test_group_daily_stats -v
```

## 日志与安全

不要在日志、截图、报错记录、Issue 或聊天记录中暴露以下内容：

- `API Key`
- `ONEBOT_ACCESS_TOKEN`
- 完整请求头
- 完整上游请求 payload
- 私密群号、管理员 QQ、真实账号信息
- 用户原始敏感消息内容

推荐做法：

- 只记录错误类型、状态码、模型名、插件名和简短上下文。
- 对 token、key、群号、QQ 号做脱敏。
- 调试上游接口时，不打印包含密钥的完整请求体。

## 常见问题

### 机器人没回复

优先检查：

- NapCatQQ 是否已连接到 `HOST:PORT`
- `ONEBOT_ACCESS_TOKEN` 是否一致
- 是否真的 `@` 到机器人
- 消息格式是否符合插件要求

### 管理命令没反应

优先检查：

- `.env` 中的 `ADMIN_QQ` 是否正确
- 当前发命令的账号是否就是管理员

### 谐音盒没反应

优先检查：

- 当前群是否同时在 `EXTRA_PLUGIN_GROUP_IDS` 和 `HOMOPHONE_GROUP_IDS`
- 修改 `.env` 后是否已重启
- 当前是否为私聊

### 昔涟接口不可用

优先检查：

- `XILIAN_API_URL`
- `XILIAN_API_KEY`
- `XILIAN_API_MODEL`
- 上游接口是否支持当前模型的 Responses API

### 群统计没有自动发

优先检查：

- 本群是否已由管理员执行 `@机器人 /开启统计`
- 机器人在 `23:59:59` 前后是否在线
- 机器人是否仍在该群中

## License

本项目使用 [MIT License](./LICENSE)。
