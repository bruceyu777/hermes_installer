---
title: 在另一台机器上安装 opencode 和 Hermes —— node16 实录、技巧与陷阱
kind: guide
created: 2026-09-23
updated: 2026-09-23
status: current
verified_against: all-in-one-node16（10.96.234.11），Ubuntu 24.04 LTS，内核 6.8.0-60；opencode 1.18.32（安装仓库 59bc42a），Hermes Agent v0.21.4（安装仓库 534f426）；2026-09-23 22:45–23:05 PDT 实际运行
summary: 两个 agent 第一次安装到 fosqa 虚拟机以外的机器上，全程无人工输入：准确的命令、每一步的输出、验收测试（opencode T1–T8、Hermes H1–H9）、在 shell 终端中的用法，以及遇到的每一个陷阱。两个安装仓库中是同一份文件。
related:
  - ../INDEX.md
  - install-on-a-node.md
  - fosqa-vm-llm-chain.zh-CN.md
---

# 在另一台机器上安装 opencode 和 Hermes：node16

本文档在两个仓库（`opencode_installer` 和 `hermes_installer`）中内容相同。
它记录了在一台从未装过这两个 agent 的机器上的第一次安装，以及之后值得知道的一切。

## 1. 机器情况

| 项目 | node16 |
|---|---|
| 主机 | `all-in-one-node16`，`10.96.234.11`（来自 Log Intelligence 的 `node_ip_mappings`） |
| 系统 | Ubuntu 24.04 LTS，内核 6.8.0-60，用户 `fosqa` |
| 安装前 | 没有 Node.js、没有 npm、没有 opencode、没有 Hermes；有 `python3` 3.12、`git`、`curl`、`crontab`，以及 VS Code 服务器（5 个版本） |
| 网络 | GitHub（HTTPS，以及以 `bruceyu777` 身份的 SSH）、npm 仓库、`fos-ai.fortinet.com`、`releaseqa-aiserver` 均可访问 |
| 磁盘 | 剩余 58 GB |

开始前用同样的方式检查新机器：

```bash
for c in node npm python3 curl git crontab; do printf '%-8s %s\n' $c "$(command -v $c || echo MISSING)"; done
ssh -T git@github.com                     # "Hi <you>!" = 可以克隆私有仓库
curl -s -o /dev/null -w "fos-ai:%{http_code}\n" https://fos-ai.fortinet.com/v1/models        # 401 = 可访问
curl -s -o /dev/null -w "qwen:%{http_code}\n"   https://releaseqa-aiserver.corp.fortinet.com/v1/models
df -h ~
```

## 2. 安装（实际运行的命令）

两次安装都关闭了 stdin（`</dev/null`）：没有任何输入，也没有任何询问。
两者使用同一个 `tokens.env`（6 个值）。

```bash
# opencode
git clone git@github.com:bruceyu777/opencode_installer.git ~/git/opencode && cd ~/git/opencode
cp /path/to/tokens.env . && chmod 600 tokens.env
./install.sh </dev/null                      # 44.6 秒，exit 0

# Hermes
git clone git@github.com:bruceyu777/hermes_installer.git ~/git/hermes && cd ~/git/hermes
cp ~/git/opencode/tokens.env . && chmod 600 tokens.env
./install.sh </dev/null                      # 217.9 秒，exit 0
```

| 步骤 | opencode（44.6 秒） | Hermes（217.9 秒） |
|---|---|---|
| 程序 | `npm not available … using the official installer (~/.opencode/bin)` → `1.18.32 at /home/fosqa/.opencode/bin/opencode` | `not installed: running the official installer, pinned to commit c0d7294769` → `v0.21.4 at /home/fosqa/.local/bin/hermes` |
| token | 6 个 `set` → `~/.config/opencode/*.key` | 6 个 `set` → `~/.hermes/.env`（官方安装脚本先创建了一个模板 `.env`，被保留，备份为 `.env.bak-…`） |
| 模型 | 个人密钥对 glm-5.3 返回 403；模型链 `fos-ai/glm-5.3-flash → fos-ai-logintel/glm-5.3 → local-qwen/qwen3.6-35b-a3b → local-qwen/qwen3.5-122b-a10b-awq` | 相同的链 |
| VS Code | `sst-dev.opencode: installed`；在 Machine 设置中关闭 Python 终端自动激活 | `formulahendry.acp-client: installed`；`acp.agents["Hermes Agent"]` 写入 Machine 设置 |
| 检查 | ✓ logintel ✓ mantis-tools ✓ jenkins，⚠ microsoft365（OAuth）；4 个 PONG | 相同的服务器都已连接；插件已启用；4 个 "gateway ok, Hermes PONG" |

