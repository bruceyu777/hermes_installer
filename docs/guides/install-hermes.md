---
title: Hermes Agent: install, check, update and roll back on this workstation
kind: guide
created: 2026-09-21
updated: 2026-09-21
status: current
verified_against: Hermes Agent v0.16.0 (2026.6.5, upstream c6e99ab3), Ubuntu (kernel 7.0.0-30), Python venv under ~/.hermes/hermes-agent/venv, checked on 2026-09-21
summary: How Hermes Agent is installed on this machine, how to confirm it works, how to update it, and how to remove it.
related:
  - ../INDEX.md
  - install-hermes.zh-CN.md
  - vscode-acp.md
  - model-fallback.md
---

# Hermes Agent: install, check, update and roll back on this workstation

Hermes Agent is Nous Research's open-source coding and chat agent. It runs as a CLI, a
TUI, a messaging gateway, and as an ACP server for editors. This guide records how it is
installed here so it can be reproduced on another machine.

State on this workstation (2026-09-21):

| Item | State |
|---|---|
| Version | `Hermes Agent v0.16.0 (2026.6.5)`, upstream commit `c6e99ab3` (2026-06-16) |
| Install method | per-user git installer; code at `~/.hermes/hermes-agent/`, Python venv at `~/.hermes/hermes-agent/venv/` |
| Launcher | `~/.local/bin/hermes` (bash wrapper that execs `venv/bin/hermes`) |
| Data directory | `~/.hermes/` — `config.yaml` (settings), `.env` (API keys), `logs/`, `sessions/`, `state.db` |
| Primary model | `glm-5.3` on the Fortinet fos-ai gateway (`provider: custom`), see `model-fallback.md` |
| ACP mode | `hermes acp --check` reports `Hermes ACP check OK` |

## 1. Install

The official one-line installer clones the repo into `~/.hermes/hermes-agent`, creates a
venv, installs the `[all]` extras (which include ACP), and writes the launcher to
`~/.local/bin/hermes`.

```bash
curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash
```

Behind the corporate proxy the browser step (Playwright + Chromium, about 400 MB) can be
skipped and added later:

```bash
curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash -s -- --skip-browser
# later, if the browser tools are wanted:
hermes acp --setup-browser
```

Make sure `~/.local/bin` is on `PATH` (it is on this machine):

```bash
grep -q '.local/bin' ~/.bashrc || echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
```

Other layouts the installer supports, for reference:

| Installer | Code lives at | `hermes` binary | Data directory |
|---|---|---|---|
| `pip install hermes-agent[all]` | Python site-packages | `~/.local/bin/hermes` | `~/.hermes/` |
| Per-user git installer (**this machine**) | `~/.hermes/hermes-agent/` | `~/.local/bin/hermes` | `~/.hermes/` |
| Root mode (`sudo curl … \| sudo bash`) | `/usr/local/lib/hermes-agent/` | `/usr/local/bin/hermes` | `/root/.hermes/` |

## 2. Configure

First-time setup is interactive:

```bash
hermes setup          # full wizard: provider, model, tools, gateway
hermes model          # only pick provider + model
```

On this machine the provider is a custom OpenAI-compatible endpoint. The relevant part of
`~/.hermes/config.yaml` (key redacted):

```yaml
model:
  default: glm-5.3
  provider: custom
  base_url: https://fos-ai.fortinet.com:443/v1
  api_key: <redacted>
  context-length: 262144
```

Secrets: the fos-ai key is inline in `config.yaml` (written by `hermes model`); the Qwen
key for the fallback is in `~/.hermes/.env` as `LOCAL_QWEN_API_KEY`. Both files are mode
600. `hermes config env-path` prints the `.env` path, `hermes config path` the YAML path.

## 3. Verify

