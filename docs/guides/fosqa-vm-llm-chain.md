---
title: Hermes Agent on the fosqa VM: update to v0.21.4 and the three-tier LLM chain (fos-ai personal → fos-ai project → local Qwen)
kind: guide
created: 2026-09-22
updated: 2026-09-23
status: current
verified_against: Hermes Agent v0.21.4 (upstream c0d7294769, 2026-09-23), Ubuntu 24.04 LTS (kernel 6.8.0-60), Python 3.11 venv under ~/.hermes/hermes-agent/venv, live one-shot runs on 2026-09-22
summary: How the pre-existing May install on the fosqa VM was brought to v0.21.4 and configured so glm-5.3 on the personal fos-ai token is primary, deepseek-v4.1-flash on the Log Intelligence project token is fallback 1, and the internal Qwen gateway is fallback 2/3, with every key kept in ~/.hermes/.env and each leg verified.
related:
  - ../INDEX.md
  - fosqa-vm-llm-chain.zh-CN.md
  - install-hermes.md
  - model-fallback.md
  - vscode-acp.md
  - /home/fosqa/ai_practice/log-intelligence/.env
  - ~/.hermes/config.yaml
---

# Hermes Agent on the fosqa VM: update to v0.21.4 and the three-tier LLM chain

> **2026-09-23:** sections 1–3 record the first setup. The current state (MCP servers, the
> ask-first plugin, the measured model chain, renamed keys, and the shareable installer this
> folder became) is in [section 7](#7-2026-09-23-mcp-servers-ask-first-measured-model-chain-installer).

The two older guides (`install-hermes.md`, `model-fallback.md`) record a different
workstation. This VM (`fosqa@`, kernel 6.8.0-60) already had a per-user git install from
2026-05-25 (v0.14.0, pointing at the retired `fos-exp-ai` gateway). This guide records how
it was updated and re-pointed at the same gateways the Log Intelligence project uses, in the
order the project owner asked for: personal token first, project token second, local Qwen
last.

State on this VM after this guide was applied (2026-09-22):

| Item | State |
|---|---|
| Version | `Hermes Agent v0.21.4`, upstream `c0d7294769` (tag `v2026.9.21` + 488) |
| Install method | per-user git install, code at `~/.hermes/hermes-agent/`, venv `~/.hermes/hermes-agent/venv/` (Python 3.11.15), launcher `~/.local/bin/hermes` |
| Primary | `glm-5.3` on `https://fos-ai.fortinet.com/v1`, key `FOS_AI_API_KEY` (= `GLM_API_KEY` in the log-intel `.env`, personal token) |
| Fallback 1 | `deepseek-v4.1-flash` on the same fos-ai gateway, key `FOS_AI_LOG_INTEL_API_KEY` (= `GLM_API_KEY_LOG_INTEL`, Log Intelligence project token) |
| Fallback 2 | `qwen3.6-35b-a3b` on `https://releaseqa-aiserver.corp.fortinet.com/v1`, key `LOCAL_LLM_API_KEY` (= `LOCAL_LLM_API_KEY`, same value) |
| Fallback 3 | `qwen3.5-122b-a10b-awq` on the same internal gateway, same key |
| Secrets | all three keys only in `~/.hermes/.env` (mode 600); `config.yaml` references them by name (`${FOS_AI_API_KEY}`, `key_env:`) |
| Backups | `~/.hermes/config.yaml.bak-20260922-215107`, `~/.hermes/.env.bak-20260922-215107` |
| Verified | primary answered in 5.9 s; a 404 on the primary was answered by deepseek-v4.1-flash; with both fos-ai keys invalid the turn was answered by qwen3.6-35b-a3b |
| ACP | `hermes acp --check` → `Hermes ACP check OK` |

## 1. Install (update of the existing git checkout)

The May checkout had its `origin` on `git@github.com:` and SSH to GitHub hangs on this VM,
so the remote was switched to HTTPS before pulling. The one local modification
(`ui-tui/package-lock.json`) was discarded.

```bash
cd ~/.hermes && cp -p config.yaml config.yaml.bak-$(date +%Y%m%d-%H%M%S) && cp -p .env .env.bak-$(date +%Y%m%d-%H%M%S)

cd ~/.hermes/hermes-agent
git remote set-url origin https://github.com/NousResearch/hermes-agent.git
git checkout -- ui-tui/package-lock.json
git pull --ff-only origin main                     # f4953bc64 (0.14.0) -> c0d7294769 (0.21.4)
uv pip install --python venv/bin/python -e ".[all]"
hermes --version                                   # Hermes Agent v0.21.4 ... Up to date
hermes doctor --fix                                # ran the config migration; left one manual item, handled in section 2
```

`hermes doctor` still lists optional tools without keys (image_gen, x_search, spotify, …)
and "No GITHUB_TOKEN"; none of those affect chat or ACP use.

## 2. Configure

### 2.1 Which endpoints and keys, and why these names

All three gateways were probed with the values from
`/home/fosqa/ai_practice/log-intelligence/.env` on 2026-09-22 21:50 PDT:

| log-intel `.env` variable | Gateway | Models listed | Name used in `~/.hermes/.env` |
|---|---|---|---|
| `GLM_API_KEY` (personal) | `https://fos-ai.fortinet.com/v1` | `deepseek-v4.1-flash`, `glm-5.3`, `glm-5.3-flash` | `FOS_AI_API_KEY` |
| `GLM_API_KEY_LOG_INTEL` (project) | same | same list | `FOS_AI_LOG_INTEL_API_KEY` |
| `LOCAL_LLM_API_KEY` | `https://releaseqa-aiserver.corp.fortinet.com/v1` (= `172.16.96.52`) | `qwen3.6-35b-a3b`, `qwen3.5-122b-a10b-awq`, `qwen3-vl-235b`, `qwen3-vl-embedding-8b` | `LOCAL_LLM_API_KEY` |

Two deliberate differences from the log-intel `.env`:

- The name `GLM_API_KEY` is **not** reused. Hermes' built-in `zai` provider plugin reads
  `GLM_API_KEY` as a z.ai credential (`plugins/model-providers/zai/__init__.py`), so a
  variable of that name in `~/.hermes/.env` would make Hermes believe z.ai is configured.
- The local gateway is addressed by hostname, not by the IP the log-intel `.env` uses. The
  hostname carries a valid certificate (curl returns 200 without `-k`), the bare IP does not,
  and Hermes has no per-provider "verify SSL off" switch.

### 2.2 Keys into `~/.hermes/.env`

The values were copied by a short Python snippet that reads the log-intel `.env`, so no
secret was typed or echoed. Resulting block (values omitted):

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

`chmod 600 ~/.hermes/.env` afterwards. Note that Hermes loads this file **over** the process
environment: exporting `FOS_AI_API_KEY=...` in the shell does not override it (this matters
for the test in section 3.3).

### 2.3 `~/.hermes/config.yaml`

The `model:` block was rewritten and the legacy `custom_providers:` entry for the retired
`fos-exp-ai` gateway deleted (the item `hermes doctor` could not fix by itself). Hermes
expands `${VAR}` references anywhere in the config at load time
(`hermes_cli/config.py::_expand_env_vars`), so the primary key can also stay out of the YAML.

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

Why fallback 1 on the *same* base URL as the primary is not skipped: in v0.21.4 the
mid-turn skip predicate is `agent/backend_identity.py::same_deployment`, which for two
`custom` entries only treats them as duplicates when base URL **and model** match. The old
guide's "same base_url is skipped" note described the v0.16 code. The 401/403
credential-scope predicate (`same_credential_surface`) is only used by auxiliary-task
resolution, not by the main-turn chain.

Everything else in `config.yaml` (personality `kawaii`, toolsets, timeouts) was left as the
May install wrote it.

## 3. Verify

### 3.1 Chain loaded

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

### 3.2 Primary and fallback 1 (404 path)

```bash
cd "$(mktemp -d)"
/usr/bin/time -f "%es" hermes -z "Reply with exactly: PONG-A"                          # primary
/usr/bin/time -f "%es" hermes -m no-such-model-404 -z "Reply with exactly: PONG-B"     # primary 404 -> chain
python3 - <<'PY'
import sqlite3
c=sqlite3.connect('/home/fosqa/.hermes/state.db')
for r in c.execute("select id, model, billing_base_url, output_tokens from sessions order by started_at desc limit 2"): print(r)
PY
```

Observed 2026-09-22 21:55 PDT:

```
PONG-A   5.88s
PONG-B   4.95s
('20260922_215520_831aad', 'deepseek-v4.1-flash', 'https://fos-ai.fortinet.com/v1/', 5)   <- fallback 1 answered
('20260922_215503_82fef2', 'glm-5.3',             'https://fos-ai.fortinet.com/v1',  5)   <- primary answered
```

In v0.21.4 the `model` column of the session row is the model that actually answered, not
the one requested (the v0.16 guide showed the requested name there).

### 3.3 Fallback 2 (both fos-ai keys rejected)

Because `~/.hermes/.env` overrides the shell environment, the test runs from a throwaway
`HERMES_HOME` whose `.env` has both fos-ai keys replaced with junk. The real config is not
touched.

```bash
T=$(mktemp -d) && cp -p ~/.hermes/config.yaml "$T/" \
  && sed -E 's/^(FOS_AI_API_KEY|FOS_AI_LOG_INTEL_API_KEY)=.*/\1=sk-invalid/' ~/.hermes/.env > "$T/.env" && chmod 600 "$T/.env"
cd "$(mktemp -d)" && HERMES_HOME="$T" /usr/bin/time -f "%es" hermes -z "Reply with exactly: PONG-D"
python3 -c "import sqlite3;print(list(sqlite3.connect('$T/state.db').execute('select model, billing_base_url from sessions order by started_at desc limit 1')))"
rm -rf "$T"      # the copy of .env holds the real Qwen key
```

Observed 2026-09-22 21:56 PDT:

```
PONG-D   6.68s
[('qwen3.6-35b-a3b', 'https://releaseqa-aiserver.corp.fortinet.com/v1/')]
```

Fallback 3 (the 122B model) shares gateway and key with fallback 2 and was not exercised
separately.

### 3.4 ACP

```bash
hermes acp --check      # Hermes ACP check OK
```

The VS Code side is unchanged from `vscode-acp.md`, with `/home/fosqa/.local/bin/hermes`
as the command.

## 4. Use

Same as `install-hermes.md` section 4. Turn-scoped behaviour: every new prompt starts on
`glm-5.3` again; within one turn the chain is walked at most once. To see which backend
answered a turn, read `billing_base_url` (and `model`) from the `sessions` table as in 3.2.

To change the order or add a leg, edit `fallback_providers:` in `~/.hermes/config.yaml` (or
`hermes fallback add/remove`) and restart any long-lived agent (`ACP: Restart Agent` in
VS Code). To rotate a key, change only `~/.hermes/.env`.

## 5. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `git pull` / `git fetch` hangs | remote is `git@github.com:` and SSH to GitHub is blocked on this VM | `git remote set-url origin https://github.com/NousResearch/hermes-agent.git` |
| `hermes doctor` says "Move custom_providers entry ... into providers:" | leftover `custom_providers:` list from the May config (v12 migration does not re-run) | delete the list from `config.yaml` (done here) or convert to `providers.<name>.api` |
| `hermes model` / `/model` picker shows a z.ai provider you never set up | a `GLM_API_KEY` variable in `~/.hermes/.env` | rename it (this guide uses `FOS_AI_API_KEY`) |
| Local Qwen leg fails with a certificate error | base URL given as `https://172.16.96.52/v1` | use `https://releaseqa-aiserver.corp.fortinet.com/v1` (same host, valid cert) |
| Env override for a test has no effect | `~/.hermes/.env` is loaded over the process environment | test from a copied `HERMES_HOME` (section 3.3) |
| fos-ai 403 "access level does not permit model glm-5.3" | the personal key's allowlist changed (it happened to glm-5.2 on 2026-08-29) | the chain moves the turn to deepseek-v4.1-flash; then update `model.default` to a model in `curl -H "Authorization: Bearer $FOS_AI_API_KEY" https://fos-ai.fortinet.com/v1/models` |
| `errors.log` warns `platform 'teams' has no valid toolsets configured (hermes-teams)` | stale platform toolset names in the May config | harmless for CLI/ACP; `hermes tools` to reconfigure if the gateway is ever used |

## 6. Security notes

- `~/.hermes/config.yaml` and `~/.hermes/.env` are mode 600 and contain no other credentials
  on this VM (the May `.env` held only non-secret timeouts and debug flags).
- No key appears in `config.yaml`: the primary is `${FOS_AI_API_KEY}`, the fallbacks use
  `key_env`. `hermes config show` therefore prints names, not values.
- The fallback sends the conversation to the internal Qwen gateway; both gateways are
  Fortinet-internal TLS endpoints.
- The test copy of `.env` in section 3.3 must be deleted after the run.

## 7. 2026-09-23: MCP servers, ask-first, measured model chain, installer

This folder is now the `hermes_installer` repo (`git@github.com:bruceyu777/hermes_installer.git`):
`install.sh` + `tokens.env.example` + `config/`. This VM was re-deployed from it
(`./install.sh --no-install`, then `--cron`). See [README.md](../../README.md) for the user view.

### 7.1 Keys renamed to the installer's names

`~/.hermes/.env`: `FOS_AI_LOG_INTEL_API_KEY` → `FOS_AI_FALLBACK_API_KEY`,
`LOCAL_LLM_API_KEY` → `LOCAL_QWEN_API_KEY` (renamed in place, values unchanged, backup
`.env.bak-20260923-114739`). Added `MANTIS_MCP_TOKEN`, `JENKINS_MCP_BASIC` (base64 of
`user:apitoken`, ready for a Basic header) and `LOGINTEL_MCP_KEY`, copied from the opencode key files.

### 7.2 MCP servers (same set and curation as opencode)

| Server | Transport / auth | Tools the model sees |
|---|---|---|
| `logintel` | streamable HTTP, `Bearer ${LOGINTEL_MCP_KEY}` | all 26 (read-only) |
| `mantis-tools` | `transport: sse`, `Bearer ${MANTIS_MCP_TOKEN}` | 8 of 95: `tools.include` |
| `jenkins` | streamable HTTP, `Basic ${JENKINS_MCP_BASIC}` | 10 of 19: `tools.include` |
| `microsoft365` | `auth: oauth` | disabled until `hermes mcp login microsoft365` has stored a token (untested) |

Findings that shaped the config:

- Jenkins and Mantis advertise MCP resources and prompts, so Hermes adds four helper tools
  each (`list_resources`, `read_resource`, `list_prompts`, `get_prompt`). `tools.resources: false`
  and `tools.prompts: false` remove them; the model then sees exactly 44 MCP tools, the same as
  opencode. Checked with `tools.mcp_tool_discovery.discover_mcp_tools()` from a throwaway home.
- Hermes' only built-in MCP approval is per server (`trust: untrusted` asks before every tool
  without `readOnlyHint: true`), and none of the three servers annotates any tool, so it would
  ask before every search. Per-tool asking is done by the plugin below instead.
