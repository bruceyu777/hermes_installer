---
marp: true
theme: default
paginate: true
size: 16:9
title: QA 虚拟机上的 AI Agent：工程深度分享
style: |
  section { font-size: 22px; }
  pre, code { font-size: 15px; }
  table { font-size: 17px; }
---

# QA 虚拟机上的 AI Agent：工程深度分享

**opencode 1.18.32** 与 **Hermes Agent v0.21.4**，以两个安装仓库的形式交付

1. 架构：网关、回退、MCP、防护
2. 实现要点（附关键代码）
3. 测量得出的模型链（移植自 Log Intelligence 适配器）
4. 安装指南
5. 现场演示
6. 测试：跑了什么、如何复现
7. 经验、限制、下一步

FortiOS QA · 2026-09-23

---

## 用工程语言描述问题

| 需求 | 难点 |
|---|---|
| 只用内部 LLM | 2 个 fos-ai 密钥，各自的允许列表和预算不同，另有一个本地 vLLM Qwen 服务器 |
| 扛住模型故障 | 网关会**不经通知**返回 403 "not permitted"、429 "no budget"、5xx |
| 接入我们的系统 | Jenkins、Mantis、Log Intelligence 三个 MCP 服务器，三种认证方式（Bearer、SSE Bearer、Basic） |
| 保证安全 | Mantis 暴露 **95** 个工具，包括邮件、Exchange、vault；Jenkins 能触发构建 |
| 所有人配置一致 | token 因人而异，但配置和行为要完全相同 |
| 终端**和** VS Code | Remote-SSH：扩展运行在虚拟机上，而不是笔记本上 |

设计目标：**一个仓库 + 一个 `tokens.env` + 一个脚本**，幂等，`git pull` 后可以直接重跑。

---

## 架构

```
                         ┌──────────── tokens.env（每人一份，不进 git）──────────────────┐
                         ▼                                                              ▼
 ┌──────────────┐   install.sh ──► ~/.config/opencode/*.key          ~/.hermes/.env（权限 600）
 │ opencode     │                  opencode.json  chain-fallback.json  config.yaml（只改受管理的键）
 │ Hermes (ACP) │                  plugin/chain-fallback.js            plugins/mcp-ask-first/
 └──────┬───────┘
        │ 模型链（由 `install.sh --adapt` 每小时测量，cron :47 / :17）
        ├──► fos-ai 网关   https://fos-ai.fortinet.com/v1           个人密钥 → glm-5.3-flash
        ├──► fos-ai 网关   （第二个密钥：项目 token）                → glm-5.3
        └──► releaseqa-aiserver（vLLM）  qwen3.6-35b-a3b → qwen3.5-122b-a10b-awq
        │ MCP（筛选为 44 个工具，写操作先询问）
        ├──► logintel      https://releaseqa-logintelligence…/mcp     streamable HTTP，Bearer limcp_…
        ├──► mantis-tools  https://releaseqa-portal…/sse               SSE，Bearer（token 与用户绑定）
        └──► jenkins       https://releaseqa-stackjenkins…/mcp-server/mcp  HTTP，Basic base64(user:token)
```

---

## 仓库结构（两个仓库结构相同）

```
opencode_installer/                     hermes_installer/
├── install.sh                          ├── install.sh
├── tokens.env.example                  ├── tokens.env.example        # 同样 6 个变量名：一个文件两边通用
├── config/                             ├── config/
│   ├── opencode.json                   │   ├── config.yaml           # 只含我们管理的键
│   ├── model-preferences.json          │   ├── model-preferences.yaml
│   └── plugin/chain-fallback.js        │   └── plugins/mcp-ask-first/{plugin.yaml,__init__.py}
├── scripts/adapt_models.py             ├── scripts/merge_into_hermes_home.py
└── docs/（指南，英文 + 中文）            ├── tests/{acp_permission_test.py,list_registered_mcp_tools.py}
                                        └── docs/（指南、幻灯片）
```

安装脚本遵守的规则：
- **任何配置文件里都没有密钥**：opencode 用 `{file:~/.config/opencode/x.key}`，Hermes 用 `${VAR}` / `key_env`
- **幂等**：只在内容变化时重写文件，并先备份为 `.bak-<时间>`
- **不打印任何密钥**：探测请求的头从权限 600 的临时文件读取（`curl -H @file`）

---

# 第 1 部分 · 实现要点：opencode

---

## opencode：provider 与模型链

两个 provider 指向**同一个网关**，只有密钥文件不同，所以回退插件能区分"个人密钥失败"和"项目密钥失败"：

```jsonc
"provider": {
  "fos-ai":          { "npm": "@ai-sdk/openai-compatible",
                       "options": { "baseURL": "https://fos-ai.fortinet.com/v1",
                                    "apiKey": "{file:~/.config/opencode/fos-ai.key}" },
                       "models": { "glm-5.3-flash": { "name": "glm-5.3-flash - primary" } } },
  "fos-ai-logintel": { … "apiKey": "{file:~/.config/opencode/fos-ai-logintel.key}",
                       "models": { "glm-5.3": { "name": "glm-5.3 - fallback 1" }, … } },
  "local-qwen":      { … "baseURL": "https://releaseqa-aiserver.corp.fortinet.com/v1", … }
},
"model": "fos-ai/glm-5.3-flash",             // 由 adapt_models.py 写入
"small_model": "local-qwen/qwen3.6-35b-a3b", // 标题和摘要留在本地模型
"autoupdate": false                          // 版本固定：由 install.sh 负责升级
```

`models` 中**只保留上次探测有应答的模型**，所以模型选择器永远不会提供已失效的模型。

---

## opencode：`chain-fallback.js`（本地插件）

从 `@renjfk/opencode-model-fallback` 0.2.1（MIT）内置并做了两处改动：

| 上游 | 我们 |
|---|---|
| 只有一层：主模型 → 回退，然后 "Exhausted" | 走完**整条链** |
| 主模型被停用期间，总是转到它的直接回退，**即使回退也被停用了** | `firstAvailableFrom()` 在任何网络请求前跳过所有被停用的模型 |
| 映射写死在 options 中 | 链从 `chain-fallback.json` 读取，**mtime 变化时重新读取** |
| — | 链中已移除的本仓库 provider 模型，会被转到链首 |

```js
function firstAvailableFrom(start) {        // 停用 = 在 cooldown_seconds（1800）内失败过
  let current = start; const seen = new Set();
  while (current && !seen.has(current) && store.getModelCooldown(current) && nextOf(current)) {
    seen.add(current); current = nextOf(current);
  }
  return current;
}
```

触发条件：HTTP `401 403 404 408 429 5xx 529`，或错误文本匹配 `no budget|does not permit|unknown model|…`。失败时中止本轮、停用该模型（`~/.local/share/opencode/chain-fallback-router.json`），然后**把最后一条用户消息重发**给下一个模型，并弹出提示 `A -> B (原因)`。

---

## opencode：热加载与失效模型转发

```js
let chainCache = { mtimeMs: -1, mappings: options.mappings, head: undefined };
function currentChain() {
  const mtimeMs = statSync(options.chain_file).mtimeMs;       // 开销很小：每轮一次 stat
  if (mtimeMs !== chainCache.mtimeMs) {
    const chain = JSON.parse(readFileSync(options.chain_file, "utf8")).chain ?? [];
    const mappings = {};
    for (let i = 0; i + 1 < chain.length; i++) mappings[chain[i]] = chain[i + 1];
    chainCache = chain.length ? { mtimeMs, mappings, head: chain[0] } : { ...chainCache, mtimeMs };
  }
  return chainCache;                                          // 文件缺失：保留上一次有效的链
}

function replacementFor(requested) {   // 已打开的 TUI 在网关移除 "fos-ai/glm-5.3" 后仍在请求它
  const { head } = currentChain();
  const provider = requested?.split("/")[0];
  if (!head || !options.managed_providers.includes(provider) || chainMembersNow().has(requested)) return undefined;
  return head;
}
```

原因：opencode 只在启动时加载一次 `opencode.json`，所以在每小时重新测量之前打开的会话，会一直请求网关已经拒绝的模型。

---

## opencode：用 `permission` 规则筛选 MCP 工具

