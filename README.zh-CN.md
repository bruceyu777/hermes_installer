# Hermes Agent 团队配置

面向 FortiOS QA 虚拟机的开箱即用 [Hermes Agent](https://hermes-agent.nousresearch.com) 配置：
接入公司内部 LLM 网关并自动回退，另接 Jenkins、Mantis 和 Log Intelligence 三个 MCP 服务器，
工具筛选与 Log Intelligence AI Assistant（以及 opencode 配置）一致。终端和 VS Code 都能用。
克隆、填入自己的 token、运行一个脚本即可。English: [README.md](README.md)

## 1. 开始之前

| 需要 | 检查方式 |
|---|---|
| 一台你能登录的 Linux 机器（你的虚拟机或测试节点），有 `bash`、`curl`、`python3`、`git`，约 3 GB 空闲空间 | `df -h ~` |
| 本**私有**仓库的读取权限：请仓库所有者把你加为协作者 | 在该机器上：`ssh -T git@github.com` 回答 `Hi <you>!` |
| 你的 token，至少一个 LLM 密钥（见 [Token](#token)） | — |
| 可选：带 Remote-SSH 的 VS Code | — |

不需要 sudo：Hermes 安装在你的主目录中。

## 2. 安装

```bash
# 1. 获取仓库
git clone git@github.com:bruceyu777/hermes_installer.git ~/git/hermes && cd ~/git/hermes

# 2. 你的 token（与 opencode_installer 的变量名相同：可以 cp ~/git/opencode/tokens.env .）
cp tokens.env.example tokens.env && chmod 600 tokens.env
vi tokens.env                       # 填写你有的，其余留空

# 3. 安装：首次 3–7 分钟，无需回答任何问题
./install.sh                        # 在你每天使用的机器上加 --cron（每小时重新检查模型）；
                                    # 共享测试节点上不要加
```

SSH 连接不稳定时，在 `tmux` 中运行第 3 步，或以分离方式运行：
`nohup ./install.sh </dev/null > ~/hermes-install.log 2>&1 &`，然后 `tail -f ~/hermes-install.log`。

**4. 确认安装成功。** 输出的最后部分应该是这样：

```
    logintel: Connected (1072ms)
    mantis-tools: Connected (1115ms)
    jenkins: Connected (1220ms)
==> Check: plugin mcp-ask-first
    enabled: Mantis filing/notes/email and Jenkins trigger/rebuild ask before running
==> Check: each model of the fallback chain (gateway first, then one Hermes prompt)
    glm-5.3-flash            gateway ok, Hermes PONG
    …                        gateway ok, Hermes PONG
```

出现 `!!`、`refused` 或错误：见 [故障排查](#故障排查)。然后打开一个**新终端**，让 `hermes` 进入 `PATH`。

随时可用 `./install.sh --check` 再次检查。完整验收测试（真实调用，需要几分钟）是 `tests/acceptance.sh`。
以后更新：`git pull && ./install.sh`，token 会保留。

## 3. 使用

**在终端中**（直接 ssh、VS Code 终端、tmux）。在项目文件夹中启动：

```bash
cd ~/git/<你的项目>
hermes                   # 交互式对话   （hermes --tui = 新的全屏界面）
hermes -c                # 继续上一次会话
hermes -z "问题"          # 问一个问题，打印回答后退出
```

| 按键 / 命令 | 作用 |
|---|---|
| `Enter` / `Alt+Enter` 或 `Ctrl+J` | 发送 / 换行 |
| `Ctrl+C` | 打断 agent（2 秒内按两次：退出） |
| `Ctrl+G` | 在编辑器中编写提示 |
| `/new` · `/sessions` · `/model` | 新会话 · 浏览会话 · 切换模型 |
| `/status` · `/tools list` · `/help` | 模型和 token · agent 拥有的工具 · 所有命令 |
| `/quit` 或 `Ctrl+D` | 退出 |

**在 VS Code 中：** 用 Remote-SSH 连接，安装后执行一次 **Developer: Reload Window**，
然后在命令面板中执行 **ACP: Connect to Agent** → *Hermes Agent*。**Ctrl+Shift+A**（Mac 上是 **Cmd+Shift+A**）
打开对话面板；**Esc** 取消当前这一轮。审批请求会显示在面板中。修改配置后：**ACP: Restart Agent**。

**试试：**
- *"最新的 FortiOS 构建中哪些用例失败了？按根因分组。"*（Log Intelligence）
- *"在 Mantis 中搜索关于 'ipsec tunnel flap after upgrade' 的 bug，总结前 3 个。"*
- *"获取 Jenkins 任务 X #1234 的构建日志，解释失败原因。"*

**需要知道：**
- 提交 Mantis bug 或备注、发邮件、触发 Jenkins 构建都会**先询问你**：允许一次 / 本会话内允许 / 永久 / 拒绝。
  建议选"一次"或"本会话"。在 `hermes -z` 中无人应答，所以会被拦截：这些操作请在对话中进行。
- 某个模型失败时，同一条消息会自动发给下一个模型。
- 更多内容：全部快捷键和命令、tmux、技巧与陷阱，见 [docs/guides/install-on-a-node.zh-CN.md](docs/guides/install-on-a-node.zh-CN.md) 第 4–6 节。

## 配置内容

| 项目 | 设置 |
|---|---|
| 模型链 | 测量得出，而非写死。`install.sh` 对每个 key 的 `prefer` 列表（[config/model-preferences.yaml](config/model-preferences.yaml)）逐个发一次单 token 的对话调用，保留最先应答的模型：先个人 fos-ai key，再第二个 fos-ai key，最后内部 Qwen 网关。2026-09-23 的结果是 `glm-5.3-flash` → `glm-5.3` → `qwen3.6-35b-a3b` → `qwen3.5-122b-a10b-awq`。 |
| 跟上网关变化 | 网关会不经通知地改变每个 key 能调用的模型。`./install.sh --adapt` 重新测量并改写模型链；`--cron` 让它每小时运行一次。网关新出现的模型只会被报告（"new models seen"），加入 `prefer` 列表后才会使用。 |
| 回退 | Hermes 内置的 `fallback_providers`：某个模型返回 401、403、404、429 或 5xx 时，本轮对话转到下一个模型。 |
| MCP 服务器 | `jenkins`（10 个工具）、`mantis-tools`（8 个）、`logintel`（26 个只读工具）、`microsoft365`（OAuth，可选，未测试）。这些服务器的其余工具对模型隐藏。 |
| 防护 | 提交 Mantis bug、添加 Mantis 备注、发邮件、启动或重跑 Jenkins 构建前都会先询问（插件 [`mcp-ask-first`](config/plugins/mcp-ask-first/__init__.py)）。终端里是提示，VS Code 里是权限请求。无人应答的运行（`hermes -z`、`hermes chat -q`、cron）中直接拦截。 |
| 密钥 | `config.yaml` 中没有任何 token；全部在 `~/.hermes/.env`（权限 600）中，按名称引用。 |

## Token

在 `tokens.env` 中填写你有的，其余留空。

| 变量 | 用途 | 获取方式 | 留空时 |
|---|---|---|---|
| `FOS_AI_API_KEY` | 主模型 | 个人 fos-ai 网关 key（AI 团队） | 模型链从第二个 key 开始 |
| `FOS_AI_FALLBACK_API_KEY` | 回退 1 | 另一个有独立预算的 fos-ai key，例如项目 token | 复用 `FOS_AI_API_KEY` |
| `LOCAL_QWEN_API_KEY` | 最后的回退 | 内部 Qwen 网关 `releaseqa-aiserver` 的 key（QA infra 团队） | 本地模型不可用 |
| `MANTIS_MCP_TOKEN` | Mantis MCP | 你的 fos-qa-assistant API token，即在 Log Intelligence AI Assistant "My connections" 中填写的那个 | 禁用 Mantis 服务器 |
| `JENKINS_MCP_TOKEN` | Jenkins MCP | `user:apitoken`。Jenkins：用户名 → Configure → API Token → Add new token | 禁用 Jenkins 服务器 |
| `LOGINTEL_MCP_KEY` | Log Intelligence MCP | Log Intelligence 管理员发放的个人 key（以 `limcp_` 开头） | 禁用 Log Intelligence 服务器 |

Microsoft 365 不需要 token：在能打开浏览器的终端里运行一次 `hermes mcp login microsoft365`，
再运行 `./install.sh`（检测到已保存的登录后会启用该服务器）。此项尚未测试。

更换 token：修改 `tokens.env` 后重新运行 `./install.sh`。

## `install.sh` 会改动什么

| 位置 | 改动 |
|---|---|
| Hermes 本身 | 没有 `hermes` 时：用官方安装脚本安装并固定到测试过的 commit，跳过设置向导、浏览器工具和 Computer Use。低于 v0.21.4 时：`hermes update --yes`。否则不动。 |
| `~/.hermes/.env` | 每个 token 一行，其余行保留；旧文件存为 `.env.bak-<时间>`。 |
| `~/.hermes/config.yaml` | 只改 `model`、`fallback_providers`、本仓库的四个 `mcp_servers` 条目和 `plugins.enabled`；注释和其他键都保留。旧文件存为 `config.yaml.bak-<时间>`。 |
| `~/.hermes/plugins/mcp-ask-first/` | 复制 |
| VS Code（如已安装） | 安装 ACP Client 扩展（`formulahendry.acp-client`），并添加使用 `hermes` 完整路径的 `acp.agents["Hermes Agent"]`（已有可用条目时不动） |
| crontab（仅 `--cron`） | `17 * * * * … install.sh --adapt`，日志在 `~/.hermes/logs/hermes_installer_adapt.log` |
| 检查 | 列出并连接 MCP 服务器，检查插件，再对模型链中每个模型做一次网关直连调用和一次 Hermes 对话（使用临时 home） |

| 选项 | 作用 |
|---|---|
| `--adapt` | 只重新测量模型并改写模型链 |
| `--cron` | 同时把每小时的 `--adapt` 加入 crontab |
| `--check` | 只做检查 |
| `--no-install` | 不动 Hermes 安装 |
| `--no-vscode` | 不动 VS Code |
| `--no-check` | 跳过检查 |
| `--tokens FILE` | 从其他文件读取 token |

支持 `HERMES_HOME`（默认 `~/.hermes`）。

## 需要了解的限制

- **筛选不是沙箱。** 隐藏工具和先询问只能阻止模型使用 MCP *工具*。模型仍以你的身份执行 shell
  命令并能读取 `~/.hermes/.env`，因此可能直接调用服务器。2026-09-23 同一台虚拟机上的一个
  opencode 会话就用 Mantis token 这样做过。请留意它运行的命令。
- 审批提示中的 **"Allow always"** 会在 `config.yaml`（`command_allowlist`）里为该工具写入永久放行规则。
  建议选 "allow once" 或 "allow for this session"。
- **`approvals.mode: off`**（或 `--yolo`）会连同其他所有审批一起关闭先询问提示。

## 故障排查

| 现象 | 原因 | 处理 |
|---|---|---|
| 检查显示 `gateway refused: HTTP 403 … does not permit model` | 网关收回了该 key 对此模型的权限 | `./install.sh --adapt`；若首选模型都不可用，把该 key 提供的模型（见 "new models seen"）加入其 `prefer` 列表 |
| 检查显示 `refused: HTTP 429 … budget` | 该 key 在当前时段预算用完 | 无需处理：模型链会转到下一个；之后 `--adapt` 会重新启用 |
| 某个 MCP 服务器显示 `✗` 或报错 | token 错误或服务器宕机 | `hermes mcp test <name>`；在 `tokens.env` 中修正后运行 `./install.sh` |
| 没有出现审批提示，工具被拦截 | `hermes -z` / `chat -q` 运行时无人应答 | 在交互式会话中执行该请求 |
| VS Code 中看不到配置改动 | agent 在改动之前已启动 | `ACP: Restart Agent` 或重新加载窗口 |
| `hermes model` 显示从未设置过的 z.ai provider | `~/.hermes/.env` 中有 `GLM_API_KEY` 行（Hermes 把这个名字当作 z.ai） | 删除它；本仓库从不使用这个名字 |

## 维护本仓库

- **模型：** [config/model-preferences.yaml](config/model-preferences.yaml)。`config/config.yaml`
  中的模型链只是全新配置的起点。
- **MCP 工具筛选** 与 Log Intelligence AI Assistant 和 opencode 配置保持一致（`config/config.yaml`
  中的 `tools.include`、插件中的 `ASK_FIRST_TOOLS`），三处一起改。
- **升级 Hermes：** 先测试新版本，再修改 `install.sh` 中的 `TESTED_HERMES_VERSION` 和 `TESTED_HERMES_COMMIT`。
- **切勿提交** `tokens.env`，`.gitignore` 已排除。

## 文档

| 文档 | English | 简体中文 |
|---|---|---|
| 在另一台机器上安装（node16）：步骤、测试、终端用法、技巧与陷阱 | [docs/guides/install-on-a-node.md](docs/guides/install-on-a-node.md) | [docs/guides/install-on-a-node.zh-CN.md](docs/guides/install-on-a-node.zh-CN.md) |
| 本虚拟机：v0.21.4、LLM 链、MCP 服务器、自动适配 | [docs/guides/fosqa-vm-llm-chain.md](docs/guides/fosqa-vm-llm-chain.md) | [docs/guides/fosqa-vm-llm-chain.zh-CN.md](docs/guides/fosqa-vm-llm-chain.zh-CN.md) |
| 安装、更新、回滚 Hermes | [docs/guides/install-hermes.md](docs/guides/install-hermes.md) | [docs/guides/install-hermes.zh-CN.md](docs/guides/install-hermes.zh-CN.md) |
| VS Code：ACP Client 扩展 | [docs/guides/vscode-acp.md](docs/guides/vscode-acp.md) | [docs/guides/vscode-acp.zh-CN.md](docs/guides/vscode-acp.zh-CN.md) |
| 最初的回退设计（另一台工作站） | [docs/guides/model-fallback.md](docs/guides/model-fallback.md) | [docs/guides/model-fallback.zh-CN.md](docs/guides/model-fallback.zh-CN.md) |

全部文档索引：[docs/INDEX.md](docs/INDEX.md)。
