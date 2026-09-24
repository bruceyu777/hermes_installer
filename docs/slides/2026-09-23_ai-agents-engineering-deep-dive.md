---
marp: true
theme: default
paginate: true
size: 16:9
title: AI agents on QA VMs — engineering deep dive
style: |
  section { font-size: 22px; }
  pre, code { font-size: 15px; }
  table { font-size: 17px; }
---

# AI agents on QA VMs: engineering deep dive

**opencode 1.18.32** and **Hermes Agent v0.21.4**, shipped as two installer repos

1. Architecture: gateways, fallback, MCP, guard rails
2. Implementation highlights, with the code that matters
3. The measured model chain (ported from the Log Intelligence adapter)
4. Installation guide
5. Live demos
6. Testing: what we ran and how to re-run it
7. Lessons, limits, next steps

FortiOS QA · 2026-09-23

---

## The problem, in engineering terms

| Need | Why it's hard |
|---|---|
| Use internal LLMs only | 2 fos-ai keys with different allowlists and budgets, plus a local vLLM Qwen server |
| Survive model outages | gateway returns 403 "not permitted", 429 "no budget", 5xx, **without notice** |
| Reach our systems | Jenkins, Mantis and Log Intelligence MCP servers: 3 auth schemes (Bearer, SSE Bearer, Basic) |
| Stay safe | Mantis exposes **95** tools, including email, Exchange and vault; Jenkins can trigger builds |
| Same setup for everyone | per-person tokens, but identical config and behaviour |
| Terminal **and** VS Code | Remote-SSH: extensions run on the VM, not on the laptop |

Design goal: **one repo + one `tokens.env` + one script**, idempotent and re-runnable after `git pull`.

---

## Architecture

```
                         ┌──────────── tokens.env (per person, git-ignored) ────────────┐
                         ▼                                                              ▼
 ┌──────────────┐   install.sh ──► ~/.config/opencode/*.key          ~/.hermes/.env (mode 600)
 │ opencode     │                  opencode.json  chain-fallback.json  config.yaml (managed keys only)
 │ Hermes (ACP) │                  plugin/chain-fallback.js            plugins/mcp-ask-first/
 └──────┬───────┘
        │ model chain (measured hourly by `install.sh --adapt`, cron :47 / :17)
        ├──► fos-ai gateway   https://fos-ai.fortinet.com/v1          personal key → glm-5.3-flash
        ├──► fos-ai gateway   (second key: project token)             → glm-5.3
        └──► releaseqa-aiserver (vLLM)  qwen3.6-35b-a3b → qwen3.5-122b-a10b-awq
        │ MCP (curated to 44 tools, write tools ask first)
        ├──► logintel      https://releaseqa-logintelligence…/mcp     streamable HTTP, Bearer limcp_…
        ├──► mantis-tools  https://releaseqa-portal…/sse               SSE, Bearer (user-bound token)
        └──► jenkins       https://releaseqa-stackjenkins…/mcp-server/mcp  HTTP, Basic base64(user:token)
```

---

## Repo layout (both repos follow the same shape)

```
opencode_installer/                     hermes_installer/
├── install.sh                          ├── install.sh
├── tokens.env.example                  ├── tokens.env.example        # same 6 names: one file works for both
├── config/                             ├── config/
│   ├── opencode.json                   │   ├── config.yaml           # only the keys we manage
│   ├── model-preferences.json          │   ├── model-preferences.yaml
│   └── plugin/chain-fallback.js        │   └── plugins/mcp-ask-first/{plugin.yaml,__init__.py}
├── scripts/adapt_models.py             ├── scripts/merge_into_hermes_home.py
└── docs/ (guides, en + zh-CN)          ├── tests/{acp_permission_test.py,list_registered_mcp_tools.py}
                                        └── docs/ (guides, slides)
```

Rules the installers follow:
- **No secret in any config file**: opencode uses `{file:~/.config/opencode/x.key}`, Hermes uses `${VAR}` / `key_env`
- **Idempotent**: files are rewritten only when the content changes, after a `.bak-<time>` copy
- **Nothing secret printed**: probes send headers from a mode-600 temp file (`curl -H @file`)

---

# Part 1 · Implementation highlights: opencode

---

## opencode: providers and chain

Two providers point at **the same gateway** and differ only by key file, so the fallback plugin can tell "personal key failed" from "project key failed":

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
"model": "fos-ai/glm-5.3-flash",           // written by adapt_models.py
"small_model": "local-qwen/qwen3.6-35b-a3b", // titles and summaries stay local
"autoupdate": false                        // pinned: install.sh owns the version
```

`models` lists **only models that answered the last probe**, so the picker never offers a dead model.

---

## opencode: `chain-fallback.js` (local plugin)

Vendored from `@renjfk/opencode-model-fallback` 0.2.1 (MIT) and changed in two ways:

| Upstream | Ours |
|---|---|
| one level: primary → fallback, then "Exhausted" | walks the **whole chain** |
| while the primary is parked, routes to its direct fallback, **even if that is parked too** | `firstAvailableFrom()` skips every parked model before any network call |
| mappings hard-coded in options | chain read from `chain-fallback.json`, **re-read on mtime change** |
| — | a model of our providers that the chain dropped is routed to the chain head |

```js
function firstAvailableFrom(start) {        // parked = failed within cooldown_seconds (1800)
  let current = start; const seen = new Set();
  while (current && !seen.has(current) && store.getModelCooldown(current) && nextOf(current)) {
    seen.add(current); current = nextOf(current);
  }
  return current;
}
```

Triggers: HTTP `401 403 404 408 429 5xx 529`, or error text matching `no budget|does not permit|unknown model|…`. On failure it aborts the turn, parks the model (`~/.local/share/opencode/chain-fallback-router.json`), and **replays the last user message** on the next model. A toast shows `A -> B (reason)`.

---

## opencode: hot reload and stale-model routing

```js
let chainCache = { mtimeMs: -1, mappings: options.mappings, head: undefined };
function currentChain() {
  const mtimeMs = statSync(options.chain_file).mtimeMs;       // cheap: one stat per turn
  if (mtimeMs !== chainCache.mtimeMs) {
    const chain = JSON.parse(readFileSync(options.chain_file, "utf8")).chain ?? [];
    const mappings = {};
    for (let i = 0; i + 1 < chain.length; i++) mappings[chain[i]] = chain[i + 1];
    chainCache = chain.length ? { mtimeMs, mappings, head: chain[0] } : { ...chainCache, mtimeMs };
  }
  return chainCache;                                          // missing file: keep last good chain
}

