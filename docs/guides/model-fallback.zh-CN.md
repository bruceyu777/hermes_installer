---
title: Hermes Agent：fos-ai 网关出错时自动切换到本地 Qwen
kind: guide
created: 2026-09-21
updated: 2026-09-21
status: current
verified_against: Hermes Agent v0.16.0（2026.6.5，上游 c6e99ab3），Ubuntu（内核 7.0.0-30），2026-09-21 一次性运行实测
summary: 使用 Hermes 内置的 fallback_providers 链，让 fos-ai 网关失败时（预算时段外的 HTTP 429、5xx、401/403/404）本轮对话继续在内部 Qwen 模型上完成，密钥保存在 ~/.hermes/.env。
related:
  - ../INDEX.md
  - model-fallback.md
  - install-hermes.zh-CN.md
  - vscode-acp.zh-CN.md
  - ~/git/opencode/docs/guides/model-fallback.zh-CN.md
  - ~/.hermes/config.yaml
---

# Hermes Agent：fos-ai 网关出错时自动切换到本地 Qwen

主模型来自 Fortinet 的 fos-ai 网关，该网关只在固定时间段内有预算，其余时间返回 HTTP 429
（opencode 的指南记录了同一个网关）。与 opencode 不同，Hermes **内置**回退链，不需要插件：
`config.yaml` 顶层的 `fallback_providers` 列表，用 `hermes fallback` 管理。

应用本文后本机的状态（2026-09-21）：

| 项目 | 状态 |
|---|---|
| 主模型 | `https://fos-ai.fortinet.com:443/v1` 上的 `glm-5.3`（`provider: custom`，密钥内联在 `config.yaml`） |
| 回退 1 | `https://releaseqa-aiserver.corp.fortinet.com/v1` 上的 `qwen3.5-122b-a10b-awq` |
| 回退 2 | 同一网关上的 `qwen3.6-35b-a3b`（更小，122B 也失败时使用） |
| Qwen 密钥 | `~/.hermes/.env` 中的 `LOCAL_QWEN_API_KEY`（权限 600），通过 `key_env` 引用 |
| 备份 | `~/.hermes/config.yaml.bak-20260921-162640`、`~/.hermes/.env.bak-20260921-162640` |
| 验证结果 | 主模型返回 HTTP 404 的一次运行由 Qwen 在 4.6 秒内回答；正常运行仍走 fos-ai |

## 1. 探测网关

两个网关都是 OpenAI 兼容接口。密钥从文件读取，命令行上不出现机密。（Hermes 的 fos-ai 密钥与
opencode 的不同，访问级别也不同：2026-09-21 16:25 太平洋时间，opencode 的密钥在聊天接口得到
429，而 Hermes 的密钥得到 200。）

```bash
HK=$(python3 -c "import yaml;print(yaml.safe_load(open('$HOME/.hermes/config.yaml'))['model']['api_key'])")
QK=$(grep '^LOCAL_QWEN_API_KEY=' ~/.hermes/.env | cut -d= -f2-)

curl -s -H "Authorization: Bearer $HK" https://fos-ai.fortinet.com/v1/models | python3 -m json.tool
curl -s -H "Authorization: Bearer $QK" https://releaseqa-aiserver.corp.fortinet.com/v1/models | python3 -m json.tool
```

2026-09-21 16:25（太平洋时间）的结果：

| 网关 | Base URL | 模型 |
|---|---|---|
| fos-ai | `https://fos-ai.fortinet.com/v1` | `deepseek-v4.1-flash`、`glm-5.3`、`glm-5.3-flash` |
| local-qwen | `https://releaseqa-aiserver.corp.fortinet.com/v1` | `qwen3.5-122b-a10b-awq`、`qwen3.6-35b-a3b`、`qwen3-vl-235b`、`qwen3-vl-embedding-8b` |

回退链必须处理的失败，即 fos-ai 在预算时段外的返回：

