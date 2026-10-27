---
title: Hermes Agent: chat with it from your phone through a Telegram bot
kind: guide
created: 2026-10-20
updated: 2026-10-20
status: current
verified_against: Hermes Agent v0.16.0 (gateway as systemd user service) on the workstation, Ubuntu (kernel 7.0.0-38), bot @nm_hermes_test_bot, 2026-10-20; the same install.sh path on a VM with v0.21.4 is not yet run
summary: Create a bot with @BotFather, put its token and your numeric user ID in tokens.env, run install.sh; it installs the Hermes messaging gateway as a user service and restarts it, and the bot answers in Telegram. Verification, group use, troubleshooting, what the token can do.
related:
  - ../INDEX.md
  - telegram.zh-CN.md
  - install-hermes.md
  - install-on-a-node.md
  - ../../install.sh
  - ../../tokens.env.example
---

# Hermes Agent: chat with it from your phone through a Telegram bot

Hermes Agent has a **messaging gateway**: one long-running process (`hermes gateway run`)
that connects the agent to Telegram, Discord, Slack, WhatsApp and others. This repo wires up
the Telegram part only. Once it runs, you talk to the same agent, with the same model chain,
MCP servers and ask-first rules, from the Telegram app on any device. Voice messages are
transcribed, images and files are passed to the agent, and scheduled tasks (`hermes cron`)
can deliver their results to your chat.

State this guide leaves the machine in:

| Item | State |
|---|---|
| Bot | one bot per person, created with @BotFather; its token in `~/.hermes/.env` as `TELEGRAM_BOT_TOKEN` |
| Access | only the numeric user IDs in `TELEGRAM_ALLOWED_USERS` get an answer; everyone else is ignored |
| Home channel | `TELEGRAM_HOME_CHANNEL` (defaults to your ID): where cron results and gateway notices go |
| Service | `~/.config/systemd/user/hermes-gateway.service`, enabled, with linger, so it survives logout and reboots |
| Transport | long polling from the gateway to `api.telegram.org` (outbound HTTPS only, nothing listens) |
| Check | `./install.sh --check` validates the token, shows the service state and the Telegram connection |

Do **not** set it up on a shared test node (nodeNN): the gateway is a process that runs as you
around the clock, and a bot that can run shell commands on a Jenkins node is nobody's idea of a
good time. Use your own VM or workstation.

## 1. Create the bot (Telegram side, 2 minutes)

