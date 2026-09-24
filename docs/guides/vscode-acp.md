---
title: Hermes Agent: use it inside VS Code through the ACP Client extension
kind: guide
created: 2026-09-21
updated: 2026-09-22
status: current
verified_against: Hermes Agent v0.16.0, VS Code 1.137.0 (snap), extension formulahendry.acp-client 0.2.0, Ubuntu (kernel 7.0.0-30), checked on 2026-09-21
summary: Hermes has no VS Code extension of its own; it plugs into VS Code as an ACP agent. This guide installs the ACP Client extension, registers Hermes, connects, and explains why the marketplace "Hermes" extension is the wrong one.
related:
  - ../INDEX.md
  - vscode-acp.zh-CN.md
  - install-hermes.md
  - model-fallback.md
  - ~/.config/Code/User/settings.json
---

# Hermes Agent: use it inside VS Code through the ACP Client extension

Hermes Agent speaks the Agent Client Protocol (ACP, from Zed Industries), a JSON-RPC
protocol over stdio that lets an editor host an external coding agent and render its
chat, tool calls, file diffs, terminal commands and approval prompts. VS Code gets ACP
support from the **ACP Client** extension by formulahendry; Hermes is one of its built-in
agents. There is no "Hermes Agent" extension in the marketplace.

State on this workstation (2026-09-21):

| Item | State |
|---|---|
| VS Code | 1.137.0, installed as a snap (`/snap/bin/code`) |
| ACP extension | `formulahendry.acp-client` 0.2.0 in `~/.vscode/extensions/` |
| Agent entry | `acp.agents["Hermes Agent"]` in `~/.config/Code/User/settings.json`, using the full launcher path |
| Backup | `~/.config/Code/User/settings.json.bak.hermes` (settings before the change) |
| Removed | `jet-propulsion-laboratory.hermes` 5.0.7 — NASA JPL's spacecraft telemetry tool, installed by mistake |
| Hermes side | `hermes acp --check` → `Hermes ACP check OK`; handshake test in section 3 passed |

## 0. The wrong "Hermes" extension