```json
{"error":"Your access level has no budget during this time block. It reopens at the start of the next budget block."}
```

## 2. Hermes 回退的工作方式

摘自上游文档（安装目录中的 `website/docs/user-guide/features/fallback-providers.md`）：

- 触发条件：**429** 和 **5xx** 在重试预算耗尽后（本机 `agent.api_max_retries: 3`），
  **401/403** 和 **404** 立即触发，反复出现的格式错误/空响应也触发。
- 触发后 Hermes 解析回退凭据，构建新客户端，就地替换模型、provider 和客户端，重置重试计数，
  继续同一轮对话。会话历史和工具调用都保留。
- **按轮生效**：每条新的用户消息都重新从主模型开始。一轮之内回退链最多走一遍；全部失败则
  进入常规错误处理。
- 子 Agent（`delegate_task`）、cron 任务以及 `provider: auto` 的辅助任务（压缩、标题、视觉等）
  都继承该链。
- 回退链刻意不提供环境变量，只存在于 `config.yaml` 中。
- CLI、TUI、消息网关和 ACP（VS Code）行为一致，因为它们共用同一个 Agent 内核。

## 3. 配置

### 3.1 把 Qwen 密钥放进 `.env`

Hermes 启动时加载 `~/.hermes/.env`。回退条目通过 `key_env` 引用变量名，密钥不会出现在
`config.yaml` 中。

```bash
cd ~/.hermes && cp -p .env .env.bak-$(date +%Y%m%d-%H%M%S)
printf '\n# Internal Qwen gateway (releaseqa-aiserver) - used by fallback_providers in config.yaml\nLOCAL_QWEN_API_KEY=%s\n' "$(tr -d '[:space:]' < ~/.config/opencode/local-qwen.key)" >> .env
chmod 600 .env
```

（密钥从 opencode 的 `~/.config/opencode/local-qwen.key` 复制；两个工具使用同一个网关账号。）

### 3.2 把回退链加入 `config.yaml`

可以交互式（`hermes fallback add` 打开与 `hermes model` 相同的选择器并追加到链尾），也可以
直接编辑顶层键，本机采用后者：

```bash
cd ~/.hermes && cp -p config.yaml config.yaml.bak-$(date +%Y%m%d-%H%M%S)
```

```yaml
model:
  default: glm-5.3
  provider: custom
  base_url: https://fos-ai.fortinet.com:443/v1
  api_key: <redacted>
  context-length: 262144

fallback_providers:
  # Tried in order when the primary (fos-ai glm-5.3) fails with 429/5xx/401/403/404.
  # Key comes from LOCAL_QWEN_API_KEY in ~/.hermes/.env
  - provider: custom
    model: qwen3.5-122b-a10b-awq
    base_url: https://releaseqa-aiserver.corp.fortinet.com/v1
    key_env: LOCAL_QWEN_API_KEY
  - provider: custom
    model: qwen3.6-35b-a3b
    base_url: https://releaseqa-aiserver.corp.fortinet.com/v1
    key_env: LOCAL_QWEN_API_KEY
```

`custom` 条目的字段说明：

| 字段 | 必需 | 含义 |
|---|---|---|
| `provider` | 是 | 任意 OpenAI 兼容端点用 `custom`；内置名称（`openrouter`、`anthropic` 等）也可以 |
| `model` | 是 | 网关列出的模型 id |
| `base_url` | `custom` 必需 | 端点，含 `/v1` |
| `key_env` | 二选一 | 存放密钥的环境变量名（别名 `api_key_env`） |
| `api_key` | 二选一 | 内联密钥；本机不用，避免机密进入 YAML |

缺少 `provider` 或 `model` 的条目会被静默忽略。`base_url` 与当前后端相同的条目会被跳过并记录
`Fallback skip` 警告（同一网关没有重试意义）。

### 3.3 重启正在运行的 Agent

CLI 运行在启动时读取配置。VS Code 的 ACP 适配器是常驻进程：修改配置后执行
`ACP: Restart Agent` 或重新加载窗口。