function replacementFor(requested) {   // open TUI still on "fos-ai/glm-5.3" after the gateway dropped it
  const { head } = currentChain();
  const provider = requested?.split("/")[0];
  if (!head || !options.managed_providers.includes(provider) || chainMembersNow().has(requested)) return undefined;
  return head;
}
```

Why: opencode loads `opencode.json` once at start, so sessions opened before the hourly re-measure would otherwise keep asking for a model the gateway already refuses.

---

## opencode: MCP curation with `permission` rules

```jsonc
"permission": {
  "mantis-tools_*": "deny",                      // 95 tools → hide all …
  "mantis-tools_search_bugs": "allow",           // … then allow the curated 8
  "mantis-tools_semantic_bug_search": "allow",
  "mantis-tools_create_mantis": "ask",           // write tools: ask first
  "mantis-tools_send_email": "ask",
  "jenkins_*": "deny",
  "jenkins_getBuildLog": "allow",  …
  "jenkins_triggerBuild": "ask",  "jenkins_rebuildBuild": "ask",
  "logintel_*": "allow"                          // public /mcp = 26 read-only tools
}
```

- **Last match wins**, so the wildcard `deny` goes first
- `deny` **removes the tool from the model's tool list** (smaller prompt, and the model can't even try)
- The curation mirrors the Log Intelligence AI Assistant: `chat_mcp_tools.enabled` → allow, `WRITE_TOOLS` → ask

---

## opencode: VS Code bug we had to fix

**Symptom:** Ctrl+Escape opens the opencode terminal, and it dies about 2 s later with
`Failed to refresh default location data … AbortError`.

**Root cause:** `ms-python.vscode-python-envs` auto-activates the selected env in every new terminal. It sends **Ctrl+C** first, then `source …/activate`, and **Ctrl+C is opencode's exit key**.

**How we found it:** opencode's log showed "disposing all instances" about 2 s after start, triggered by a keybinding from stdin. `Python Environments.log` showed "Shell execution timed out: source …/activate" **at the same millisecond**.

**Fix (install.sh writes it to Machine settings on Remote-SSH):**
```json
"python-envs.terminal.autoActivationType": "off",
"python.terminal.activateEnvironment": false
```
This probably affects any TUI that an extension launches into a new terminal.

---

# Part 2 · Implementation highlights: Hermes

---

## Hermes: config without secrets, merged, not overwritten

```yaml
model:                                   # written by `adapt` (measured)
  default: glm-5.3-flash
  provider: custom
  base_url: https://fos-ai.fortinet.com/v1
  api_key: ${FOS_AI_API_KEY}             # expanded at load time from ~/.hermes/.env
