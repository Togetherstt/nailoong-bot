# Nailoong Bot

基于 `NoneBot2 + OneBot V11 + NapCatQQ` 的自定义 QQ 机器人项目。

当前已实现的核心功能：

- 奶龙表情包记忆
  - `@机器人 /添加奶龙 [名称可选]`
  - `@机器人 /随机奶龙`
  - `@机器人 /奶龙列表`
  - `@机器人 /删除奶龙 <序号|名称|最近一个>`
  - `@机器人 /撤销删除奶龙`
- 谐音盒
  - `@机器人 /添加首字母 <2到5位字母> [@群友可选]`
  - `@机器人 /绑定群友 <2到5位字母> @群友`
  - `@机器人 /解绑群友 <2到5位字母>`
  - `@机器人 /首字母列表`
  - `@机器人 /删除首字母 <2到5位字母>`
  - `@机器人 /谐音盒`
  - `引用一段文本 + /盒`

## 1. 适用场景

这个仓库只负责机器人逻辑层，不负责 QQ 客户端本体。

推荐部署结构：

- `NapCatQQ` 负责登录 QQ 小号、接收消息、发送消息
- 本项目负责通过 `OneBot V11 正向 WebSocket` 接收事件并执行业务逻辑

如果你是 fork 以后自己部署，这份文档就是按“从零部署到可用”来写的。

## 2. 项目结构

```text
Nailoong Bot/
├─ AGENTS.MD
├─ LICENSE
├─ README.md
├─ bot.py
├─ pyproject.toml
├─ requirements.txt
├─ .env.example
├─ data/
│  ├─ homophone_box/
│  └─ nailoong/
├─ src/
│  └─ plugins/
│     ├─ basic_reply/
│     ├─ homophone_box/
│     ├─ nailoong_memory/
│     ├─ utils/
│     └─ vision_api/
└─ tests/
```

## 3. 环境准备

### Python

要求：

- `Python 3.10+`

### 安装依赖

使用你自己的虚拟环境或 Conda 环境均可。

```powershell
pip install -r requirements.txt
```

如果你使用 Conda，也可以：

```powershell
conda create -n nailoong-bot python=3.11 -y
conda activate nailoong-bot
pip install -r requirements.txt
```

## 4. 创建配置文件

把 `.env.example` 复制成 `.env`，然后按你的实际环境修改。

### 推荐最小配置

```env
ENVIRONMENT=dev
LOG_LEVEL=DEBUG
HOST=127.0.0.1
PORT=8080
COMMAND_START=["/"]
ONEBOT_ACCESS_TOKEN=nailoong-local-token
ADMIN_QQ=123456789
HOMOPHONE_GROUP_IDS=123456789,987654321
SUPERUSERS=[]
VISION_API_URL=https://api.example.com/v1/chat/completions
VISION_API_KEY=sk-xxxxxx
```

### 关键字段说明

- `HOST`
  - 机器人监听地址
  - 一般保持 `127.0.0.1`
- `PORT`
  - 机器人监听端口
  - 必须和 NapCatQQ 里配置的正向 WebSocket 端口一致
- `ONEBOT_ACCESS_TOKEN`
  - NapCatQQ 和本项目之间的访问令牌
  - 两边必须完全一致
- `ADMIN_QQ`
  - 管理员 QQ 号
  - 只有这个 QQ 号可以使用奶龙库存管理命令
  - fork 本项目后，你只需要改这个值，就能换成你自己的管理员账号
- `HOMOPHONE_GROUP_IDS`
  - 允许使用谐音盒的群号列表
  - 多个群号用英文逗号分隔
  - 只有这些群里才能触发 `/谐音盒` 和 `/盒`
- `SUPERUSERS`
  - NoneBot 级别的超级用户配置
  - 当前项目并不依赖它做奶龙管理权限判断，但你可以为后续扩展保留
- `VISION_API_URL`
  - 预留给图像识别 / 多模态接口
- `VISION_API_KEY`
  - 对应的 API Key

## 5. 配置 NapCatQQ

### 第一步：登录机器人小号

在 NapCatQQ 中登录你要作为机器人的小号。

要求：

- 这个号能正常收发消息
- 能在群里被 `@`
- 你愿意长期用它作为机器人账号

### 第二步：开启 OneBot V11 正向 WebSocket

在 NapCatQQ 配置里：

1. 选择 `OneBot V11`
2. 连接方式选择 `正向 WebSocket`
3. 地址配置为：

```text
127.0.0.1:8080
```

如果你在 `.env` 中改了 `PORT`，这里也要同步改。

4. Access Token 填：

```text
nailoong-local-token
```

如果你在 `.env` 中改了 `ONEBOT_ACCESS_TOKEN`，这里也要同步改。

## 6. 启动机器人

### 直接运行

```powershell
python bot.py
```

### 或使用 NoneBot 热重载

```powershell
nb run --reload
```

如果启动正常，你会在终端看到 NoneBot 初始化和插件加载日志。

## 7. 功能使用

下面示例里的 `@机器人`，指的是你部署后的 QQ 小号。

### 7.1 奶龙表情包记忆

#### 添加奶龙

先引用一张奶龙图片，或者引用一个 QQ 表情包，然后发送：

```text
@机器人 /添加奶龙 憨笑奶龙
```

名称可省略：

```text
@机器人 /添加奶龙
```

机器人会记录：

- 图片或表情包内容
- 名称
- 添加者 QQ
- 添加时间

#### 随机奶龙

```text
@机器人 /随机奶龙
```

机器人会返回：

- 一张随机奶龙表情包
- 奶龙名称
- 添加者 QQ
- 添加日期

### 7.2 奶龙库存管理

只有 `.env` 里的 `ADMIN_QQ` 可以使用这些命令。

