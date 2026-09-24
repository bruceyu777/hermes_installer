---
marp: true
theme: default
paginate: true
title: AI coding agents on our QA VMs
---

# AI coding agents on our QA VMs

**opencode** and **Hermes Agent**, set up in one script

- Fortinet's internal models, with automatic fallback
- Jenkins, Mantis and Log Intelligence built in
- Works in a terminal and in VS Code (Remote-SSH too)

FortiOS QA · 2026-09-23

---

## Why this exists

Setting up an AI agent by hand takes an afternoon:

- Which gateway, which model, which key?
- What happens when a model runs out of budget or disappears?
- How do I connect Jenkins, Mantis, Log Intelligence?
- Which tools are safe to let it use?

Now it's **clone, add your own tokens, run one script**. Everyone gets the same tested setup.

---

## Two agents, one setup

| | **opencode** | **Hermes Agent** |
|---|---|---|
| What it is | coding agent with a terminal UI | general agent: terminal, chat, skills, memory |
| In VS Code | `sst-dev.opencode` extension (Ctrl+Escape) | ACP Client extension → "Hermes Agent" |
| Repo | `bruceyu777/opencode_installer` | `bruceyu777/hermes_installer` |
| Models, tools, guard rails | same | same |

Pick one or use both. **One `tokens.env` file works for both.**

---

## Quick start (about 5 minutes)

```bash
git clone git@github.com:bruceyu777/opencode_installer.git ~/git/opencode
cd ~/git/opencode
cp tokens.env.example tokens.env && chmod 600 tokens.env
vi tokens.env             # your own tokens
./install.sh --cron

# Hermes: same steps, reuse the same token file
git clone git@github.com:bruceyu777/hermes_installer.git ~/git/hermes
cd ~/git/hermes && cp ~/git/opencode/tokens.env . && ./install.sh --cron
```

The repos are private: ask for collaborator access first.
Update later with `git pull && ./install.sh`. Your tokens are kept.

---

## The tokens you need

Fill in what you have and leave the rest empty.

| Token | For | If you leave it empty |
|---|---|---|
| `FOS_AI_API_KEY` | your personal fos-ai model key | chain starts at the next key |
| `FOS_AI_FALLBACK_API_KEY` | a second fos-ai key (e.g. a project token) | reuses your personal key |
| `LOCAL_QWEN_API_KEY` | internal Qwen server (last resort) | no local models |
| `MANTIS_MCP_TOKEN` | Mantis tools | Mantis off |
| `JENKINS_MCP_TOKEN` | Jenkins tools (`user:apitoken`) | Jenkins off |
| `LOGINTEL_MCP_KEY` | Log Intelligence tools (`limcp_…`) | Log Intelligence off |

The token file is never committed, and tokens never appear in the config files.

---

## Models: a chain, not a single model

```
your personal key   ──►  second fos-ai key  ──►  internal Qwen  ──►  bigger Qwen
glm-5.3-flash            glm-5.3                 qwen3.6-35b         qwen3.5-122b
```

- If a model fails (refused, out of budget, server error), **the same message goes to the next one**
- You see a notice telling you which model took over
- A failed model is skipped for 30 minutes, then tried again

---

## The chain is measured, not fixed

**What happened on 2026-09-23:** within a few hours the gateway

- stopped allowing `glm-5.3` on personal keys
- stopped allowing `deepseek-v4.1-flash` on every key, although it was still in the model list

With a fixed chain, every chat quietly fell back to the local Qwen server.

**What we do now** (the same rules as the Log Intelligence model watcher):

- Every hour, test each preferred model with a tiny real request
- Use only the models that answer, in the preferred order
- Report new models the gateway starts offering; don't use them until someone approves them
- If the gateway can't be reached, keep the current chain

---

## Tools the agent can use

The same set as the **Log Intelligence AI Assistant**:

| Server | Examples | Tools |
|---|---|---|
| Log Intelligence | failures, build health, QAID history, build compare, triage status | 26 (all read-only) |
| Mantis | search bugs, semantic search, ask the knowledge base, prepare / file a bug | 8 |
| Jenkins | job and build status, build logs, test results, trigger / rebuild | 10 |

Tools outside this list are hidden from the agent.
Microsoft 365 is optional and not yet tested.

---

## Guard rails: it asks before changing anything

These always need your OK first:

- File a Mantis bug · add a Mantis note · send an email
- Start or re-run a Jenkins build

| Where | What happens |
|---|---|
| Terminal | a prompt: allow once / for this session / deny |
| VS Code | a permission pop-up |
| One-shot or scheduled runs (nobody to ask) | **blocked** |

Everything else (searches, logs, reports) runs without asking.

---

## Example prompts

- *"What failed in the latest FortiOS 7.6 build, grouped by root cause?"*
- *"Is QAID 123456 a new regression? Show its history."*
- *"Search Mantis for bugs like this crash log, then draft a new bug."* (asks before filing)
- *"Get the last 200 lines of the build log for job X #1234 and explain the failure."*
- *"Compare build 3510 with 3498: what's new?"*

---

## Using it in VS Code

**opencode**
- Open a project folder → **Ctrl+Escape**

**Hermes**
- **Developer: Reload Window** → **ACP: Connect to Agent** → *Hermes Agent*
- After a config change: **ACP: Restart Agent**

Tip: start in a project folder, not your home directory.

---

## Know the limits

- **The guard rails aren't a security boundary.** They control the agent's *tools*. The agent still runs shell commands as you and can read your tokens. On our VM, an agent once wrote its own Mantis script with the token file. Watch what it runs.
- **"Allow always"** is permanent. Prefer "allow once" or "allow for this session".
- **Turning approvals off** (`--yolo`) also turns these prompts off.
- **Answers can be wrong.** Check before you file or send anything.

---

## When something looks wrong

| You see | Do |
|---|---|
| `refused … does not permit model` | nothing: the chain moves on, and the hourly check picks a working model |
| `429 … budget` | nothing: that key is out of budget for now |
| An MCP server shows failed | check that token in `tokens.env`, run `./install.sh` |
| The opencode terminal closes after 2 s in VS Code | already fixed by `install.sh` (Python terminal auto-activation) |
| Config change not picked up | restart the agent (VS Code: reload / Restart Agent) |

Health check any time: `./install.sh --check`

---

## Under the hood (for the curious)

| Piece | opencode | Hermes |
|---|---|---|
| Model preferences | `config/model-preferences.json` | `config/model-preferences.yaml` |
| Hourly re-check | `install.sh --adapt` (cron at :47) | `install.sh --adapt` (cron at :17) |
| Fallback | `chain-fallback.js` plugin | built-in `fallback_providers` |
| Ask-first rule | per-tool `permission` rules | `mcp-ask-first` plugin |
| Pinned version | opencode 1.18.32 | Hermes v0.21.4 |

Want to add a new model? Add it to the preference list, then run `./install.sh --adapt`.

---

## Get started today

1. Ask for access to the two repos
2. Collect your tokens (the table on the "tokens" slide)
3. `./install.sh --cron`
4. Try an example prompt on your latest build

Questions and ideas are welcome, especially which tools you'd like the agent to have next.
