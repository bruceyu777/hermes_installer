#!/usr/bin/env bash
# install.sh — install Hermes Agent and deploy this repo's shared config.
#
#   cp tokens.env.example tokens.env && chmod 600 tokens.env   # your own tokens
#   ./install.sh            install Hermes if needed, deploy tokens, config and plugin, verify
#   ./install.sh --check    only verify an existing install
#   ./install.sh --adapt    only re-measure the models each key can call and rebuild the chain
#                           (quiet enough for cron, e.g. hourly: see README)
#
# Options:
#   --tokens FILE   token file (default: tokens.env next to this script)
#   --no-install    leave the Hermes install alone
#   --no-vscode     leave VS Code alone (ACP Client extension + Hermes entry)
#   --no-gateway    leave the messaging gateway alone (Telegram bot service)
#   --profile NAME  deploy into the Hermes profile ~/.hermes/profiles/NAME (created first with
#                   `hermes profile create NAME --no-skills --description '...'`); applies
#                   config/profiles/NAME/ (SOUL.md, overlay.yaml, local skills); implies --no-install --no-vscode
#   --host NAME     which host of config/hosts.yaml this is (else HERMES_HOST in tokens.env, else auto-detect)
#   --no-check      skip the verification at the end
#   --check         only run the verification
#   --adapt         only probe the models and update the chain in config.yaml
#   --cron          also put `install.sh --adapt` in your crontab every 15 min (idempotent; with --adapt: only that)
#
# Works on $HERMES_HOME (default ~/.hermes). Safe to re-run after every `git pull`:
# tokens already in $HERMES_HOME/.env are kept unless tokens.env gives a new value.
# With a TELEGRAM_BOT_TOKEN the gateway is installed as a systemd user service and
# restarted, so the bot answers from your phone (docs/guides/telegram.md).
# Nothing secret is printed.
set -euo pipefail

TESTED_HERMES_VERSION="0.21.4"
TESTED_HERMES_COMMIT="c0d7294769a38c17ceae51d8f7995e66e1dcae27"
HERMES_INSTALLER_URL="https://hermes-agent.nousresearch.com/install.sh"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOKENS_FILE="$REPO_DIR/tokens.env"
DO_INSTALL=1; DO_VSCODE=1; DO_GATEWAY=1; DO_CHECK=1; CHECK_ONLY=0; ADAPT_ONLY=0; DO_CRON=0
PROFILE=""; HOST_NAME=""

while [ $# -gt 0 ]; do
  case "$1" in
    --tokens)     TOKENS_FILE="$2"; shift 2 ;;
    --no-install) DO_INSTALL=0; shift ;;
    --no-vscode)  DO_VSCODE=0; shift ;;
    --no-gateway) DO_GATEWAY=0; shift ;;
    --no-check)   DO_CHECK=0; shift ;;
    --check)      CHECK_ONLY=1; shift ;;
    --adapt)      ADAPT_ONLY=1; shift ;;
    --cron)       DO_CRON=1; shift ;;
    --profile)    PROFILE="$2"; DO_INSTALL=0; DO_VSCODE=0; shift 2 ;;
    --host)       HOST_NAME="$2"; shift 2 ;;
    -h|--help)    sed -n '2,29p' "$0"; exit 0 ;;
    *) echo "unknown option: $1 (see --help)" >&2; exit 2 ;;
  esac
done

export HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
HP=()   # `hermes` arguments that select the profile (gateway commands need -p; everything else follows HERMES_HOME)
if [ -n "$PROFILE" ]; then
  export HERMES_HOME="$HOME/.hermes/profiles/$PROFILE"
  [ -f "$HERMES_HOME/profile.yaml" ] || { echo "profile '$PROFILE' does not exist; create it first: hermes profile create $PROFILE --no-skills --description '...'" >&2; exit 1; }
  HP=(-p "$PROFILE")
