---
title: Hermes Agent：在本机安装、检查、更新与回滚
kind: guide
created: 2026-09-21
updated: 2026-09-21
status: current
verified_against: Hermes Agent v0.16.0（2026.6.5，上游 c6e99ab3），Ubuntu（内核 7.0.0-30），venv 位于 ~/.hermes/hermes-agent/venv，2026-09-21 实测
summary: 记录 Hermes Agent 在本机的安装方式、如何确认可用、如何更新以及如何卸载。
related:
  - ../INDEX.md
  - install-hermes.md
  - vscode-acp.zh-CN.md
  - model-fallback.zh-CN.md
---

# Hermes Agent：在本机安装、检查、更新与回滚

Hermes Agent 是 Nous Research 的开源编码/对话 Agent，可作为 CLI、TUI、消息网关运行，也可作为
编辑器的 ACP 服务端。本文记录本机的安装方式，便于在另一台机器上复现。

本机状态（2026-09-21）：

| 项目 | 状态 |
|---|---|
| 版本 | `Hermes Agent v0.16.0 (2026.6.5)`，上游提交 `c6e99ab3`（2026-06-16） |
| 安装方式 | 单用户 git 安装脚本；代码在 `~/.hermes/hermes-agent/`，Python venv 在 `~/.hermes/hermes-agent/venv/` |
| 启动器 | `~/.local/bin/hermes`（bash 包装脚本，exec `venv/bin/hermes`） |
| 数据目录 | `~/.hermes/` —— `config.yaml`（设置）、`.env`（API 密钥）、`logs/`、`sessions/`、`state.db` |
| 主模型 | Fortinet fos-ai 网关上的 `glm-5.3`（`provider: custom`），见 `model-fallback.zh-CN.md` |
| ACP 模式 | `hermes acp --check` 输出 `Hermes ACP check OK` |

## 1. 安装

官方一键安装脚本会把仓库克隆到 `~/.hermes/hermes-agent`，创建 venv，安装 `[all]` 附加依赖
（包含 ACP），并把启动器写到 `~/.local/bin/hermes`。

```bash
curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash
```

在公司代理后面，可以先跳过浏览器组件（Playwright + Chromium，约 400 MB），以后再补：

```bash
curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash -s -- --skip-browser
# 之后如需浏览器工具：
hermes acp --setup-browser
```

确认 `~/.local/bin` 在 `PATH` 中（本机已在）：

```bash
grep -q '.local/bin' ~/.bashrc || echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
```

安装脚本支持的其他布局，供参考：

| 安装方式 | 代码位置 | `hermes` 可执行文件 | 数据目录 |
|---|---|---|---|
| `pip install hermes-agent[all]` | Python site-packages | `~/.local/bin/hermes` | `~/.hermes/` |
| 单用户 git 安装脚本（**本机**） | `~/.hermes/hermes-agent/` | `~/.local/bin/hermes` | `~/.hermes/` |
| root 模式（`sudo curl … \| sudo bash`） | `/usr/local/lib/hermes-agent/` | `/usr/local/bin/hermes` | `/root/.hermes/` |

## 2. 配置

首次配置是交互式的：

```bash
hermes setup          # 完整向导：provider、模型、工具、网关
hermes model          # 只选择 provider + 模型
```

本机的 provider 是自定义的 OpenAI 兼容端点。`~/.hermes/config.yaml` 相关部分（密钥已打码）：

```yaml
model:
  default: glm-5.3
  provider: custom
  base_url: https://fos-ai.fortinet.com:443/v1
  api_key: <redacted>
  context-length: 262144
```

机密：fos-ai 密钥内联在 `config.yaml` 中（由 `hermes model` 写入）；回退用的 Qwen 密钥在
`~/.hermes/.env` 的 `LOCAL_QWEN_API_KEY`。两个文件权限均为 600。`hermes config env-path`
打印 `.env` 路径，`hermes config path` 打印 YAML 路径。

## 3. 验证

```bash
hermes --version                       # Hermes Agent v0.16.0 (2026.6.5) · upstream c6e99ab3
hermes doctor                          # 环境、安装方式、缺失依赖
hermes acp --check                     # Hermes ACP check OK
cd "$(mktemp -d)" && /usr/bin/time -f "%es" hermes -z "Reply with exactly: PONG"
```

