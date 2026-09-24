# Hermes Agent team config

A ready-to-use [Hermes Agent](https://hermes-agent.nousresearch.com) setup for FortiOS QA
VMs. It connects Fortinet's internal LLM gateways with automatic fallback, plus the Jenkins,
Mantis and Log Intelligence MCP servers, curated the same way as the Log Intelligence AI
Assistant (and the opencode config). It works in a terminal and in VS Code.
Clone it, add your own tokens, run one script. 简体中文: [README.zh-CN.md](README.zh-CN.md)

## Quick start

```bash
git clone git@github.com:bruceyu777/hermes_installer.git ~/git/hermes && cd ~/git/hermes
cp tokens.env.example tokens.env && chmod 600 tokens.env
vi tokens.env           # your own tokens; the file is git-ignored
./install.sh --cron     # installs Hermes v0.21.4 if missing, deploys the config, checks everything,
                        # and re-measures the models every hour
```

Already use the opencode config? Its `tokens.env` has the same names: `cp ~/git/opencode/tokens.env .`

Then run `hermes` in a terminal, or in VS Code run **Developer: Reload Window**, then
**ACP: Connect to Agent** → Hermes Agent (Remote-SSH works). Update later with
`git pull && ./install.sh`. Your tokens are kept.

## What the config gives you

| Piece | Setting |
|---|---|
| Model chain | Measured, not fixed. For each key, `install.sh` sends a one-token chat call to the models in its `prefer` list ([config/model-preferences.yaml](config/model-preferences.yaml)) and keeps the first ones that answer: personal fos-ai key first, then the second fos-ai key, then the internal Qwen gateway. On 2026-09-23 that gave `glm-5.3-flash` → `glm-5.3` → `qwen3.6-35b-a3b` → `qwen3.5-122b-a10b-awq`. |
| Keeping up with the gateway | The gateway changes what each key may call without notice. `./install.sh --adapt` re-measures and rewrites the chain; `--cron` runs that every hour. A model the gateway starts offering is only reported ("new models seen") until you add it to a `prefer` list. |
| Fallback | Hermes' built-in `fallback_providers`. A 401, 403, 404, 429 or 5xx on one model moves the turn to the next. |
| MCP servers | `jenkins` (10 tools), `mantis-tools` (8 tools), `logintel` (26 read-only tools), `microsoft365` (OAuth, optional, untested). Other tools of those servers are hidden from the model. |
| Guard rails | Filing a Mantis bug, adding a Mantis note, sending email, and starting or re-running a Jenkins build ask before running (plugin [`mcp-ask-first`](config/plugins/mcp-ask-first/__init__.py)). In the terminal it is a prompt; in VS Code a permission request. In runs with nobody to ask (`hermes -z`, `hermes chat -q`, cron) they are blocked. |
| Secrets | None in `config.yaml`. Every token is in `~/.hermes/.env` (mode 600) and referenced by name. |

## Tokens

Fill in what you have in `tokens.env`. Leave the rest empty.

| Variable | Used for | Where to get it | If empty |
|---|---|---|---|
| `FOS_AI_API_KEY` | primary model | your personal key for the fos-ai gateway (AI team) | the chain starts at the second key |
| `FOS_AI_FALLBACK_API_KEY` | fallback 1 | a second fos-ai key with its own budget, for example a project token | reuses `FOS_AI_API_KEY` |
| `LOCAL_QWEN_API_KEY` | last fallbacks | key for the internal Qwen gateway `releaseqa-aiserver` (QA infra team) | local models unavailable |
| `MANTIS_MCP_TOKEN` | Mantis MCP | your fos-qa-assistant API token, the one you paste into the Log Intelligence AI Assistant under "My connections" | Mantis server disabled |
| `JENKINS_MCP_TOKEN` | Jenkins MCP | `user:apitoken`. In Jenkins: your user name, Configure, API Token, Add new token | Jenkins server disabled |
| `LOGINTEL_MCP_KEY` | Log Intelligence MCP | a personal key (starts with `limcp_`) from the Log Intelligence admin | Log Intelligence server disabled |

Microsoft 365 needs no token: run `hermes mcp login microsoft365` once in a terminal that can
open a browser, then `./install.sh` again (it turns the server on when a login is stored).
This one has not been tested.

To rotate a token, change it in `tokens.env` and run `./install.sh` again.

## What `install.sh` changes

| Where | Change |
|---|---|
| Hermes itself | if `hermes` is missing: the official installer pinned to the tested commit, without the setup wizard, browser tools and Computer Use. If older than v0.21.4: `hermes update --yes`. Otherwise left alone. |
| `~/.hermes/.env` | your tokens, one line each; other lines are kept. Previous file saved as `.env.bak-<time>`. |
| `~/.hermes/config.yaml` | only `model`, `fallback_providers`, this repo's four `mcp_servers` entries and `plugins.enabled`; comments and every other key are kept. Previous file saved as `config.yaml.bak-<time>`. |
| `~/.hermes/plugins/mcp-ask-first/` | copied |
| VS Code, if present | installs the ACP Client extension (`formulahendry.acp-client`) and adds `acp.agents["Hermes Agent"]` with the full path of `hermes`, unless a working entry exists |
| crontab (`--cron` only) | `17 * * * * … install.sh --adapt`, log in `~/.hermes/logs/hermes_installer_adapt.log` |
| Check | lists and connects the MCP servers, checks the plugin, then for each model of the chain a direct gateway call and one Hermes prompt from a throwaway home |

| Option | Effect |
|---|---|
| `--adapt` | only re-measure the models and rewrite the chain |
| `--cron` | also add the hourly `--adapt` to your crontab |
| `--check` | only run the check |
| `--no-install` | leave the Hermes install alone |
| `--no-vscode` | leave VS Code alone |
| `--no-check` | skip the check |
| `--tokens FILE` | read tokens from another file |

`HERMES_HOME` is honoured (default `~/.hermes`).

## Limits worth knowing

- **Curation is not a sandbox.** Hidden tools and ask-first stop the model from using the MCP
  *tools*. The model still runs shell commands as you and can read `~/.hermes/.env`, so it
  could call a server directly. On 2026-09-23 an opencode session on the same VM did exactly
  that with the Mantis token. Watch what it runs.
- **"Allow always"** in the approval prompt stores a permanent allow rule for that tool in
  `config.yaml` (`command_allowlist`). Prefer "allow once" or "allow for this session".
- **`approvals.mode: off`** (or `--yolo`) turns the ask-first prompts off along with every
  other approval.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Check shows `gateway refused: HTTP 403 … does not permit model` | the gateway took that model away from the key | `./install.sh --adapt`; if no preferred model answers, add one the key offers (see "new models seen") to its `prefer` list |
| Check shows `refused: HTTP 429 … budget` | the key is out of budget for this time block | nothing: the chain moves on; `--adapt` picks it up again later |
| An MCP server shows `✗` or an error | wrong token, or the server is down | `hermes mcp test <name>`; fix the token in `tokens.env`, `./install.sh` |
| The approval prompt never shows and the tool is blocked | `hermes -z` / `chat -q` run: nobody to ask | run the request in an interactive session |
| Config changes not visible in VS Code | the agent was started before the change | `ACP: Restart Agent`, or reload the window |
| `hermes model` shows a z.ai provider you never set up | a `GLM_API_KEY` line in `~/.hermes/.env` (Hermes reads that name as z.ai) | remove it; this repo never uses that name |

## Maintaining this repo

- **Models:** [config/model-preferences.yaml](config/model-preferences.yaml). The chain in
  `config/config.yaml` is only the starting point for a brand-new config.
- **MCP tool curation** mirrors the Log Intelligence AI Assistant and the opencode config
  (`tools.include` in `config/config.yaml`, `ASK_FIRST_TOOLS` in the plugin). Change all
  three together.
- **Hermes upgrades:** test the new version, then change `TESTED_HERMES_VERSION` and
  `TESTED_HERMES_COMMIT` in `install.sh`.
- **Never commit** `tokens.env`. `.gitignore` excludes it.

## Docs

| Doc | English | 简体中文 |
|---|---|---|
| Install on another machine (node16): steps, tests, terminal use, tips and traps | [docs/guides/install-on-a-node.md](docs/guides/install-on-a-node.md) | [docs/guides/install-on-a-node.zh-CN.md](docs/guides/install-on-a-node.zh-CN.md) |
| This VM: v0.21.4, LLM chain, MCP servers, adapter | [docs/guides/fosqa-vm-llm-chain.md](docs/guides/fosqa-vm-llm-chain.md) | [docs/guides/fosqa-vm-llm-chain.zh-CN.md](docs/guides/fosqa-vm-llm-chain.zh-CN.md) |
| Install, update, roll back Hermes | [docs/guides/install-hermes.md](docs/guides/install-hermes.md) | [docs/guides/install-hermes.zh-CN.md](docs/guides/install-hermes.zh-CN.md) |
| VS Code: ACP Client extension | [docs/guides/vscode-acp.md](docs/guides/vscode-acp.md) | [docs/guides/vscode-acp.zh-CN.md](docs/guides/vscode-acp.zh-CN.md) |
| Original fallback design (other workstation) | [docs/guides/model-fallback.md](docs/guides/model-fallback.md) | [docs/guides/model-fallback.zh-CN.md](docs/guides/model-fallback.zh-CN.md) |

Index of all docs: [docs/INDEX.md](docs/INDEX.md).