fi
export PATH="$HOME/.local/bin:$PATH"
HELPER="$REPO_DIR/scripts/merge_into_hermes_home.py"
FRAGMENT="$REPO_DIR/config/config.yaml"
PREFERENCES="$REPO_DIR/config/model-preferences.yaml"
PLUGIN_NAMES=(mcp-ask-first chain-adapt)   # config/plugins/<name>/ -> $HERMES_HOME/plugins/<name>/
HOSTS_FILE="$REPO_DIR/config/hosts.yaml"
HOST_SKILL="host-environment"              # config/skills/<name>/ -> $HERMES_HOME/skills/<name>/
PLACEHOLDER="NOT_SET"
HERMES_PYTHON=""

say()  { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
info() { printf '    %s\n' "$*"; }
warn() { printf '\033[33m    !! %s\033[0m\n' "$*"; }
need() { command -v "$1" >/dev/null 2>&1 || { echo "missing required command: $1" >&2; exit 1; }; }
strip_ansi() { sed 's/\x1b\[[0-9;]*m//g'; }
need curl; need python3

hermes_version() { hermes --version 2>/dev/null | grep -oE 'v[0-9]+\.[0-9]+\.[0-9]+' | head -1 | tr -d v || true; }

find_hermes_python() {   # the Python of the Hermes venv (it has ruamel.yaml, which the helper needs)
  local install_dir candidate
  install_dir="$(hermes --version 2>/dev/null | sed -n 's/^Install directory: //p' | head -1 || true)"
  for candidate in "$install_dir/venv/bin/python" "$HERMES_HOME/hermes-agent/venv/bin/python" "$HOME/.hermes/hermes-agent/venv/bin/python" \
                   /usr/local/lib/hermes-agent/venv/bin/python; do
    if [ -x "$candidate" ]; then HERMES_PYTHON="$candidate"; return; fi
  done
  echo "cannot find the Python of the Hermes install: is Hermes installed? (run without --no-install)" >&2
  exit 1
}

env_value() {   # $1 = name -> its value in $HERMES_HOME/.env, empty if absent
  [ -f "$HERMES_HOME/.env" ] || return 0
  sed -n -E "s/^[[:space:]]*(export[[:space:]]+)?$1[[:space:]]*=[[:space:]]*//p" "$HERMES_HOME/.env" \
    | tail -1 | sed -E "s/^[\"'](.*)[\"']\$/\1/"
}

# The official installer runs `sudo apt install ripgrep` / `libatomic1` with NEEDRESTART_MODE=a
# whenever passwordless sudo exists. On a shared test node Ubuntu's needrestart then restarts
# every service with a replaced library — including jenkins-agent.service, which killed the
# autotest.py run in progress on node15 and node16 on 2026-09-23 and hung both pipelines for
# 48 h (jenkins-master docs/bug_fix/2026-09-24_agent-restart-kills-autotest-pipeline-hangs.md).
telegram_token() {   # the bot token in $HERMES_HOME/.env, empty if none
  local value; value="$(env_value TELEGRAM_BOT_TOKEN)"
  [ "$value" = "$PLACEHOLDER" ] && value=""
  printf '%s' "$value"
}

guard_shared_node() {   # refuse to install while a test is running here
  if pgrep -f 'autotest\.py' >/dev/null 2>&1 || [ -e /run/lock/fortistack-node.lock ]; then
    echo "a test is running on this node (autotest.py / fortistack-node.lock); install Hermes between builds" >&2
    exit 1
  fi
}

preinstall_system_packages() {   # so the official installer finds them and skips its own apt calls
  command -v apt-get >/dev/null 2>&1 || return 0
  sudo -n true 2>/dev/null || return 0        # no passwordless sudo: upstream skips apt too
  local missing=()
  command -v rg >/dev/null 2>&1 || missing+=(ripgrep)
  ldconfig -p 2>/dev/null | grep -q 'libatomic\.so\.1' || missing+=(libatomic1)
  [ ${#missing[@]} -eq 0 ] && return 0
  info "installing ${missing[*]} with NEEDRESTART_MODE=l (list only: no service is restarted)"
  sudo NEEDRESTART_MODE=l DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${missing[@]}" >/dev/null \
    || warn "could not install ${missing[*]}; the official installer may try again and restart services"
}

# ── 1. Hermes itself ──────────────────────────────────────────────────────────
install_hermes() {
  say "Hermes Agent (tested: v$TESTED_HERMES_VERSION)"
  local version
  version="$(hermes_version)"
  if [ -z "$version" ]; then
    guard_shared_node
    preinstall_system_packages
    info "not installed: running the official installer, pinned to commit ${TESTED_HERMES_COMMIT:0:10}"
    info "(skips the setup wizard, the browser tools and Computer Use; add them later with 'hermes setup')"
    curl -fsSL "$HERMES_INSTALLER_URL" | bash -s -- --skip-setup --skip-browser --skip-computer-use \
      --commit "$TESTED_HERMES_COMMIT" --hermes-home "$HERMES_HOME"
    hash -r
    version="$(hermes_version)"
    [ -n "$version" ] || { echo "the Hermes installer finished but 'hermes' does not run" >&2; exit 1; }
  elif [ "$(printf '%s\n%s\n' "$version" "$TESTED_HERMES_VERSION" | sort -V | head -1)" != "$TESTED_HERMES_VERSION" ]; then
    info "found v$version, older than the tested version: running 'hermes update --yes'"
    hermes update --yes
    hash -r
    version="$(hermes_version)"
  fi
  info "now: v$version at $(command -v hermes)"
}

# ── 2. tokens, config, plugin ─────────────────────────────────────────────────
deploy_tokens() {
  say "Tokens -> $HERMES_HOME/.env"
  "$HERMES_PYTHON" "$HELPER" tokens --tokens-file "$TOKENS_FILE" --hermes-home "$HERMES_HOME"
}

deploy_config() {
  say "Config -> $HERMES_HOME/config.yaml, plugins ${PLUGIN_NAMES[*]}"
  local plugin
  for plugin in "${PLUGIN_NAMES[@]}"; do
    mkdir -p "$HERMES_HOME/plugins/$plugin"
    cp -f "$REPO_DIR/config/plugins/$plugin/plugin.yaml" "$REPO_DIR/config/plugins/$plugin/__init__.py" \
          "$HERMES_HOME/plugins/$plugin/"
  done
  adapt_models
}

# The chain is measured, not fixed: every key gets a one-token chat probe and the chain is
# built from the prefer lists in config/model-preferences.yaml (see the comment there).
adapt_models() {
  info "probing the models each key can call now:"
  "$HERMES_PYTHON" "$HELPER" adapt --preferences "$PREFERENCES" --fragment "$FRAGMENT" --hermes-home "$HERMES_HOME"
}

# ── 2b. this host, the other hosts, the profile ──────────────────────────────
# config/hosts.yaml says which hosts exist; HERMES_HOST (tokens.env) or --host says which one
# this is. scripts/render_host_context.py writes $HERMES_HOME/HOST.md, the managed block of
# SOUL.md, HERMES_HOST in .env and the ssh aliases ~/.ssh/config.d/hermes_hosts.
deploy_host() {
  say "Host -> $HERMES_HOME/HOST.md, SOUL.md block, ssh aliases (config/hosts.yaml)"
  local args=(--hosts "$HOSTS_FILE" --hermes-home "$HERMES_HOME" --tokens-file "$TOKENS_FILE" --write-ssh-config)
  [ -n "$HOST_NAME" ] && args+=(--host "$HOST_NAME")
  "$HERMES_PYTHON" "$REPO_DIR/scripts/render_host_context.py" "${args[@]}" 2>&1 | sed 's/^/    /'
  mkdir -p "$HERMES_HOME/skills/$HOST_SKILL"
  cp -f "$REPO_DIR/config/skills/$HOST_SKILL/SKILL.md" "$HERMES_HOME/skills/$HOST_SKILL/"
  info "skill $HOST_SKILL -> $HERMES_HOME/skills/$HOST_SKILL/"
}

# --profile NAME: config/profiles/NAME/{SOUL.md,overlay.yaml} on top of the shared config.
apply_profile() {
  say "Profile $PROFILE -> SOUL.md, overlay, local skills (config/profiles/$PROFILE/)"
  if [ ! -d "$REPO_DIR/config/profiles/$PROFILE" ]; then
    warn "no config/profiles/$PROFILE/: shared config only"
    return 0
  fi
  "$HERMES_PYTHON" "$REPO_DIR/scripts/apply_profile.py" "$PROFILE" --hermes-home "$HERMES_HOME"
}

# Every 15 minutes (2026-10-19; was hourly), offset from the opencode installer and the Log
# Intelligence server so the three probes do not hit the gateway in the same minute. A line
# with another schedule is replaced, so re-running --cron updates it.
install_cron() {
  say "Cron: re-measure the models every 15 minutes"
  local log="$HERMES_HOME/logs/hermes_installer_adapt.log"
  local marker="$REPO_DIR/install.sh --adapt"
  local line="2-59/15 * * * * HERMES_HOME=$HERMES_HOME $marker >> $log 2>&1"
  local current
  mkdir -p "$HERMES_HOME/logs"
  current="$(crontab -l 2>/dev/null || true)"
  if printf '%s\n' "$current" | grep -qxF "$line"; then
    info "already in your crontab"
  else
    ( printf '%s\n' "$current" | grep -vF "HERMES_HOME=$HERMES_HOME $marker" || true; printf '%s\n' "$line" ) | grep -v '^$' | crontab -
    info "installed: $line"
  fi
}

# ── 3. VS Code ────────────────────────────────────────────────────────────────
configure_vscode() {
  say "VS Code (ACP Client extension)"
  local code_cli="" settings=""
  if command -v code >/dev/null 2>&1; then
    code_cli="$(command -v code)"
  else
    code_cli="$(ls -t "$HOME"/.vscode-server/cli/servers/*/server/bin/code-server 2>/dev/null | head -1 || true)"
  fi
  if [ -d "$HOME/.vscode-server" ]; then
    settings="$HOME/.vscode-server/data/Machine/settings.json"      # Remote-SSH host
  elif [ -d "$HOME/.config/Code/User" ]; then
    settings="$HOME/.config/Code/User/settings.json"                # desktop VS Code
  fi
  if [ -z "$code_cli" ] && [ -z "$settings" ]; then
    info "VS Code not found on this machine, skipped"
    return
  fi

  if [ -n "$code_cli" ]; then
    if compgen -G "$HOME/.vscode-server/extensions/formulahendry.acp-client-*" >/dev/null \
       || compgen -G "$HOME/.vscode/extensions/formulahendry.acp-client-*" >/dev/null; then
      info "extension formulahendry.acp-client: already installed"
    elif "$code_cli" --install-extension formulahendry.acp-client >/dev/null 2>&1; then
      info "extension formulahendry.acp-client: installed (reload the VS Code window)"
    else
      warn "could not install formulahendry.acp-client; install \"ACP Client\" by formulahendry from the Extensions view"
    fi
  fi

  # The extension starts Hermes itself; give it the full path so it does not depend on
  # the PATH VS Code happens to have. An entry that already works is left alone.
  if [ -n "$settings" ]; then
    mkdir -p "$(dirname "$settings")"
    python3 - "$settings" "$(command -v hermes)" "$HERMES_HOME" <<'PY' || warn "could not edit $settings (comments in it?): add the \"acp.agents\" entry from README.md by hand"
import json, os, sys, time
path, hermes, hermes_home = sys.argv[1:4]
data = json.load(open(path)) if os.path.exists(path) and os.path.getsize(path) else {}
agents = data.setdefault("acp.agents", {})
current = agents.get("Hermes Agent", {})
if os.access(current.get("command", ""), os.X_OK):
    print("    acp.agents[\"Hermes Agent\"]: already set (%s)" % current["command"])
    sys.exit(0)
entry = {"command": hermes, "args": ["acp"],
         "env": {"PATH": ":".join([os.path.dirname(hermes), os.path.join(hermes_home, "bin"),
                                   "/usr/local/bin", "/usr/bin", "/bin"])}}
if os.path.realpath(hermes_home) != os.path.realpath(os.path.expanduser("~/.hermes")):
    entry["env"]["HERMES_HOME"] = hermes_home
if os.path.exists(path):
    open(path + ".bak-" + time.strftime("%Y%m%d-%H%M%S"), "w").write(open(path).read())
agents["Hermes Agent"] = entry
json.dump(data, open(path, "w"), indent=4)
open(path, "a").write("\n")
print("    acp.agents[\"Hermes Agent\"]: written to " + path)
PY
  fi
}

# ── 4. verification ───────────────────────────────────────────────────────────
# ── 3b. messaging gateway (Telegram) ─────────────────────────────────────────
# Only when tokens.env (or an earlier run) gave a TELEGRAM_BOT_TOKEN. `hermes gateway install`
# writes ~/.config/systemd/user/hermes-gateway.service, enables linger so the bot survives
# logout, and is a no-op when the unit exists. Its two questions (start now, start on login)
# are answered "y" on stdin: on EOF Hermes exits 1 instead of taking the default. The
# restart makes a running gateway re-read .env.
configure_gateway() {
  say "Gateway: Telegram bot"
  if [ -z "$(telegram_token)" ]; then
    info "no TELEGRAM_BOT_TOKEN: gateway left alone (fill in the Telegram part of tokens.env to chat from your phone)"
    return 0
  fi
  local out
  if out="$(printf 'y\ny\n' | hermes "${HP[@]}" gateway install 2>&1 | strip_ansi)"; then
    printf '%s\n' "$out" | sed -E 's/[^?]*\? \[Y\/n\]: //g' | grep -E '✓|↻|already installed|linger' | sed -E 's/^ */    /' || true
  else
    warn "hermes gateway install failed:"; printf '%s\n' "$out" | tail -5 | sed -E 's/^ */       /'
    warn "the bot needs a running gateway: fix the message above or run 'hermes gateway run' in tmux"
    return 0
  fi
  if out="$(hermes "${HP[@]}" gateway restart </dev/null 2>&1 | strip_ansi)"; then
    info "gateway restarted (it re-reads .env on start)"
  else
    warn "hermes gateway restart failed:"; printf '%s\n' "$out" | tail -5 | sed -E 's/^ */       /'
  fi
}

gateway_probe() {   # $1 base URL, $2 key, $3 model -> "ok" or "refused: <HTTP code> <reason>"
  local header body code
  header="$(mktemp)"
  ( umask 077; printf 'Authorization: Bearer %s\n' "$2" > "$header" )
  body="$(mktemp)"
  code="$(curl -s -m 60 -o "$body" -w '%{http_code}' -H @"$header" -H 'Content-Type: application/json' "$1/chat/completions" \
            -d "{\"model\":\"$3\",\"messages\":[{\"role\":\"user\",\"content\":\"Reply with exactly: PONG\"}],\"max_tokens\":300}" || true)"
  rm -f "$header"
  python3 -c '
import json, sys
code = sys.argv[2]
try:
    error = json.load(open(sys.argv[1])).get("error")
except Exception:
    error = None
if code == "200" and not error:
    print("ok")
else:
    message = error.get("message") if isinstance(error, dict) else error
    print("refused: HTTP %s %s" % (code, str(message or "no reply")[:100]))' "$body" "$code"
  rm -f "$body"
}

verify() {
  local scratch list server result connected
  scratch="$(mktemp -d)"

  say "Check: hosts (config/hosts.yaml)"
  local hargs=(--hosts "$HOSTS_FILE" --hermes-home "$HERMES_HOME" --tokens-file "$TOKENS_FILE" --check)
  [ -n "$HOST_NAME" ] && hargs+=(--host "$HOST_NAME")
  "$HERMES_PYTHON" "$REPO_DIR/scripts/render_host_context.py" "${hargs[@]}" 2>&1 | sed 's/^/    /' || warn "host not resolved: set HERMES_HOST in tokens.env or pass --host"
  if [ -f "$HERMES_HOME/HOST.md" ]; then info "HOST.md: $(head -1 "$HERMES_HOME/HOST.md" | cut -c1-100)"; else warn "no $HERMES_HOME/HOST.md (run ./install.sh without --check)"; fi
  if [ -f "$HERMES_HOME/skills/$HOST_SKILL/SKILL.md" ]; then info "skill $HOST_SKILL present"; else warn "skill $HOST_SKILL missing"; fi
  local alias me
  me="$(env_value HERMES_HOST)"
  for alias in $(awk '/^Host /{print $2}' "$HOME/.ssh/config.d/hermes_hosts" 2>/dev/null); do
    [ "$alias" = "$me" ] && continue
    if timeout 12 ssh -o BatchMode=yes -o ConnectTimeout=5 "$alias" true </dev/null >/dev/null 2>&1; then
      info "ssh $alias: ok"
    else
      warn "ssh $alias: no key login (key not authorized, password-only host, or unreachable from here)"
    fi
  done

  say "Check: MCP servers"
  list="$(cd "$scratch" && timeout 120 hermes mcp list 2>&1 | strip_ansi || true)"
  printf '%s\n' "$list" | grep -E '✓ enabled|✗ disabled' | sed -E 's/^ +/    /' || warn "hermes mcp list printed no servers"
  for server in $(printf '%s\n' "$list" | awk '/✓ enabled/ {print $1}'); do
    result="$(cd "$scratch" && timeout 120 hermes mcp test "$server" 2>&1 | strip_ansi || true)"
    connected="$(printf '%s\n' "$result" | grep -oE 'Connected \([0-9]+ms\)' | head -1 || true)"
    if [ -n "$connected" ]; then
      info "$server: $connected"
    else
      warn "$server: $(printf '%s\n' "$result" | grep -iE '✗|error|fail' | head -1 | sed -E 's/^ +//')"
    fi
  done

  say "Check: plugins ${PLUGIN_NAMES[*]}"
  local plugins_list plugin
  plugins_list="$(hermes plugins list 2>&1 | strip_ansi || true)"
  for plugin in "${PLUGIN_NAMES[@]}"; do
    if printf '%s\n' "$plugins_list" | grep -qE "$plugin +│ enabled"; then
      case "$plugin" in
        mcp-ask-first) info "$plugin enabled: Mantis filing/notes/email and Jenkins trigger/rebuild ask before running" ;;
        chain-adapt)   info "$plugin enabled: a refused model re-measures the chain at once; chain notice at session start" ;;
        *)             info "$plugin enabled" ;;
      esac
    else
      warn "$plugin is not enabled (see 'hermes plugins list')"
    fi
  done

  # Telegram: the token against api.telegram.org (bot name back, token never printed), then
  # the gateway's own view: service state and the Telegram connection from gateway_state.json.
  local token
  token="$(telegram_token)"
  if [ -n "$token" ]; then
    say "Check: Telegram bot"
    local me
    me="$(curl -sS -m 15 "https://api.telegram.org/bot$token/getMe" 2>&1 | python3 -I -c '
import json, sys
try:
    data = json.load(sys.stdin)
except Exception:
    print("no answer from api.telegram.org (blocked network? see telegram.proxy_url in the guide)"); sys.exit(0)
if data.get("ok"):
    print("token ok: bot @" + data["result"].get("username", "?"))
else:
    print("token refused: " + str(data.get("description", data)))
' 2>&1 || true)"
    case "$me" in token\ ok*) info "$me" ;; *) warn "${me:-no answer from api.telegram.org}" ;; esac
    info "allowed users: $(env_value TELEGRAM_ALLOWED_USERS | tr ',' ' ' | wc -w | tr -d ' ') ID(s), home channel $( [ -n "$(env_value TELEGRAM_HOME_CHANNEL)" ] && echo set || echo unset)"
    local state
    state="$(hermes "${HP[@]}" gateway status 2>&1 | strip_ansi | grep -E '^\s*(Active:|✓|✗|!)' | head -3 | sed -E 's/^ +//' || true)"
    if [ -n "$state" ]; then printf '%s\n' "$state" | sed 's/^/    /'; else warn "hermes gateway status printed nothing useful (service installed? --no-gateway?)"; fi
    if [ -f "$HERMES_HOME/gateway_state.json" ]; then
      python3 -I - "$HERMES_HOME/gateway_state.json" <<'PY' || true
