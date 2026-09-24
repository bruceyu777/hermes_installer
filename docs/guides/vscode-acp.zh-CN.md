---
title: Hermes Agent：通过 ACP Client 扩展在 VS Code 中使用
kind: guide
created: 2026-09-21
updated: 2026-09-22
status: current
verified_against: Hermes Agent v0.16.0，VS Code 1.137.0（snap），扩展 formulahendry.acp-client 0.2.0，Ubuntu（内核 7.0.0-30），2026-09-21 实测
summary: Hermes 没有自己的 VS Code 扩展，而是作为 ACP Agent 接入 VS Code。本文安装 ACP Client 扩展、注册 Hermes、连接使用，并说明为什么应用市场里的 "Hermes" 扩展是错的。
related:
  - ../INDEX.md
  - vscode-acp.md
  - install-hermes.zh-CN.md
  - model-fallback.zh-CN.md
  - ~/.config/Code/User/settings.json
---

# Hermes Agent：通过 ACP Client 扩展在 VS Code 中使用

Hermes Agent 实现了 Agent Client Protocol（ACP，由 Zed Industries 提出），这是一个基于 stdio 的
JSON-RPC 协议，让编辑器托管外部编码 Agent，并渲染其聊天、工具调用、文件 diff、终端命令和审批
提示。VS Code 通过 formulahendry 的 **ACP Client** 扩展获得 ACP 支持；Hermes 是它内置的 Agent 之一。
应用市场里**没有** "Hermes Agent" 扩展。

本机状态（2026-09-21）：

| 项目 | 状态 |
|---|---|
| VS Code | 1.137.0，snap 安装（`/snap/bin/code`） |
| ACP 扩展 | `formulahendry.acp-client` 0.2.0，位于 `~/.vscode/extensions/` |
| Agent 条目 | `~/.config/Code/User/settings.json` 中的 `acp.agents["Hermes Agent"]`，使用启动器完整路径 |
| 备份 | `~/.config/Code/User/settings.json.bak.hermes`（修改前的设置） |
| 已移除 | `jet-propulsion-laboratory.hermes` 5.0.7 —— NASA JPL 的航天器遥测工具，误装 |
| Hermes 侧 | `hermes acp --check` → `Hermes ACP check OK`；第 3 节握手测试通过 |

## 0. 装错的 "Hermes" 扩展

在应用市场搜索 "Hermes"，会得到 jet-propulsion-laboratory 发布的 **Hermes Extension for Visual
Studio Code**。那是 NASA JPL 面向航天器地面系统的遥测与指令前端（F' 框架、上行/下行、EVR）。
它只有在打开含 F' 字典的工作区后才显示一个火星车图标，所以安装后"什么都没出现"。它与
Hermes Agent 毫无关系。

```bash
code --uninstall-extension jet-propulsion-laboratory.hermes
```

## 1. 安装 ACP Client 扩展

```bash
code --install-extension formulahendry.acp-client
```

或在 VS Code 中：扩展视图 → 搜索 "ACP Client" → 安装 formulahendry 的那个。它会在活动栏添加
**ACP Client** 图标，并在命令面板中添加：`ACP: Connect to Agent`、`ACP: New Conversation`、
`ACP: Open Chat Panel`、`ACP: Add Agent Configuration`、`ACP: Set Agent Model`、
`ACP: Set Agent Mode`、`ACP: Show Log`、`ACP: Show Protocol Traffic`、`ACP: Restart Agent`。

它内置的 Agent 列表（设置项 `acp.agents`）已经包含：

```json
"Hermes Agent": { "command": "hermes", "args": ["acp"], "env": {} }
```

## 2. 配置

内置条目依赖 VS Code 的 PATH 中能找到 `hermes`。本机 VS Code 是 snap，snap 启动的应用通常看不到
`~/.local/bin`，因此用完整路径和显式 PATH 覆盖该条目。在 `~/.config/Code/User/settings.json` 中：

```json
"acp.agents": {
    "Hermes Agent": {
        "command": "/home/yzhengfeng/.local/bin/hermes",
        "args": ["acp"],
        "env": {
            "PATH": "/home/yzhengfeng/.local/bin:/home/yzhengfeng/.hermes/bin:/usr/local/bin:/usr/bin:/bin"
        }
    }
}
```

用户在 `acp.agents` 下定义的条目会与内置条目合并，其他 Agent（Claude Code、Gemini CLI、
GitHub Copilot 等）仍然可用。

其他值得了解的设置：

| 设置 | 默认值 | 含义 |
|---|---|---|
| `acp.autoApprovePermissions` | `"ask"` | 工具调用（写文件、终端）是否需要审批 |
| `acp.defaultWorkingDirectory` | `""` | 交给 Agent 的工作目录；空 = 当前打开的工作区 |
| `acp.logTraffic` | `true` | 在 "ACP: Show Protocol Traffic" 视图中保留 JSON-RPC 流量 |

Hermes 不需要 ACP 专用配置；它复用 `~/.hermes/config.yaml` 中的 provider、模型和回退链。
ACP 模式下它暴露精选的 `hermes-acp` 工具集（文件读/写/patch/搜索、终端、网页、记忆、todo、
技能、委派、视觉），隐藏消息和 cron 工具。

## 3. 验证

