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
#   --no-check      skip the verification at the end
#   --check         only run the verification
#   --adapt         only probe the models and update the chain in config.yaml
#   --cron          also add an hourly `install.sh --adapt` to your crontab (idempotent)
#
# Works on $HERMES_HOME (default ~/.hermes). Safe to re-run after every `git pull`:
# tokens already in $HERMES_HOME/.env are kept unless tokens.env gives a new value.
# Nothing secret is printed.
set -euo pipefail

TESTED_HERMES_VERSION="0.21.4"
TESTED_HERMES_COMMIT="c0d7294769a38c17ceae51d8f7995e66e1dcae27"
HERMES_INSTALLER_URL="https://hermes-agent.nousresearch.com/install.sh"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOKENS_FILE="$REPO_DIR/tokens.env"
DO_INSTALL=1; DO_VSCODE=1; DO_CHECK=1; CHECK_ONLY=0; ADAPT_ONLY=0; DO_CRON=0

while [ $# -gt 0 ]; do
  case "$1" in
    --tokens)     TOKENS_FILE="$2"; shift 2 ;;
    --no-install) DO_INSTALL=0; shift ;;
    --no-vscode)  DO_VSCODE=0; shift ;;
    --no-check)   DO_CHECK=0; shift ;;
    --check)      CHECK_ONLY=1; shift ;;
    --adapt)      ADAPT_ONLY=1; shift ;;
    --cron)       DO_CRON=1; shift ;;
    -h|--help)    sed -n '2,22p' "$0"; exit 0 ;;
    *) echo "unknown option: $1 (see --help)" >&2; exit 2 ;;
  esac
done

export HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
export PATH="$HOME/.local/bin:$PATH"
HELPER="$REPO_DIR/scripts/merge_into_hermes_home.py"
FRAGMENT="$REPO_DIR/config/config.yaml"
PREFERENCES="$REPO_DIR/config/model-preferences.yaml"
PLUGIN_NAME="mcp-ask-first"
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
  for candidate in "$install_dir/venv/bin/python" "$HERMES_HOME/hermes-agent/venv/bin/python" \
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

# ── 1. Hermes itself ──────────────────────────────────────────────────────────
install_hermes() {
  say "Hermes Agent (tested: v$TESTED_HERMES_VERSION)"
  local version
  version="$(hermes_version)"
  if [ -z "$version" ]; then
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
  say "Config -> $HERMES_HOME/config.yaml, plugin $PLUGIN_NAME"
  mkdir -p "$HERMES_HOME/plugins/$PLUGIN_NAME"
  cp -f "$REPO_DIR/config/plugins/$PLUGIN_NAME/plugin.yaml" "$REPO_DIR/config/plugins/$PLUGIN_NAME/__init__.py" \
        "$HERMES_HOME/plugins/$PLUGIN_NAME/"
  adapt_models
}

# The chain is measured, not fixed: every key gets a one-token chat probe and the chain is
# built from the prefer lists in config/model-preferences.yaml (see the comment there).
adapt_models() {
  info "probing the models each key can call now:"
  "$HERMES_PYTHON" "$HELPER" adapt --preferences "$PREFERENCES" --fragment "$FRAGMENT" --hermes-home "$HERMES_HOME"
}

install_cron() {
  say "Cron: re-measure the models every hour"
  local log="$HERMES_HOME/logs/hermes_installer_adapt.log"
  local line="17 * * * * HERMES_HOME=$HERMES_HOME $REPO_DIR/install.sh --adapt >> $log 2>&1"
  mkdir -p "$HERMES_HOME/logs"
  if crontab -l 2>/dev/null | grep -qF "$REPO_DIR/install.sh --adapt"; then
    info "already in your crontab"
  else
    ( crontab -l 2>/dev/null || true; printf '%s\n' "$line" ) | crontab -
    info "added: $line"
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

  say "Check: plugin $PLUGIN_NAME"
  if hermes plugins list 2>&1 | strip_ansi | grep -qE "$PLUGIN_NAME +│ enabled"; then
    info "enabled: Mantis filing/notes/email and Jenkins trigger/rebuild ask before running"
  else
    warn "$PLUGIN_NAME is not enabled (see 'hermes plugins list')"
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
[ "$DO_VSCODE" = 1 ] && configure_vscode
[ "$DO_CRON" = 1 ] && install_cron
[ "$DO_CHECK" = 1 ] && verify

say "Done"
info "Start Hermes with 'hermes' in a terminal. In VS Code: Developer: Reload Window, then 'ACP: Connect to Agent' -> Hermes Agent."
info "A Hermes that was already running keeps the old config: restart it (VS Code: 'ACP: Restart Agent')."
info "Microsoft 365 MCP (optional, untested): 'hermes mcp login microsoft365' in a terminal with a browser, then ./install.sh again."
info "The model chain follows the gateway: ./install.sh --adapt re-measures it (--cron runs that hourly)."
info "Update later with: git pull && ./install.sh"
