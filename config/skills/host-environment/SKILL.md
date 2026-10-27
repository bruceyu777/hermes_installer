---
name: host-environment
description: Which host this Hermes runs on, what is where, which tasks must run on another host and how to ssh there. Use before any task that touches another machine, a database, a lab device, a FortiGate or a GUI run, or when asked "where are you", "which host", "ssh to", "run on <host>".
version: 1.0
---
# Host environment

This install is one of several. `config/hosts.yaml` in the hermes_installer repo describes every host;
`install.sh` rendered the part that matters for this machine into `$HERMES_HOME/HOST.md` and set
`HERMES_HOST` in the environment.

## Procedure

1. Confirm where you are: `echo $HERMES_HOST` and the first line of `$HERMES_HOME/HOST.md`
   (default `~/.hermes/HOST.md`). If either is missing, stop and say that `install.sh` has not been
   run on this machine (or `HERMES_HOST` is not set in `tokens.env`); do not guess.
2. Find the task in the **Tasks that must run elsewhere** table of HOST.md. If it names another host,
   run it there: `ssh <alias> '<command>'`. The alias is the host name; `~/.ssh/config.d/hermes_hosts`
   (written by install.sh from `SSH_<NAME>_*` in `.env`) holds address, user and key. Never put a
   password, key path or IP in the command line.
3. A host that only accepts a password: `sshpass -e ssh <alias> '<command>'` with
   `SSHPASS="$SSH_<NAME>_PASSWORD"` in the environment, if `sshpass` exists; otherwise ask the user.
4. Do not copy `.env` files, keys, database dumps or test data from the remote host to this one.
   Bring back a summary, the relevant lines, and the remote path of anything large.
5. `kind: fortigate` hosts are FortiOS devices, not Linux: use `get`, `show`, `diagnose` only,
   unless the task explicitly approves a `config` change (and then only after human approval).
6. Shared test nodes (nodeNN) are not in the list on purpose: do not ssh there to install anything.
7. In the answer, say on which host each command ran.

## Quick checks

```bash
echo "$HERMES_HOST"; head -1 "${HERMES_HOME:-$HOME/.hermes}/HOST.md"
awk '/^Host /{print $2}' ~/.ssh/config.d/hermes_hosts          # aliases available here
ssh -o BatchMode=yes -o ConnectTimeout=5 log_intel_server true && echo reachable
```