- ACP (VS Code) reads `mcp_servers` from `config.yaml` too (`acp_adapter/entry.py` starts the
  discovery in the background), so one config serves the terminal and VS Code.

### 7.3 Plugin `mcp-ask-first`

`~/.hermes/plugins/mcp-ask-first/` (enabled in `plugins.enabled`) registers a `pre_tool_call`
hook. For `create_mantis`, `mantis_add_note`, `send_email`, `triggerBuild` and `rebuildBuild`
it returns `{"action": "approve"}`, which sends the call to Hermes' human-approval gate
(`tools/approval.py::request_tool_approval`), the same gate dangerous shell commands use.

`hermes -z` sets `HERMES_YOLO_MODE=1` (`hermes_cli/oneshot.py`), which approves everything,
so a first version let a gated tool run in `-z`. The plugin therefore returns `block` when
Hermes marks the run as having nobody to ask (`HERMES_SINGLE_QUERY_SESSION`, `-z` and `chat -q`;
`HERMES_CRON_SESSION`).

Verified 2026-09-23 with a copy of the plugin that also gated the harmless
`logintel_list_projects`:

| Path | Result |
|---|---|
| `hermes -z` | blocked with the plugin's message |
| `hermes chat -q` | blocked, also through Hermes' `tool_call` tool-search wrapper |
| ACP, scripted client answering `deny` | one `session/request_permission` ("TEST GATE list projects: {}: <mcp__logintel__logintel_list_projects> (plugin approval rule)", options allow_once / allow_session / allow_always / deny / deny_always); tool not run, model did not retry |
| ACP, answering `allow_once` | tool ran, 13 projects returned |

