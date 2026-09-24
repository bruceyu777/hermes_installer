# docs index

Management view of every document in this project: where it lives, when it was last
touched, whether it is still current. Update this table in the same change as the doc.
Dates are ISO `YYYY-MM-DD`.

## Layout

| Folder | Holds | Lifetime |
|---|---|---|
| `docs/guides/` | setup and how-to guides for Hermes Agent on this workstation (install, VS Code, fallback) | living, edit in place, re-verify dates |
| `docs/templates/` | the template for each kind above | edit when the process changes |
| repo root (outside `docs/`) | the shareable setup: `README.md` / `README.zh-CN.md`, `install.sh`, `tokens.env.example`, `config/config.yaml` (managed part of `config.yaml`), `config/model-preferences.yaml` (measured model chain), `config/plugins/mcp-ask-first/`, `scripts/merge_into_hermes_home.py` | living; `install.sh` is the source of truth for what a VM gets |

Other folders from the standard layout (`design/`, `dataflow/`, `review/`, `bugfix/`,
`decisions/`) are added when the first document of that kind is written.

Rules: every doc has frontmatter with `created`, `updated`, `status`, and a History table at
the end. A doc that no longer matches the machine is marked `status: superseded` with a
pointer, not deleted. New docs come from `docs/templates/`.

## Documents

| Doc | Kind | Created | Updated | Status | Summary |
|---|---|---|---|---|---|
| [guides/install-hermes.md](guides/install-hermes.md) | guide | 2026-09-21 | 2026-09-21 | current | how Hermes Agent is installed on this machine (git installer, `~/.hermes/hermes-agent`), how to check, update and roll back it (English) |
| [guides/install-hermes.zh-CN.md](guides/install-hermes.zh-CN.md) | guide | 2026-09-21 | 2026-09-21 | current | same content in Simplified Chinese |
| [guides/vscode-acp.md](guides/vscode-acp.md) | guide | 2026-09-21 | 2026-09-22 | current | install the ACP Client extension, register Hermes in `settings.json`, connect and chat from VS Code; why the marketplace "Hermes" extension is the wrong one; 2026-09-22 row covers the Remote-SSH variant on the fosqa VM (server-side install, remote machine settings) (English) |
| [guides/vscode-acp.zh-CN.md](guides/vscode-acp.zh-CN.md) | guide | 2026-09-21 | 2026-09-22 | current | same content in Simplified Chinese |
| [guides/model-fallback.md](guides/model-fallback.md) | guide | 2026-09-21 | 2026-09-21 | current | built-in `fallback_providers` chain from fos-ai glm-5.3 to the internal Qwen models, key in `.env`, verified with a live failover (English) |
| [guides/model-fallback.zh-CN.md](guides/model-fallback.zh-CN.md) | guide | 2026-09-21 | 2026-09-21 | current | same content in Simplified Chinese |
| [guides/fosqa-vm-llm-chain.md](guides/fosqa-vm-llm-chain.md) | guide | 2026-09-22 | 2026-09-23 | current | fosqa VM: update of the May checkout to v0.21.4, keys in `~/.hermes/.env`, three-tier chain; §7 (2026-09-23): MCP servers with the AI Assistant's curation, `mcp-ask-first` plugin, per-key measured model chain with hourly `--adapt`, the `hermes_installer` repo and its tests (English) |
| [guides/fosqa-vm-llm-chain.zh-CN.md](guides/fosqa-vm-llm-chain.zh-CN.md) | guide | 2026-09-22 | 2026-09-23 | current | same content in Simplified Chinese |
| [templates/guide.md](templates/guide.md) | template | 2026-09-17 | 2026-09-17 | current | template for guides (copied from the opencode project) |

## History

| Date | Change |
|---|---|
| 2026-09-21 | Created with the install, VS Code ACP and model-fallback guides in English and Chinese. |
| 2026-09-22 | Added the fosqa-VM guide (v0.21.4 update + three-tier fos-ai/Qwen chain) in English and Chinese; pointer rows added to the install and model-fallback guides; VS Code ACP guides gained the Remote-SSH row for the fosqa VM. |
| 2026-09-23 | Made the folder the shareable `hermes_installer` repo: `install.sh` (install, tokens, config merge, plugin, VS Code, measured model chain, `--adapt`/`--cron`, check), `tokens.env.example`, `config/`, `scripts/`, `README.md` / `README.zh-CN.md` rewritten as config summary + quick start. fosqa-VM guides gained §7 in both languages. |
