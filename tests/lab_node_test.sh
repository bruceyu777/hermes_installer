#!/usr/bin/env bash
# lab_node_test.sh - offline test of what install.sh does on a shared lab node: the node-lock guard and the
# host name from the `lab_nodes` class of config/hosts.yaml. No network, no tokens, nothing outside a temp dir.
# Run: tests/lab_node_test.sh      (PYTHON=<python with PyYAML or ruamel.yaml> if python3 has neither)
set -u
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-python3}"
W="$(mktemp -d)"; trap 'rm -rf "$W"' EXIT
PASS=0; FAIL=0
check() { if [ "$2" = "$3" ]; then PASS=$((PASS+1)); echo "  ok    $1"; else FAIL=$((FAIL+1)); echo "  FAIL  $1: expected '$2', got '$3'"; fi; }
has()   { case "$2" in *"$3"*) PASS=$((PASS+1)); echo "  ok    $1" ;; *) FAIL=$((FAIL+1)); echo "  FAIL  $1: no '$3' in: $2" ;; esac; }

echo "== node lock guard"
eval "$(sed -n '/^node_lock_state()/,/^}/p; /^guard_shared_node()/,/^}/p' "$REPO/install.sh")"
NODE_LOCK_FILE="$W/node.lock"
TEST_RUNNING=1; pgrep() { return "$TEST_RUNNING"; }        # 1 = no autotest.py
guard() { ( guard_shared_node ) >"$W/out" 2>&1; echo $?; }

check "no lock file: installs" 0 "$(guard)"
echo "pid=1 target=provision_fgt" > "$NODE_LOCK_FILE"
check "lock file left behind, nobody holds it: installs" 0 "$(guard)"
chmod 444 "$NODE_LOCK_FILE"
check "the same, file not writable for us: installs" 0 "$(guard)"
chmod 644 "$NODE_LOCK_FILE"
flock "$NODE_LOCK_FILE" sleep 30 & HOLDER=$!
for _ in $(seq 50); do flock -n "$NODE_LOCK_FILE" true 2>/dev/null || break; sleep 0.1; done
check "a job holds the lock: refuses" 1 "$(guard)"; has "  and says so" "$(cat "$W/out")" "holds the node lock"
pkill -P "$HOLDER" 2>/dev/null; kill "$HOLDER" 2>/dev/null; wait "$HOLDER" 2>/dev/null
for _ in $(seq 50); do flock -n "$NODE_LOCK_FILE" true 2>/dev/null && break; sleep 0.1; done
check "the job died, the file is still there: installs" 0 "$(guard)"
TEST_RUNNING=0
check "autotest.py runs: refuses" 1 "$(guard)"; has "  and says so" "$(cat "$W/out")" "autotest.py"
TEST_RUNNING=1
if [ "$(id -u)" != 0 ]; then
  chmod 000 "$NODE_LOCK_FILE"
  check "lock file not readable: refuses (cannot tell)" 1 "$(guard)"; has "  and says so" "$(cat "$W/out")" "cannot tell"
  chmod 644 "$NODE_LOCK_FILE"
fi
flock() { return 127; }
check "no flock command: refuses (cannot tell)" 1 "$(guard)"
unset -f flock

echo "== host name"
mkdir -p "$W/bin"
printf '#!/bin/sh\necho "$FAKE_IPS"\n' > "$W/bin/hostname"; chmod +x "$W/bin/hostname"
render() {   # $1 hostname, $2 ips, rest: arguments of render_host_context.py
  local name="$1" ips="$2"; shift 2
  env -u HERMES_HOST HOME="$W/home" HERMES_HOME="$W/home/.hermes" PATH="$W/bin:$PATH" FAKE_IPS="$ips" FAKE_NAME="$name" \
    "$PYTHON" -c 'import os,runpy,socket,sys; socket.gethostname=lambda: os.environ["FAKE_NAME"]; s=sys.argv[1]; sys.argv=sys.argv[1:]; runpy.run_path(s, run_name="__main__")' \
    "$REPO/scripts/render_host_context.py" --tokens-file "" "$@" 2>&1
}
fresh() { rm -rf "$W/home"; mkdir -p "$W/home/.ssh"; echo "# everybody's" > "$W/home/.ssh/config"; }
LAB_IPS="172.17.0.1 192.168.122.1 10.161.69.16"

fresh; out="$(render all-in-one-node16 "$LAB_IPS" --write-ssh-config)"; rc=$?
check "lab node not in hosts.yaml: exit 0" 0 "$rc"; has "  is node16" "$out" "host: node16"
has "  HOST.md has the lab address, not a bridge" "$(cat "$W/home/.hermes/HOST.md" 2>/dev/null)" "IP 10.161.69.16"
check "  HERMES_HOST in .env" "HERMES_HOST=node16" "$(cat "$W/home/.hermes/.env" 2>/dev/null)"
check "  ~/.ssh/config untouched" "# everybody's" "$(cat "$W/home/.ssh/config")"
check "  no ssh aliases written" "no" "$([ -e "$W/home/.ssh/config.d" ] && echo yes || echo no)"
out="$(render all-in-one-node16 "$LAB_IPS" --check)"; has "  second run resolves from .env" "$out" "host: node16  (from $W/home/.hermes/.env)"
fresh; has "lab node with --host node16" "$(render all-in-one-node16 "$LAB_IPS" --host node16 --check)" "host: node16  (from --host)"
fresh; out="$(render all-in-one-node16 "$LAB_IPS" --host node17 --check)"; rc=$?
check "node16 told it is node17: stops" 1 "$rc"
fresh; has "all-in-one-node254 stays log_intel_server" "$(render all-in-one-node254 "10.161.69.237" --check)" "host: log_intel_server"
fresh; out="$(render some-laptop "10.9.9.9" --check)"; rc=$?
check "other unknown machine: still stops" 1 "$rc"; has "  and says why" "$out" "cannot tell which host"
fresh; out="$(render some-laptop "10.9.9.9" --host node16 --check)"; rc=$?
check "other machine told it is node16: stops" 1 "$rc"
fresh; out="$(render VAN-903614-PC6 "172.19.136.41" --write-ssh-config --dry-run)"; has "listed host unchanged: office1, ssh config still written" "$out" "Include config.d"

echo "$PASS passed, $FAIL failed"
[ "$FAIL" = 0 ]