```jsonc
"permission": {
  "mantis-tools_*": "deny",                      // 95 个工具 → 先全部隐藏 …
  "mantis-tools_search_bugs": "allow",           // … 再放行筛选出的 8 个
  "mantis-tools_semantic_bug_search": "allow",
  "mantis-tools_create_mantis": "ask",           // 写操作：先询问
  "mantis-tools_send_email": "ask",
  "jenkins_*": "deny",
  "jenkins_getBuildLog": "allow",  …
  "jenkins_triggerBuild": "ask",  "jenkins_rebuildBuild": "ask",
  "logintel_*": "allow"                          // 公开的 /mcp = 26 个只读工具
}
```

- **最后匹配的规则生效**，所以通配 `deny` 放在最前面
- `deny` 会**把工具从模型的工具列表中移除**（提示词更短，模型连尝试都做不到）
- 筛选与 Log Intelligence AI Assistant 一致：`chat_mcp_tools.enabled` → allow，`WRITE_TOOLS` → ask

---

## opencode：必须修复的 VS Code 问题

**现象：** 按 Ctrl+Escape 打开 opencode 终端，约 2 秒后退出，
输出 `Failed to refresh default location data … AbortError`。

**根因：** `ms-python.vscode-python-envs` 会在每个新终端里自动激活选中的环境。它先发送 **Ctrl+C**，再发送 `source …/activate`，而 **Ctrl+C 正是 opencode 的退出键**。

**如何定位：** opencode 的日志显示启动约 2 秒后 "disposing all instances"，由 stdin 上的一个按键触发。`Python Environments.log` 中的 "Shell execution timed out: source …/activate" **与之发生在同一毫秒**。

**修复（Remote-SSH 下 install.sh 写入 Machine 设置）：**
```json
"python-envs.terminal.autoActivationType": "off",
"python.terminal.activateEnvironment": false
```
这很可能影响所有由扩展在新终端中启动的 TUI。

---

# 第 2 部分 · 实现要点：Hermes

---

## Hermes：配置里没有密钥，合并而不是覆盖

```yaml
model:                                   # 由 `adapt` 写入（测量得出）
  default: glm-5.3-flash
  provider: custom
  base_url: https://fos-ai.fortinet.com/v1
  api_key: ${FOS_AI_API_KEY}             # 加载时从 ~/.hermes/.env 展开
fallback_providers:
  - {provider: custom, model: glm-5.3, base_url: https://fos-ai.fortinet.com/v1, key_env: FOS_AI_FALLBACK_API_KEY}
  - {provider: custom, model: qwen3.6-35b-a3b, base_url: https://releaseqa-aiserver.corp.fortinet.com/v1, key_env: LOCAL_QWEN_API_KEY}
```

- 用户的 `config.yaml` 约有 545 行由 Hermes 生成的内容。我们**只**改 `model`、`fallback_providers`、我们的 4 个 `mcp_servers` 条目和 `plugins.enabled`
- 用 **ruamel 往返（round-trip）合并，设置与 Hermes 自己的写入器相同**（`indent(mapping=2, sequence=4, offset=2)`、`preserve_quotes`），注释和键的顺序都能保留。本机上的 diff 恰好只有受管理的行
- 用 **Hermes 自带 venv 的 Python** 运行，它已经有 ruamel，所以安装脚本没有额外依赖

踩过的坑：
- 变量名不能叫 `GLM_API_KEY`：Hermes 内置的 z.ai provider 会占用它
- `~/.hermes/.env` 会**覆盖**进程环境变量，所以不能靠 export 变量来测试某一环。要用临时的 `HERMES_HOME`
- 只有 **base URL 和模型**都与失败的那一环相同时，回退条目才会被跳过，所以同一网关上不要给两个密钥配同一个模型

---

## Hermes：MCP 配置与被隐藏的 4 个额外工具

```yaml
mcp_servers:
  mantis-tools:
    url: https://releaseqa-portal.corp.fortinet.com/sse
    transport: sse
    headers: {Authorization: "Bearer ${MANTIS_MCP_TOKEN}"}
    tools:
      resources: false          # ← 不设置的话，只要服务器声明了这两项能力，Hermes 就会为它
      prompts: false            #   添加 list_resources、read_resource、list_prompts、get_prompt
      include: [search_bugs, semantic_bug_search, ask_rag, mantis_fetch_activity,
                prepare_mantis, create_mantis, mantis_add_note, send_email]
  jenkins:
    url: https://releaseqa-stackjenkins.corp.fortinet.com/mcp-server/mcp
    headers: {Authorization: "Basic ${JENKINS_MCP_BASIC}"}     # base64(user:apitoken)，由 install.sh 生成
```

用 Hermes 自己的发现代码测量（`tests/list_registered_mcp_tools.py`）：

| | 设置 `resources/prompts: false` 之前 | 之后 |
|---|---|---|
| jenkins | 14 | **10** |
| mantis_tools | 12 | **8** |
| logintel | 26 | **26** |
| 合计 | 52 | **44**（与 opencode 相同） |

---

## Hermes：为什么按服务器的 `trust` 不够用

Hermes 内置的 MCP 审批是**按服务器**的：设置 `trust: untrusted` 后，每个没有 `readOnlyHint: true` 的工具都会先询问。

我们用 MCP SDK 探测了三个服务器：

```
logintel:     26 tools, readOnlyHint=True on 0
mantis-tools: 95 tools, readOnlyHint=True on 0
jenkins:      19 tools, readOnlyHint=True on 0
```

所以 `untrusted` 会在**每次搜索**前都询问，完全不可用，我们需要**按工具**询问。

Hermes 提供 `pre_tool_call` hook，它可以返回：
- `{"action": "block", "message": …}`：工具不会运行
- `{"action": "approve", "message": …, "rule_key": …}`：进入**与危险 shell 命令相同的人工审批关口**（`tools/approval.py::request_tool_approval`）。不会被"智能"自动批准，没有人应答时失败即拦截。

---

## Hermes：`mcp-ask-first` 插件（全部代码）

```python
ASK_FIRST_TOOLS = {
    "mcp__mantis_tools__create_mantis": "file a new Mantis bug",
    "mcp__mantis_tools__mantis_add_note": "add a note to a Mantis bug",
    "mcp__mantis_tools__send_email": "send an email",
    "mcp__jenkins__triggerBuild": "start a Jenkins build",
    "mcp__jenkins__rebuildBuild": "re-run a Jenkins build",
}

def nobody_can_answer():                      # -z、chat -q 和 cron
    try:
        from tools.approval_context import _is_cron_approval_context, _is_single_query_approval_context
        return _is_single_query_approval_context() or _is_cron_approval_context()
    except ImportError:                       # 新版 Hermes 挪走了这些函数：直接读标记
        return any(os.environ.get(n, "").lower() in ("1", "true", "yes")
                   for n in ("HERMES_SINGLE_QUERY_SESSION", "HERMES_CRON_SESSION"))

def ask_before_shared_system_change(tool_name="", args=None, **_):
    action = ASK_FIRST_TOOLS.get(tool_name)
    if action is None:
        return None
    if nobody_can_answer():
        return {"action": "block", "message": f"Not run: '{tool_name}' would {action}, …"}
    preview = json.dumps(args or {}, ensure_ascii=False)[:300]
    return {"action": "approve", "message": f"{action}: {preview}", "rule_key": tool_name}

def register(ctx):
    ctx.register_hook("pre_tool_call", ask_before_shared_system_change)
```

`rule_key = tool_name`，所以选择"本会话内允许"后，同一工具后续的调用都不再询问。

---

## Hermes：测试抓到的 bug

第一版总是返回 `approve`。在 `hermes -z`（单次运行）中的测试结果：

```
$ hermes -z "Call mcp__logintel__logintel_list_projects …"    # 测试副本中设为受控
The tool returned 13 projects.                               # ← 没有询问就运行了
```

根因在 `hermes_cli/oneshot.py`：

```python
# Non-interactive by definition — an approval prompt would hang forever.
os.environ["HERMES_YOLO_MODE"] = "1"          # 审批关口的第一项检查：yolo → 直接批准
os.environ["HERMES_SINGLE_QUERY_SESSION"] = "1"
```