1. In Telegram, open **@BotFather** (https://t.me/BotFather) and send `/newbot`.
2. Give it a display name (anything) and a username that ends in `bot` and is still free,
   for example `yourname_hermes_bot`.
3. BotFather answers with the token: `123456789:ABCdefGHIjklMNOpqrSTUvwxYZ`. Treat it like a
   password. If it ever leaks: `/revoke` in BotFather and run `install.sh` again with the new one.
4. Your **numeric user ID**: message **@userinfobot**; it replies with a number like
   `7648384944`. The `@username` is not it.

Optional, also in BotFather: `/setcommands` with

```
help - Show help information
new - Start a new conversation
sethome - Set this chat as the home channel
```

## 2. Configure

### With this repo (recommended)

In `tokens.env`:

```bash
TELEGRAM_BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrSTUvwxYZ
TELEGRAM_ALLOWED_USERS=7648384944        # comma-separated for several people
TELEGRAM_HOME_CHANNEL=                   # empty = the single ID above
```

then `./install.sh`. What it does, in order ([install.sh](../../install.sh), function
`configure_gateway`):

| Step | Command it runs | Notes |
|---|---|---|
| tokens | `scripts/merge_into_hermes_home.py tokens` | the three names go to `~/.hermes/.env`; IDs must be digits and commas, anything else is reported and ignored; an empty home channel becomes the single allowed ID |
| service | `hermes gateway install` (answers "y" to its two questions) | writes the user unit, `systemctl --user enable`, enables linger. A second run prints `Service already installed` and changes nothing |
| restart | `hermes gateway restart` | the gateway reads `.env` at start, so this is what applies a new token or ID |
| check | `./install.sh --check`, section "Check: Telegram bot" | see section 3 |

With all three Telegram lines empty the step prints `no TELEGRAM_BOT_TOKEN: gateway left alone`
and nothing else happens. `--no-gateway` skips the step even with a token.

### By hand (no repo)

```bash
cat >> ~/.hermes/.env <<'EOT'
TELEGRAM_BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrSTUvwxYZ
TELEGRAM_ALLOWED_USERS=7648384944
TELEGRAM_HOME_CHANNEL=7648384944
EOT
chmod 600 ~/.hermes/.env
hermes gateway install        # user service, answer Y twice
hermes gateway restart
```

Or `hermes gateway setup` and pick Telegram: an interactive wizard that asks for the same two
values. Without systemd (WSL1, a container, Termux) run `hermes gateway run` in `tmux` instead
of installing a service.

## 3. Verify

Observed on the workstation on 2026-10-20 (`./install.sh --check`, Telegram section; the
service had been installed by hand earlier, so the install step reported it as already there):

```
==> Gateway: Telegram bot
    Service already installed at: /home/yzhengfeng/.config/systemd/user/hermes-gateway.service
    ✓ User service started
    gateway restarted (it re-reads .env on start)
…
==> Check: Telegram bot
    token ok: bot @nm_hermes_test_bot
    allowed users: 1 ID(s), home channel set
    Active: active (running) since Tue 2026-10-20 14:45:32 PDT; 22s ago
    ✓ User gateway service is running
    ✓ Systemd linger is enabled (service survives logout)
    telegram: connected (gateway running, updated 2026-10-20T21:45:40)
```

The four lines mean: the token is valid at `api.telegram.org` (the check calls `getMe` and
prints only the bot name); the service is up; and the gateway's own status file
(`~/.hermes/gateway_state.json`) says the Telegram connection is `connected`. Right after a
restart that last line can still read `connecting` or show the previous timestamp: it took
about 8 seconds here.

Then the real test: open the bot in Telegram, send *hello*. The first answer takes a few
seconds (model call). By hand:

```bash
hermes gateway status                     # service, linger, platform states
journalctl --user -u hermes-gateway -f    # live log; look for "[telegram] Connected"
```

## 4. Use

| In the chat | Effect |
|---|---|
| any text | a turn of the agent, same tools and approvals as in the terminal; ask-first tools (Mantis filing, email, Jenkins builds) ask in the chat; reply `yes` or `no`. Questions from the agent (`clarify`) come with buttons |
| `/new` | start a fresh session (context is kept per chat otherwise) |
| `/sethome` | make this chat the home channel (writes `TELEGRAM_HOME_CHANNEL`) |
| `/help` | the gateway's command list |
| voice message | transcribed, then handled as text |
| photo or file | handed to the agent; it can read PDFs, logs, images |
| a reply containing `MEDIA:/path/on/host` | the agent sends that file to you as an attachment (png, pdf, log, csv, docx, zip, …) |

Scheduled tasks: `hermes cron` jobs created in a terminal deliver their result to the home
channel, so "every morning at 8, summarise last night's failed builds" lands on your phone.

**Groups.** Telegram bots start with *privacy mode* on: in a group the bot only sees
`/commands` and replies to its own messages. Either turn it off in BotFather (`/mybots` →
your bot → Bot Settings → Group Privacy → Turn off, then **remove and re-add the bot** to the
group) or make the bot a group admin. Group chats also need their chat ID in
`telegram.allowed_chats` in `config.yaml`; this repo does not manage that key.

## 5. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Check: `token refused: Unauthorized` | token mistyped or revoked | copy it again from BotFather (`/mybots` → API Token) into `tokens.env`, `./install.sh` |
| Check: `no answer from api.telegram.org` | the machine cannot reach Telegram (corporate egress) | `curl -sS -m 10 https://api.telegram.org` from that machine; if blocked, `telegram: proxy_url: "socks5://host:port"` in `config.yaml` or `TELEGRAM_PROXY=socks5://host:port` in `.env`, then `hermes gateway restart` |
| Check: `hermes gateway install failed: User systemd not reachable` | fresh SSH session without a user D-Bus (no linger yet) | `loginctl enable-linger $USER` (may need sudo once), log out and in, `./install.sh` again; or `tmux new -s hermes 'hermes gateway run'` |
| Bot never answers, no error | your ID is not in `TELEGRAM_ALLOWED_USERS` (wrong number, or `@username` instead of the ID) | ask @userinfobot again, fix `tokens.env`, `./install.sh`. Unknown users are silently ignored |
| Bot answered before, stopped after a token change | the gateway still holds the old `.env` | `hermes gateway restart` (install.sh does this; by hand you must) |
| `telegram: retrying … Conflict: terminated by other getUpdates request` | the same token is polled by two gateways (two machines, or a stray `hermes gateway run`) | stop the other one: one bot token per running gateway |
| In a group the bot ignores everything but `/commands` | privacy mode | section 4, Groups |
| Service state `activating (auto-restart)` | the gateway crashes at start, usually a config error | `journalctl --user -u hermes-gateway --since '5 min ago'` |

## 6. Security notes

- **The token is the bot.** Anyone holding it can read and send as the bot; combined with the
  allowlist they still cannot make Hermes run anything unless their user ID is listed, but keep
  it in `~/.hermes/.env` (mode 600) and nowhere else. `install.sh` and the check never print it.
- **The allowlist is the only gate.** A listed user can do everything you can do in the
  terminal: shell as your user, your MCP tokens, your files. List yourself and people you would
  hand your shell to. `hermes pairing` (one-time codes) is an alternative the Hermes docs
  describe; this repo keeps the static list because it is reviewable.
- **Ask-first still applies** (plugin `mcp-ask-first`): Mantis filing, email and Jenkins
  triggers ask in the chat and run only after you reply `yes`. Do not run the gateway with
  `approvals.mode: off`.
- **Outbound only.** Long polling opens no port. Webhook mode (`TELEGRAM_WEBHOOK_URL`) is for
  cloud hosts that sleep; it is not used here.
- **Revocation** is one BotFather command (`/revoke`) plus `./install.sh` with the new token.

## History

| Date | Change |
|---|---|
| 2026-10-20 | Created. install.sh gained the gateway step, `--no-gateway` and the Telegram check; tokens.env gained the three Telegram lines; verified on the workstation with bot @nm_hermes_test_bot (gateway service already present, restart and reconnect in 8 s). |
