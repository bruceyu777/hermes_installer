---
title: Hermes Agent：通过 Telegram 机器人在手机上使用
kind: guide
created: 2026-10-20
updated: 2026-10-20
status: current
verified_against: 工作站上的 Hermes Agent v0.16.0（gateway 作为 systemd 用户服务），Ubuntu（内核 7.0.0-38），机器人 @nm_hermes_test_bot，2026-10-20 实测；v0.21.4 虚拟机上的同一 install.sh 路径尚未运行
summary: 用 @BotFather 创建机器人，把 token 和你的数字用户 ID 填进 tokens.env，运行 install.sh；它会把 Hermes 消息网关安装为用户服务并重启，机器人即可在 Telegram 中应答。含验证、群聊用法、故障排查和 token 的权限说明。
related:
  - ../INDEX.md
  - telegram.md
  - install-hermes.zh-CN.md
  - install-on-a-node.zh-CN.md
  - ../../install.sh
  - ../../tokens.env.example
---

# Hermes Agent：通过 Telegram 机器人在手机上使用

Hermes Agent 有一个**消息网关**：一个常驻进程（`hermes gateway run`），把 agent 接到
Telegram、Discord、Slack、WhatsApp 等平台。本仓库只接 Telegram。网关跑起来后，你在任何设备的
Telegram 里对话的就是同一个 agent：同样的模型链、MCP 服务器和先询问规则。语音消息会被转写，
图片和文件会交给 agent，定时任务（`hermes cron`）的结果可以投递到你的聊天里。

本指南完成后机器的状态：

| 项目 | 状态 |
|---|---|
| 机器人 | 每人一个，用 @BotFather 创建；token 以 `TELEGRAM_BOT_TOKEN` 存在 `~/.hermes/.env` |
| 访问控制 | 只有 `TELEGRAM_ALLOWED_USERS` 里的数字用户 ID 会得到回答，其他人被忽略 |
| 主频道 | `TELEGRAM_HOME_CHANNEL`（默认是你的 ID）：cron 结果和网关通知发到这里 |
| 服务 | `~/.config/systemd/user/hermes-gateway.service`，已启用并开启 linger，注销和重启后仍在 |
| 传输 | 网关向 `api.telegram.org` 长轮询（仅出站 HTTPS，不监听任何端口） |
| 检查 | `./install.sh --check` 验证 token，显示服务状态和 Telegram 连接状态 |

**不要**在共享测试节点（nodeNN）上配置：网关是以你的身份全天运行的进程，一个能在 Jenkins
节点上执行 shell 的机器人对谁都不是好事。请用自己的虚拟机或工作站。

## 1. 创建机器人（Telegram 侧，2 分钟）

1. 在 Telegram 中打开 **@BotFather**（https://t.me/BotFather），发送 `/newbot`。
2. 填显示名（随意）和用户名（必须以 `bot` 结尾且未被占用），例如 `yourname_hermes_bot`。
3. BotFather 回复 token：`123456789:ABCdefGHIjklMNOpqrSTUvwxYZ`。当作密码对待。若泄露：
   在 BotFather 中 `/revoke`，然后用新 token 重新运行 `install.sh`。
4. 你的**数字用户 ID**：给 **@userinfobot** 发条消息，它会回复一个数字，如 `7648384944`。
   `@用户名` 不是它。

可选，同样在 BotFather 中 `/setcommands`：

```
help - Show help information
new - Start a new conversation
sethome - Set this chat as the home channel
```

## 2. 配置

### 用本仓库（推荐）

在 `tokens.env` 中：

```bash
TELEGRAM_BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrSTUvwxYZ
TELEGRAM_ALLOWED_USERS=7648384944        # 多人用逗号分隔
TELEGRAM_HOME_CHANNEL=                   # 留空 = 上面那一个 ID
```

然后 `./install.sh`。它依次做（[install.sh](../../install.sh) 中的 `configure_gateway`）：