## 4. 验证

### 4.1 回退链已加载

```bash
hermes fallback list
```

2026-09-21 实测：

```
  Primary:   glm-5.3  (via custom)

  Fallback chain (2 entries):
    1. qwen3.5-122b-a10b-awq  (via custom)  [https://releaseqa-aiserver.corp.fortinet.com/v1]
    2. qwen3.6-35b-a3b  (via custom)  [https://releaseqa-aiserver.corp.fortinet.com/v1]
```

### 4.2 真实切换

验证时 Hermes 密钥的 fos-ai 正处于预算时段内，无法制造真实的 429。用一个不存在的模型名让
网关返回 HTTP 404（`{"error":"unknown model: ..."}`），走的是同样的立即回退路径。两次运行都在
空目录用一次性模式；由哪个后端回答，从 `~/.hermes/state.db` 的会话表读取。

```bash
cd "$(mktemp -d)"
/usr/bin/time -f "%es" hermes -z "Reply with exactly: PONG-A"                          # A：主模型
/usr/bin/time -f "%es" hermes -m no-such-model-404 -z "Reply with exactly: PONG-B"     # B：主模型 404 -> 回退

python3 - <<'PY'
import sqlite3
c=sqlite3.connect('/home/yzhengfeng/.hermes/state.db')
for r in c.execute("select id, model, billing_base_url, output_tokens from sessions order by started_at desc limit 2"): print(r)
PY
```

2026-09-21 16:27（太平洋时间）实测：

```
A: PONG-A   3.76s
B: PONG-B   4.60s
('20260921_162705_44d037', 'no-such-model-404', 'https://releaseqa-aiserver.corp.fortinet.com/v1/', 4)   <- 由 Qwen 回答
('20260921_162700_906844', 'glm-5.3',           'https://fos-ai.fortinet.com:443/v1',               40)  <- 由 fos-ai 回答
```

INFO 级别的 `agent.log` 不会为切换打印日志；`billing_base_url` 列是可靠的证据。遇到 429 时，
同样的切换会在三次重试（几秒退避）之后发生，因此回退回答的耗时会比测试 B 长。

## 5. 使用

1. 照常提问（终端、TUI 或 VS Code）。fos-ai 正常回答时一切不变。
2. fos-ai 以列表中的错误失败时，本轮在 `qwen3.5-122b-a10b-awq` 上完成；它也失败则用
   `qwen3.6-35b-a3b`。对话不中断，消息不丢失。
3. 下一条提示词会再次尝试 fos-ai。没有冷却计时器（与 opencode 插件不同），所以在预算关闭
   时段，每条提示词都要先经历一次失败的 fos-ai 尝试加重试，Qwen 才回答。

手动控制：

| 目的 | 操作 |
|---|---|
| 整个会话都用 Qwen（已知预算时段关闭） | `hermes -m qwen3.5-122b-a10b-awq --provider custom` 不够，因为 `--provider custom` 仍使用主模型的 `base_url`；应运行 `hermes model` 选择 Qwen 端点，或在 VS Code 中用 `ACP: Set Agent Model` |
| 调整顺序或扩展链 | `hermes fallback add` / `hermes fallback remove`，或编辑 YAML |
| 关闭回退 | `hermes fallback clear`（写入 `fallback_providers: []`） |
| 查看由哪个后端回答 | 查询 `~/.hermes/state.db` 的 `billing_base_url`（4.2 节） |
| 把辅助任务（压缩、标题）路由到别处 | `config.yaml` 中的 `auxiliary.<task>.provider/model/base_url`；`auto` 时继承本链 |

各部分所在位置：