Hermes 侧，在终端：

```bash
hermes acp --check
# Hermes ACP check OK

# 原始 ACP 握手：适配器必须启动并记录客户端的 initialize
printf '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":1,"clientCapabilities":{}}}\n' | timeout 30 hermes acp 2>&1 | head -5
```

2026-09-21 16:21（太平洋时间）实测：

```
[INFO] acp_adapter.entry: Loaded env from /home/yzhengfeng/.hermes/.env
[INFO] acp_adapter.entry: Starting hermes-agent ACP adapter
[INFO] acp_adapter.server: ACP client connected
[INFO] acp_adapter.server: Initialize from unknown (protocol v1)
```

VS Code 侧：执行 `Developer: Reload Window` 后，运行 `ACP: Connect to Agent`，选择
**Hermes Agent**，然后 `ACP: Open Chat Panel` 并发送提示词。聊天面板显示回复，
`ACP: Show Log` 显示与上面相同的适配器日志。

## 4. 使用

1. 安装扩展或修改 `acp.agents` 后重新加载窗口一次。
2. 点击活动栏的 **ACP Client** 图标（或 `ACP: Open Chat Panel`）。
3. `ACP: Connect to Agent` → **Hermes Agent**。Agent 进程是
   `/home/yzhengfeng/.local/bin/hermes acp`，每个 VS Code 窗口一个。
4. 在聊天面板输入。文件修改以 diff 显示，shell 命令以终端块显示，除非修改了
   `acp.autoApprovePermissions`，否则每次工具调用都会请求审批。
5. `ACP: Set Agent Model` 切换本会话的模型；`ACP: Set Agent Mode` 切换 Hermes 的模式（若适配器提供多种）。
6. `ACP: Attach File to Prompt` 把文件作为上下文发送。
7. 修改 `~/.hermes/config.yaml` 后执行 `ACP: Restart Agent`（适配器在启动时读取配置）。
   `ACP: Disconnect Agent` 停止进程。

从 VS Code 发起的会话就是普通的 Hermes 会话：`hermes sessions list` 能看到，
`hermes --continue` 可以在终端里继续。

## 5. 故障排除

| 现象 | 原因 | 处理 |
|---|---|---|
| 安装 "Hermes Extension for Visual Studio Code" 后没有 Hermes 图标 | 那是 NASA JPL 的遥测扩展（第 0 节） | 卸载它，安装 `formulahendry.acp-client` |
| `ACP: Show Log` 中出现 `spawn hermes ENOENT` 或 "agent exited" | VS Code（snap）的 PATH 找不到 `hermes` | 使用第 2 节的完整路径条目 |
| 已连接但第一条提示词报 provider 错误 | 主网关不可用或密钥过期；未配置回退 | 在终端执行 `hermes -z "ping"` 查看真实错误；按 `model-fallback.zh-CN.md` 配置回退 |
| `config.yaml` 的修改不生效 | 适配器在修改前就已启动 | `ACP: Restart Agent` 或重新加载窗口 |
| 每次工具调用都要审批 | `acp.autoApprovePermissions` 为 `"ask"` | 若工作区可信，在设置中修改 |
| 需要看线上到底发了什么 | `acp.logTraffic` 默认开启 | `ACP: Show Protocol Traffic` |

## 6. 安全说明

- Agent 以你的用户权限在工作区目录中运行。在不可信仓库中保持 `acp.autoApprovePermissions`
  为 `"ask"`。
- `ACP: Show Protocol Traffic` 包含提示词和文件内容；未脱敏前不要粘贴到工单中。
- Agent 条目中的 `env.PATH` 刻意保持最小且不含机密。API 密钥来自 `~/.hermes/config.yaml`
  和 `~/.hermes/.env`，由适配器自行加载。

## 7. 回滚

```bash
cp ~/.config/Code/User/settings.json.bak.hermes ~/.config/Code/User/settings.json
code --uninstall-extension formulahendry.acp-client
```

然后重新加载 VS Code 窗口。

## History

| 日期 | 变更 |
|---|---|
| 2026-09-21 | 创建。卸载 JPL 扩展，安装 `formulahendry.acp-client` 0.2.0，添加完整路径的 `acp.agents` 条目，在终端验证 ACP 握手。 |
| 2026-09-22 | fosqa 虚拟机（Remote-SSH，Hermes v0.21.4）：用 `~/.vscode-server/cli/servers/Stable-<hash>/server/bin/code-server --install-extension formulahendry.acp-client` 把 `formulahendry.acp-client` 0.2.0 装进 VS Code **服务端**（该扩展有 `main` 入口且未声明 `extensionKind`，因此在远程侧运行，并在虚拟机上启动 `hermes`）。由于窗口是远程窗口，`acp.agents` 条目写入远程机器设置 `~/.vscode-server/data/Machine/settings.json`（备份 `settings.json.bak.hermes`），`command` 为 `/home/fosqa/.local/bin/hermes`，`env.PATH` 与原文相同；客户端用户设置未改动。第 3 节的终端握手在 22:04（太平洋时间）通过，日志显示 `Loaded env from /home/fosqa/.hermes/.env`。编辑器内连接（`Developer: Reload Window` → `ACP: Connect to Agent` → Hermes Agent）留给用户操作。 |