| 步骤 | 执行的命令 | 说明 |
|---|---|---|
| token | `scripts/merge_into_hermes_home.py tokens` | 三个名字写入 `~/.hermes/.env`；ID 只能是数字和逗号，否则报告并忽略；主频道留空时取唯一的那个 ID |
| 服务 | `hermes gateway install`（对它的两个问题回答 "y"） | 写用户 unit，`systemctl --user enable`，开启 linger。再次运行时打印 `Service already installed`，不做改动 |
| 重启 | `hermes gateway restart` | 网关在启动时读 `.env`，所以新 token 或新 ID 靠这一步生效 |
| 检查 | `./install.sh --check` 的 "Check: Telegram bot" 部分 | 见第 3 节 |

三个 Telegram 行都为空时，这一步只打印 `no TELEGRAM_BOT_TOKEN: gateway left alone`，别的什么都不做。
`--no-gateway` 即使有 token 也跳过这一步。

### 手工（不用仓库）

```bash
cat >> ~/.hermes/.env <<'EOT'
TELEGRAM_BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrSTUvwxYZ
TELEGRAM_ALLOWED_USERS=7648384944
TELEGRAM_HOME_CHANNEL=7648384944
EOT
chmod 600 ~/.hermes/.env
hermes gateway install        # 用户服务，两次回答 Y
hermes gateway restart
```

或者 `hermes gateway setup` 选 Telegram：交互式向导，问的是同样两个值。没有 systemd 的环境
（WSL1、容器、Termux）不装服务，改在 `tmux` 里跑 `hermes gateway run`。

## 3. 验证

2026-10-20 在工作站上的实测输出（`./install.sh --check` 的 Telegram 部分；服务此前已手工装过，
所以安装步骤报告"已存在"）：

```
==> Gateway: Telegram bot
    Service already installed at: /home/yzhengfeng/.config/systemd/user/hermes-gateway.service
    ✓ User service started
    gateway restarted (it re-reads .env on start)
…
==> Check: Telegram bot
    token ok: bot @nm_hermes_test_bot
    allowed users: 1 ID(s), home channel set
    Active: active (running) since Tue 2026-10-20 14:45:32 PDT; 22s ago
    ✓ User gateway service is running
    ✓ Systemd linger is enabled (service survives logout)
    telegram: connected (gateway running, updated 2026-10-20T21:45:40)
```

含义：token 在 `api.telegram.org` 有效（检查调用 `getMe`，只打印机器人名字）；服务在运行；
网关自己的状态文件（`~/.hermes/gateway_state.json`）显示 Telegram 连接为 `connected`。刚重启后
最后一行可能还是 `connecting` 或旧时间戳：这里用了大约 8 秒。

然后是真正的测试：在 Telegram 里打开机器人，发 *hello*。第一次回答需要几秒（模型调用）。手工查看：

```bash
hermes gateway status                     # 服务、linger、各平台状态
journalctl --user -u hermes-gateway -f    # 实时日志；找 "[telegram] Connected"
```

## 4. 使用

| 在聊天中 | 效果 |
|---|---|
| 任意文字 | agent 的一轮对话，工具和审批与终端相同；先询问的工具（提交 Mantis、发邮件、触发 Jenkins）在聊天里问你，回复 `yes` 或 `no`；agent 的提问（`clarify`）带按钮 |
| `/new` | 开始新会话（否则每个聊天保留上下文） |
| `/sethome` | 把当前聊天设为主频道（写入 `TELEGRAM_HOME_CHANNEL`） |
| `/help` | 网关的命令列表 |
| 语音消息 | 转写后按文字处理 |
| 图片或文件 | 交给 agent；它能读 PDF、日志、图片 |
| 回复中含 `MEDIA:/主机上的路径` | agent 把该文件作为附件发给你（png、pdf、log、csv、docx、zip……） |

定时任务：在终端里建的 `hermes cron` 任务把结果投递到主频道，于是"每天早上 8 点总结昨晚失败的构建"
就直接到手机上。