fallback_providers:
  - {provider: custom, model: glm-5.3, base_url: https://fos-ai.fortinet.com/v1, key_env: FOS_AI_FALLBACK_API_KEY}
  - {provider: custom, model: qwen3.6-35b-a3b, base_url: https://releaseqa-aiserver.corp.fortinet.com/v1, key_env: LOCAL_QWEN_API_KEY}
```

- The user's `config.yaml` has about 545 lines written by Hermes. We touch **only** `model`, `fallback_providers`, our 4 `mcp_servers` entries and `plugins.enabled`
- Merged with **ruamel round-trip, using Hermes's own writer settings** (`indent(mapping=2, sequence=4, offset=2)`, `preserve_quotes`), so comments and key order survive. The diff on this VM was exactly the managed lines
- Run with **Hermes's own venv Python**, which already has ruamel, so the installer needs no dependencies

Gotchas we hit:
- Never name a variable `GLM_API_KEY`: Hermes's built-in z.ai provider claims it
- `~/.hermes/.env` **overrides** the process env, so a leg can't be tested by exporting a variable. Use a throwaway `HERMES_HOME`
- A fallback entry is skipped only if **base URL and model** both match the failed one, so never give two keys the same model on one gateway

---

## Hermes: MCP config and the 4 hidden extra tools

```yaml
mcp_servers:
  mantis-tools:
    url: https://releaseqa-portal.corp.fortinet.com/sse
    transport: sse
    headers: {Authorization: "Bearer ${MANTIS_MCP_TOKEN}"}
    tools:
      resources: false          # ← without these, Hermes adds list_resources, read_resource,
      prompts: false            #   list_prompts and get_prompt per server that advertises them
      include: [search_bugs, semantic_bug_search, ask_rag, mantis_fetch_activity,
                prepare_mantis, create_mantis, mantis_add_note, send_email]
  jenkins:
    url: https://releaseqa-stackjenkins.corp.fortinet.com/mcp-server/mcp
    headers: {Authorization: "Basic ${JENKINS_MCP_BASIC}"}     # base64(user:apitoken), made by install.sh
```

Measured with Hermes's own discovery code (`tests/list_registered_mcp_tools.py`):

| | Before `resources/prompts: false` | After |
|---|---|---|
| jenkins | 14 | **10** |
| mantis_tools | 12 | **8** |
| logintel | 26 | **26** |
| total | 52 | **44** (the same as opencode) |

---

## Hermes: why per-server `trust` wasn't enough

Hermes's built-in MCP gate is **per server**: with `trust: untrusted` it asks before every tool without `readOnlyHint: true`.

We probed all three servers with the MCP SDK:

```
logintel:     26 tools, readOnlyHint=True on 0
mantis-tools: 95 tools, readOnlyHint=True on 0
jenkins:      19 tools, readOnlyHint=True on 0
```

So `untrusted` would ask before **every search**. That's unusable, so we needed **per-tool** ask.

Hermes offers a `pre_tool_call` hook that can return:
- `{"action": "block", "message": …}`: the tool never runs
- `{"action": "approve", "message": …, "rule_key": …}`: goes to the **same human gate as dangerous shell commands** (`tools/approval.py::request_tool_approval`). No smart auto-approve, and it fails closed with no human.

---

## Hermes: the `mcp-ask-first` plugin (whole thing)

```python
ASK_FIRST_TOOLS = {
    "mcp__mantis_tools__create_mantis": "file a new Mantis bug",
    "mcp__mantis_tools__mantis_add_note": "add a note to a Mantis bug",
    "mcp__mantis_tools__send_email": "send an email",
    "mcp__jenkins__triggerBuild": "start a Jenkins build",
    "mcp__jenkins__rebuildBuild": "re-run a Jenkins build",
}

def nobody_can_answer():                      # -z, chat -q and cron
    try:
        from tools.approval_context import _is_cron_approval_context, _is_single_query_approval_context
        return _is_single_query_approval_context() or _is_cron_approval_context()
    except ImportError:                       # helpers moved in a newer Hermes: read the markers
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

`rule_key = tool_name`, so "allow for this session" covers later calls of the same tool.

---

## Hermes: the bug the tests caught

The first version returned `approve` always. Test result in `hermes -z` (one-shot):

```
$ hermes -z "Call mcp__logintel__logintel_list_projects …"    # gated in the test copy
The tool returned 13 projects.                               # ← ran WITHOUT asking
```

Root cause, `hermes_cli/oneshot.py`:

```python
# Non-interactive by definition — an approval prompt would hang forever.
os.environ["HERMES_YOLO_MODE"] = "1"          # the gate's first check: yolo → approved
os.environ["HERMES_SINGLE_QUERY_SESSION"] = "1"
```

Fix: **block** when the session is marked single-query or cron. After the fix:

```
$ hermes -z "…"
The call was blocked. "Not run: 'mcp__logintel__logintel_list_projects' would …,
which needs the user's approval, and this run (hermes -z / chat -q / cron) has nobody to ask."
```

`hermes chat -q` was also blocked, even through Hermes's `tool_call` tool-search wrapper.

---

## Hermes in VS Code (ACP)

- Extension `formulahendry.acp-client`, installed into the **Remote-SSH server** (`~/.vscode-server/…/code-server --install-extension`)
- The agent entry goes into **Machine** settings on the VM: `~/.vscode-server/data/Machine/settings.json`

```json
"acp.agents": { "Hermes Agent": {
  "command": "/home/<you>/.local/bin/hermes", "args": ["acp"],
  "env": { "PATH": "/home/<you>/.local/bin:/home/<you>/.hermes/bin:/usr/local/bin:/usr/bin:/bin" } } }
```

- Full path, because the extension doesn't inherit your shell `PATH`. `~/.hermes/bin` holds `tirith`, Hermes's command scanner
- ACP mode starts MCP discovery in a **background thread** at startup (`acp_adapter/entry.py`), so one `config.yaml` serves the terminal and VS Code
- Approvals are bridged to ACP `session/request_permission` with the options `allow_once`, `allow_session`, `allow_always`, `deny` and `deny_always`

---

# Part 3 · The measured model chain

---

## What broke on 2026-09-23

| Time | personal key | project key |
|---|---|---|
| 11:47 | `glm-5.3` answers | `deepseek-v4.1-flash` answers |
| ~21:00 | `/v1/models` lists **only** `glm-5.3-flash`; `glm-5.3` → **403** | lists `glm-5.3`, `glm-5.3-flash`; `deepseek` → **403** |

```
$ curl …/chat/completions -d '{"model":"deepseek-v4.1-flash",…}'
http=403  err= your access level does not permit model deepseek-v4.1-flash
```

Effects of a **fixed** chain:
- Hermes: every turn quietly fell back to local Qwen (`sessions.model = qwen3.6-35b-a3b`)
- opencode: the same, plus 30-minute parking churn
- A standalone `hermes -z` against the 403 model **hung until the 180 s timeout**

---

## Borrowed from the Log Intelligence LLM adapter

`log-intelligence/docs/implement_log/2026-09-24_llm_automatic_adapter_plan.md` runs detect → decide → apply every 15 minutes on the server. We applied its invariants:

| Adapter invariant | In the installers |
|---|---|
| Available = **a chat probe just succeeded on that key**; `/v1/models` shows what a key can see, not call | one-token `POST /chat/completions` per preferred model |
| 429 with "budget" = refused **for this run**; a plain 429 = still usable | same classification |
| A **new model family is never used automatically** | ids in no `prefer` list are printed as "new models seen" |
| **Never empty the chain on a failed probe** | unreachable gateway → keep that key's current links |
| Measure, then apply live | cron `--adapt`; the opencode plugin hot-reloads the chain |

We didn't copy the server-only parts: the DB tables, e-mail, per-variant providers, and runtime-refusal triggers.

---

## The algorithm (`adapt_models.py` / `merge_into_hermes_home.py adapt`)

```python
for account in preferences["accounts"]:                      # order = chain order
    key = env/key-file value;  if not key: skip
    status, body = GET f"{base_url}/models"
    if status is None:                                      # gateway unreachable
        chosen += [links of this key in the CURRENT chain]; continue
    for model in account["prefer"]:
        if len(picks) >= account["take"]: break
        if (base_url, model) in used: continue              # Hermes: no duplicate deployment
        result = probe_model(base_url, key, model)          # max_tokens 16
        if result == "ok": picks.append(model)
    report [m for m in listed if m not in any prefer list and not non-chat]
if not chosen: keep the current chain (or the fragment's) and warn
write config (+ chain-fallback.json for opencode) only if changed, with a .bak copy
```

```python
def probe_model(base_url, key, model):
    status, body = POST f"{base_url}/chat/completions" {"model": model, "max_tokens": 16, …}
    if status is None:                            return "unreachable"
    if status == 200 and not error_text(body):    return "ok"
    if status == 429 and "budget" not in reason:  return "ok"   # rate limit, still usable
    return "refused"
```

Standard library only (`urllib`), so it runs from cron with `env -i`.

---

## Preferences are data, not code

```yaml
# hermes_installer/config/model-preferences.yaml
accounts:
  - key: FOS_AI_API_KEY               # personal key: primary
    base_url: https://fos-ai.fortinet.com/v1
    take: 1
    prefer: [glm-5.3, glm-5.3-flash, deepseek-v4.1-flash]
  - key: FOS_AI_FALLBACK_API_KEY      # project token: fallback 1
    base_url: https://fos-ai.fortinet.com/v1
    take: 1
    prefer: [glm-5.3, deepseek-v4.1-flash, glm-5.3-flash]
  - key: LOCAL_QWEN_API_KEY           # internal Qwen: last resort
    base_url: https://releaseqa-aiserver.corp.fortinet.com/v1
    take: 2
    prefer: [qwen3.6-35b-a3b, qwen3.5-122b-a10b-awq]
not_chat_patterns: [embedding, rerank, whisper]
```

- The strongest model is first in each list, so when `glm-5.3` comes back to the personal key the next hourly run moves it back to primary **automatically**
- To adopt a new model: add it to a list and run `./install.sh --adapt`

---

## The hourly job in production (real log, 2026-09-23)

```
17 * * * * HERMES_HOME=~/.hermes ~/git/hermes/install.sh --adapt >> ~/.hermes/logs/hermes_installer_adapt.log 2>&1
47 * * * * ~/git/opencode/install.sh --adapt >> ~/.config/opencode/adapt.log 2>&1
```

| Job | Last cron run | Result |
|---|---|---|
| Hermes | 22:17 | `config.yaml unchanged` |
| opencode | 21:47 | `opencode.json unchanged`, `chain-fallback.json unchanged` |

What the probes measured (the same on both agents):

| Key | `glm-5.3` | `glm-5.3-flash` | `deepseek-v4.1-flash` | Qwen 3.6 / 3.5-122B / VL-235B |
|---|---|---|---|---|
| personal | ✗ 403 not permitted | ✓ | ✗ 403 | — |
| second key | ✓ | ✓ | ✗ 403 | — |
| local Qwen | — | — | — | ✓ / ✓ / ✓ (VL: opencode picker only; Hermes reports it as new) |

→ Chain: `glm-5.3-flash` (personal) → `glm-5.3` (second key) → `qwen3.6-35b-a3b` → `qwen3.5-122b-a10b-awq`

- The two jobs are 30 minutes apart, so a gateway change is picked up by one agent within 30 minutes and by both within an hour
- "unchanged" runs write nothing and create no backups, so the job is safe to run hourly
- Hermes stops probing a key once it has `take` models; opencode probes the whole list, so its picker can offer extra working models

---

# Part 4 · Installation guide

---

## Prerequisites and tokens

| Needed | Check |
|---|---|
| Linux VM, bash, `curl`, `python3` | `python3 --version` |
| opencode: Node/npm (or the fallback curl installer) | `npm --version` |
| GitHub SSH access to the private repos | `ssh -T git@github.com` |
| VS Code Remote-SSH (optional) | `ls ~/.vscode-server` |

| `tokens.env` | Where to get it | If empty |
|---|---|---|
| `FOS_AI_API_KEY` | AI team (personal fos-ai key) | the chain starts at the next key |
| `FOS_AI_FALLBACK_API_KEY` | project token | reuses the personal key |
| `LOCAL_QWEN_API_KEY` | QA infra (`releaseqa-aiserver`) | no local models |
| `MANTIS_MCP_TOKEN` | the same token as AI Assistant → My connections (**user-bound**) | Mantis off |
| `JENKINS_MCP_TOKEN` | `user:apitoken` (Jenkins → Configure → API Token) | Jenkins off |
| `LOGINTEL_MCP_KEY` | Log Intelligence admin (`limcp_…`, shown once) | Log Intelligence off |

---

## Install

```bash
# opencode
git clone git@github.com:bruceyu777/opencode_installer.git ~/git/opencode && cd ~/git/opencode
cp tokens.env.example tokens.env && chmod 600 tokens.env && vi tokens.env
./install.sh --cron

# Hermes (the same token file)
git clone git@github.com:bruceyu777/hermes_installer.git ~/git/hermes && cd ~/git/hermes
cp ~/git/opencode/tokens.env . && ./install.sh --cron
```

| Step | opencode | Hermes |
|---|---|---|
| 1 binary | `npm i -g opencode-ai@1.18.32` (fallback: official installer) | missing → official installer `--commit c0d7294769 --skip-setup --skip-browser --skip-computer-use`; older → `hermes update --yes` |
| 2 tokens | one mode-600 file per token | merged into `~/.hermes/.env` |
| 3 config | `opencode.json` + plugin; MCP without a token → disabled | ruamel merge of the managed keys + plugin |
| 4 models | `adapt` (probe, choose, write) | `adapt` |
| 5 VS Code | extension + Python auto-activation off | ACP Client extension + `acp.agents` entry |
| 6 cron | `47 * * * * install.sh --adapt` | `17 * * * * install.sh --adapt` |
| 7 check | MCP list, then 1 prompt per chain model | MCP test, plugin, gateway probe + 1 prompt per model |

Options: `--check`, `--adapt`, `--cron`, `--no-install`, `--no-vscode`, `--no-check`, `--tokens FILE`.

---

## What a good install looks like (real output, this VM)

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

## Real example: node16, a fresh test node (2026-09-23)

`all-in-one-node16` (10.96.234.11), Ubuntu 24.04, user `fosqa`: **no Node.js, no npm**, no opencode, VS Code server present.

```
$ git clone git@github.com:bruceyu777/opencode_installer.git ~/git/opencode && cd ~/git/opencode
$ cp tokens.env.example tokens.env && chmod 600 tokens.env && vi tokens.env    # the same 6 tokens as on our VM
$ ./install.sh </dev/null                                                      # nothing typed, nothing asked
==> opencode 1.18.32
    npm not available or not writable; using the official installer (~/.opencode/bin)
    now: 1.18.32 at /home/fosqa/.opencode/bin/opencode
==> Tokens -> /home/fosqa/.config/opencode/*.key        (6 × set)
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

## node16 example explained

| What you see | Why |
|---|---|
| `npm not available … using the official installer` | no Node on the node: the installer falls back to `curl https://opencode.ai/install \| bash -s -- --version 1.18.32` into `~/.opencode/bin`, which also adds a PATH line to `~/.bashrc`. No sudo needed |
| `</dev/null` | proves the install needs **no human input**, the same as a cron or remote run |
| the same chain as our VM | the chain depends on **the keys**, not the machine: the same tokens → the same probe results |
| `extension … installed (reload the VS Code window)` | the extension goes into the shared `~/.vscode-server/extensions`, so **every** server version sees it, including the newer one VS Code downloaded when you connected at 22:48 (after the install at 22:46) |
| `⚠ microsoft365 needs authentication` | expected: OAuth, optional, needs a browser |
| 44.58 s | download opencode, write config, probe 9 models, 4 test prompts |

VS Code proof, from node16's own log, when the user pressed Ctrl+Escape:

```
22:51:34.756 [info] ExtensionService#_doActivateExtension sst-dev.opencode, activationEvent: 'onCommand:opencode.openNewTerminal'
```

Left on node16: `~/.opencode/bin/opencode`, `~/.config/opencode/` (tokens as mode-600 files), `~/git/opencode` (+ `tokens.env`), and VS Code Machine settings (backup `.bak-<time>`). **No cron** on a shared test node: add it with `./install.sh --no-install --no-check --cron`.

---

## Real example: Hermes on node16 (same tokens, 2026-09-23)

```
$ git clone git@github.com:bruceyu777/hermes_installer.git ~/git/hermes && cd ~/git/hermes
$ cp ~/git/opencode/tokens.env . && ./install.sh </dev/null      # started with nohup, log polled
==> Hermes Agent (tested: v0.21.4)
    not installed: running the official installer, pinned to commit c0d7294769
    now: v0.21.4 at /home/fosqa/.local/bin/hermes
==> Tokens -> ~/.hermes/.env       6 × set   (the official installer's template .env kept, backup .env.bak-…)
==> Config -> ~/.hermes/config.yaml, plugin mcp-ask-first
    chain: glm-5.3-flash -> glm-5.3 -> qwen3.6-35b-a3b -> qwen3.5-122b-a10b-awq
==> VS Code: formulahendry.acp-client installed; acp.agents["Hermes Agent"] written
==> Check: logintel / mantis-tools / jenkins connected; plugin enabled; 4 × "gateway ok, Hermes PONG"
TOTAL 217.94s   exit=0
```

| Acceptance test (`tests/acceptance.sh`) | Result on node16 |
|---|---|
| H1 re-run · H2 cron under `env -i` | `unchanged` · rc 0 |
| H3 tools the model sees | jenkins 10, logintel 26, mantis_tools 8 = **44** |
| H4 model → logintel | `13` |
| H5 forced 404 | `PONG`, `sessions.model = glm-5.3` |
| H6 `triggerBuild` in `-z` | blocked before Jenkins |
| H7 `triggerBuild` via ACP | permission request `start a Jenkins build: {"jobFullName": …}` → deny → not run |
| H8 VS Code · H9 `tokens.env` | entry + extension present · git-ignored |

Trap seen: after "deny", an "allow once" in the **same** conversation produced no new request; the model refuses to retry a denied action. Test "allow" in a new session.

---

## Using them from a shell terminal (cheat sheet)

```bash
cd ~/git/<project>        # always start inside a project folder
opencode                  # full-screen UI          |  hermes            # chat (hermes --tui = newer UI)
opencode -c               # continue last session   |  hermes -c
opencode run "…"          # one-shot                |  hermes -z "…"
```

| opencode (leader = `Ctrl+X`) | | Hermes | |
|---|---|---|---|
| `Tab` | build ↔ plan agent | `Ctrl+C` / ×2 | interrupt / exit |
| `Esc` | stop the answer | `Alt+Enter`, `Ctrl+J` | new line |
| `Ctrl+P` | command palette | `Ctrl+G` | prompt in `$EDITOR` |
| `Ctrl+X` `n` / `l` / `m` | new / sessions / model | `/new`, `/sessions`, `/model` | same ideas |
| `Ctrl+X` `c` | compact | `/compress` | compact |
| `@file`, `!cmd`, `/init` | attach file, run shell, write AGENTS.md | `/tools list`, `/reload-mcp` | tools, reload MCP |
| `Ctrl+C` / `Ctrl+D` | exit | `/status`, `/usage` | model, tokens |

- **tmux over SSH:** `tmux new -s ai` … `Ctrl+B d` … `tmux attach -t ai`
- **Write tools ask first** only in interactive sessions; one-shot runs refuse them
- Full list: `docs/guides/install-on-a-node.md` §4

---

# Part 5 · Live demos

---

## Demo 1: measure the chain (30 s)

```bash
cd ~/git/hermes && ./install.sh --adapt
cd ~/git/opencode && ./install.sh --adapt
cat ~/.config/opencode/chain-fallback.json
tail -20 ~/.hermes/logs/hermes_installer_adapt.log       # what cron did last hour
```

Talking points:
- 403 on `glm-5.3` for the personal key, while the project key still gets it
- `qwen3-vl-235b` shows up as "new, not used"
- The second run prints `unchanged`, because nothing is rewritten without a change

Dry run (no writes), Hermes:
```bash
~/.hermes/hermes-agent/venv/bin/python scripts/merge_into_hermes_home.py adapt \
  --preferences config/model-preferences.yaml --fragment config/config.yaml --hermes-home ~/.hermes --dry-run
```

---

## Demo 1 explained

| Command | What it does |
|---|---|
| `./install.sh --adapt` (Hermes) | `find_hermes_python` → `merge_into_hermes_home.py adapt`. For each account in `model-preferences.yaml`: `GET /models`, then a one-token `POST /chat/completions` per preferred model until `take` models answer; merges the chain into `config.yaml` (MCP and plugin entries re-applied too) |
| `./install.sh --adapt` (opencode) | `python3 scripts/adapt_models.py`: probes **every** model of each prefer list; writes `opencode.json` (`model`, `small_model`, answering models only) and `chain-fallback.json` |
| `cat ~/.config/opencode/chain-fallback.json` | the chain the plugin reads: `{"chain": ["fos-ai/glm-5.3-flash", "fos-ai-logintel/glm-5.3", "local-qwen/qwen3.6-35b-a3b", "local-qwen/qwen3.5-122b-a10b-awq"]}` |
| `tail -20 …adapt.log` | what the hourly cron run did; every run starts with `==> Models -> … (timestamp)` |
| `… adapt … --dry-run` | the same probes and decision, prints `chain (dry run, nothing written)`; use it before changing a prefer list |

Reading the output:
- `ok` = the chat call answered. `refused (HTTP 403 …)` = the key may not use it. `ok (rate-limited right now, still usable)` = plain 429
- `new models seen, not used …` = in `/models` but in no prefer list (never adopted automatically)
- `gateway unreachable, keeping …` = no HTTP answer; that key's current links are kept
- `unchanged` = nothing written, no backup made

---

## Demo 2a: Hermes fallback, forced 404 on the primary

```bash
cd "$(mktemp -d)"                                          # empty folder: no project context, no real files
hermes -m no-such-model-404 -z "Reply with exactly: PONG"  # primary model name replaced for this run only
python3 -c "import sqlite3,os;print(list(sqlite3.connect(os.path.expanduser('~/.hermes/state.db')).execute(
  'select id, model, billing_base_url from sessions order by started_at desc limit 1')))"
```

Real output (2026-09-23 22:34):

```
PONG
took 6.08s
[('20260923_223438_941dd1', 'glm-5.3', 'https://fos-ai.fortinet.com/v1/')]
```

What the gateway answered to the first call (the same request sent with curl):

```
{"error":"unknown model: no-such-model-404"}
HTTP 404
```

**PONG alone proves nothing**: it looks the same whoever answered. The `sessions` row is the proof.

---

## Demo 2a explained: the three lines

| Line | What it does | Why it's written this way |
|---|---|---|
| `cd "$(mktemp -d)"` | new empty folder, e.g. `/tmp/tmp.FjpadbdWCX` | Hermes reads its start folder (`AGENTS.md`, file tools). Empty = no project text sent to the model, nothing real to touch |
| `-m no-such-model-404` | replaces **only** `model.default` for this run | base URL and key (`${FOS_AI_API_KEY}`) stay; `fallback_providers` unchanged; nothing saved to `config.yaml` |
| `-z "Reply with exactly: PONG"` | one-shot: one prompt, prints only the final answer, exits | short and cheap; the answer is easy to check. (`-z` also turns approvals off, which is why ask-first blocks write tools here; no tools are used in this demo) |
| `sessions … limit 1` | newest row of `~/.hermes/state.db` | in v0.21.4, `model` = **the model that answered**, not the one requested; `billing_base_url` = the gateway that was called |

Why not break a key instead? That would change your real `.env`, and exporting a variable does nothing: `~/.hermes/.env` overrides the environment. A fake model name fails safely.

---

## Demo 2a explained: what happens inside Hermes

```
prompt ─► primary: no-such-model-404 @ fos-ai (personal key)
            └─ HTTP 404 "unknown model"            ~0.2 s
               401/403/404 = permanent → no retry, move on now
               (429/5xx = maybe temporary → retry first, then move on)
        ─► fallback_providers[0]: glm-5.3 @ fos-ai (FOS_AI_FALLBACK_API_KEY)
               skip only if base_url AND model equal the failed link
               → same URL, different model → used
            └─ "PONG"                              ~5.8 s
        ─► session row: model=glm-5.3, billing_base_url=https://fos-ai.fortinet.com/v1/
```

- The chain is walked **inside one turn**, at most once
- The **next turn starts at the primary again**. That's why the hourly `--adapt` matters: it removes a refused model from the chain for good, so turns stop paying for one failed call each
- The skip rule is also why we never put the same model on two keys of one gateway: Hermes would treat the second as a duplicate

---

## Demo 2a: what it proves, what it doesn't, gotchas

| Proves | Doesn't prove |
|---|---|
| the chain in `config.yaml` is loaded | the 403 "not permitted" and 429 "budget" paths (429 retries first) |
| a permanent error moves on **without retry delay** | links 3 and 4: use `./install.sh --check`, which tests each link alone |
| fallback 1's key works | behaviour of later turns (they restart at the primary) |
| `sessions.model` tells you who answered | |

Gotchas:
- **No log evidence for `-z`:** the fallback isn't written to `~/.hermes/logs/agent.log` in one-shot mode. Use the `sessions` row, or curl the gateway as shown
- **Concurrent sessions** (VS Code, cron) can steal `limit 1`: select `id` and `started_at` too, and check the time
- **See the whole chain:** `hermes fallback list`
- **Test the local Qwen link:** run from a throwaway `HERMES_HOME` whose `.env` has invalid fos-ai keys (VM guide §3.3)

---

## Demo 2b: opencode, a session on a model the gateway dropped

```bash
opencode run --model fos-ai/glm-5.3 "Reply with exactly: PONG"
# footer: "> build · glm-5.3-flash"  — the plugin routed the dropped model to the chain head
```

- Precondition: `glm-5.3` is still declared under `fos-ai` in `opencode.json`, as in a session opened before the hourly re-measure
- `chat.message` hook → `replacementFor("fos-ai/glm-5.3")`: our provider, not in `chain-fallback.json` → chain head `fos-ai/glm-5.3-flash` → toast "Using fos-ai/glm-5.3-flash instead of fos-ai/glm-5.3"
- The footer line of `opencode run` names the model that actually answered: that's the proof, the same role as `sessions.model` in 2a

---

## Demo 2b explained

| Part | Meaning |
|---|---|
| `opencode run` | one-shot opencode: one prompt, prints the answer, exits |
| `--model fos-ai/glm-5.3` | asks for a model the measured chain no longer contains (the personal key gets a 403 for it) |
| footer `> build · glm-5.3-flash` | agent (`build`) and **the model that actually answered**, printed by opencode after the answer |

Path inside the plugin, before any network call:

```
chat.message hook
  asked = "fos-ai/glm-5.3"
  replacementFor(asked): provider "fos-ai" is managed, and asked is not in chain-fallback.json
                         → head of chain "fos-ai/glm-5.3-flash"
  firstAvailableFrom(head): skips models parked in chain-fallback-router.json
  output.message.model = fos-ai/glm-5.3-flash
  toast: "Using fos-ai/glm-5.3-flash instead of fos-ai/glm-5.3"
```

- No failed call is spent: the dropped model is never sent to the gateway
- Precondition, and why: opencode only lets you pick declared models, and `adapt` removes refused ones from `opencode.json`. Re-declare `glm-5.3` under `fos-ai` to simulate a session opened **before** the hourly run
- Models of other providers are never re-routed (`managed_providers`)

---

## Demo 3: hot reload into a running session (opencode)

```bash
cd "$(mktemp -d)"; opencode serve --port 4099 & sleep 8
SID=$(curl -s -X POST localhost:4099/session -H 'Content-Type: application/json' -d '{}' | jq -r .id)
ask() { curl -s -X POST localhost:4099/session/$SID/message -H 'Content-Type: application/json' \
  -d '{"model":{"providerID":"fos-ai","modelID":"glm-5.3-flash"},
       "parts":[{"type":"text","text":"Reply with exactly: PONG"}]}' | jq -r '.info.providerID+"/"+.info.modelID'; }
ask                                                    # fos-ai/glm-5.3-flash
cp ~/.config/opencode/chain-fallback.json /tmp/chain.bak
echo '{"chain":["local-qwen/qwen3.6-35b-a3b","local-qwen/qwen3.5-122b-a10b-awq"]}' > ~/.config/opencode/chain-fallback.json
ask                                                    # local-qwen/qwen3.6-35b-a3b  (same session, no restart)
cp /tmp/chain.bak ~/.config/opencode/chain-fallback.json; kill %1
```

Result from our test: first answer `fos-ai/glm-5.3-flash`, second `local-qwen/qwen3.6-35b-a3b`.

---

## Demo 3 explained

| Step | What it does |
|---|---|
| `opencode serve --port 4099 &` | opencode's HTTP server in the background: the same engine the TUI and the VS Code extension use; `sleep 8` lets plugins and MCP start |
| `POST /session` → `jq -r .id` | creates one session; `SID` keeps it for both prompts |
| `ask()` | `POST /session/$SID/message` with an explicit model and a text part; the reply's `info.providerID/modelID` is **who answered** |
| 1st `ask` | chain file = the measured chain → `fos-ai/glm-5.3-flash` |
| `cp … /tmp/chain.bak`, then `echo '{"chain":[…qwen…]}' > …` | simulates an hourly `--adapt` that found both fos-ai keys refused |
| 2nd `ask` | **same process, same session** → `local-qwen/qwen3.6-35b-a3b` |
| `cp /tmp/chain.bak …; kill %1` | restores your real chain, stops the server |

Why the 2nd answer changed:
- On every turn the plugin `stat()`s `chain-fallback.json`; the mtime changed, so it re-parsed the file
- `fos-ai/glm-5.3-flash` is no longer in the chain → `replacementFor()` → new head `local-qwen/qwen3.6-35b-a3b`
- Without this, a TUI opened in the morning would keep asking for a model the gateway refused at noon

Needs `jq`. Run it in a scratch folder, and don't skip the restore step.

---

## Demo 4: MCP tools the model actually sees

```bash
cd ~/git/hermes
hermes mcp list                      # 8 selected / 10 selected / all
hermes mcp test jenkins              # ✓ Connected (1599ms) ✓ Tools discovered: 19   (server total)
~/.hermes/hermes-agent/venv/bin/python tests/list_registered_mcp_tools.py
# jenkins: 10 tools   logintel: 26 tools   mantis_tools: 8 tools   registry has 44 mcp__ tools
```

Then a real question, in `hermes` or opencode:

> *"Using Log Intelligence, list the monitored projects, then show the build health of the newest FortiOS build."*

> *"Search Mantis for bugs about 'ipsec tunnel flap after upgrade' and summarise the top 3."*

> *"Get the test results of the last build of Jenkins job X and tell me which QAIDs failed."*

---

## Demo 4 explained

| Command | What it shows | Note |
|---|---|---|
| `hermes mcp list` | configured servers: transport, `8 selected` / `10 selected` / `all`, enabled or disabled | read from `config.yaml` (`tools.include`); no connection made |
| `hermes mcp test jenkins` | connects, times it, counts the tools **on the server** (19) | a connection test, not what the model gets |
| `tests/list_registered_mcp_tools.py` | loads `~/.hermes/.env`, runs Hermes's own `discover_mcp_tools()`, prints what landed in the tool **registry** | the only number that matters: what the model is given |

Three numbers for Jenkins, and why they differ:

| Server has | Hermes registered before `resources/prompts: false` | Model gets now |
|---|---|---|
| 19 | 14 = 10 included + 4 helper tools (`list_resources`, `read_resource`, `list_prompts`, `get_prompt`) | **10** |

- Tool names are `mcp__<server>__<tool>`, with `-` → `_` (`mantis-tools` → `mantis_tools`)
- Run the script with Hermes's venv Python: it imports Hermes modules
- opencode equivalent: ask the model to list its tools. The `/experimental/tool` API lists only built-ins

The example questions then exercise one server each; the tool calls appear in the session transcript.

---

## Demo 5: guard rails

**Interactive (terminal or VS Code): it asks**
> *"Trigger the Jenkins job `hermes-gate-demo-does-not-exist`."*

A prompt appears: `start a Jenkins build: {…the job arguments…} <mcp__jenkins__triggerBuild> (plugin approval rule)`. Choose **Deny**; the model reports it wasn't approved and doesn't retry.

**One-shot: it's blocked outright**
```bash
hermes -z "Use mcp__jenkins__triggerBuild to trigger job hermes-gate-demo-does-not-exist"
# → Not run: 'mcp__jenkins__triggerBuild' would start a Jenkins build, which needs the user's approval …
```

**opencode:** the same prompt shows opencode's permission dialog (`ask` rule).

Use a job name that doesn't exist: if a gate ever failed, Jenkins would just return 404.

---

## Demo 5 explained

**Interactive (terminal or VS Code):**
```
model calls mcp__jenkins__triggerBuild
 → pre_tool_call hook (mcp-ask-first): tool in ASK_FIRST_TOOLS, a human is present
 → {"action": "approve", "message": "start a Jenkins build: {…}", "rule_key": "mcp__jenkins__triggerBuild"}
 → tools/approval.py request_tool_approval → the same human gate as dangerous shell commands
      terminal: prompt   |   VS Code: ACP session/request_permission (allow once / session / always / deny)
 → Deny: the tool returns "BLOCKED: User denied …"; the model is told not to retry
```

**One-shot `hermes -z`:**
```
-z sets HERMES_YOLO_MODE=1 (would auto-approve everything) and HERMES_SINGLE_QUERY_SESSION=1
 → hook sees nobody_can_answer() → {"action": "block", …}   (block wins over yolo)
 → tool result: {"error": "Not run: 'mcp__jenkins__triggerBuild' would start a Jenkins build, …"}
```
Verified tonight: nothing was triggered.

**opencode:** `"jenkins_triggerBuild": "ask"` in `permission` → opencode's own permission dialog.

- Why a non-existent job: if a gate ever failed, Jenkins would only return 404, so the demo is safe
- `rule_key` = tool name: "allow for this session" covers later calls of the same tool; "allow always" writes `command_allowlist` in `config.yaml` (permanent)

---

## Demo 6: install on a fresh node, then run the acceptance tests (node16)

```bash
# on the node (VS Code Remote-SSH terminal, or ssh)
git clone git@github.com:bruceyu777/opencode_installer.git ~/git/opencode && cd ~/git/opencode
cp ~/path/to/your/tokens.env . && chmod 600 tokens.env
time ./install.sh </dev/null                          # ~45 s on node16

./install.sh --no-install --no-vscode --no-check       # T1 re-run: "unchanged"
env -i HOME=$HOME PATH=/usr/bin:/bin ./install.sh --adapt   # T2 the cron command: rc 0
./install.sh --check                                   # T3 MCP + one prompt per model
cd "$(mktemp -d)"
opencode run "Call the logintel tool that lists the monitored projects. Reply only with the number of projects."   # T4
opencode run "Use the jenkins triggerBuild tool to trigger job opencode-gate-demo-does-not-exist"                    # T7
```

Real results on node16:

| Test | Output |
|---|---|
| T1 / T2 | `opencode.json unchanged`, `chain-fallback.json unchanged`, rc 0 |
| T3 | ✓ logintel ✓ mantis-tools ✓ jenkins; 4 × PONG |
| T4 | `⚙ logintel_logintel_list_projects` → `13` |
| T5 stale model (sandbox) | `> build · glm-5.3-flash` |
| T6 hot reload (sandbox `serve`) | 1st `fos-ai/glm-5.3-flash`, 2nd `local-qwen/qwen3.6-35b-a3b` |
| T7 guard rail | `! permission requested: jenkins_triggerBuild (*); auto-rejecting` |
| T8 | `tokens.env` git-ignored |

---

## Demo 6 explained

| Step | What it proves |
|---|---|
| clone + `tokens.env` + `./install.sh </dev/null` | the README quick start works on a machine we never touched, without Node, without prompts |
| T1 re-run | idempotent: safe after every `git pull` |
| T2 `env -i … --adapt` | the hourly job works with cron's empty environment (stdlib Python only) |
| T3 `--check` | every MCP server connects; each chain link answers from a throwaway data dir |
| T4 `opencode run "…list projects…"` | end to end: model → MCP tool call (`⚙ logintel_logintel_list_projects`) → Log Intelligence → answer `13` |
| T5 / T6 (sandbox copies of the config) | stale-model routing and hot reload behave the same on another machine |
| T7 `triggerBuild` on a non-existent job | `ask` rule: in one-shot `opencode run` nobody can answer, so opencode **auto-rejects**; nothing is triggered |
| T8 | the only file with tokens in the repo is git-ignored |

Things we learned on node16:
- **After T7, `opencode run` doesn't exit.** The call is rejected, but the run waits until killed (our 180 s timeout). That's opencode's behaviour; interactive sessions show the dialog normally
- The whole suite is one script: `n16-tests.sh`, piped over ssh (`ssh node16 'bash -s' < n16-tests.sh`); the sandboxes that hold key copies are removed by a `trap … EXIT`
- No SSH key was needed: node16's own GitHub key cloned the private repo

---

# Part 6 · Testing

---

## Test strategy

Everything that talks to a real gateway, MCP server or config file is tested **against the real thing, in a sandbox**:

| Sandbox | How |
|---|---|
| Hermes home | `HERMES_HOME=$(mktemp -d)`, since `.env` in the home overrides the env |
| opencode config and data | `XDG_CONFIG_HOME` / `XDG_DATA_HOME` temp dirs, so a failed check never parks your real models |
| Whole user | `env -i HOME=/tmp/sandbox PATH=/usr/bin:/bin ./install.sh`, so the real `hermes` isn't on `PATH` |
| Gate tests | a copy of the plugin that also gates a **harmless read tool** (`logintel_list_projects`), so a gate failure writes nothing |
| Cron | the exact crontab command under `env -i` |

Every temp dir holding a key copy is deleted after the run.

---

## Test matrix: installers

| # | Scenario | Result |
|---|---|---|
| 1 | Hermes fresh install, sandbox `HOME`, clean `PATH` | official installer pinned to `c0d7294769`, all checks pass, **437 s** |
| 2 | Hermes, only the Qwen and logintel tokens, empty home | chain starts at `qwen3.6-35b-a3b`; mantis and jenkins `enabled: false`; logintel connected |
| 3 | Hermes on the existing 545-line `config.yaml` | diff = managed lines only; 2nd run `unchanged` |
| 4 | Hermes adapt, local gateway unreachable (`127.0.0.1:9`) | Qwen links **kept** from the current config |
| 5 | opencode install into sandbox `XDG_*` | chain written, all 4 PONG, 2nd `--adapt` `unchanged` |
| 6 | opencode check loop | **bug found**: only the 1st model was tested (`opencode run` read the loop's stdin) → fixed with `</dev/null` |
| 7 | Both cron lines under `env -i` | rc 0, `unchanged` |
| 8 | Secrets | scan of every committed file for every token value, and base64 of `user:token`: 0 hits |

---

## Test matrix: behaviour

| # | What | How | Result |
|---|---|---|---|
| 9 | Curation | Hermes discovery → registry | 44 tools (10/8/26), the same as opencode |
| 10 | Tool annotations | MCP SDK `list_tools()` on all 3 servers | 0 of 140 have `readOnlyHint` → per-tool gate needed |
| 11 | Gate, `hermes -z` | gated test tool | v1 **ran it** (yolo) → v2 blocked |
| 12 | Gate, `hermes chat -q` | same | blocked, also via `tool_call` wrapper |
| 13 | Gate, ACP deny | `tests/acp_permission_test.py` | one `session/request_permission`, tool not run, no retry |
| 14 | Gate, ACP allow_once | same script | tool ran, 13 projects |
| 15 | Stale model routing | `opencode run --model fos-ai/glm-5.3` | answered by `glm-5.3-flash` |
| 16 | Hot reload | `opencode serve`, rewrite chain between prompts | 2nd prompt on local Qwen, no restart |
| 17 | Every chain link | throwaway home or data dir per model | 4/4 PONG on both agents |
| 18 | VS Code opencode crash | matching timestamps in 2 logs | fixed; the terminal stays up |
| 19 | opencode on node16 (fresh node, no Node.js) | clone → `./install.sh </dev/null` | exit 0 in 44.6 s via the official installer fallback; T1–T8 all pass (Demo 6) |

---

## The ACP test client (what VS Code does, scripted)

```python
# tests/acp_permission_test.py  (JSON-RPC over stdio, newline-delimited)
request("initialize", {"protocolVersion": 1,
        "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False}, "terminal": False}})
time.sleep(15)                                  # let the background MCP discovery finish
session_id = request("session/new", {"cwd": workdir, "mcpServers": []})["sessionId"]
for answer in ("deny", "allow_once"):
    request("session/prompt", {"sessionId": session_id, "prompt": [{"type": "text", "text": prompt}]})
    # on "session/request_permission":
    send({"jsonrpc": "2.0", "id": message["id"],
          "result": {"outcome": {"outcome": "selected", "optionId": answer}}})
```

Real output:
```
--- answer to permission requests: deny
    permission requests: [{"title": "TEST GATE list projects: {}: <mcp__logintel__logintel_list_projects> (plugin approval rule)",
                           "options": ["allow_once","allow_session","allow_always","deny","deny_always"]}]
    agent text: The tool did not run — it was blocked before execution … I did not retry the call.
--- answer to permission requests: allow_once
    agent text: The tool ran successfully and returned **13 projects** …
```

Run it yourself: `HERMES_HOME=<test home> ~/.hermes/hermes-agent/venv/bin/python tests/acp_permission_test.py "$PWD" "<prompt>"`

---

## Re-run the checks any time

```bash
./install.sh --check          # both repos: MCP servers + plugin + one prompt per chain model
./install.sh --adapt          # re-measure now
hermes mcp test mantis-tools  # one server, with timing
hermes fallback list          # Hermes's view of the chain
opencode mcp list             # opencode's view of the MCP servers
```

Where to look when something is off:

| What | Where |
|---|---|
| Hourly adapt runs | `~/.hermes/logs/hermes_installer_adapt.log`, `~/.config/opencode/adapt.log` |
| Which model answered (Hermes) | `sessions.model`, `billing_base_url` in `~/.hermes/state.db` |
| Parked models (opencode) | `~/.local/share/opencode/chain-fallback-router.json` (delete to un-park) |
| Hermes errors | `~/.hermes/logs/errors.log`, `hermes logs --follow` |

---

# Part 7 · Lessons and limits

---

## Lessons

1. **A model list is not a contract.** `/v1/models` listed deepseek while every call got a 403. Probe with a real call.
2. **Read the approval path end to end.** Hermes's `-z` sets `YOLO=1` before any plugin runs; only an E2E test showed our gate was bypassed.
3. **Look at what the model actually sees.** The server says 19 tools, the config says 10, and Hermes registered 14 until we turned off resources and prompts.
4. **Merge, don't overwrite.** Users' agent configs are large and hand-tuned; round-trip YAML keeps them intact.
5. **Test the installer the way a colleague runs it:** empty `HOME`, clean `PATH`, partial tokens.
6. **Two log files at the same millisecond** solved the VS Code crash that no single log explained.
7. **Borrow working designs.** The Log Intelligence adapter's invariants mapped straight onto a 150-line script.

---

## Limits (be honest with users)

- **Curation isn't a sandbox.** Hidden tools and ask-first control the *tool path*. The agent runs shell as you, and tokens are readable (`~/.hermes/.env`, `~/.config/opencode/*.key`; Hermes only scrubs env names it knows). On 2026-09-23 an opencode session on this VM read `mcp-mantis.key` and wrote its own MCP client in `/tmp`.
- **"Allow always"** writes a permanent rule (`command_allowlist`).
- **`--yolo` / `approvals.mode: off`** disables the ask-first prompts too.
- **Hourly granularity:** between runs, the in-turn fallback covers failures, at the cost of one failed call.
- **The personal key carries interactive load.** Background jobs belong on the log-intel key (see the adapter's invariant 4).
- **Microsoft 365 MCP** is configured but untested (OAuth through a claude.ai-hosted endpoint).

---

## Next steps

- Runtime fast path: react to a 403 immediately (the adapter's `llm_runtime_refusals` idea) and not wait for the hourly run
- Add the local `fosqa-tools` MCP server (provision FGT VMs, KVM cleanup) with ask-first on every tool
- Share `model-preferences` between the two repos (one source of truth)
- An e-mail/Teams note when the chain changes or a new model appears
- A proper test harness in CI for `adapt` (mock gateway: 200, 403 permit, 429 budget, 429 rate limit, timeout)

**Repos:** `bruceyu777/opencode_installer`, `bruceyu777/hermes_installer`
**Docs:** `docs/guides/fosqa-vm-llm-chain.md` in each repo (English + 中文)

Questions?