Limit: curation is not a sandbox. The tokens are in `~/.hermes/.env` and in the environment of
the agent's shell (Hermes only scrubs names it registers itself), so the model could call a
server directly. An opencode session on this VM did that on 2026-09-23 with the Mantis key file.

### 7.4 Measured model chain (lessons from the log-intelligence LLM adapter)

On 2026-09-23 the gateway changed the allowlists within hours: at 11:47 `glm-5.3` answered on
the personal key; by 21:00 the personal key offered only `glm-5.3-flash`, `deepseek-v4.1-flash`
was refused to both keys ("your access level does not permit model", although `/v1/models`
still listed it at first), and a turn fell all the way to local Qwen. A standalone Hermes
`-z` against a 403 model hung until the 180 s timeout.

So the chain is measured, following the log-intelligence adapter
(`docs/implement_log/2026-09-24_llm_automatic_adapter_plan.md` there):

- `config/model-preferences.yaml`: per key a `prefer` list and how many to `take`.
- `install.sh` and `install.sh --adapt` send each preferred model a one-token chat call; only
  a model that answers is used (`/v1/models` shows what a key may see, not call). A 429 with
  "budget" counts as refused, a plain 429 as usable. Ids in no `prefer` list are only reported
  as "new models seen". A key whose gateway does not answer keeps its current links. Two keys
  never get the same model on the same gateway (Hermes would skip the second as a duplicate).
