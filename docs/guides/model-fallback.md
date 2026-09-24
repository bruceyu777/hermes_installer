---
title: Hermes Agent: automatic fallback from the fos-ai gateway to local Qwen
kind: guide
created: 2026-09-21
updated: 2026-09-21
status: current
verified_against: Hermes Agent v0.16.0 (2026.6.5, upstream c6e99ab3), Ubuntu (kernel 7.0.0-30), live one-shot runs on 2026-09-21
summary: Use Hermes' built-in fallback_providers chain so that when the fos-ai gateway fails (HTTP 429 outside budget hours, 5xx, 401/403/404) the turn continues on the internal Qwen models, with the key kept in ~/.hermes/.env.
related:
  - ../INDEX.md
  - model-fallback.zh-CN.md
  - install-hermes.md
  - vscode-acp.md
  - ~/git/opencode/docs/guides/model-fallback.md
  - ~/.hermes/config.yaml
---

# Hermes Agent: automatic fallback from the fos-ai gateway to local Qwen

The primary model comes from the Fortinet fos-ai gateway, which only has budget during
fixed time blocks and answers HTTP 429 the rest of the day (the opencode guide documents
the same gateway). Unlike opencode, Hermes has a **built-in** fallback chain, so no plugin
is needed: a top-level `fallback_providers` list in `config.yaml`, managed by
`hermes fallback`.

State on this workstation after this guide was applied (2026-09-21):

| Item | State |
|---|---|
| Primary | `glm-5.3` on `https://fos-ai.fortinet.com:443/v1` (`provider: custom`, key inline in `config.yaml`) |
| Fallback 1 | `qwen3.5-122b-a10b-awq` on `https://releaseqa-aiserver.corp.fortinet.com/v1` |
| Fallback 2 | `qwen3.6-35b-a3b` on the same gateway (smaller, used if the 122B model fails too) |
| Qwen key | `LOCAL_QWEN_API_KEY` in `~/.hermes/.env` (mode 600), referenced by `key_env` |
| Backups | `~/.hermes/config.yaml.bak-20260921-162640`, `~/.hermes/.env.bak-20260921-162640` |
| Verified | a run whose primary returned HTTP 404 was answered by Qwen in 4.6 s; a normal run stayed on fos-ai |

## 1. Detect the gateways

Both gateways are OpenAI-compatible. Keys are read from files; nothing secret is typed on
the command line. (The Hermes fos-ai key differs from the opencode one and has a
different access level: on 2026-09-21 16:25 PDT the opencode key got 429 on chat while the
Hermes key got 200.)

```bash
HK=$(python3 -c "import yaml;print(yaml.safe_load(open('$HOME/.hermes/config.yaml'))['model']['api_key'])")
QK=$(grep '^LOCAL_QWEN_API_KEY=' ~/.hermes/.env | cut -d= -f2-)

curl -s -H "Authorization: Bearer $HK" https://fos-ai.fortinet.com/v1/models | python3 -m json.tool
curl -s -H "Authorization: Bearer $QK" https://releaseqa-aiserver.corp.fortinet.com/v1/models | python3 -m json.tool
```

Result on 2026-09-21 16:25 PDT:

| Gateway | Base URL | Models |
|---|---|---|
| fos-ai | `https://fos-ai.fortinet.com/v1` | `deepseek-v4.1-flash`, `glm-5.3`, `glm-5.3-flash` |
| local-qwen | `https://releaseqa-aiserver.corp.fortinet.com/v1` | `qwen3.5-122b-a10b-awq`, `qwen3.6-35b-a3b`, `qwen3-vl-235b`, `qwen3-vl-embedding-8b` |

The failure the chain has to handle, as returned by fos-ai outside budget hours:

```json
{"error":"Your access level has no budget during this time block. It reopens at the start of the next budget block."}
```

## 2. How Hermes fallback works

From the upstream doc (`website/docs/user-guide/features/fallback-providers.md` in the
install):

- Triggers: **429** and **5xx** after the retry budget (`agent.api_max_retries: 3` here),
  **401/403** and **404** immediately, repeated malformed/empty responses.
- On trigger Hermes resolves the fallback credentials, builds a new client, swaps model,
  provider and client in place, resets the retry counter and continues the same turn.
  Conversation history and tool calls are preserved.
- **Turn-scoped**: every new user message starts on the primary again. Within one turn the
  chain advances at most through its entries once; if all fail, normal error handling
  takes over.
- The chain is inherited by subagents (`delegate_task`), cron jobs, and auxiliary tasks on
  `provider: auto` (compression, titles, vision, …).