修复：会话被标记为单次查询或 cron 时直接 **block**。修复后：

```
$ hermes -z "…"
The call was blocked. "Not run: 'mcp__logintel__logintel_list_projects' would …,
which needs the user's approval, and this run (hermes -z / chat -q / cron) has nobody to ask."
```

`hermes chat -q` 同样被拦截，即使经过 Hermes 的 `tool_call` 工具搜索包装调用也一样。

---

## VS Code 中的 Hermes（ACP）

- 扩展 `formulahendry.acp-client`，安装到 **Remote-SSH 服务器端**（`~/.vscode-server/…/code-server --install-extension`）
- agent 条目写入虚拟机上的 **Machine** 设置：`~/.vscode-server/data/Machine/settings.json`

```json
"acp.agents": { "Hermes Agent": {
  "command": "/home/<you>/.local/bin/hermes", "args": ["acp"],
  "env": { "PATH": "/home/<you>/.local/bin:/home/<you>/.hermes/bin:/usr/local/bin:/usr/bin:/bin" } } }
```

- 用完整路径，因为扩展不会继承你 shell 的 `PATH`。`~/.hermes/bin` 里是 `tirith`，Hermes 的命令扫描器
- ACP 模式在启动时用**后台线程**做 MCP 发现（`acp_adapter/entry.py`），所以一份 `config.yaml` 同时服务终端和 VS Code
- 审批通过 ACP `session/request_permission` 桥接，选项有 `allow_once`、`allow_session`、`allow_always`、`deny` 和 `deny_always`

---

# 第 3 部分 · 测量得出的模型链

---

## 2026-09-23 发生了什么

| 时间 | 个人密钥 | 项目密钥 |
|---|---|---|
| 11:47 | `glm-5.3` 正常应答 | `deepseek-v4.1-flash` 正常应答 |
| ~21:00 | `/v1/models` **只**列出 `glm-5.3-flash`；`glm-5.3` → **403** | 列出 `glm-5.3`、`glm-5.3-flash`；`deepseek` → **403** |

```
$ curl …/chat/completions -d '{"model":"deepseek-v4.1-flash",…}'
http=403  err= your access level does not permit model deepseek-v4.1-flash
```

**固定**模型链的后果：
- Hermes：每轮对话都悄悄回退到本地 Qwen（`sessions.model = qwen3.6-35b-a3b`）
- opencode：同样如此，外加 30 分钟停用带来的来回切换
- 单独对 403 模型运行 `hermes -z` 会**一直挂到 180 秒超时**

---

## 借鉴 Log Intelligence LLM 适配器

`log-intelligence/docs/implement_log/2026-09-24_llm_automatic_adapter_plan.md` 在服务器上每 15 分钟做一次检测 → 决策 → 应用。我们沿用了它的不变量：

| 适配器不变量 | 在安装脚本中的体现 |
|---|---|
| 可用 = **该密钥的对话探测刚刚成功**；`/v1/models` 表示密钥能看到什么，而不是能调用什么 | 对每个首选模型发一次单 token 的 `POST /chat/completions` |
| 含 "budget" 的 429 = **本轮**拒绝；普通 429 = 仍可用 | 同样的分类 |
| **新模型家族绝不自动启用** | 不在任何 `prefer` 列表中的 id 打印为 "new models seen" |
| **探测失败时绝不清空模型链** | 网关不可达 → 保留该密钥当前的链接 |
| 先测量，再实时应用 | cron `--adapt`；opencode 插件热加载模型链 |

我们没有照搬仅限服务器的部分：数据库表、邮件、按变体划分的 provider、运行时拒绝触发。

---

## 算法（`adapt_models.py` / `merge_into_hermes_home.py adapt`）

```python
for account in preferences["accounts"]:                      # 顺序 = 链的顺序
    key = env/key-file value;  if not key: skip
    status, body = GET f"{base_url}/models"
    if status is None:                                      # 网关不可达
        chosen += [当前链中该密钥的链接]; continue
    for model in account["prefer"]:
        if len(picks) >= account["take"]: break
        if (base_url, model) in used: continue              # Hermes：避免重复部署
        result = probe_model(base_url, key, model)          # max_tokens 16
        if result == "ok": picks.append(model)
    报告 [m for m in listed if m 不在任何 prefer 列表 且 不是非对话模型]
if not chosen: 保留当前链（或配置片段中的链）并警告
只在有变化时写入配置（opencode 另写 chain-fallback.json），并备份 .bak
```

```python
def probe_model(base_url, key, model):
    status, body = POST f"{base_url}/chat/completions" {"model": model, "max_tokens": 16, …}
    if status is None:                            return "unreachable"
    if status == 200 and not error_text(body):    return "ok"
    if status == 429 and "budget" not in reason:  return "ok"   # 限流，仍可用
    return "refused"
```

只用标准库（`urllib`），所以能在 `env -i` 的 cron 环境下运行。

---

## 偏好是数据，不是代码

```yaml
# hermes_installer/config/model-preferences.yaml
accounts:
  - key: FOS_AI_API_KEY               # 个人密钥：主模型
    base_url: https://fos-ai.fortinet.com/v1
    take: 1
    prefer: [glm-5.3, glm-5.3-flash, deepseek-v4.1-flash]
  - key: FOS_AI_FALLBACK_API_KEY      # 项目 token：回退 1
    base_url: https://fos-ai.fortinet.com/v1
    take: 1
    prefer: [glm-5.3, deepseek-v4.1-flash, glm-5.3-flash]
  - key: LOCAL_QWEN_API_KEY           # 内部 Qwen：最后的回退
    base_url: https://releaseqa-aiserver.corp.fortinet.com/v1
    take: 2
    prefer: [qwen3.6-35b-a3b, qwen3.5-122b-a10b-awq]
not_chat_patterns: [embedding, rerank, whisper]
```

- 每个列表中最强的模型排在最前，所以个人密钥重新拿到 `glm-5.3` 后，下一次每小时运行会**自动**把它恢复为主模型
- 启用新模型：把它加入列表，然后运行 `./install.sh --adapt`

---

## 每小时任务的实际运行（真实日志，2026-09-23）

```
17 * * * * HERMES_HOME=~/.hermes ~/git/hermes/install.sh --adapt >> ~/.hermes/logs/hermes_installer_adapt.log 2>&1
47 * * * * ~/git/opencode/install.sh --adapt >> ~/.config/opencode/adapt.log 2>&1
```

| 任务 | 最近一次 cron 运行 | 结果 |
|---|---|---|
| Hermes | 22:17 | `config.yaml unchanged` |
| opencode | 21:47 | `opencode.json unchanged`、`chain-fallback.json unchanged` |

探测结果（两个助手相同）：

| 密钥 | `glm-5.3` | `glm-5.3-flash` | `deepseek-v4.1-flash` | Qwen 3.6 / 3.5-122B / VL-235B |
|---|---|---|---|---|
| 个人 | ✗ 403 not permitted | ✓ | ✗ 403 | — |
| 第二个密钥 | ✓ | ✓ | ✗ 403 | — |
| 本地 Qwen | — | — | — | ✓ / ✓ / ✓（VL：只在 opencode 选择器中；Hermes 报告为新模型） |

→ 模型链：`glm-5.3-flash`（个人）→ `glm-5.3`（第二个密钥）→ `qwen3.6-35b-a3b` → `qwen3.5-122b-a10b-awq`

- 两个任务相隔 30 分钟，所以网关的变化 30 分钟内会被一个助手发现，一小时内两个都会跟上
- "unchanged" 的运行不写任何文件、也不产生备份，所以每小时运行是安全的
- Hermes 取满 `take` 个模型后就不再探测该密钥；opencode 会探测整个列表，所以它的选择器能多提供一些可用的模型

---

# 第 4 部分 · 安装指南

---

## 前置条件与 token

| 需要 | 检查方式 |
|---|---|
| Linux 虚拟机、bash、`curl`、`python3` | `python3 --version` |
| opencode：Node/npm（或备用的 curl 安装脚本） | `npm --version` |
| 私有仓库的 GitHub SSH 访问 | `ssh -T git@github.com` |
| VS Code Remote-SSH（可选） | `ls ~/.vscode-server` |