node16 上的模型链与 fosqa 虚拟机相同：它取决于**密钥**，而不是机器。

## 3. 验收测试

两个仓库都有 `tests/acceptance.sh`。安装后在仓库根目录运行：

```bash
cd ~/git/opencode && tests/acceptance.sh
cd ~/git/hermes   && tests/acceptance.sh
```

两者都会用你的 token 做真实调用。防护测试请求触发一个不存在的 Jenkins 任务
（`…-gate-demo-does-not-exist`），即使防护失效也只会得到 404。

**node16 上的 opencode：**

| # | 测试 | 结果 |
|---|---|---|
| T1 | 重跑 `install.sh --no-install --no-vscode --no-check` | `opencode.json unchanged`、`chain-fallback.json unchanged` |
| T2 | 在 `env -i` 下运行 cron 命令 | rc 0，`unchanged` |
| T3 | `install.sh --check` | 3 个服务器已连接，4 个 PONG |
| T4 | 模型 → logintel MCP 工具 | `⚙ logintel_logintel_list_projects` → `13` |
| T5 | 会话请求已移除的 `fos-ai/glm-5.3`（沙箱） | `> build · glm-5.3-flash` |
| T6 | 在运行中的 `opencode serve` 下改写 `chain-fallback.json`（沙箱） | 第 1 次 `fos-ai/glm-5.3-flash`，第 2 次 `local-qwen/qwen3.6-35b-a3b` |
| T7 | 在 `opencode run` 中调用 `jenkins_triggerBuild` | `permission requested: jenkins_triggerBuild (*); auto-rejecting` |
| T8 | `tokens.env` | 已被 git 忽略 |

**node16 上的 Hermes：**

| # | 测试 | 结果 |
|---|---|---|
| H1 | 重跑 | `.env unchanged`、`config.yaml unchanged` |
| H2 | 在 `env -i` 下运行 cron 命令 | rc 0，`unchanged` |
| H3 | 模型看到的工具（`tests/list_registered_mcp_tools.py`） | jenkins 10、logintel 26、mantis_tools 8 = 44 |
| H4 | 模型 → logintel MCP 工具（`hermes -z`） | `13` |
| H5 | 强制主模型返回 404 | `PONG`，`sessions.model = glm-5.3`（回退 1） |
| H6 | 在 `hermes -z` 中调用 `triggerBuild` | 在到达 Jenkins 之前被拦截 |
| H7 | 通过 ACP 调用 `triggerBuild`（`tests/acp_permission_test.py`） | 一个权限请求 `start a Jenkins build: {"jobFullName": …}`，回答 deny → 没有运行 |
| H8 | VS Code 条目和扩展 | `/home/fosqa/.local/bin/hermes ['acp']`、`formulahendry.acp-client-0.2.0` |
| H9 | `tokens.env` | 已被 git 忽略 |

## 4. 在 shell 终端中使用

VS Code 不是必需的。两个 agent 都是终端程序：可以在节点上的任何 shell 中运行
（直接 `ssh`、VS Code 终端、tmux），并且作用于**启动时所在的文件夹**。

```bash
ssh fosqa@node16                         # 或 VS Code Remote-SSH 中的终端
cd ~/git/<你的项目>                        # 一定要在项目文件夹中启动，不要在 ~ 中
opencode                                 # 全屏终端界面
hermes                                   # 交互式对话（经典 REPL）；`hermes --tui` = 新界面
```

安装完成后，打开一个**新**终端（或 `source ~/.bashrc`），让 `opencode` 和 `hermes` 进入 `PATH`。

### 常用命令