- There is no environment variable for the chain by design; it lives only in `config.yaml`.
- Works in CLI, TUI, messaging gateway and ACP (VS Code) alike, because they share the same
  agent core.

## 3. Configure

### 3.1 Put the Qwen key in `.env`

Hermes loads `~/.hermes/.env` at start. The fallback entry references the variable name via
`key_env`, so the key never appears in `config.yaml`.

```bash
cd ~/.hermes && cp -p .env .env.bak-$(date +%Y%m%d-%H%M%S)
printf '\n# Internal Qwen gateway (releaseqa-aiserver) - used by fallback_providers in config.yaml\nLOCAL_QWEN_API_KEY=%s\n' "$(tr -d '[:space:]' < ~/.config/opencode/local-qwen.key)" >> .env
chmod 600 .env
```

(The key was copied from opencode's `~/.config/opencode/local-qwen.key`; both tools use
the same gateway account.)

### 3.2 Add the chain to `config.yaml`

Either interactively (`hermes fallback add` opens the same picker as `hermes model` and
appends to the chain) or by editing the top-level key directly, which is what was done here:

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

Field reference for a `custom` entry:

| Field | Required | Meaning |
|---|---|---|
| `provider` | yes | `custom` for any OpenAI-compatible endpoint; built-in names (`openrouter`, `anthropic`, …) also work |
| `model` | yes | model id as the gateway lists it |
| `base_url` | for `custom` | endpoint incl. `/v1` |
| `key_env` | one of | name of the env var holding the key (alias `api_key_env`) |
| `api_key` | one of | inline key; avoided here to keep secrets out of the YAML |

Entries missing `provider` or `model` are ignored silently. An entry whose `base_url`
matches the current backend is skipped with a `Fallback skip` warning (no point retrying
the same gateway).

### 3.3 Restart running agents

CLI runs read the config at start. The VS Code ACP adapter is a long-lived process: run
`ACP: Restart Agent` or reload the window after editing the config.

## 4. Verify

### 4.1 Chain is loaded

```bash
hermes fallback list
```

Observed 2026-09-21:

```
  Primary:   glm-5.3  (via custom)

  Fallback chain (2 entries):
    1. qwen3.5-122b-a10b-awq  (via custom)  [https://releaseqa-aiserver.corp.fortinet.com/v1]
    2. qwen3.6-35b-a3b  (via custom)  [https://releaseqa-aiserver.corp.fortinet.com/v1]
```

### 4.2 Live failover

fos-ai was inside its budget window for the Hermes key at verification time, so a real
429 could not be produced. A nonexistent model name makes the gateway return HTTP 404
(`{"error":"unknown model: ..."}`), which takes the same immediate-fallback path. Both
runs use one-shot mode from an empty directory; which backend answered is read from the
session table in `~/.hermes/state.db`.

```bash
cd "$(mktemp -d)"
/usr/bin/time -f "%es" hermes -z "Reply with exactly: PONG-A"                          # A: primary
/usr/bin/time -f "%es" hermes -m no-such-model-404 -z "Reply with exactly: PONG-B"     # B: primary 404 -> fallback

python3 - <<'PY'
import sqlite3
c=sqlite3.connect('/home/yzhengfeng/.hermes/state.db')
for r in c.execute("select id, model, billing_base_url, output_tokens from sessions order by started_at desc limit 2"): print(r)
PY
```

Observed 2026-09-21 16:27 PDT:

```
A: PONG-A   3.76s
B: PONG-B   4.60s
('20260921_162705_44d037', 'no-such-model-404', 'https://releaseqa-aiserver.corp.fortinet.com/v1/', 4)   <- answered by Qwen
('20260921_162700_906844', 'glm-5.3',           'https://fos-ai.fortinet.com:443/v1',               40)  <- answered by fos-ai
```

`agent.log` at level INFO does not print a line for the switch; the `billing_base_url`
column is the reliable evidence. For a 429 the same swap happens after the three retries
(a few seconds of backoff), so expect the fallback answer to take longer than test B.

## 5. Use

1. Prompt as usual (terminal, TUI or VS Code). While fos-ai answers, nothing changes.
2. When fos-ai fails with a listed error, the current turn finishes on
   `qwen3.5-122b-a10b-awq`; if that fails too, on `qwen3.6-35b-a3b`. The conversation is
   not interrupted and no message is lost.
3. The next prompt tries fos-ai again. There is no cooldown timer (unlike the opencode
   plugin), so during a closed budget block every prompt costs one failed fos-ai attempt
   plus retries before Qwen answers.