| `tokens.env` | 获取方式 | 留空时 |
|---|---|---|
| `FOS_AI_API_KEY` | AI 团队（个人 fos-ai 密钥） | 模型链从下一个密钥开始 |
| `FOS_AI_FALLBACK_API_KEY` | 项目 token | 复用个人密钥 |
| `LOCAL_QWEN_API_KEY` | QA infra（`releaseqa-aiserver`） | 没有本地模型 |
| `MANTIS_MCP_TOKEN` | 与 AI Assistant → My connections 中相同的 token（**与用户绑定**） | 关闭 Mantis |
| `JENKINS_MCP_TOKEN` | `user:apitoken`（Jenkins → Configure → API Token） | 关闭 Jenkins |
| `LOGINTEL_MCP_KEY` | Log Intelligence 管理员（`limcp_…`，只显示一次） | 关闭 Log Intelligence |

---

## 安装

```bash
# opencode
git clone git@github.com:bruceyu777/opencode_installer.git ~/git/opencode && cd ~/git/opencode
cp tokens.env.example tokens.env && chmod 600 tokens.env && vi tokens.env
./install.sh --cron

# Hermes（同一个 token 文件）
git clone git@github.com:bruceyu777/hermes_installer.git ~/git/hermes && cd ~/git/hermes
cp ~/git/opencode/tokens.env . && ./install.sh --cron
```

| 步骤 | opencode | Hermes |
|---|---|---|
| 1 程序 | `npm i -g opencode-ai@1.18.32`（备用：官方安装脚本） | 未安装 → 官方安装脚本 `--commit c0d7294769 --skip-setup --skip-browser --skip-computer-use`；版本较旧 → `hermes update --yes` |
| 2 token | 每个 token 一个权限 600 的文件 | 合并到 `~/.hermes/.env` |
| 3 配置 | `opencode.json` + 插件；没有 token 的 MCP → 禁用 | ruamel 合并受管理的键 + 插件 |
| 4 模型 | `adapt`（探测、选择、写入） | `adapt` |
| 5 VS Code | 扩展 + 关闭 Python 自动激活 | ACP Client 扩展 + `acp.agents` 条目 |
| 6 cron | `47 * * * * install.sh --adapt` | `17 * * * * install.sh --adapt` |
| 7 检查 | 列出 MCP，再对链上每个模型发 1 条提示 | MCP 测试、插件、网关探测 + 每个模型 1 条提示 |

选项：`--check`、`--adapt`、`--cron`、`--no-install`、`--no-vscode`、`--no-check`、`--tokens FILE`。

---

## 正常安装的输出（本机真实输出）

```
==> Tokens -> /home/fosqa/.hermes/.env
    FOS_AI_API_KEY           set      .env FOS_AI_API_KEY
    JENKINS_MCP_TOKEN        set      .env JENKINS_MCP_BASIC
    …
==> Config -> /home/fosqa/.hermes/config.yaml, plugin mcp-ask-first
    probing the models each key can call now:
    FOS_AI_API_KEY           glm-5.3                  refused (HTTP 403: your access level does not permit model glm-5.3)
    FOS_AI_API_KEY           glm-5.3-flash            ok
    FOS_AI_FALLBACK_API_KEY  glm-5.3                  ok
    LOCAL_QWEN_API_KEY       qwen3.6-35b-a3b          ok
    LOCAL_QWEN_API_KEY: new models seen, not used until added to a prefer list: qwen3-vl-235b
    chain: glm-5.3-flash (FOS_AI_API_KEY) -> glm-5.3 (FOS_AI_FALLBACK_API_KEY) -> qwen3.6-35b-a3b -> qwen3.5-122b-a10b-awq
==> Check: MCP servers
    logintel: Connected (1072ms)   mantis-tools: Connected (1115ms)   jenkins: Connected (1220ms)
==> Check: plugin mcp-ask-first
    enabled: Mantis filing/notes/email and Jenkins trigger/rebuild ask before running
==> Check: each model of the fallback chain (gateway first, then one Hermes prompt)
    glm-5.3-flash            gateway ok, Hermes PONG
    glm-5.3                  gateway ok, Hermes PONG
    qwen3.6-35b-a3b          gateway ok, Hermes PONG
    qwen3.5-122b-a10b-awq    gateway ok, Hermes PONG
```

---

## 真实示例：node16，一台全新的测试节点（2026-09-23）

`all-in-one-node16`（10.96.234.11），Ubuntu 24.04，用户 `fosqa`：**没有 Node.js、没有 npm**，没有 opencode，已有 VS Code 服务器。

```
$ git clone git@github.com:bruceyu777/opencode_installer.git ~/git/opencode && cd ~/git/opencode
$ cp tokens.env.example tokens.env && chmod 600 tokens.env && vi tokens.env    # 与我们虚拟机相同的 6 个 token
$ ./install.sh </dev/null                                                      # 无需输入，也不会询问
==> opencode 1.18.32
    npm not available or not writable; using the official installer (~/.opencode/bin)
    now: 1.18.32 at /home/fosqa/.opencode/bin/opencode
==> Tokens -> /home/fosqa/.config/opencode/*.key        （6 个都是 set）
==> Config -> /home/fosqa/.config/opencode
    fos-ai           glm-5.3                  refused (HTTP 403: your access level does not permit model glm-5.3)
    fos-ai           glm-5.3-flash            ok
    …
    chain: fos-ai/glm-5.3-flash -> fos-ai-logintel/glm-5.3 -> local-qwen/qwen3.6-35b-a3b -> local-qwen/qwen3.5-122b-a10b-awq
==> VS Code
    extension sst-dev.opencode: installed (reload the VS Code window)
    Python terminal auto-activation: turned off in /home/fosqa/.vscode-server/data/Machine/settings.json
==> Check: MCP servers        ✓ logintel  ✓ mantis-tools  ✓ jenkins  ⚠ microsoft365 needs authentication
==> Check: one prompt per model in the measured chain            4 × PONG
TOTAL 44.58s   exit=0
```

---

## node16 示例解读

| 看到的内容 | 原因 |
|---|---|
| `npm not available … using the official installer` | 节点上没有 Node：安装脚本改用 `curl https://opencode.ai/install \| bash -s -- --version 1.18.32` 安装到 `~/.opencode/bin`，并在 `~/.bashrc` 中加一行 PATH。不需要 sudo |
| `</dev/null` | 证明安装**不需要人工输入**，与 cron 或远程运行相同 |
| 模型链与我们的虚拟机相同 | 模型链取决于**密钥**，而不是机器：相同的 token → 相同的探测结果 |
| `extension … installed (reload the VS Code window)` | 扩展装在共享的 `~/.vscode-server/extensions` 中，所以**所有**服务器版本都能看到，包括用户 22:48 连接时 VS Code 新下载的版本（安装发生在 22:46） |
| `⚠ microsoft365 needs authentication` | 符合预期：OAuth，可选，需要浏览器 |
| 44.58 秒 | 下载 opencode、写配置、探测 9 个模型、4 条测试提示 |

VS Code 的证据，来自 node16 自己的日志（用户按下 Ctrl+Escape 时）：

```
22:51:34.756 [info] ExtensionService#_doActivateExtension sst-dev.opencode, activationEvent: 'onCommand:opencode.openNewTerminal'
```

留在 node16 上的内容：`~/.opencode/bin/opencode`、`~/.config/opencode/`（token 为权限 600 的文件）、`~/git/opencode`（含 `tokens.env`），以及 VS Code Machine 设置（备份 `.bak-<时间>`）。共享测试节点上**没有加 cron**：需要时运行 `./install.sh --no-install --no-check --cron`。

---

## 真实示例：node16 上的 Hermes（相同 token，2026-09-23）