**群聊。** Telegram 机器人默认开启*隐私模式*：在群里只看得到 `/命令` 和对它自己消息的回复。
要么在 BotFather 中关掉（`/mybots` → 你的机器人 → Bot Settings → Group Privacy → Turn off，
然后**把机器人移出群再加回来**），要么把机器人设为群管理员。群聊还需要把群的 chat ID 写进
`config.yaml` 的 `telegram.allowed_chats`；本仓库不管理这个键。

## 5. 故障排查

| 现象 | 原因 | 处理 |
|---|---|---|
| 检查：`token refused: Unauthorized` | token 抄错或已吊销 | 从 BotFather（`/mybots` → API Token）重新复制到 `tokens.env`，`./install.sh` |
| 检查：`no answer from api.telegram.org` | 这台机器连不上 Telegram（公司出口限制） | 在该机器上 `curl -sS -m 10 https://api.telegram.org`；若被拦，在 `config.yaml` 写 `telegram: proxy_url: "socks5://host:port"` 或在 `.env` 写 `TELEGRAM_PROXY=socks5://host:port`，然后 `hermes gateway restart` |
| 检查：`hermes gateway install failed: User systemd not reachable` | 新 SSH 会话没有用户 D-Bus（还没开 linger） | `loginctl enable-linger $USER`（可能需要一次 sudo），注销再登录，重新 `./install.sh`；或 `tmux new -s hermes 'hermes gateway run'` |
| 机器人始终不回答，也没有报错 | 你的 ID 不在 `TELEGRAM_ALLOWED_USERS`（数字错了，或填了 `@用户名`） | 再问一次 @userinfobot，改 `tokens.env`，`./install.sh`。未知用户会被静默忽略 |
| 之前能回答，换 token 后不行 | 网关还拿着旧的 `.env` | `hermes gateway restart`（install.sh 会做；手工配置必须自己做） |
| `telegram: retrying … Conflict: terminated by other getUpdates request` | 同一个 token 被两个网关轮询（两台机器，或一个遗留的 `hermes gateway run`） | 停掉另一个：一个 token 只能有一个在跑的网关 |
| 群里机器人只理 `/命令` | 隐私模式 | 第 4 节"群聊" |
| 服务状态 `activating (auto-restart)` | 网关启动即崩溃，通常是配置错误 | `journalctl --user -u hermes-gateway --since '5 min ago'` |

## 6. 安全说明

- **token 就是机器人。** 拿到 token 的人能以机器人身份收发消息；配合白名单，只要其用户 ID 不在列表里，
  仍不能让 Hermes 执行任何东西，但 token 只应放在 `~/.hermes/.env`（权限 600）。`install.sh` 和检查从不打印它。
- **白名单是唯一的门。** 列表里的用户能做你在终端里能做的一切：以你的身份执行 shell、用你的 MCP token、
  读你的文件。只列自己和你愿意把 shell 交给的人。Hermes 文档里的 `hermes pairing`（一次性配对码）是另一种
  方式；本仓库保留静态列表，因为它可审阅。
- **先询问仍然有效**（插件 `mcp-ask-first`）：提交 Mantis、发邮件、触发 Jenkins 会在聊天里询问，你回复 `yes` 后才执行。
  不要用 `approvals.mode: off` 运行网关。
- **只有出站连接。** 长轮询不开端口。Webhook 模式（`TELEGRAM_WEBHOOK_URL`）是给会休眠的云主机用的，这里不用。
- **吊销**只需 BotFather 一条命令（`/revoke`），再用新 token 运行 `./install.sh`。

## History

| 日期 | 变更 |
|---|---|
| 2026-10-20 | 创建。install.sh 新增网关步骤、`--no-gateway` 和 Telegram 检查；tokens.env 新增三个 Telegram 行；在工作站上用机器人 @nm_hermes_test_bot 验证（网关服务已存在，重启后 8 秒内重新连接）。 |
