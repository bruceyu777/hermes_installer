# Hermes Agent 团队配置

面向 FortiOS QA 虚拟机的开箱即用 [Hermes Agent](https://hermes-agent.nousresearch.com) 配置：
接入公司内部 LLM 网关并自动回退，另接 Jenkins、Mantis 和 Log Intelligence 三个 MCP 服务器，
工具筛选与 Log Intelligence AI Assistant（以及 opencode 配置）一致。终端和 VS Code 都能用。
克隆、填入自己的 token、运行一个脚本即可。English: [README.md](README.md)

## 快速开始

```bash
git clone git@github.com:bruceyu777/hermes_installer.git ~/git/hermes && cd ~/git/hermes
cp tokens.env.example tokens.env && chmod 600 tokens.env
vi tokens.env           # 填入自己的 token；该文件不会进 git
./install.sh --cron     # 缺少时安装 Hermes v0.21.4，部署配置，全部检查一遍，并每小时重新测量模型
```

已经在用 opencode 配置？它的 `tokens.env` 变量名相同：`cp ~/git/opencode/tokens.env .`

之后在终端运行 `hermes`；或在 VS Code 中执行 **Developer: Reload Window**，再执行
**ACP: Connect to Agent** → Hermes Agent（Remote-SSH 同样可用）。以后更新：
`git pull && ./install.sh`，token 会保留。

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
| 本虚拟机：v0.21.4、LLM 链、MCP 服务器、自动适配 | [docs/guides/fosqa-vm-llm-chain.md](docs/guides/fosqa-vm-llm-chain.md) | [docs/guides/fosqa-vm-llm-chain.zh-CN.md](docs/guides/fosqa-vm-llm-chain.zh-CN.md) |
| 安装、更新、回滚 Hermes | [docs/guides/install-hermes.md](docs/guides/install-hermes.md) | [docs/guides/install-hermes.zh-CN.md](docs/guides/install-hermes.zh-CN.md) |
| VS Code：ACP Client 扩展 | [docs/guides/vscode-acp.md](docs/guides/vscode-acp.md) | [docs/guides/vscode-acp.zh-CN.md](docs/guides/vscode-acp.zh-CN.md) |
| 最初的回退设计（另一台工作站） | [docs/guides/model-fallback.md](docs/guides/model-fallback.md) | [docs/guides/model-fallback.zh-CN.md](docs/guides/model-fallback.zh-CN.md) |

全部文档索引：[docs/INDEX.md](docs/INDEX.md)。