```
$ git clone git@github.com:bruceyu777/hermes_installer.git ~/git/hermes && cd ~/git/hermes
$ cp ~/git/opencode/tokens.env . && ./install.sh </dev/null      # 用 nohup 启动，轮询日志
==> Hermes Agent (tested: v0.21.4)
    not installed: running the official installer, pinned to commit c0d7294769
    now: v0.21.4 at /home/fosqa/.local/bin/hermes
==> Tokens -> ~/.hermes/.env       6 个 set（官方安装脚本的模板 .env 被保留，备份 .env.bak-…）
==> Config -> ~/.hermes/config.yaml, plugin mcp-ask-first
    chain: glm-5.3-flash -> glm-5.3 -> qwen3.6-35b-a3b -> qwen3.5-122b-a10b-awq
==> VS Code：formulahendry.acp-client 已安装；acp.agents["Hermes Agent"] 已写入
==> Check：logintel / mantis-tools / jenkins 已连接；插件已启用；4 个 "gateway ok, Hermes PONG"
TOTAL 217.94s   exit=0
```

| 验收测试（`tests/acceptance.sh`） | node16 上的结果 |
|---|---|
| H1 重跑 · H2 `env -i` 下的 cron | `unchanged` · rc 0 |
| H3 模型看到的工具 | jenkins 10、logintel 26、mantis_tools 8 = **44** |
| H4 模型 → logintel | `13` |
| H5 强制 404 | `PONG`，`sessions.model = glm-5.3` |
| H6 在 `-z` 中 `triggerBuild` | 在到达 Jenkins 之前被拦截 |
| H7 通过 ACP `triggerBuild` | 权限请求 `start a Jenkins build: {"jobFullName": …}` → 拒绝 → 未运行 |
| H8 VS Code · H9 `tokens.env` | 条目和扩展都在 · 已被 git 忽略 |

遇到的陷阱：拒绝之后，在**同一**对话中选"允许一次"不会产生新请求；模型拒绝重试被拒绝过的操作。测试"允许"请开新会话。

---

## 在 shell 终端中使用（速查表）

```bash
cd ~/git/<项目>            # 一定要在项目文件夹中启动
opencode                  # 全屏界面              |  hermes            # 对话（hermes --tui = 新界面）
opencode -c               # 继续上一次会话         |  hermes -c
opencode run "…"          # 单次运行              |  hermes -z "…"
```

| opencode（leader = `Ctrl+X`） | | Hermes | |
|---|---|---|---|
| `Tab` | build ↔ plan agent | `Ctrl+C` / 按两次 | 打断 / 退出 |
| `Esc` | 停止回答 | `Alt+Enter`、`Ctrl+J` | 换行 |
| `Ctrl+P` | 命令面板 | `Ctrl+G` | 在 `$EDITOR` 中写提示 |
| `Ctrl+X` `n` / `l` / `m` | 新会话 / 会话列表 / 模型 | `/new`、`/sessions`、`/model` | 同样的功能 |
| `Ctrl+X` `c` | 压缩对话 | `/compress` | 压缩对话 |
| `@文件`、`!命令`、`/init` | 附加文件、运行 shell、生成 AGENTS.md | `/tools list`、`/reload-mcp` | 工具、重载 MCP |
| `Ctrl+C` / `Ctrl+D` | 退出 | `/status`、`/usage` | 模型、token |

- **通过 SSH 使用 tmux：** `tmux new -s ai` … `Ctrl+B d` … `tmux attach -t ai`
- **写操作工具先询问**只在交互式会话中生效；单次运行会直接拒绝
- 完整列表：`docs/guides/install-on-a-node.zh-CN.md` 第 4 节

---

# 第 5 部分 · 现场演示

---

## 演示 1：测量模型链（30 秒）

```bash
cd ~/git/hermes && ./install.sh --adapt
cd ~/git/opencode && ./install.sh --adapt
cat ~/.config/opencode/chain-fallback.json
tail -20 ~/.hermes/logs/hermes_installer_adapt.log       # 上一小时 cron 做了什么
```

讲解要点：
- 个人密钥对 `glm-5.3` 返回 403，而项目密钥仍然可以用
- `qwen3-vl-235b` 显示为"新模型、未使用"
- 第二次运行打印 `unchanged`，因为没有变化就不会重写

Dry run（不写入），Hermes：
```bash
~/.hermes/hermes-agent/venv/bin/python scripts/merge_into_hermes_home.py adapt \
  --preferences config/model-preferences.yaml --fragment config/config.yaml --hermes-home ~/.hermes --dry-run
```

---

## 演示 1 解读

| 命令 | 作用 |
|---|---|
| `./install.sh --adapt`（Hermes） | `find_hermes_python` → `merge_into_hermes_home.py adapt`。对 `model-preferences.yaml` 中的每个账户：先 `GET /models`，再对每个首选模型发一次单 token 的 `POST /chat/completions`，直到有 `take` 个模型应答；把模型链合并进 `config.yaml`（MCP 和插件条目也会重新应用） |
| `./install.sh --adapt`（opencode） | `python3 scripts/adapt_models.py`：探测每个 prefer 列表中的**所有**模型；写入 `opencode.json`（`model`、`small_model`、只保留有应答的模型）和 `chain-fallback.json` |
| `cat ~/.config/opencode/chain-fallback.json` | 插件读取的模型链：`{"chain": ["fos-ai/glm-5.3-flash", "fos-ai-logintel/glm-5.3", "local-qwen/qwen3.6-35b-a3b", "local-qwen/qwen3.5-122b-a10b-awq"]}` |
| `tail -20 …adapt.log` | 每小时 cron 运行做了什么；每次运行以 `==> Models -> …（时间）` 开头 |
| `… adapt … --dry-run` | 同样的探测和决策，打印 `chain (dry run, nothing written)`；修改 prefer 列表前先用它 |

怎么看输出：
- `ok` = 对话调用有应答。`refused (HTTP 403 …)` = 该密钥不允许使用。`ok (rate-limited right now, still usable)` = 普通 429
- `new models seen, not used …` = 出现在 `/models` 中但不在任何 prefer 列表里（绝不自动启用）
- `gateway unreachable, keeping …` = 没有 HTTP 应答；保留该密钥当前的链接
- `unchanged` = 没有写入任何文件，也没有备份

---

## 演示 2a：Hermes 回退，强制主模型返回 404

```bash
cd "$(mktemp -d)"                                          # 空文件夹：没有项目上下文，也没有真实文件
hermes -m no-such-model-404 -z "Reply with exactly: PONG"  # 只在本次运行中替换主模型名
python3 -c "import sqlite3,os;print(list(sqlite3.connect(os.path.expanduser('~/.hermes/state.db')).execute(
  'select id, model, billing_base_url from sessions order by started_at desc limit 1')))"
```

真实输出（2026-09-23 22:34）：

```
PONG
took 6.08s
[('20260923_223438_941dd1', 'glm-5.3', 'https://fos-ai.fortinet.com/v1/')]
```

网关对第一次调用的回复（用 curl 发送相同请求）：

```
{"error":"unknown model: no-such-model-404"}
HTTP 404
```

**只看 PONG 证明不了什么**：不管是谁应答，看起来都一样。`sessions` 那一行才是证据。

---

## 演示 2a 解读：三行命令

| 命令 | 作用 | 为什么这样写 |
|---|---|---|
| `cd "$(mktemp -d)"` | 新建一个空文件夹，例如 `/tmp/tmp.FjpadbdWCX` | Hermes 会读取启动目录（`AGENTS.md`、文件工具）。空目录 = 不会把项目内容发给模型，也碰不到真实文件 |
| `-m no-such-model-404` | **只**替换本次运行的 `model.default` | base URL 和密钥（`${FOS_AI_API_KEY}`）不变；`fallback_providers` 不变；不写入 `config.yaml` |
| `-z "Reply with exactly: PONG"` | 单次运行：一条提示，只打印最终回答，然后退出 | 简短、便宜，结果容易检查。（`-z` 还会关闭审批，所以先询问插件在这里会拦截写操作；本演示不用工具） |
| `sessions … limit 1` | `~/.hermes/state.db` 中最新的一行 | 在 v0.21.4 中，`model` = **实际应答的模型**，而不是请求的模型；`billing_base_url` = 实际调用的网关 |

为什么不用弄坏密钥的方式？那会改动你真实的 `.env`，而 export 一个变量也没用：`~/.hermes/.env` 会覆盖环境变量。假模型名是一种安全的制造失败的方法。

---

## 演示 2a 解读：Hermes 内部发生了什么