2026-09-21 16:27（太平洋时间）实测：输出 `PONG-A`，耗时 3.76 秒（一次性模式包含 Agent 启动和
插件注册）。会话记录在 `~/.hermes/state.db` 中，`billing_base_url = https://fos-ai.fortinet.com:443/v1`。

注意参数顺序：`-z`、`-m`、`--provider` 是全局选项，必须放在子命令**之前**
（`hermes -z "..."`，而不是 `hermes chat -z "..."`）。

## 4. 使用

| 目的 | 命令 |
|---|---|
| 终端交互聊天 | `hermes` 或 `hermes chat` |
| 一次性提问，打印回答后退出 | `hermes -z "prompt"` |
| 单次运行换一个模型 | `hermes -m <model> -z "prompt"` |
| 恢复上一个会话 | `hermes --continue` |
| 列出历史会话 | `hermes sessions list` |
| 查看各组件状态 | `hermes status` |
| 查看日志 | `hermes logs`、`hermes logs errors`、`hermes logs -f` |
| 管理回退链 | `hermes fallback`（见 `model-fallback.zh-CN.md`） |
| 编辑器集成 | `hermes acp`（见 `vscode-acp.zh-CN.md`） |

## 5. 更新

`hermes update` 会识别安装方式并打印或执行对应的更新命令。对于 git 布局，等价于：

```bash
cd ~/.hermes/hermes-agent
git pull
uv pip install -e ".[all]"     # 或：venv/bin/pip install -e ".[all]"
hermes --version
```

更新前先备份，因为下次启动会执行 `config.yaml` 迁移：

```bash
hermes backup                  # 或：cp -p ~/.hermes/config.yaml ~/.hermes/config.yaml.bak-$(date +%Y%m%d-%H%M%S)
```

## 6. 故障排除

| 现象 | 原因 | 处理 |
|---|---|---|
| `hermes: command not found` | 该 shell 的 PATH 不含 `~/.local/bin`（snap 启动的应用和服务账号常见） | 使用完整路径 `/home/yzhengfeng/.local/bin/hermes`，或在 `~/.bashrc` 中导出 PATH |
| `unrecognized arguments: -z ...` | 全局参数放在了子命令之后 | 用 `hermes -z "..."`，不要用 `hermes chat -z "..."` |
| `No LLM provider configured` | 缺少 `model:` 块或密钥为空 | `hermes model` |
| `Provider 'custom' ... no API key was found` | `model:` 中没有 `api_key`，且回退也无法解析 | 重新执行 `hermes model`，或检查回退链（`model-fallback.zh-CN.md`） |
| `agent.log` 中出现 `[Email] IMAP fetch error: AUTHENTICATIONFAILED` | `.env` 里的邮件网关凭据已失效；对 CLI/ACP 使用无影响 | 修正或删除 `~/.hermes/.env` 中的 `EMAIL_*` |
| 第一次提问较慢（3–5 秒） | 启动时注册插件并探测 provider | 一次性模式下属正常；交互和 ACP 会话只付一次启动成本 |

## 7. 安全说明

- `~/.hermes/config.yaml` 和 `~/.hermes/.env` 含 API 密钥，权限 600。切勿复制到本文档目录。
- `hermes config show` 会打印配置；粘贴前先检查 `api_key`。
- `.env` 还包含消息网关用的 Telegram 和邮箱凭据。
- `hermes security` 列出安全相关设置（命令审批、白名单）。

## 8. 回滚 / 卸载

```bash
hermes uninstall               # 删除代码和启动器；会询问是否保留 ~/.hermes
# 或手动：
rm -rf ~/.hermes/hermes-agent ~/.local/bin/hermes     # 若要重装，保留 ~/.hermes/config.yaml 和 .env
```

## History

| 日期 | 变更 |
|---|---|
| 2026-09-21 | 创建。记录现有 git 安装布局，验证 `--version`、`acp --check` 和一次性提问。 |
| 2026-09-22 | fosqa 虚拟机上同一布局从 v0.14.0 升级到 v0.21.4（因到 GitHub 的 SSH 挂起，git 远程改为 HTTPS）；密钥只放在 `.env`。见 `fosqa-vm-llm-chain.zh-CN.md`。 |