- `install.sh --cron` adds `17 * * * * … install.sh --adapt` (log
  `~/.hermes/logs/hermes_installer_adapt.log`). Tested under `env -i` like cron.

Result on this VM, 2026-09-23 21:21: `glm-5.3-flash` (personal) → `glm-5.3` (project key) →
`qwen3.6-35b-a3b` → `qwen3.5-122b-a10b-awq`; `qwen3-vl-235b` reported as new, not used.
`install.sh --check`: all four "gateway ok, Hermes PONG"; logintel, mantis-tools, jenkins connected.

### 7.5 Installer tests

| Scenario | Result |
|---|---|
| Fresh install in a sandbox `HOME` (`env -i`, clean `PATH`) | official installer pinned to `c0d7294769`, v0.21.4, all checks passed, 437 s |
| Only the Qwen key and the Log Intelligence key, empty `HERMES_HOME` | chain starts at `qwen3.6-35b-a3b`; mantis-tools and jenkins disabled; logintel connected |
| This VM (existing 545-line `config.yaml`) | only the managed keys changed, comments and every other key kept; re-run prints "unchanged" |
| Unreachable local gateway (dry run) | Qwen links kept from the current config |

## History

| Date | Change |
|---|---|
| 2026-09-22 | Created. Updated the May v0.14.0 checkout to v0.21.4 over HTTPS, probed the three gateways with the log-intel `.env` keys, moved the keys into `~/.hermes/.env` under non-colliding names, wrote the primary + three-entry `fallback_providers` chain with `${VAR}`/`key_env` references, removed the retired `fos-exp-ai` entry, verified primary, the 404 → deepseek path and the invalid-keys → Qwen path via `state.db`, and `hermes acp --check`. |
| 2026-09-23 | Section 7: MCP servers (logintel, mantis-tools, jenkins; microsoft365 off) with the opencode curation plus `resources/prompts: false`; plugin `mcp-ask-first` (approve in CLI/ACP, block in -z/-q/cron), verified via `-z`, `chat -q` and a scripted ACP client; keys renamed to the installer names; model chain measured per key from `model-preferences.yaml` (lessons of the log-intel LLM adapter) after the gateway refused glm-5.3 and deepseek to the personal key; hourly `--adapt` cron; folder turned into the `hermes_installer` repo, tested fresh / partial tokens / this VM. |