```
提示 ─► 主模型：no-such-model-404 @ fos-ai（个人密钥）
          └─ HTTP 404 "unknown model"              约 0.2 秒
             401/403/404 = 永久性错误 → 不重试，立即切换
             （429/5xx = 可能是暂时的 → 先重试，再切换）
      ─► fallback_providers[0]：glm-5.3 @ fos-ai（FOS_AI_FALLBACK_API_KEY）
             只有 base_url 和模型都与失败的那一环相同时才跳过
             → URL 相同、模型不同 → 使用
          └─ "PONG"                                约 5.8 秒
      ─► 会话记录：model=glm-5.3，billing_base_url=https://fos-ai.fortinet.com/v1/
```

- 模型链在**一轮对话之内**走，最多走一遍
- **下一轮会重新从主模型开始**。这就是每小时 `--adapt` 的意义：它把被拒绝的模型从链中彻底移除，每轮对话就不必再白白失败一次
- 这条跳过规则也是我们从不在同一网关的两个密钥上配置同一个模型的原因：Hermes 会把第二个当作重复项

---

## 演示 2a：证明了什么、没证明什么、注意事项

| 证明了 | 没有证明 |
|---|---|
| `config.yaml` 中的模型链已加载 | 403 "not permitted" 和 429 "budget" 路径（429 会先重试） |
| 永久性错误会**立即切换，没有重试延迟** | 第 3、4 环：用 `./install.sh --check`，它会单独测试每一环 |
| 回退 1 的密钥可用 | 后续轮次的行为（它们会重新从主模型开始） |
| `sessions.model` 能告诉你是谁应答的 | |

注意事项：
- **`-z` 没有日志证据：** 单次运行模式下回退不会写入 `~/.hermes/logs/agent.log`。请用 `sessions` 那一行，或像上面那样用 curl 直接问网关
- **同时运行的会话**（VS Code、cron）可能抢走 `limit 1`：同时查询 `id` 和 `started_at`，核对时间
- **查看整条链：** `hermes fallback list`
- **测试本地 Qwen 那一环：** 用一个临时 `HERMES_HOME`，其 `.env` 中的 fos-ai 密钥是无效的（见 VM 指南 §3.3）

---

## 演示 2b：opencode，会话仍在请求网关已移除的模型

```bash
opencode run --model fos-ai/glm-5.3 "Reply with exactly: PONG"
# 页脚："> build · glm-5.3-flash" —— 插件把已移除的模型转到了链首
```

- 前提：`opencode.json` 的 `fos-ai` 下仍声明着 `glm-5.3`，就像在每小时重新测量之前打开的会话那样
- `chat.message` hook → `replacementFor("fos-ai/glm-5.3")`：是本仓库的 provider，但不在 `chain-fallback.json` 中 → 链首 `fos-ai/glm-5.3-flash` → 弹出提示 "Using fos-ai/glm-5.3-flash instead of fos-ai/glm-5.3"
- `opencode run` 的页脚写的是实际应答的模型：这就是证据，作用与 2a 中的 `sessions.model` 相同

---

## 演示 2b 解读

| 部分 | 含义 |
|---|---|
| `opencode run` | opencode 单次运行：一条提示，打印回答，然后退出 |
| `--model fos-ai/glm-5.3` | 请求一个测量得出的链中已经没有的模型（个人密钥调用它会得到 403） |
| 页脚 `> build · glm-5.3-flash` | agent（`build`）和**实际应答的模型**，由 opencode 在回答之后打印 |

插件内部的路径（在任何网络请求之前）：

```
chat.message hook
  asked = "fos-ai/glm-5.3"
  replacementFor(asked)：provider "fos-ai" 受管理，且 asked 不在 chain-fallback.json 中
                        → 链首 "fos-ai/glm-5.3-flash"
  firstAvailableFrom(链首)：跳过 chain-fallback-router.json 中被停用的模型
  output.message.model = fos-ai/glm-5.3-flash
  提示："Using fos-ai/glm-5.3-flash instead of fos-ai/glm-5.3"
```

- 不会白白失败一次：已移除的模型根本不会被发给网关
- 前提条件及原因：opencode 只允许选择已声明的模型，而 `adapt` 会把被拒绝的模型从 `opencode.json` 中移除。在 `fos-ai` 下重新声明 `glm-5.3`，就能模拟一个在每小时运行**之前**打开的会话
- 其他 provider 的模型永远不会被转发（`managed_providers`）

---

## 演示 3：热加载到正在运行的会话（opencode）

```bash
cd "$(mktemp -d)"; opencode serve --port 4099 & sleep 8
SID=$(curl -s -X POST localhost:4099/session -H 'Content-Type: application/json' -d '{}' | jq -r .id)
ask() { curl -s -X POST localhost:4099/session/$SID/message -H 'Content-Type: application/json' \
  -d '{"model":{"providerID":"fos-ai","modelID":"glm-5.3-flash"},
       "parts":[{"type":"text","text":"Reply with exactly: PONG"}]}' | jq -r '.info.providerID+"/"+.info.modelID'; }
ask                                                    # fos-ai/glm-5.3-flash
cp ~/.config/opencode/chain-fallback.json /tmp/chain.bak
echo '{"chain":["local-qwen/qwen3.6-35b-a3b","local-qwen/qwen3.5-122b-a10b-awq"]}' > ~/.config/opencode/chain-fallback.json
ask                                                    # local-qwen/qwen3.6-35b-a3b（同一会话，无需重启）
cp /tmp/chain.bak ~/.config/opencode/chain-fallback.json; kill %1
```

我们的测试结果：第一次由 `fos-ai/glm-5.3-flash` 应答，第二次由 `local-qwen/qwen3.6-35b-a3b` 应答。

---

## 演示 3 解读

| 步骤 | 作用 |
|---|---|
| `opencode serve --port 4099 &` | 在后台运行 opencode 的 HTTP 服务器：TUI 和 VS Code 扩展用的是同一个引擎；`sleep 8` 等插件和 MCP 启动 |
| `POST /session` → `jq -r .id` | 创建一个会话；`SID` 让两次提问都在这个会话里 |
| `ask()` | `POST /session/$SID/message`，显式指定模型并附一段文本；回复中的 `info.providerID/modelID` 就是**实际应答者** |
| 第 1 次 `ask` | 链文件 = 测量得出的链 → `fos-ai/glm-5.3-flash` |
| `cp … /tmp/chain.bak`，再 `echo '{"chain":[…qwen…]}' > …` | 模拟一次每小时的 `--adapt` 发现两个 fos-ai 密钥都被拒绝 |
| 第 2 次 `ask` | **同一个进程、同一个会话** → `local-qwen/qwen3.6-35b-a3b` |
| `cp /tmp/chain.bak …; kill %1` | 恢复你真实的模型链，停止服务器 |

为什么第 2 次回答变了：
- 插件每轮都会对 `chain-fallback.json` 做一次 `stat()`；mtime 变了，所以重新解析了文件
- `fos-ai/glm-5.3-flash` 已不在链中 → `replacementFor()` → 新链首 `local-qwen/qwen3.6-35b-a3b`
- 没有这个机制的话，早上打开的 TUI 会一直请求中午就被网关拒绝的模型

需要 `jq`。请在临时目录中运行，并且不要跳过恢复那一步。

---

## 演示 4：模型实际看到的 MCP 工具

```bash
cd ~/git/hermes
hermes mcp list                      # 8 selected / 10 selected / all
hermes mcp test jenkins              # ✓ Connected (1599ms) ✓ Tools discovered: 19   （服务器总数）
~/.hermes/hermes-agent/venv/bin/python tests/list_registered_mcp_tools.py
# jenkins: 10 tools   logintel: 26 tools   mantis_tools: 8 tools   registry has 44 mcp__ tools
```

然后在 `hermes` 或 opencode 中问一个真实的问题：

> *"用 Log Intelligence 列出监控的项目，再给出最新 FortiOS 构建的健康状况。"*

> *"在 Mantis 中搜索关于 'ipsec tunnel flap after upgrade' 的 bug，总结前 3 个。"*

> *"获取 Jenkins 任务 X 最近一次构建的测试结果，告诉我哪些 QAID 失败了。"*