| 任务 | opencode | Hermes |
|---|---|---|
| 在当前文件夹启动 | `opencode` | `hermes` |
| 在其他文件夹启动 | `opencode ~/git/proj` | `hermes --in ~/git/proj` |
| 继续上一次会话 | `opencode -c` | `hermes -c` |
| 恢复指定会话 | `opencode -s <id>`（列表：`opencode session list`） | `hermes -r <id>`，或在对话中 `/sessions` |
| 一个问题，回答后退出 | `opencode run "…"` | `hermes -z "…"` |
| 本次运行指定模型 | `opencode -m local-qwen/qwen3.6-35b-a3b` | `hermes -m glm-5.3` |
| MCP 服务器 | `opencode mcp list` | `hermes mcp list`、`hermes mcp test <name>` |
| 模型 / 模型链 | `opencode models fos-ai` | `hermes fallback list` |
| 用量与费用 | `opencode stats` | 对话中 `/usage` |
| 健康检查 | 在 `~/git/opencode` 中 `./install.sh --check` | 在 `~/git/hermes` 中 `./install.sh --check` |

写操作工具（Mantis bug、备注、邮件、Jenkins 触发/重跑）在交互式会话中总会先询问。
在单次运行模式（`opencode run`、`hermes -z`）中会被拒绝，所以这些操作请在交互界面中进行。

### opencode 快捷键（终端界面）

leader 键是 `Ctrl+X`：先按它，再按字母。

| 按键 | 作用 |
|---|---|
| `Enter` / `Shift+Enter` 或 `Ctrl+J` | 发送 / 换行 |
| `Esc` | 停止当前回答 |
| `Tab` / `Shift+Tab` | 切换 agent：**build**（修改文件、运行命令）↔ **plan**（只读分析） |
| `Ctrl+P` | 命令面板：所有命令，可搜索 |
| `Ctrl+X` `n` / `l` | 新会话 / 会话列表 |
| `Ctrl+X` `m` | 选择模型 |
| `Ctrl+X` `e` | 在 `$EDITOR` 中编写提示 |
| `Ctrl+X` `c` | 压缩对话（对话很长时） |
| `Ctrl+X` `b` | 侧边栏（上下文、MCP、改动的文件） |
| `Ctrl+X` `x` / `y` | 导出会话 / 复制最后一个回答 |
| `Ctrl+C` / `Ctrl+D` / `Ctrl+X` `q` | 退出（`Ctrl+C` 会先清空输入） |
| 提示中输入 `@` | 附加项目中的文件（模糊搜索） |
| 行首输入 `!` | 运行一条 shell 命令，并把输出交给模型 |
| 行首输入 `/` | 斜杠命令，例如 `/init`（为项目生成 `AGENTS.md`）、`/undo`、`/redo` |

（默认值来自 opencode 的 keybinds 文档；可在 `opencode.json` 的 `keybinds` 中修改。）

### Hermes 快捷键与命令（对话）

| 按键 | 作用 |
|---|---|
| `Enter` / `Alt+Enter` 或 `Ctrl+J` | 发送 / 换行（`Shift+Enter` 仅在能区分它的终端中有效） |
| `Ctrl+C` | 打断 agent；2 秒内按两次则退出 |
| `Ctrl+D` | 退出 |
| `Ctrl+G` | 在 `$EDITOR` 中编写提示 |
| `Ctrl+S` | 暂存当前草稿，先发别的，再按 `Ctrl+S` 恢复 |
| `Ctrl+T` / `F6` | 子 agent 和后台进程的实时监视器 |
| `Ctrl+Z` | 挂起到 shell；`fg` 返回 |

| 命令 | 作用 |
|---|---|
| `/help` | 所有命令（`/help <文本>` 过滤） |
| `/new` | 新会话 |
| `/sessions`、`/resume [名称]`、`/title [名称]` | 浏览、恢复和命名会话 |
| `/model [名称]` | 为本会话切换模型（加 `--global` 则保留） |
| `/retry`、`/undo [N]` | 重发上一条消息 / 回退 N 轮 |
| `/compress` | 压缩过长的对话 |
| `/status`、`/usage` | 模型、token、上下文大小 / token 用量 |
| `/tools list` | 模型拥有的工具（MCP 工具名为 `mcp__<服务器>__<工具>`） |
| `/reload-mcp` | 修改配置后重新加载 MCP 服务器 |
| `/save md [文件]` | 把对话导出为 Markdown（或 `json`、`html`） |
| `/copy` | 把最后一个回答复制到剪贴板 |
| `/queue <提示>` | agent 还在工作时排队下一条提示 |
| `/quit` | 退出 |