Searching the marketplace for "Hermes" returns **Hermes Extension for Visual Studio Code**
by jet-propulsion-laboratory. That is NASA JPL's telemetry and command frontend for
spacecraft ground systems (F' framework, uplink/downlink, EVRs). It only shows a rover icon
after a workspace with F' dictionaries is opened, which is why "nothing appeared" after
installing it. It has nothing to do with Hermes Agent.

```bash
code --uninstall-extension jet-propulsion-laboratory.hermes
```

## 1. Install the ACP Client extension

```bash
code --install-extension formulahendry.acp-client
```

Or in VS Code: Extensions view → search "ACP Client" → install the one by formulahendry.
It adds an **ACP Client** icon to the Activity Bar and these commands to the palette:
`ACP: Connect to Agent`, `ACP: New Conversation`, `ACP: Open Chat Panel`,
`ACP: Add Agent Configuration`, `ACP: Set Agent Model`, `ACP: Set Agent Mode`,
`ACP: Show Log`, `ACP: Show Protocol Traffic`, `ACP: Restart Agent`.

Its built-in agent list (setting `acp.agents`) already contains:

```json
"Hermes Agent": { "command": "hermes", "args": ["acp"], "env": {} }
```

## 2. Configure

The built-in entry relies on `hermes` being on VS Code's PATH. VS Code here is a snap, and
snap-launched apps often do not see `~/.local/bin`, so the entry is overridden with the
full path and an explicit PATH. In `~/.config/Code/User/settings.json`:

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

User-defined entries under `acp.agents` merge with the built-ins, so the other agents
(Claude Code, Gemini CLI, GitHub Copilot, …) stay available.

Other settings worth knowing:

| Setting | Default | Meaning |
|---|---|---|
| `acp.autoApprovePermissions` | `"ask"` | whether tool calls (file writes, terminal) prompt for approval |
| `acp.defaultWorkingDirectory` | `""` | working directory handed to the agent; empty = the open workspace |
| `acp.logTraffic` | `true` | keep the JSON-RPC traffic in the "ACP: Show Protocol Traffic" view |

Hermes needs no ACP-specific configuration; it reuses the provider, model and fallback chain
from `~/.hermes/config.yaml`. In ACP mode it exposes a curated `hermes-acp` toolset (file
read/write/patch/search, terminal, web, memory, todo, skills, delegate, vision) and hides
messaging and cron tools.

## 3. Verify

Hermes side, from a terminal:

```bash
hermes acp --check
# Hermes ACP check OK

# raw ACP handshake: the adapter must start and log the client's initialize
printf '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":1,"clientCapabilities":{}}}\n' | timeout 30 hermes acp 2>&1 | head -5
```

Observed 2026-09-21 16:21 PDT:

```
[INFO] acp_adapter.entry: Loaded env from /home/yzhengfeng/.hermes/.env
[INFO] acp_adapter.entry: Starting hermes-agent ACP adapter
[INFO] acp_adapter.server: ACP client connected
[INFO] acp_adapter.server: Initialize from unknown (protocol v1)
```

VS Code side: after `Developer: Reload Window`, run `ACP: Connect to Agent`, pick
**Hermes Agent**, then `ACP: Open Chat Panel` and send a prompt. The chat panel shows the
reply, and `ACP: Show Log` shows the same adapter lines as above.

## 4. Use

1. Reload the window once after installing or editing `acp.agents`.
2. Click the **ACP Client** icon in the Activity Bar (or `ACP: Open Chat Panel`).
3. `ACP: Connect to Agent` → **Hermes Agent**. The agent process is
   `/home/yzhengfeng/.local/bin/hermes acp`, one per VS Code window.
4. Type in the chat panel. File edits appear as diffs, shell commands as terminal blocks,
   and each tool call asks for approval unless `acp.autoApprovePermissions` is changed.
5. `ACP: Set Agent Model` switches the model for the session; `ACP: Set Agent Mode`
   switches Hermes' mode if the adapter offers several.
6. `ACP: Attach File to Prompt` sends a file as context.
7. `ACP: Restart Agent` after changing `~/.hermes/config.yaml` (the adapter reads it at
   start). `ACP: Disconnect Agent` stops the process.

Sessions started from VS Code are ordinary Hermes sessions: `hermes sessions list` shows
them and `hermes --continue` can resume them from a terminal.

## 5. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| No Hermes icon after installing "Hermes Extension for Visual Studio Code" | that is the NASA JPL telemetry extension (section 0) | uninstall it, install `formulahendry.acp-client` |
| `spawn hermes ENOENT` or "agent exited" in `ACP: Show Log` | VS Code (snap) cannot find `hermes` on its PATH | use the full path entry from section 2 |
| Agent connects but the first prompt fails with a provider error | primary gateway down or key expired; fallback not configured | `hermes -z "ping"` in a terminal to see the real error; set up `model-fallback.md` |
| Changes to `config.yaml` not picked up | adapter started before the edit | `ACP: Restart Agent` or reload the window |
| Every tool call asks for approval | `acp.autoApprovePermissions` is `"ask"` | change it in settings if the workspace is trusted |
| Need to see what was sent on the wire | `acp.logTraffic` is on by default | `ACP: Show Protocol Traffic` |

## 6. Security notes

- The agent runs with your user's permissions in the workspace directory. Keep
  `acp.autoApprovePermissions` at `"ask"` in untrusted repos.
- `ACP: Show Protocol Traffic` includes prompts and file contents; do not paste it into
  tickets without scrubbing.
- The `env.PATH` in the agent entry is intentionally minimal and contains no secrets. API
  keys come from `~/.hermes/config.yaml` and `~/.hermes/.env`, which the adapter loads
  itself.

## 7. Rollback

```bash
cp ~/.config/Code/User/settings.json.bak.hermes ~/.config/Code/User/settings.json
code --uninstall-extension formulahendry.acp-client
```

Then reload the VS Code window.

## History

| Date | Change |
|---|---|
| 2026-09-21 | Created. Uninstalled the JPL extension, installed `formulahendry.acp-client` 0.2.0, added the full-path `acp.agents` entry, verified the ACP handshake from a terminal. |
| 2026-09-22 | fosqa VM (Remote-SSH, Hermes v0.21.4): installed `formulahendry.acp-client` 0.2.0 into the VS Code **server** with `~/.vscode-server/cli/servers/Stable-<hash>/server/bin/code-server --install-extension formulahendry.acp-client` (the extension has a `main` entry and no `extensionKind`, so it runs remote-side and spawns `hermes` on the VM). Because the window is remote, the `acp.agents` entry went into the remote machine settings `~/.vscode-server/data/Machine/settings.json` (backup `settings.json.bak.hermes`) with `command: /home/fosqa/.local/bin/hermes` and the same minimal `env.PATH`; the client-side user settings were not touched. Terminal handshake (section 3) passed at 22:04 PDT with `Loaded env from /home/fosqa/.hermes/.env`. The in-editor connect (`Developer: Reload Window` → `ACP: Connect to Agent` → Hermes Agent) is left to the user. |