---

## 演示 4 解读

| 命令 | 显示什么 | 说明 |
|---|---|---|
| `hermes mcp list` | 已配置的服务器：传输方式、`8 selected` / `10 selected` / `all`、启用或禁用 | 读取 `config.yaml`（`tools.include`）；不建立连接 |
| `hermes mcp test jenkins` | 建立连接、计时，统计**服务器上**的工具数（19） | 这是连接测试，不是模型拿到的工具 |
| `tests/list_registered_mcp_tools.py` | 加载 `~/.hermes/.env`，运行 Hermes 自己的 `discover_mcp_tools()`，打印进入工具**注册表**的内容 | 唯一重要的数字：模型实际拿到的工具 |

以 Jenkins 为例，三个数字为什么不同：

| 服务器上有 | 设置 `resources/prompts: false` 之前 Hermes 注册了 | 现在模型拿到 |
|---|---|---|
| 19 | 14 = 10 个 include 的工具 + 4 个辅助工具（`list_resources`、`read_resource`、`list_prompts`、`get_prompt`） | **10** |

- 工具名是 `mcp__<服务器>__<工具>`，`-` 会变成 `_`（`mantis-tools` → `mantis_tools`）
- 用 Hermes 的 venv Python 运行脚本：它会导入 Hermes 的模块
- opencode 中的对应方法：让模型列出它的工具。`/experimental/tool` API 只列出内置工具

后面的示例问题各自用到一个服务器；工具调用会显示在会话记录里。

---

## 演示 5：防护

**交互式（终端或 VS Code）：会先询问**
> *"触发 Jenkins 任务 `hermes-gate-demo-does-not-exist`。"*

会出现提示：`start a Jenkins build: {…任务参数…} <mcp__jenkins__triggerBuild> (plugin approval rule)`。选择 **Deny**，模型会报告未获批准，并且不会重试。

**单次运行：直接拦截**
```bash
hermes -z "Use mcp__jenkins__triggerBuild to trigger job hermes-gate-demo-does-not-exist"
# → Not run: 'mcp__jenkins__triggerBuild' would start a Jenkins build, which needs the user's approval …
```

**opencode：** 同样的提示会弹出 opencode 的权限对话框（`ask` 规则）。

使用一个不存在的任务名：即使防护失效，Jenkins 也只会返回 404。

---

## 演示 5 解读

**交互式（终端或 VS Code）：**
```
模型调用 mcp__jenkins__triggerBuild
 → pre_tool_call hook（mcp-ask-first）：工具在 ASK_FIRST_TOOLS 中，且有人在场
 → {"action": "approve", "message": "start a Jenkins build: {…}", "rule_key": "mcp__jenkins__triggerBuild"}
 → tools/approval.py request_tool_approval → 与危险 shell 命令相同的人工审批关口
      终端：提示   |   VS Code：ACP session/request_permission（允许一次 / 本会话 / 永久 / 拒绝）
 → 拒绝：工具返回 "BLOCKED: User denied …"；模型被告知不要重试
```

**单次运行 `hermes -z`：**
```
-z 设置 HERMES_YOLO_MODE=1（本会自动批准一切）和 HERMES_SINGLE_QUERY_SESSION=1
 → hook 发现 nobody_can_answer() → {"action": "block", …}   （block 优先于 yolo）
 → 工具结果：{"error": "Not run: 'mcp__jenkins__triggerBuild' would start a Jenkins build, …"}
```
今晚已验证：没有触发任何构建。

**opencode：** `permission` 中的 `"jenkins_triggerBuild": "ask"` → opencode 自己的权限对话框。

- 为什么用不存在的任务：即使防护失效，Jenkins 也只会返回 404，所以演示是安全的
- `rule_key` = 工具名："本会话内允许"覆盖同一工具后续的调用；"永久允许"会写入 `config.yaml` 的 `command_allowlist`（永久生效）

---

## 演示 6：在全新节点上安装，然后运行验收测试（node16）

```bash
# 在节点上（VS Code Remote-SSH 终端，或 ssh）
git clone git@github.com:bruceyu777/opencode_installer.git ~/git/opencode && cd ~/git/opencode
cp ~/path/to/your/tokens.env . && chmod 600 tokens.env
time ./install.sh </dev/null                          # node16 上约 45 秒

./install.sh --no-install --no-vscode --no-check       # T1 重跑："unchanged"
env -i HOME=$HOME PATH=/usr/bin:/bin ./install.sh --adapt   # T2 cron 命令：rc 0
./install.sh --check                                   # T3 MCP + 每个模型一条提示
cd "$(mktemp -d)"
opencode run "Call the logintel tool that lists the monitored projects. Reply only with the number of projects."   # T4
opencode run "Use the jenkins triggerBuild tool to trigger job opencode-gate-demo-does-not-exist"                    # T7
```

node16 上的真实结果：

| 测试 | 输出 |
|---|---|
| T1 / T2 | `opencode.json unchanged`、`chain-fallback.json unchanged`，rc 0 |
| T3 | ✓ logintel ✓ mantis-tools ✓ jenkins；4 个 PONG |
| T4 | `⚙ logintel_logintel_list_projects` → `13` |
| T5 失效模型（沙箱） | `> build · glm-5.3-flash` |
| T6 热加载（沙箱 `serve`） | 第 1 次 `fos-ai/glm-5.3-flash`，第 2 次 `local-qwen/qwen3.6-35b-a3b` |
| T7 防护 | `! permission requested: jenkins_triggerBuild (*); auto-rejecting` |
| T8 | `tokens.env` 已被 git 忽略 |

---

## 演示 6 解读

| 步骤 | 证明了什么 |
|---|---|
| 克隆 + `tokens.env` + `./install.sh </dev/null` | README 的快速开始在一台我们从未碰过的机器上可用，没有 Node，也没有任何提示 |
| T1 重跑 | 幂等：每次 `git pull` 之后都可以放心重跑 |
| T2 `env -i … --adapt` | 每小时任务在 cron 的空环境下也能运行（只用标准库 Python） |
| T3 `--check` | 每个 MCP 服务器都能连接；链上每一环都能在临时数据目录中应答 |
| T4 `opencode run "…list projects…"` | 端到端：模型 → MCP 工具调用（`⚙ logintel_logintel_list_projects`）→ Log Intelligence → 回答 `13` |
| T5 / T6（配置的沙箱副本） | 失效模型转发和热加载在另一台机器上表现相同 |
| T7 对不存在的任务调用 `triggerBuild` | `ask` 规则：单次运行的 `opencode run` 中无人应答，所以 opencode **自动拒绝**；没有触发任何构建 |
| T8 | 仓库中唯一含 token 的文件已被 git 忽略 |

在 node16 上学到的：
- **T7 之后 `opencode run` 不会退出。** 调用被拒绝了，但运行会一直等到被杀掉（我们的 180 秒超时）。这是 opencode 的行为；交互式会话会正常弹出对话框
- 整套测试是一个脚本：`n16-tests.sh`，通过 ssh 管道执行（`ssh node16 'bash -s' < n16-tests.sh`）；含密钥副本的沙箱由 `trap … EXIT` 删除
- 不需要额外的 SSH 密钥：node16 自己的 GitHub 密钥就能克隆私有仓库

---

# 第 6 部分 · 测试

---

## 测试策略

凡是涉及真实网关、MCP 服务器或配置文件的部分，都**在沙箱中对真实系统测试**：

| 沙箱 | 方法 |
|---|---|
| Hermes home | `HERMES_HOME=$(mktemp -d)`，因为 home 中的 `.env` 会覆盖环境变量 |
| opencode 配置与数据 | `XDG_CONFIG_HOME` / `XDG_DATA_HOME` 临时目录，检查失败也不会停用你真实的模型 |
| 整个用户环境 | `env -i HOME=/tmp/sandbox PATH=/usr/bin:/bin ./install.sh`，真实的 `hermes` 不在 `PATH` 上 |
| 防护测试 | 插件副本额外把一个**无害的只读工具**（`logintel_list_projects`）设为受控，即使防护失效也不会写入任何东西 |
| Cron | 在 `env -i` 下运行 crontab 中的完整命令 |