| 部件 | 路径 |
|---|---|
| 回退链 | `~/.hermes/config.yaml` 中的 `fallback_providers:` |
| Qwen 密钥 | `~/.hermes/.env` 中的 `LOCAL_QWEN_API_KEY` |
| 回退逻辑 | `~/.hermes/hermes-agent/agent/chat_completion_helpers.py`（轮中），`agent/agent_init.py`（初始化时，主模型完全没有凭据的情况） |
| 会话/计费记录 | `~/.hermes/state.db`，表 `sessions` |
| 日志 | `~/.hermes/logs/agent.log`、`errors.log`（`hermes logs`） |
| 上游文档 | `~/.hermes/hermes-agent/website/docs/user-guide/features/fallback-providers.md` |

## 6. 故障排除

| 现象 | 原因 | 处理 |
|---|---|---|
| 编辑后 `hermes fallback list` 仍显示 "No fallback providers configured" | YAML 缩进错误，或条目缺少 `provider`/`model` | 对照 3.2 节；`python3 -c "import yaml;print(yaml.safe_load(open('$HOME/.hermes/config.yaml'))['fallback_providers'])"` |
| `errors.log` 中出现 `Fallback to custom failed: provider not configured` | `.env` 中缺少或为空的 `LOCAL_QWEN_API_KEY`，或该进程未加载 `.env` | `grep -c '^LOCAL_QWEN_API_KEY=' ~/.hermes/.env`；重启 Agent |
| `Fallback skip: chain entry base_url ... matches current backend` | 该条目指向与主模型相同的网关 | 只对不同网关有意义；删除或修正该条目 |
| 回退生效但 429 时回答要 10–20 秒 | Hermes 在切换前会按 `api_max_retries` 次数带退避重试主模型 | 若无法接受，调低 `config.yaml` 中的 `agent.api_max_retries` |
| Qwen 也失败 | 没有配置第三级 | 用第 1 节的 curl 检查网关；再加一个条目 |
| VS Code 仍是旧行为 | ACP 适配器在修改配置前就已启动 | `ACP: Restart Agent` |

已知限制：

- 没有冷却期：每一轮新对话都会重试主模型。对于一次关闭数小时的网关，只有在重试开销可接受时
  才合适；否则在该时段手动切换主模型。
- 122B 和 35B 的 Qwen 模型做过聊天冒烟测试，opencode 指南中也做过工具调用测试。未在长时间的
  agent 会话中验证。
- 视觉模型（`qwen3-vl-235b`）不在链中；如需要请配置 `auxiliary.vision`。

## 7. 安全说明

- `~/.hermes/` 下的 `.env` 和 `config.yaml` 权限为 600。Qwen 密钥按名称引用（`key_env`），
  `hermes config show` 不会显示它；fos-ai 密钥是内联的，会显示。粘贴输出前给 `sk-...` 打码。
- 回退会把对话发送到第二个网关。两个网关都是 Fortinet 内部 TLS 端点，不涉及第三方。
- 启用前已阅读 `agent_init.py` 和 `chat_completion_helpers.py` 中的回退代码路径；内联 `api_key`
  和 `key_env` 都受支持。

## 8. 回滚

```bash
cd ~/.hermes
cp -p config.yaml.bak-20260921-162640 config.yaml
cp -p .env.bak-20260921-162640 .env
# 或者只需：hermes fallback clear
```

然后重启正在运行的 Agent（VS Code 中执行 `ACP: Restart Agent`）。

## History

| 日期 | 变更 |
|---|---|
| 2026-09-21 | 创建。探测两个网关，把 `LOCAL_QWEN_API_KEY` 加入 `.env`，写入两级 `fallback_providers` 链，通过 `state.db` 计费记录验证主路径和回退路径。 |
| 2026-09-22 | fosqa 虚拟机采用不同的配置（v0.21.4，三级链，第二个 fos-ai 令牌作为回退 1，所有密钥通过 `${VAR}`/`key_env` 引用）：见 `fosqa-vm-llm-chain.zh-CN.md`。注意 v0.21.4 中与主模型同一 base URL 的条目只有在模型也相同时才会被跳过，与 3.2 节针对 v0.16 的说明不同。 |
