---
title: fosqa 虚拟机上的 Hermes Agent：升级到 v0.21.4 与三级 LLM 链（fos-ai 个人令牌 → fos-ai 项目令牌 → 本地 Qwen）
kind: guide
created: 2026-09-22
updated: 2026-09-23
status: current
verified_against: Hermes Agent v0.21.4（上游 c0d7294769，2026-09-23），Ubuntu 24.04 LTS（内核 6.8.0-60），Python 3.11 venv 位于 ~/.hermes/hermes-agent/venv，2026-09-22 一次性运行实测
summary: 记录 fosqa 虚拟机上 5 月遗留的安装如何升级到 v0.21.4，并配置为：个人 fos-ai 令牌上的 glm-5.3 为主模型，Log Intelligence 项目令牌上的 deepseek-v4.1-flash 为回退 1，内部 Qwen 网关为回退 2/3；所有密钥只存放在 ~/.hermes/.env，每一级都经过验证。
related:
  - ../INDEX.md
  - fosqa-vm-llm-chain.md
  - install-hermes.zh-CN.md
  - model-fallback.zh-CN.md
  - vscode-acp.zh-CN.md
  - /home/fosqa/ai_practice/log-intelligence/.env
  - ~/.hermes/config.yaml
---

# fosqa 虚拟机上的 Hermes Agent：升级到 v0.21.4 与三级 LLM 链