每个含有密钥副本的临时目录在测试后都会删除。

---

## 测试矩阵：安装脚本

| # | 场景 | 结果 |
|---|---|---|
| 1 | Hermes 全新安装，沙箱 `HOME`，干净的 `PATH` | 官方安装脚本固定到 `c0d7294769`，所有检查通过，**437 秒** |
| 2 | Hermes，只有 Qwen 和 logintel 的 token，空 home | 模型链从 `qwen3.6-35b-a3b` 开始；mantis 和 jenkins 为 `enabled: false`；logintel 已连接 |
| 3 | Hermes，在已有的 545 行 `config.yaml` 上 | diff 只有受管理的行；第二次运行 `unchanged` |
| 4 | Hermes adapt，本地网关不可达（`127.0.0.1:9`） | **保留**当前配置中的 Qwen 链接 |
| 5 | opencode 安装到沙箱 `XDG_*` | 写入模型链，4 个都 PONG，第二次 `--adapt` 为 `unchanged` |
| 6 | opencode 检查循环 | **发现 bug**：只测了第 1 个模型（`opencode run` 读走了循环的 stdin）→ 用 `</dev/null` 修复 |
| 7 | 两条 cron 命令在 `env -i` 下运行 | rc 0，`unchanged` |
| 8 | 密钥泄露 | 扫描每个提交的文件中是否含任何 token 值及 `user:token` 的 base64：0 处命中 |

---

## 测试矩阵：行为

| # | 测试内容 | 方法 | 结果 |
|---|---|---|---|
| 9 | 工具筛选 | Hermes 发现 → 注册表 | 44 个工具（10/8/26），与 opencode 相同 |
| 10 | 工具标注 | 对 3 个服务器调用 MCP SDK `list_tools()` | 140 个中 0 个有 `readOnlyHint` → 需要按工具控制 |
| 11 | 防护，`hermes -z` | 受控的测试工具 | 第 1 版**直接运行了**（yolo）→ 第 2 版被拦截 |
| 12 | 防护，`hermes chat -q` | 同上 | 被拦截，经 `tool_call` 包装调用时也一样 |
| 13 | 防护，ACP 选 deny | `tests/acp_permission_test.py` | 收到一个 `session/request_permission`，工具未运行，未重试 |
| 14 | 防护，ACP 选 allow_once | 同一脚本 | 工具运行，返回 13 个项目 |
| 15 | 失效模型转发 | `opencode run --model fos-ai/glm-5.3` | 由 `glm-5.3-flash` 应答 |
| 16 | 热加载 | `opencode serve`，两次提问之间改写模型链 | 第 2 次提问由本地 Qwen 应答，无需重启 |
| 17 | 模型链的每一环 | 每个模型一个临时 home 或数据目录 | 两个 agent 都是 4/4 PONG |
| 18 | VS Code 中 opencode 崩溃 | 两份日志时间戳对齐 | 已修复；终端保持打开 |
| 19 | 在 node16 上安装 opencode（全新节点，没有 Node.js） | 克隆 → `./install.sh </dev/null` | 44.6 秒 exit 0，走官方安装脚本备用路径；T1–T8 全部通过（演示 6） |

---

## ACP 测试客户端（把 VS Code 做的事写成脚本）

```python
# tests/acp_permission_test.py  （基于 stdio 的 JSON-RPC，按行分隔）
request("initialize", {"protocolVersion": 1,
        "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False}, "terminal": False}})
time.sleep(15)                                  # 等后台 MCP 发现完成
session_id = request("session/new", {"cwd": workdir, "mcpServers": []})["sessionId"]
for answer in ("deny", "allow_once"):
    request("session/prompt", {"sessionId": session_id, "prompt": [{"type": "text", "text": prompt}]})
    # 收到 "session/request_permission" 时：
    send({"jsonrpc": "2.0", "id": message["id"],
          "result": {"outcome": {"outcome": "selected", "optionId": answer}}})
```

真实输出：
```
--- answer to permission requests: deny
    permission requests: [{"title": "TEST GATE list projects: {}: <mcp__logintel__logintel_list_projects> (plugin approval rule)",
                           "options": ["allow_once","allow_session","allow_always","deny","deny_always"]}]
    agent text: The tool did not run — it was blocked before execution … I did not retry the call.
--- answer to permission requests: allow_once
    agent text: The tool ran successfully and returned **13 projects** …
```

自己运行：`HERMES_HOME=<测试 home> ~/.hermes/hermes-agent/venv/bin/python tests/acp_permission_test.py "$PWD" "<提示词>"`

---

## 随时重跑检查

```bash
./install.sh --check          # 两个仓库都支持：MCP 服务器 + 插件 + 链上每个模型一条提示
./install.sh --adapt          # 立即重新测量
hermes mcp test mantis-tools  # 单个服务器，带耗时
hermes fallback list          # Hermes 视角下的模型链
opencode mcp list             # opencode 视角下的 MCP 服务器
```

出问题时看哪里：

| 内容 | 位置 |
|---|---|
| 每小时的 adapt 运行记录 | `~/.hermes/logs/hermes_installer_adapt.log`、`~/.config/opencode/adapt.log` |
| 哪个模型应答的（Hermes） | `~/.hermes/state.db` 中的 `sessions.model`、`billing_base_url` |
| 被停用的模型（opencode） | `~/.local/share/opencode/chain-fallback-router.json`（删除即解除停用） |
| Hermes 错误 | `~/.hermes/logs/errors.log`、`hermes logs --follow` |

---

# 第 7 部分 · 经验与限制

---

## 经验

1. **模型列表不是契约。** `/v1/models` 列着 deepseek，而每次调用都返回 403。要用真实调用去探测。
2. **把审批路径从头读到尾。** Hermes 的 `-z` 在任何插件运行之前就设置了 `YOLO=1`；只有端到端测试才发现我们的防护被绕过了。
3. **看模型实际看到了什么。** 服务器说有 19 个工具，配置写的是 10 个，而在关闭 resources 和 prompts 之前 Hermes 注册了 14 个。
4. **合并，不要覆盖。** 用户的 agent 配置又大又经过手工调整；YAML 往返合并能保持原样。
5. **按同事实际的使用方式测试安装脚本：** 空的 `HOME`、干净的 `PATH`、不完整的 token。
6. **两份日志在同一毫秒的记录**，解开了任何单份日志都解释不了的 VS Code 崩溃。
7. **借鉴已经可用的设计。** Log Intelligence 适配器的不变量直接对应成了一个 150 行的脚本。

---

## 限制（要对用户说清楚）

- **筛选不是沙箱。** 隐藏工具和先询问只控制*工具这条路径*。agent 以你的身份执行 shell，token 也可读（`~/.hermes/.env`、`~/.config/opencode/*.key`；Hermes 只清除它自己认识的环境变量名）。2026-09-23 本机上的一个 opencode 会话就读取了 `mcp-mantis.key`，并在 `/tmp` 中写了自己的 MCP 客户端。
- **"Allow always"** 会写入一条永久规则（`command_allowlist`）。
- **`--yolo` / `approvals.mode: off`** 也会关闭先询问提示。
- **按小时的粒度：** 两次运行之间，由单轮内的回退兜底，代价是一次失败的调用。
- **个人密钥承担交互负载。** 后台任务应使用 log-intel 密钥（见适配器的不变量 4）。
- **Microsoft 365 MCP** 已配置但未测试（通过 claude.ai 托管的端点做 OAuth）。

---

## 下一步

- 运行时快速通道：立即对 403 做出反应（适配器的 `llm_runtime_refusals` 思路），不必等每小时的运行
- 加入本地的 `fosqa-tools` MCP 服务器（创建 FGT 虚拟机、KVM 清理），所有工具都先询问
- 两个仓库共享 `model-preferences`（单一可信来源）
- 模型链变化或出现新模型时发邮件/Teams 通知
- 在 CI 中为 `adapt` 建立正式的测试框架（模拟网关：200、403 permit、429 budget、429 限流、超时）

**仓库：** `bruceyu777/opencode_installer`、`bruceyu777/hermes_installer`
**文档：** 各仓库的 `docs/guides/fosqa-vm-llm-chain.md`（English + 中文）

提问时间