审批提示（先询问的工具）提供：允许一次、本会话内允许、永久允许、拒绝。
建议选"一次"或"本会话"；"永久"是永久生效的。

### 终端技巧

- **通过 SSH 使用时用 tmux**，断线也不会中断长会话：
  `tmux new -s ai`，工作，`Ctrl+B` `d` 分离；之后 `tmux attach -t ai`。（Hermes 也会保存每个会话：
  `hermes -c` 从上次停下的地方继续。）
- **一个项目，一个终端。** 两个 agent 都会读取和修改启动目录中的文件；opencode 的 `/init`
  和项目中的 `AGENTS.md` 能告诉它们项目的规则。
- **先规划：** 在 opencode 中按 `Tab` 切到 **plan** agent 做提问和分析，需要改动时再按 `Tab`
  回到 **build**。
- **粘贴日志，而不是描述日志：** 粘贴失败的日志行，或 `@path/to/log`，或在 Hermes 中给出文件路径。
  Log Intelligence 工具已经知道构建和 QAID："*QAID 123456 在构建 3510 上为什么失败？*"
- **是谁应答的？** opencode 在状态栏以及 `opencode run` 之后显示模型（`> build · <model>`）；
  Hermes：`/status`。
- **给脚本用的输出：** `hermes -z "…"` 只打印最终回答，方便管道处理；
  `opencode run "…"` 会在回答后加一行 `> build · <model>`。
- **不要让两个 agent 同时改同一批文件。** 它们不知道彼此的修改。

## 5. 技巧

- **关闭 stdin 来证明"无需人工"：** `./install.sh </dev/null`。如果有任何步骤试图询问，它会失败而不是等待。
- **复用同一个 token 文件。** 两个安装脚本读取相同的 6 个变量名，直接复制已有的即可。使用相同密钥的机器，得到的模型链也相同。
- **通过 SSH 做耗时安装：** Hermes 需要约 3–7 分钟。以分离方式启动，然后轮询日志：
  ```bash
  nohup bash -c './install.sh </dev/null; echo exit=$?' > ~/hermes-install.log 2>&1 </dev/null &
  grep -q '^exit=' ~/hermes-install.log && tail -3 ~/hermes-install.log
  ```
- **在节点上的克隆中运行测试：** `ssh node 'cd ~/git/opencode && tests/acceptance.sh'`。脚本根据自身路径找到仓库，Hermes 的脚本还需要 `tests/` 中的两个 Python 辅助脚本，所以不要用 `bash -s` 管道方式传入。
- **安装前先检查：** 第 1 节的命令块只需 10 秒，能提前回答所有"为什么失败"的问题。
- **确认是谁应答的：** opencode 在 `opencode run` 之后打印 `> build · <model>`；Hermes 记录在 `~/.hermes/state.db`（`sessions.model`）。
- **安装后的 VS Code：** Developer: Reload Window。opencode：在项目文件夹中按 Ctrl+Escape。Hermes：ACP: Connect to Agent → Hermes Agent。

## 6. 陷阱（每一个都真实发生过）