```bash
hermes --version                       # Hermes Agent v0.16.0 (2026.6.5) · upstream c6e99ab3
hermes doctor                          # environment, install method, missing deps
hermes acp --check                     # Hermes ACP check OK
cd "$(mktemp -d)" && /usr/bin/time -f "%es" hermes -z "Reply with exactly: PONG"
```

Observed 2026-09-21 16:27 PDT: `PONG-A` in 3.76 s (one-shot mode includes agent startup
and plugin registration). The session is recorded in `~/.hermes/state.db` with
`billing_base_url = https://fos-ai.fortinet.com:443/v1`.

Note the flag order: `-z`, `-m` and `--provider` are global options and go **before** any
subcommand (`hermes -z "..."`, not `hermes chat -z "..."`).

## 4. Use

| Want | Command |
|---|---|
| Interactive chat in the terminal | `hermes` or `hermes chat` |
| One-shot prompt, print answer, exit | `hermes -z "prompt"` |
| Pick a different model for one run | `hermes -m <model> -z "prompt"` |
| Resume the last session | `hermes --continue` |
| List past sessions | `hermes sessions list` |
| Show status of all components | `hermes status` |
| View logs | `hermes logs`, `hermes logs errors`, `hermes logs -f` |
| Manage the fallback chain | `hermes fallback` (see `model-fallback.md`) |
| Editor integration | `hermes acp` (see `vscode-acp.md`) |

## 5. Update

`hermes update` detects the install method and prints or runs the matching update. For the
git layout it is equivalent to:

```bash
cd ~/.hermes/hermes-agent
git pull
uv pip install -e ".[all]"     # or: venv/bin/pip install -e ".[all]"
hermes --version
```

Back up first, because `config.yaml` migrations run on the next start:

```bash
hermes backup                  # or: cp -p ~/.hermes/config.yaml ~/.hermes/config.yaml.bak-$(date +%Y%m%d-%H%M%S)
```

## 6. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `hermes: command not found` | `~/.local/bin` not on PATH in that shell (common for snap-launched apps and service accounts) | use the full path `/home/yzhengfeng/.local/bin/hermes` or add the PATH export to `~/.bashrc` |
| `unrecognized arguments: -z ...` | global flag placed after a subcommand | `hermes -z "..."`, not `hermes chat -z "..."` |
| `No LLM provider configured` | `model:` block missing or key empty | `hermes model` |
| `Provider 'custom' ... no API key was found` | `api_key` missing in `model:` and no fallback could be resolved | re-run `hermes model`, or check the fallback chain (`model-fallback.md`) |
| `[Email] IMAP fetch error: AUTHENTICATIONFAILED` in `agent.log` | the email gateway in `.env` has stale credentials; harmless for CLI/ACP use | fix `EMAIL_*` in `~/.hermes/.env` or remove them |
| Slow first prompt (3–5 s) | plugin registration + provider discovery at startup | expected in one-shot mode; interactive and ACP sessions pay it once |

## 7. Security notes

- `~/.hermes/config.yaml` and `~/.hermes/.env` hold API keys and are mode 600. Never copy
  them into this docs folder.
- `hermes config show` prints the config; check for `api_key` before pasting it anywhere.
- The `.env` also contains Telegram and email credentials for the messaging gateway.
- `hermes security` lists the security-related settings (command approvals, allowlists).

## 8. Rollback / uninstall

```bash
hermes uninstall               # removes the code and launcher; asks about ~/.hermes
# or manually:
rm -rf ~/.hermes/hermes-agent ~/.local/bin/hermes     # keep ~/.hermes/config.yaml and .env if reinstalling
```

## History

| Date | Change |
|---|---|
| 2026-09-21 | Created. Recorded the existing git-installer layout, verified `--version`, `acp --check` and a one-shot prompt. |
| 2026-09-22 | On the fosqa VM the same layout was updated from v0.14.0 to v0.21.4 (git remote switched to HTTPS because SSH to GitHub hangs there); keys live in `.env` only. See `fosqa-vm-llm-chain.md`. |