import json, sys
data = json.load(open(sys.argv[1]))
platform = data.get("platforms", {}).get("telegram") or {}
state = platform.get("state", "not started")
line = f"    telegram: {state} (gateway {data.get('gateway_state', '?')}, updated {platform.get('updated_at', '?')[:19]})"
if platform.get("error_message"):
    line += f" !! {platform['error_message']}"
print(line)
PY
    else
      warn "no $HERMES_HOME/gateway_state.json yet: the gateway has not started (hermes gateway status)"
    fi
  fi

  # One prompt per model, each from a throwaway HERMES_HOME that holds only that model
  # and its key, so every link of the chain is tested on its own.
  say "Check: each model of the fallback chain (gateway first, then one Hermes prompt)"
  local key_name model base_url value gateway home answer
  while read -r key_name model base_url; do
    value="$(env_value "$key_name")"
    if [ -z "$value" ] || [ "$value" = "$PLACEHOLDER" ]; then
      printf '    %-24s skipped (no %s)\n' "$model" "$key_name"
      continue
    fi
    gateway="$(gateway_probe "$base_url" "$value" "$model")"
    if [ "$gateway" != "ok" ]; then
      warn "$(printf '%-24s gateway %s' "$model" "$gateway")"
      continue
    fi
    home="$(mktemp -d)"
    ( umask 077
      printf '%s=%s\n' "$key_name" "$value" > "$home/.env"
      printf 'model:\n  default: %s\n  provider: custom\n  base_url: %s\n  api_key: ${%s}\nfallback_providers: []\n' \
        "$model" "$base_url" "$key_name" > "$home/config.yaml"
      : > "$home/.no-bundled-skills" )
    answer="$(cd "$scratch" && HERMES_HOME="$home" timeout 120 hermes -z "Reply with exactly: PONG" 2>&1 | strip_ansi || true)"
    rm -rf "$home"
    if printf '%s\n' "$answer" | grep -q 'PONG'; then
      printf '    %-24s gateway ok, Hermes PONG\n' "$model"
    else
      warn "$(printf '%-24s gateway ok, but Hermes gave no answer: %s' "$model" \
              "$(printf '%s\n' "$answer" | grep -v '^[[:space:]]*$' | tail -1 | cut -c1-120)")"
    fi
  done < <("$HERMES_PYTHON" "$HELPER" chain --hermes-home "$HERMES_HOME")
  rm -rf "$scratch"
}