Manual controls:

| Want | Do |
|---|---|
| Stay on Qwen for a whole session (budget block known to be closed) | `hermes -m qwen3.5-122b-a10b-awq --provider custom` is not enough because `--provider custom` still uses the primary `base_url`; instead run `hermes model` and pick the Qwen endpoint, or in VS Code use `ACP: Set Agent Model` |
| Reorder or extend the chain | `hermes fallback add` / `hermes fallback remove`, or edit the YAML |
| Turn the fallback off | `hermes fallback clear` (writes `fallback_providers: []`) |
| See which backend answered | query `billing_base_url` in `~/.hermes/state.db` (section 4.2) |
| Route side tasks (compression, titles) elsewhere | `auxiliary.<task>.provider/model/base_url` in `config.yaml`; on `auto` they inherit this chain |

Where the pieces live:

| Piece | Path |
|---|---|
| Chain | `fallback_providers:` in `~/.hermes/config.yaml` |
| Qwen key | `LOCAL_QWEN_API_KEY` in `~/.hermes/.env` |
| Fallback logic | `~/.hermes/hermes-agent/agent/chat_completion_helpers.py` (mid-turn), `agent/agent_init.py` (init-time, when the primary has no credentials at all) |
| Session/billing records | `~/.hermes/state.db`, table `sessions` |
| Logs | `~/.hermes/logs/agent.log`, `errors.log` (`hermes logs`) |
| Upstream doc | `~/.hermes/hermes-agent/website/docs/user-guide/features/fallback-providers.md` |

## 6. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `hermes fallback list` says "No fallback providers configured" after editing | YAML indentation wrong, or entry missing `provider`/`model` | compare with section 3.2; `python3 -c "import yaml;print(yaml.safe_load(open('$HOME/.hermes/config.yaml'))['fallback_providers'])"` |
| `Fallback to custom failed: provider not configured` in `errors.log` | `LOCAL_QWEN_API_KEY` missing or empty in `.env`, or `.env` not loaded in that process | `grep -c '^LOCAL_QWEN_API_KEY=' ~/.hermes/.env`; restart the agent |
| `Fallback skip: chain entry base_url ... matches current backend` | the entry points at the same gateway as the primary | only useful for a different gateway; remove or fix the entry |
| Fallback fires but the answer takes 10–20 s on a 429 | Hermes retries the primary `api_max_retries` times with backoff before switching | lower `agent.api_max_retries` in `config.yaml` if the wait is unacceptable |
| Qwen also fails | no third level configured | check the gateway with the curl in section 1; add another entry |
| VS Code still shows the old behaviour | the ACP adapter started before the config edit | `ACP: Restart Agent` |

Known limits:

- No cooldown: the primary is retried on every new turn. Fine for a gateway that is
  closed for hours only if the retry overhead is acceptable; otherwise switch the primary
  manually for that period.
- The 122B and 35B Qwen models were smoke-tested for chat and, in the opencode guide, for
  tool calling. Long agentic sessions on them were not exercised.
- Vision (`qwen3-vl-235b`) is not in the chain; configure `auxiliary.vision` if needed.

## 7. Security notes

- `.env` and `config.yaml` are mode 600 under `~/.hermes/`. The Qwen key is referenced by
  name (`key_env`), so `hermes config show` does not reveal it; the fos-ai key is inline
  and will show. Mask `sk-...` before pasting output anywhere.
- The fallback sends the conversation to a second gateway. Both are internal Fortinet
  endpoints with TLS; no third party is involved.
- The fallback code path was read in `agent_init.py` and `chat_completion_helpers.py`
  before enabling it; inline `api_key` and `key_env` are both honoured.

## 8. Rollback

```bash
cd ~/.hermes
cp -p config.yaml.bak-20260921-162640 config.yaml
cp -p .env.bak-20260921-162640 .env
# or just: hermes fallback clear
```

Then restart any running agent (`ACP: Restart Agent` in VS Code).

## History

| Date | Change |
|---|---|
| 2026-09-21 | Created. Probed both gateways, added `LOCAL_QWEN_API_KEY` to `.env`, wrote the two-entry `fallback_providers` chain, verified primary and fallback paths via `state.db` billing records. |
| 2026-09-22 | The fosqa VM uses a different setup (v0.21.4, three-tier chain with a second fos-ai token as fallback 1, all keys via `${VAR}`/`key_env`): see `fosqa-vm-llm-chain.md`. Note that in v0.21.4 an entry on the same base URL as the primary is only skipped when the model also matches, unlike the v0.16 note in section 3.2. |