> **2026-09-23：** 第 1–3 节记录的是最初的配置。当前状态（MCP 服务器、先询问插件、测量得出的模型链、
> 改名后的密钥，以及本目录变成的可共享安装仓库）见[第 7 节](#7-2026-09-23mcp-服务器先询问测量得出的模型链安装脚本)。

两篇旧指南（`install-hermes.zh-CN.md`、`model-fallback.zh-CN.md`）记录的是另一台工作站。
本虚拟机（`fosqa@`，内核 6.8.0-60）在 2026-05-25 已有一份单用户 git 安装（v0.14.0，指向已
退役的 `fos-exp-ai` 网关）。本文记录如何升级它，并按项目负责人要求的顺序重新指向 Log
Intelligence 项目使用的同一组网关：个人令牌优先，项目令牌其次，本地 Qwen 兜底。

应用本文后本机的状态（2026-09-22）：

| 项目 | 状态 |
|---|---|
| 版本 | `Hermes Agent v0.21.4`，上游 `c0d7294769`（标签 `v2026.9.21` + 488） |
| 安装方式 | 单用户 git 安装，代码在 `~/.hermes/hermes-agent/`，venv 在 `~/.hermes/hermes-agent/venv/`（Python 3.11.15），启动器 `~/.local/bin/hermes` |
| 主模型 | `https://fos-ai.fortinet.com/v1` 上的 `glm-5.3`，密钥 `FOS_AI_API_KEY`（= log-intel `.env` 的 `GLM_API_KEY`，个人令牌） |
| 回退 1 | 同一 fos-ai 网关上的 `deepseek-v4.1-flash`，密钥 `FOS_AI_LOG_INTEL_API_KEY`（= `GLM_API_KEY_LOG_INTEL`，Log Intelligence 项目令牌） |
| 回退 2 | `https://releaseqa-aiserver.corp.fortinet.com/v1` 上的 `qwen3.6-35b-a3b`，密钥 `LOCAL_LLM_API_KEY`（同名同值） |
| 回退 3 | 同一内部网关上的 `qwen3.5-122b-a10b-awq`，同一密钥 |
| 机密 | 三个密钥只在 `~/.hermes/.env`（权限 600）；`config.yaml` 只按名字引用（`${FOS_AI_API_KEY}`、`key_env:`） |
| 备份 | `~/.hermes/config.yaml.bak-20260922-215107`、`~/.hermes/.env.bak-20260922-215107` |
| 验证结果 | 主模型 5.9 秒回答；主模型 404 时由 deepseek-v4.1-flash 回答；两个 fos-ai 密钥均无效时由 qwen3.6-35b-a3b 回答 |
| ACP | `hermes acp --check` → `Hermes ACP check OK` |

## 1. 安装（更新现有 git 检出）

5 月的检出把 `origin` 设为 `git@github.com:`，而本机到 GitHub 的 SSH 会挂起，因此拉取前先把
远程改为 HTTPS。唯一的本地改动（`ui-tui/package-lock.json`）被丢弃。

```bash
cd ~/.hermes && cp -p config.yaml config.yaml.bak-$(date +%Y%m%d-%H%M%S) && cp -p .env .env.bak-$(date +%Y%m%d-%H%M%S)

cd ~/.hermes/hermes-agent
git remote set-url origin https://github.com/NousResearch/hermes-agent.git
git checkout -- ui-tui/package-lock.json
git pull --ff-only origin main                     # f4953bc64 (0.14.0) -> c0d7294769 (0.21.4)
uv pip install --python venv/bin/python -e ".[all]"
hermes --version                                   # Hermes Agent v0.21.4 ... Up to date
hermes doctor --fix                                # 执行了配置迁移；剩下一个手动项，见第 2 节
```

`hermes doctor` 仍会列出没有密钥的可选工具（image_gen、x_search、spotify……）和 "No
GITHUB_TOKEN"，这些都不影响聊天和 ACP 使用。

## 2. 配置

### 2.1 端点、密钥与命名理由

2026-09-22 21:50（太平洋时间）用 `/home/fosqa/ai_practice/log-intelligence/.env` 中的值探测了
三个网关：

| log-intel `.env` 变量 | 网关 | 列出的模型 | `~/.hermes/.env` 中的名字 |
|---|---|---|---|
| `GLM_API_KEY`（个人） | `https://fos-ai.fortinet.com/v1` | `deepseek-v4.1-flash`、`glm-5.3`、`glm-5.3-flash` | `FOS_AI_API_KEY` |
| `GLM_API_KEY_LOG_INTEL`（项目） | 同上 | 同上 | `FOS_AI_LOG_INTEL_API_KEY` |
| `LOCAL_LLM_API_KEY` | `https://releaseqa-aiserver.corp.fortinet.com/v1`（= `172.16.96.52`） | `qwen3.6-35b-a3b`、`qwen3.5-122b-a10b-awq`、`qwen3-vl-235b`、`qwen3-vl-embedding-8b` | `LOCAL_LLM_API_KEY` |

与 log-intel `.env` 的两处刻意区别：

- **不**沿用 `GLM_API_KEY` 这个名字。Hermes 内置的 `zai` provider 插件把 `GLM_API_KEY` 当作
  z.ai 凭据（`plugins/model-providers/zai/__init__.py`），在 `~/.hermes/.env` 里放这个名字会让
  Hermes 以为已配置了 z.ai。
- 本地网关用主机名而不是 log-intel `.env` 里的 IP。主机名有有效证书（curl 不加 `-k` 也返回
  200），裸 IP 没有，而 Hermes 没有按 provider 关闭 SSL 校验的开关。

### 2.2 把密钥写入 `~/.hermes/.env`

用一小段 Python 从 log-intel `.env` 读值追加，机密没有被手输或打印。结果块（值省略）：

```bash
# ── LLM gateways for model: / fallback_providers: in config.yaml (added 2026-09-22)
# Values mirrored from /home/fosqa/ai_practice/log-intelligence/.env (source var in parens).
# GLM_API_KEY is deliberately NOT used as a name here: Hermes reads that name as a z.ai credential.
# fos-ai personal token (primary: glm-5.3)  (GLM_API_KEY)
FOS_AI_API_KEY=...
# fos-ai Log Intelligence project token (fallback 1: deepseek-v4.1-flash)  (GLM_API_KEY_LOG_INTEL)
FOS_AI_LOG_INTEL_API_KEY=...
# internal Qwen gateway releaseqa-aiserver (fallback 2/3)  (LOCAL_LLM_API_KEY)
LOCAL_LLM_API_KEY=...
```

之后 `chmod 600 ~/.hermes/.env`。注意 Hermes 加载这个文件时会**覆盖**进程环境变量：在 shell 里
`export FOS_AI_API_KEY=...` 不会生效（3.3 节的测试因此要换方法）。

### 2.3 `~/.hermes/config.yaml`

重写了 `model:` 块，并删除了指向已退役 `fos-exp-ai` 网关的旧 `custom_providers:` 条目（这是
`hermes doctor` 无法自动处理的那一项）。Hermes 在加载时会展开配置中任何位置的 `${VAR}` 引用
（`hermes_cli/config.py::_expand_env_vars`），所以主模型密钥也可以不进 YAML。

```yaml
model:
  default: glm-5.3
  provider: custom
  base_url: https://fos-ai.fortinet.com/v1
  api_key: ${FOS_AI_API_KEY}
  context-length: 262144

fallback_providers:
  - provider: custom
    model: deepseek-v4.1-flash
    base_url: https://fos-ai.fortinet.com/v1
    key_env: FOS_AI_LOG_INTEL_API_KEY
  - provider: custom
    model: qwen3.6-35b-a3b
    base_url: https://releaseqa-aiserver.corp.fortinet.com/v1
    key_env: LOCAL_LLM_API_KEY
  - provider: custom
    model: qwen3.5-122b-a10b-awq
    base_url: https://releaseqa-aiserver.corp.fortinet.com/v1
    key_env: LOCAL_LLM_API_KEY
```

为什么与主模型**同一** base URL 的回退 1 不会被跳过：v0.21.4 中轮内跳过判定是
`agent/backend_identity.py::same_deployment`，两个 `custom` 条目只有在 base URL **和模型**都
相同时才算重复。旧指南里"相同 base_url 会被跳过"描述的是 v0.16 的代码。401/403 的凭据范围
判定（`same_credential_surface`）只用于辅助任务解析，不用于主轮回退链。

`config.yaml` 中其余内容（personality `kawaii`、工具集、超时）保持 5 月安装时的原样。

## 3. 验证

### 3.1 回退链已加载

```bash
hermes fallback list
```

```
  Primary:   glm-5.3  (via custom)

  Fallback chain (3 entries):
    1. deepseek-v4.1-flash  (via custom)  [https://fos-ai.fortinet.com/v1]
    2. qwen3.6-35b-a3b  (via custom)  [https://releaseqa-aiserver.corp.fortinet.com/v1]
    3. qwen3.5-122b-a10b-awq  (via custom)  [https://releaseqa-aiserver.corp.fortinet.com/v1]
```

### 3.2 主模型与回退 1（404 路径）

```bash
cd "$(mktemp -d)"
/usr/bin/time -f "%es" hermes -z "Reply with exactly: PONG-A"                          # 主模型
/usr/bin/time -f "%es" hermes -m no-such-model-404 -z "Reply with exactly: PONG-B"     # 主模型 404 -> 回退链
python3 - <<'PY'
import sqlite3
c=sqlite3.connect('/home/fosqa/.hermes/state.db')
for r in c.execute("select id, model, billing_base_url, output_tokens from sessions order by started_at desc limit 2"): print(r)
PY
```

2026-09-22 21:55（太平洋时间）实测：

```
PONG-A   5.88s
PONG-B   4.95s
('20260922_215520_831aad', 'deepseek-v4.1-flash', 'https://fos-ai.fortinet.com/v1/', 5)   <- 回退 1 回答
('20260922_215503_82fef2', 'glm-5.3',             'https://fos-ai.fortinet.com/v1',  5)   <- 主模型回答
```

v0.21.4 中会话行的 `model` 列是实际回答的模型，而不是请求的模型（v0.16 指南里显示的是请求
的名字）。

### 3.3 回退 2（两个 fos-ai 密钥都被拒绝）

由于 `~/.hermes/.env` 会覆盖 shell 环境变量，测试用一个临时 `HERMES_HOME`，其 `.env` 中两个
fos-ai 密钥被替换为无效值。真实配置不动。

```bash
T=$(mktemp -d) && cp -p ~/.hermes/config.yaml "$T/" \
  && sed -E 's/^(FOS_AI_API_KEY|FOS_AI_LOG_INTEL_API_KEY)=.*/\1=sk-invalid/' ~/.hermes/.env > "$T/.env" && chmod 600 "$T/.env"
cd "$(mktemp -d)" && HERMES_HOME="$T" /usr/bin/time -f "%es" hermes -z "Reply with exactly: PONG-D"
python3 -c "import sqlite3;print(list(sqlite3.connect('$T/state.db').execute('select model, billing_base_url from sessions order by started_at desc limit 1')))"
rm -rf "$T"      # 这份 .env 副本含真实的 Qwen 密钥
```

2026-09-22 21:56（太平洋时间）实测：

```
PONG-D   6.68s
[('qwen3.6-35b-a3b', 'https://releaseqa-aiserver.corp.fortinet.com/v1/')]
```

回退 3（122B 模型）与回退 2 共用网关和密钥，未单独测试。

### 3.4 ACP

```bash
hermes acp --check      # Hermes ACP check OK
```

VS Code 侧与 `vscode-acp.zh-CN.md` 相同，命令为 `/home/fosqa/.local/bin/hermes`。

## 4. 使用

同 `install-hermes.zh-CN.md` 第 4 节。按轮生效：每条新提示词都重新从 `glm-5.3` 开始；一轮内
回退链最多走一遍。要知道某一轮由谁回答，按 3.2 节读取 `sessions` 表的 `billing_base_url`
（和 `model`）。

调整顺序或增加一级：编辑 `~/.hermes/config.yaml` 的 `fallback_providers:`（或
`hermes fallback add/remove`），然后重启常驻 Agent（VS Code 中 `ACP: Restart Agent`）。轮换
密钥只需改 `~/.hermes/.env`。

## 5. 故障排除

| 现象 | 原因 | 处理 |
|---|---|---|
| `git pull` / `git fetch` 挂起 | 远程是 `git@github.com:`，本机到 GitHub 的 SSH 被阻断 | `git remote set-url origin https://github.com/NousResearch/hermes-agent.git` |
| `hermes doctor` 提示 "Move custom_providers entry ... into providers:" | 5 月配置遗留的 `custom_providers:` 列表（v12 迁移不会重跑） | 从 `config.yaml` 删除该列表（本文已做）或改写为 `providers.<name>.api` |
| `hermes model` / `/model` 选择器出现从未配置的 z.ai | `~/.hermes/.env` 里有 `GLM_API_KEY` 变量 | 改名（本文用 `FOS_AI_API_KEY`） |
| 本地 Qwen 一级报证书错误 | base URL 写成 `https://172.16.96.52/v1` | 用 `https://releaseqa-aiserver.corp.fortinet.com/v1`（同一主机，证书有效） |
| 测试时环境变量覆盖不生效 | `~/.hermes/.env` 加载时覆盖进程环境 | 用复制的 `HERMES_HOME` 测试（3.3 节） |
| fos-ai 返回 403 "access level does not permit model glm-5.3" | 个人密钥的白名单变了（2026-08-29 glm-5.2 就发生过） | 回退链会把本轮交给 deepseek-v4.1-flash；随后把 `model.default` 改为 `curl -H "Authorization: Bearer $FOS_AI_API_KEY" https://fos-ai.fortinet.com/v1/models` 中列出的模型 |
| `errors.log` 警告 `platform 'teams' has no valid toolsets configured (hermes-teams)` | 5 月配置中过期的平台工具集名称 | 对 CLI/ACP 无影响；若以后使用网关，用 `hermes tools` 重新配置 |

## 6. 安全说明

- `~/.hermes/config.yaml` 和 `~/.hermes/.env` 权限 600；本机上没有其他凭据（5 月的 `.env` 只有
  非机密的超时和调试开关）。
- `config.yaml` 中没有任何密钥：主模型用 `${FOS_AI_API_KEY}`，回退用 `key_env`。因此
  `hermes config show` 打印的是名字而不是值。
- 回退会把对话发送到内部 Qwen 网关；两个网关都是 Fortinet 内部 TLS 端点。
- 3.3 节的 `.env` 测试副本运行后必须删除。

## 7. 2026-09-23：MCP 服务器、先询问、测量得出的模型链、安装脚本

本目录现在是 `hermes_installer` 仓库（`git@github.com:bruceyu777/hermes_installer.git`）：
`install.sh` + `tokens.env.example` + `config/`。本虚拟机已从它重新部署
（`./install.sh --no-install`，然后 `--cron`）。用户视角见 [README.zh-CN.md](../../README.zh-CN.md)。

### 7.1 密钥改用安装脚本的名字

`~/.hermes/.env`：`FOS_AI_LOG_INTEL_API_KEY` → `FOS_AI_FALLBACK_API_KEY`，
`LOCAL_LLM_API_KEY` → `LOCAL_QWEN_API_KEY`（原地改名，值不变，备份 `.env.bak-20260923-114739`）。
新增 `MANTIS_MCP_TOKEN`、`JENKINS_MCP_BASIC`（`user:apitoken` 的 base64，可直接用于 Basic 头）和
`LOGINTEL_MCP_KEY`，取自 opencode 的密钥文件。

### 7.2 MCP 服务器（与 opencode 相同的服务器和筛选）

| 服务器 | 传输 / 认证 | 模型可见的工具 |
|---|---|---|
| `logintel` | streamable HTTP，`Bearer ${LOGINTEL_MCP_KEY}` | 全部 26 个（只读） |
| `mantis-tools` | `transport: sse`，`Bearer ${MANTIS_MCP_TOKEN}` | 95 个中的 8 个：`tools.include` |
| `jenkins` | streamable HTTP，`Basic ${JENKINS_MCP_BASIC}` | 19 个中的 10 个：`tools.include` |
| `microsoft365` | `auth: oauth` | 在 `hermes mcp login microsoft365` 保存 token 之前禁用（未测试） |

影响配置的发现：

- Jenkins 和 Mantis 声明了 MCP resources 和 prompts，Hermes 会为每个服务器多加四个辅助工具
  （`list_resources`、`read_resource`、`list_prompts`、`get_prompt`）。`tools.resources: false` 和
  `tools.prompts: false` 去掉它们后，模型恰好看到 44 个 MCP 工具，与 opencode 相同。
  用临时 home 调用 `tools.mcp_tool_discovery.discover_mcp_tools()` 核实。
- Hermes 内置的 MCP 审批只按服务器生效（`trust: untrusted` 会在每个没有 `readOnlyHint: true` 的工具前询问），
  而三个服务器都没有标注任何工具，那样每次搜索都要询问。因此按工具询问改由下面的插件完成。
- ACP（VS Code）同样读取 `config.yaml` 的 `mcp_servers`（`acp_adapter/entry.py` 在后台启动发现），
  终端和 VS Code 共用一份配置。

### 7.3 插件 `mcp-ask-first`

`~/.hermes/plugins/mcp-ask-first/`（在 `plugins.enabled` 中启用）注册一个 `pre_tool_call` hook。
对 `create_mantis`、`mantis_add_note`、`send_email`、`triggerBuild`、`rebuildBuild` 返回
`{"action": "approve"}`，把调用交给 Hermes 的人工审批关口（`tools/approval.py::request_tool_approval`），
与危险 shell 命令用的是同一个关口。

`hermes -z` 会设置 `HERMES_YOLO_MODE=1`（`hermes_cli/oneshot.py`），自动批准一切，所以第一个版本在
`-z` 中放行了受控工具。因此当 Hermes 标记本次运行无人应答时（`HERMES_SINGLE_QUERY_SESSION`：`-z` 和
`chat -q`；`HERMES_CRON_SESSION`），插件直接返回 `block`。

2026-09-23 用一个额外把无害的 `logintel_list_projects` 也设为受控的插件副本验证：

| 路径 | 结果 |
|---|---|
| `hermes -z` | 被拦截，显示插件的消息 |
| `hermes chat -q` | 被拦截，通过 Hermes 的 `tool_call` 工具搜索包装调用时也一样 |
| ACP，脚本客户端回答 `deny` | 收到一个 `session/request_permission`（"TEST GATE list projects: {}: <mcp__logintel__logintel_list_projects> (plugin approval rule)"，选项 allow_once / allow_session / allow_always / deny / deny_always）；工具未运行，模型未重试 |
| ACP，回答 `allow_once` | 工具运行，返回 13 个项目 |

限制：筛选不是沙箱。token 在 `~/.hermes/.env` 中，也在 agent shell 的环境变量里（Hermes 只清除它自己登记的名字），
模型可能直接调用服务器。2026-09-23 本虚拟机上一个 opencode 会话就用 Mantis 密钥文件这样做过。

### 7.4 测量得出的模型链（借鉴 log-intelligence 的 LLM 自动适配）

2026-09-23 网关在几小时内改了允许列表：11:47 个人 key 上 `glm-5.3` 还能应答；到 21:00 个人 key 只提供
`glm-5.3-flash`，`deepseek-v4.1-flash` 对两个 key 都被拒绝（"your access level does not permit model"，
虽然一开始 `/v1/models` 仍列出它），一轮对话一路回退到本地 Qwen。单独对一个 403 模型运行 Hermes `-z`
会一直挂到 180 秒超时。

因此模型链改为测量得出，沿用 log-intelligence 适配器的做法（见其 `docs/implement_log/2026-09-24_llm_automatic_adapter_plan.md`）：

- `config/model-preferences.yaml`：每个 key 一个 `prefer` 列表和要取的数量 `take`。
- `install.sh` 和 `install.sh --adapt` 对每个首选模型发一次单 token 对话调用，只使用有应答的模型
  （`/v1/models` 表示 key 能看到什么，而不是能调用什么）。含 "budget" 的 429 算拒绝，普通 429 算可用。
  不在任何 `prefer` 列表中的 id 只报告为 "new models seen"。网关无应答的 key 保留当前的链接。
  两个 key 不会在同一网关上选同一个模型（Hermes 会把第二个当重复跳过）。
- `install.sh --cron` 添加 `17 * * * * … install.sh --adapt`（日志 `~/.hermes/logs/hermes_installer_adapt.log`），
  已在 `env -i` 下按 cron 环境测试。

本虚拟机 2026-09-23 21:21 的结果：`glm-5.3-flash`（个人）→ `glm-5.3`（项目 key）→ `qwen3.6-35b-a3b` →
`qwen3.5-122b-a10b-awq`；`qwen3-vl-235b` 报告为新模型、未使用。`install.sh --check`：四个都是
"gateway ok, Hermes PONG"；logintel、mantis-tools、jenkins 均已连接。

### 7.5 安装脚本测试

| 场景 | 结果 |
|---|---|
| 在沙箱 `HOME` 中全新安装（`env -i`，干净的 `PATH`） | 官方安装脚本固定到 `c0d7294769`，v0.21.4，全部检查通过，437 秒 |
| 只有 Qwen key 和 Log Intelligence key，空的 `HERMES_HOME` | 模型链从 `qwen3.6-35b-a3b` 开始；mantis-tools 和 jenkins 禁用；logintel 已连接 |
| 本虚拟机（已有 545 行的 `config.yaml`） | 只改了受管理的键，注释和其他键都保留；再次运行显示 "unchanged" |
| 本地网关不可达（dry run） | 保留当前配置中的 Qwen 链接 |

## History

| 日期 | 变更 |
|---|---|
| 2026-09-22 | 创建。通过 HTTPS 把 5 月的 v0.14.0 检出升级到 v0.21.4，用 log-intel `.env` 的密钥探测三个网关，把密钥以不冲突的名字放进 `~/.hermes/.env`，写入主模型 + 三级 `fallback_providers` 链（`${VAR}`/`key_env` 引用），删除退役的 `fos-exp-ai` 条目，通过 `state.db` 验证主路径、404 → deepseek 路径和密钥无效 → Qwen 路径，以及 `hermes acp --check`。 |
| 2026-09-23 | 第 7 节：MCP 服务器（logintel、mantis-tools、jenkins；microsoft365 关闭），沿用 opencode 的筛选并加上 `resources/prompts: false`；插件 `mcp-ask-first`（CLI/ACP 中询问，-z/-q/cron 中拦截），通过 `-z`、`chat -q` 和脚本化 ACP 客户端验证；密钥改用安装脚本的名字；网关对个人 key 拒绝 glm-5.3 和 deepseek 后，模型链改为按 `model-preferences.yaml` 逐 key 测量（借鉴 log-intel LLM 适配器）；每小时 `--adapt` cron；本目录变为 `hermes_installer` 仓库，测试了全新安装 / 部分 token / 本虚拟机。 |