# ── main ──────────────────────────────────────────────────────────────────────
if [ "$ADAPT_ONLY" = 1 ]; then
  find_hermes_python
  say "Models -> $HERMES_HOME/config.yaml ($(date '+%F %T'))"
  adapt_models
  [ "$DO_CRON" = 1 ] && install_cron   # --adapt --cron: re-measure and (re)install the schedule
  exit 0
fi
if [ "$CHECK_ONLY" = 1 ]; then
  find_hermes_python
  verify
  exit 0
fi
[ "$DO_INSTALL" = 1 ] && install_hermes
find_hermes_python
mkdir -p "$HERMES_HOME"
deploy_tokens
deploy_config
[ -n "$PROFILE" ] && apply_profile
deploy_host
[ "$DO_VSCODE" = 1 ] && configure_vscode
[ "$DO_GATEWAY" = 1 ] && configure_gateway
[ "$DO_CRON" = 1 ] && install_cron
[ "$DO_CHECK" = 1 ] && verify

say "Done"
info "Start Hermes with 'hermes' in a terminal. In VS Code: Developer: Reload Window, then 'ACP: Connect to Agent' -> Hermes Agent."
info "A Hermes that was already running keeps the old config: restart it (VS Code: 'ACP: Restart Agent')."
info "Microsoft 365 MCP (optional, untested): 'hermes mcp login microsoft365' in a terminal with a browser, then ./install.sh again."
info "The model chain follows the gateway: ./install.sh --adapt re-measures it (--cron runs that every 15 min; a refused model triggers it too, plugin chain-adapt)."
if [ -n "$(telegram_token)" ]; then
  info "Telegram: open your bot in Telegram and send it a message. Logs: journalctl --user -u hermes-gateway -f"
else
  info "Telegram (optional): fill in TELEGRAM_BOT_TOKEN and TELEGRAM_ALLOWED_USERS in tokens.env, ./install.sh again (docs/guides/telegram.md)."
fi
info "Update later with: git pull && ./install.sh"