| 陷阱 | 看到的现象 | 处理方式 |
|---|---|---|
| 节点上没有 SSH 密钥 | `fosqa` 和 `root` 都是 `Permission denied (publickey,password,keyboard-interactive)` | 同事按正常方式登录（VS Code Remote-SSH、密码）。脚本化访问时，我们用了 Log Intelligence `.env` 中已有的节点登录信息（`NODE_SSH_USER` / `NODE_SSH_PASSWORD`），通过 `sshpass -e` 传递，密码不会出现在命令行或输出中。不要在共享节点上额外安装 SSH 密钥 |
| 私有仓库 | 克隆需要的是**节点**上的 GitHub 访问权限，而不是你笔记本上的 | 先在节点上运行 `ssh -T git@github.com`。node16 已有密钥；否则把你的密钥加到 GitHub，或用 token 通过 HTTPS 克隆 |
| 没有 Node.js / npm | opencode 无法走 npm 路径 | 无需处理：安装脚本会改用 opencode 官方安装脚本装到 `~/.opencode/bin`。不需要 sudo |
| 刚装完就提示 `opencode` / `hermes` "command not found" | 安装脚本把 PATH 写进了 `~/.bashrc`；而 `~/.local/bin` 只有在登录时已存在才会被 `~/.profile` 加入 | 打开新终端，或 `source ~/.bashrc`，或直接调用 `~/.opencode/bin/opencode` / `~/.local/bin/hermes` |
| "VS Code 扩展不在" | 你连接（22:48）时 VS Code 下载了更新的服务器，而安装发生在 22:46 | 它**在**：扩展装在共享的 `~/.vscode-server/extensions` 中，节点日志显示按 Ctrl+Escape 时 `sst-dev.opencode` 已激活。旧窗口看不到就重新加载窗口 |
| SSH 会话一直不返回 | 在 `ssh` 中启动 `nohup … &`，会话一直保持到安装结束 | 把后台任务的**三个**流都重定向，包括 `</dev/null` |
| 工具被拒绝后 `opencode run` 挂住 | T7：先询问的调用被自动拒绝，然后运行一直等到我们的 180 秒超时 | 单次运行时属于预期行为；交互式会话会弹出对话框。脚本中的 `opencode run` 一定要套上 `timeout` |
| Hermes 不会重试被拒绝的操作 | H7：拒绝之后，在同一对话中选"允许一次"也没有新的请求；模型拒绝重试 | 设计如此（拒绝信息写明"不要重试"）。要测试"允许"，请开新会话 |
| Hermes `-z` 自动批准一切 | `hermes -z` 设置了 `HERMES_YOLO_MODE=1` | `mcp-ask-first` 插件会在 `-z`、`chat -q` 和 cron 中拦截写操作工具。需要审批的事不要用 `-z` |
| 共享节点上的 token | 任何能以 `fosqa` 登录的人都能读取 `~/.config/opencode/*.key` 和 `~/.hermes/.env` | 使用你自己的节点，或用完后移除 agent。安装完成后仓库里的 `tokens.env` 就不再需要了：`shred -u tokens.env`（重跑会保留已安装的密钥） |
| 测试节点上的每小时 cron | `--cron` 会添加一个使用你密钥的每小时任务 | node16 **没有**加 cron。只在你每天工作的机器上添加：`./install.sh --no-install --no-check --cron` |
| VS Code Machine 设置被修改 | 两个安装脚本都会修改 `~/.vscode-server/data/Machine/settings.json`（关闭 Python 自动激活、`acp.agents`） | 会先写一份 `.bak-<时间>` 备份；这个修改会影响节点上以该用户使用 VS Code 的所有人 |
| Microsoft 365 显示 ⚠ / 禁用 | OAuth 需要浏览器 | 符合预期；可选且未测试 |

## 7. 卸载

```bash
# opencode
rm -rf ~/.opencode ~/.config/opencode ~/.local/share/opencode ~/git/opencode
sed -i '/\.opencode\/bin/d' ~/.bashrc
# Hermes
rm -rf ~/.hermes ~/.local/bin/hermes ~/git/hermes
# VS Code：用 ~/.vscode-server/data/Machine/settings.json.bak-<时间> 恢复设置，
# 并在扩展视图中卸载 sst-dev.opencode / formulahendry.acp-client
crontab -l | grep -v 'install.sh --adapt' | crontab -     # 仅当你加过 --cron 时
```

## History

| Date | Change |
|---|---|
| 2026-09-23 | 在 node16 上首次安装两个 agent 后创建：命令、输出、验收测试 T1–T8 / H1–H9（`tests/acceptance.sh`）、在 shell 终端中使用两个 agent（命令、快捷键、斜杠命令）、技巧、陷阱、卸载。 |
