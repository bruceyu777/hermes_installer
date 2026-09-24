#!/usr/bin/env bash
# Acceptance tests for Hermes, run on the machine after ./install.sh (from the repo root: tests/acceptance.sh).
# Makes real calls with your tokens. H6/H7 ask for a Jenkins build of a job that does not exist:
# it must be blocked (one-shot) or denied (ACP); "allow_once" in H7 would only get a 404 from Jenkins.
export PATH="$HOME/.local/bin:$PATH"
cd "$(dirname "$0")/.."; TESTS="$PWD/tests"
strip() { sed 's/\x1b\[[0-9;]*m//g'; }
W=$(mktemp -d); trap 'rm -rf "$W"' EXIT
PY=~/.hermes/hermes-agent/venv/bin/python

echo "### H1 re-run is idempotent"
./install.sh --no-install --no-vscode --no-check </dev/null 2>&1 | strip | grep -E "unchanged|written|chain:"

echo "### H2 cron command under env -i"
env -i HOME="$HOME" PATH=/usr/bin:/bin SHELL=/bin/sh HERMES_HOME="$HOME/.hermes" "$PWD/install.sh" --adapt </dev/null > "$W/adapt.log" 2>&1; echo "rc=$?"
strip < "$W/adapt.log" | grep -E "unchanged|chain:"

echo "### H3 tools the model sees (Hermes discovery -> registry)"
(cd "$W" && timeout 200 $PY "$TESTS/list_registered_mcp_tools.py" 2>&1 | grep -E "tools$|registry")

echo "### H4 real MCP call through the model (logintel)"
(cd "$W" && timeout 240 hermes -z "Call the logintel tool that lists the monitored projects. Reply only with the number of projects." </dev/null 2>&1 | strip | tail -2)

echo "### H5 fallback: forced 404 on the primary"
(cd "$W" && timeout 200 hermes -m no-such-model-404 -z "Reply with exactly: PONG" </dev/null 2>&1 | tail -1)
$PY -c "import sqlite3,os;print(list(sqlite3.connect(os.path.expanduser('~/.hermes/state.db')).execute('select id, model, billing_base_url from sessions order by started_at desc limit 1')))"

echo "### H6 guard rail in one-shot mode (job does not exist)"
(cd "$W" && timeout 240 hermes -z "Use mcp__jenkins__triggerBuild to trigger job hermes-gate-demo-does-not-exist. Report the exact result." </dev/null 2>&1 | strip | grep -v '^\s*$' | head -3)

echo "### H7 guard rail through ACP (what VS Code does): deny, then allow_once"
(cd "$W" && timeout 600 $PY "$TESTS/acp_permission_test.py" "$W" "Use mcp__jenkins__triggerBuild to trigger the Jenkins job hermes-gate-demo-does-not-exist (it may not exist). Report the exact result in one sentence." 2>&1 | tail -9)

echo "### H8 VS Code ACP entry + extension"
python3 -c "import json,os;e=json.load(open(os.path.expanduser('~/.vscode-server/data/Machine/settings.json')))['acp.agents']['Hermes Agent'];print(e['command'],e['args'])"
ls ~/.vscode-server/extensions | grep -i acp-client

echo "### H9 tokens.env git-ignored"
git check-ignore -q tokens.env && echo "tokens.env ignored"; git status --short