#### 查看库存

```text
@机器人 /奶龙列表
```

#### 按序号删除

```text
@机器人 /删除奶龙 2
```

#### 按名称删除

```text
@机器人 /删除奶龙 憨笑奶龙
```

#### 删除最近一条

```text
@机器人 /删除奶龙 最近一个
```

#### 撤销最近一次删除

```text
@机器人 /撤销删除奶龙
```

### 7.3 谐音盒

#### 添加首字母模式

```text
@机器人 /添加首字母 yzh
```

规则：

- 支持 `2` 到 `5` 位英文字母
- 自动转为小写
- 持久化保存，重启后不会丢失
- 可以在添加时直接绑定群友：

```text
@机器人 /添加首字母 yzh @某个群友
```

#### 查看首字母库

```text
@机器人 /首字母列表
```

#### 绑定群友

如果首字母已经存在，可以补绑或覆盖绑定关系：

```text
@机器人 /绑定群友 yzh @某个群友
```

#### 解绑群友

如果你不想再让某个首字母关联到某位群友：

```text
@机器人 /解绑群友 yzh
```

#### 删除首字母模式

```text
@机器人 /删除首字母 yzh
```

#### 触发谐音盒

先引用一段短文本，再发送：

```text
@机器人 /谐音盒
```

或者更快一点，直接：

```text
/盒
```

这个快捷入口不需要 `@机器人`。

注意：

- 谐音盒只在 `.env` 的 `HOMOPHONE_GROUP_IDS` 指定群内生效
- 私聊和未列入白名单的群不会触发谐音盒

### 7.4 谐音盒规则

- 引用文本中的有效中文字符数不能超过 `40`
- 候选单元可以是：
  - 单个中文字符
  - 连续英文单词
- 中文按拼音首字母匹配
- 英文按单词首字母匹配
- 选取的候选单元个数，和首字母模式长度一致
  - `yz` 匹配 2 个候选单元
  - `yzh` 匹配 3 个候选单元
  - `yzhh` 匹配 4 个候选单元
  - `yzhhl` 匹配 5 个候选单元
- 如果某个首字母绑定了群友，命中时会在结果后显示群昵称
- 命中结果超过 `10` 条时，只随机显示 `10` 条
- 同一条引用文本短时间连续触发时，会尽量轮换结果
- 同一条被引用消息有 `5` 秒冷却
  - 冷却期内再次触发，只回复：

```text
已经盒过了。
```

- 瞬时请求太多时会启用内部缓冲
  - 队列满了会回复：

```text
当前盒子太忙，请稍后再试。
```

### 7.5 绑定群友后的输出格式

如果某个首字母没有绑定群友：

```text
盒出了：
<杨州话>
```

如果某个首字母绑定了群友：

```text
盒出了：
<杨州话>（张三）
```

说明：

- 这里显示的是群昵称 / 群名片
- 不会再次 `@` 该群友，避免骚扰
- 如果后续执行 `/解绑群友 yzh`，则会恢复为不带括号昵称的原始输出

## 8. 数据存储

本项目当前不依赖数据库，数据保存在本地：

- 奶龙索引：`data/nailoong/index.json`
- 奶龙回收区：`data/nailoong/deleted.json`
- 奶龙图片：`data/nailoong/images/`
- 奶龙回收站图片：`data/nailoong/trash/`
- 谐音盒首字母库：`data/homophone_box/initials.json`

这意味着：

- 重启 bot 后，奶龙记忆和首字母库不会丢失
- 如果你要迁移机器人，记得连同 `data/` 一起迁移

## 9. 最小验证流程

部署完成后，至少验证这些步骤：

1. 启动 `bot.py`
2. 确认 NapCatQQ 已成功连接
3. 引用一张奶龙图片并发送：

```text
@机器人 /添加奶龙 憨笑奶龙
```

4. 发送：

```text
@机器人 /随机奶龙
```

5. 使用你的管理员 QQ 测试：

```text
@机器人 /奶龙列表
```

6. 测试删除：

```text
@机器人 /删除奶龙 1
```

7. 测试撤销：

```text
@机器人 /撤销删除奶龙
```

8. 添加首字母：

```text
@机器人 /添加首字母 yzh
```

9. 如果需要，测试绑定群友：

```text
@机器人 /绑定群友 yzh @某个群友
```

10. 如果需要，测试解绑：

```text
@机器人 /解绑群友 yzh
```

11. 在白名单群内引用一段短文本并发送：

```text
@机器人 /谐音盒
```

12. 再次对同一条引用消息在 5 秒内重复触发，确认是否返回：

```text
已经盒过了。
```

## 10. 单元测试

```powershell
python -m unittest discover -s tests -v
```

## 11. 常见问题

### 机器人没有回复

优先检查：

- NapCatQQ 是否已连接到 `HOST:PORT`
- `ONEBOT_ACCESS_TOKEN` 是否两边一致
- 小号是否真的被 `@`
- 消息格式是否符合命令要求

### 奶龙管理命令没反应

检查：

- `.env` 中是否正确设置了 `ADMIN_QQ`
- 你当前发命令的账号是否就是这个 QQ

### 谐音盒完全没反应

优先检查：

- 当前群号是否已经写入 `.env` 的 `HOMOPHONE_GROUP_IDS`
- 修改 `.env` 后是否已经重启 bot
- 当前是不是私聊，或不是白名单群

### 谐音盒提示“已经盒过了”

这是正常行为，表示同一条被引用消息还在 5 秒冷却内。

### 谐音盒提示“当前盒子太忙，请稍后再试。”

这是正常保护机制，说明短时间内请求太多。稍后重试即可。

## 12. License

本项目使用 [MIT License](./LICENSE)。
