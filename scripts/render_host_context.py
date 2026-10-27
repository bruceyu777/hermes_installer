#!/usr/bin/env python3
"""Tell a Hermes install which host it runs on.

Reads config/hosts.yaml, resolves the local host name, and writes into HERMES_HOME:
  HOST.md          this host: identity, folders, capabilities; tasks that must run elsewhere;
                   the hosts reachable from here and how to ssh to them
  SOUL.md          a short managed block between <!-- hermes_installer:host begin/end --> markers
                   (everything outside the markers is left alone; the file is created if missing)
  .env             HERMES_HOST=<name> (added or replaced), so the agent can `echo $HERMES_HOST`
With --write-ssh-config also:
  ~/.ssh/config.d/hermes_hosts   one `Host <name>` block per host that has SSH_<NAME>_USER in
                   HERMES_HOME/.env (HostName, User, Port, IdentityFile); and `Include config.d/*`
                   at the top of ~/.ssh/config if it is not there yet.

Resolution order for the host name (first hit wins):
  --host NAME  >  $HERMES_HOST  >  HERMES_HOST in HERMES_HOME/.env  >  HERMES_HOST in --tokens-file
  >  auto-detect: `hostname` equals a host's `hostname`, or one of `hostname -I` equals a host's `ip`.
A configured name that disagrees with auto-detection is used, with a warning (a tokens.env copied
from another machine is the usual cause).

Usage:
  scripts/render_host_context.py                       # HERMES_HOME from env or ~/.hermes
  scripts/render_host_context.py --host cdn --dry-run  # show what would be written
  scripts/render_host_context.py --check               # print the resolved host only, write nothing
"""
import argparse
import os
import re
import socket
import subprocess
import sys
from pathlib import Path

try:
    import yaml
    load_yaml = yaml.safe_load
except ImportError:  # pragma: no cover
    try:  # the Hermes v0.22+ venv ships ruamel.yaml but not PyYAML
        from ruamel.yaml import YAML
        load_yaml = YAML(typ="safe").load
    except ImportError:
        sys.exit("PyYAML is required (python3 -m pip install pyyaml)")

REPO = Path(__file__).resolve().parent.parent
BEGIN = "<!-- hermes_installer:host begin -->"
END = "<!-- hermes_installer:host end -->"


def env_file_value(path: Path, name: str) -> str:
    if not path.is_file():
        return ""
    m = re.search(rf"^[ \t]*(?:export[ \t]+)?{name}[ \t]*=[ \t]*([^\n#]*?)[ \t]*(?:#.*)?$", path.read_text(), flags=re.M)
    return m.group(1).strip().strip("'\"") if m else ""


def local_ips() -> list:
    try:
        out = subprocess.run(["hostname", "-I"], capture_output=True, text=True, timeout=5).stdout
        return out.split()
    except Exception:
        return []


def autodetect(hosts: dict) -> str:
    name = socket.gethostname()
    for key, h in hosts.items():
        if h.get("hostname") and h["hostname"].lower() == name.lower():
            return key
    ips = set(local_ips())
    for key, h in hosts.items():
        if h.get("ip") in ips:
            return key
    return ""


def resolve(args, hosts: dict, hermes_home: Path) -> tuple:
    detected = autodetect(hosts)
    for source, value in (
        ("--host", args.host),
        ("$HERMES_HOST", os.environ.get("HERMES_HOST", "")),
        (f"{hermes_home}/.env", env_file_value(hermes_home / ".env", "HERMES_HOST")),
        (str(args.tokens_file), env_file_value(Path(args.tokens_file), "HERMES_HOST") if args.tokens_file else ""),
    ):
        if value:
            if value not in hosts:
                sys.exit(f"!! HERMES_HOST '{value}' (from {source}) is not in {args.hosts}; known: {', '.join(hosts)}")
            if detected and detected != value:
                print(f"!! warning: {source} says {value} but this machine looks like {detected} "
                      f"(hostname {socket.gethostname()}, ips {' '.join(local_ips())})", file=sys.stderr)
            return value, source
    if detected:
        return detected, "auto-detect"
    sys.exit(f"!! cannot tell which host this is: hostname {socket.gethostname()}, ips {' '.join(local_ips())} "
             f"match nothing in {args.hosts}. Set HERMES_HOST in tokens.env or pass --host; known: {', '.join(hosts)}")


def fmt_folders(f: dict) -> str:
    return " · ".join(f"{k} {v}" for k, v in (f or {}).items()) or "—"


def render_host_md(me: str, data: dict, source: str) -> str:
    hosts, tasks = data["hosts"], data.get("tasks", {})
    h = hosts[me]
    hm = h.get("hermes") or {}
    lines = [
        f"# This host: {me} — {h.get('description', '')}",
        "",
        f"IP {h.get('ip', '?')} · network {h.get('network', '?')} · {h.get('kind', '?')} / {h.get('os', '?')}"
        + (f" · hostname {h['hostname']}" if h.get("hostname") else "") + f" · resolved from {source}",
        "",
        f"Hermes: {'installed ' + str(hm.get('version', '')) if hm.get('installed') else 'not installed'}"
        + (f", profiles {', '.join(hm.get('profiles', []))}" if hm.get("profiles") else "")
        + (", the Telegram gateway runs HERE" if hm.get("gateway") else ", no messaging gateway here"),
        f"Folders: {fmt_folders(h.get('folders'))}",
        f"Capabilities: {', '.join(h.get('capabilities', [])) or '—'}",
        "",
        "## Tasks that must run on THIS host",
        "",
    ]
    here = [(t, v) for t, v in tasks.items() if v.get("host") == me]
    lines += [f"- `{t}` — {v.get('why', '')}" for t, v in here] or ["- none"]
    lines += ["", "## Tasks that must run elsewhere", "",
              "Run them with `ssh <alias> '<command>'` (alias = host name below; credentials come from",
              "`SSH_<NAME>_*` in `.env` through `~/.ssh/config.d/hermes_hosts`). Do not copy credentials",
              "or data back to this host; report a summary and the remote path. Say on which host each command ran.",
              "", "| task | host | why |", "|---|---|---|"]
    away = [(t, v) for t, v in tasks.items() if v.get("host") != me]
    lines += [f"| `{t}` | {v.get('host')} | {v.get('why', '')} |" for t, v in away] or ["| — | — | — |"]
    lines += ["", "## Hosts reachable from here", "", "| alias | ip | kind / os | folders | capabilities |", "|---|---|---|---|---|"]
    for r in h.get("reaches", []):
        o = hosts.get(r, {})
        lines.append(f"| `{r}` | {o.get('ip', '?')} | {o.get('kind', '?')} / {o.get('os', '—')} | "
                     f"{fmt_folders(o.get('folders'))} | {', '.join(o.get('capabilities', [])) or '—'} |")
    if not h.get("reaches"):
        lines.append("| — | — | — | — | — |")
    fg = [k for k in h.get("reaches", []) if hosts.get(k, {}).get("kind") == "fortigate"]
    if fg:
        lines += ["", f"FortiGate devices ({', '.join(fg)}): FortiOS CLI over ssh, not Linux. Read-only "
                      "(`get`, `show`, `diagnose`) unless the task explicitly approves a `config` change."]
    lines += ["", f"_Rendered by hermes_installer from config/hosts.yaml; edit that file, not this one._", ""]
    return "\n".join(lines)


def soul_block(me: str, h: dict, hermes_home: Path) -> str:
    return (f"{BEGIN}\n"
            f"You run on host **{me}** ({h.get('kind', '?')}, {h.get('ip', '?')}). Before any task that touches another\n"
            f"machine, a database, a lab device or a GUI run, read {hermes_home}/HOST.md: it lists which tasks\n"
            f"must run on which host and the ssh alias to use. Say in your answer on which host each command ran.\n"
            f"{END}")


def upsert_soul(path: Path, block: str) -> str:
    text = path.read_text() if path.is_file() else ""
    if BEGIN in text and END in text:
        new = text[: text.index(BEGIN)] + block + text[text.index(END) + len(END):]
    else:
        new = (text.rstrip("\n") + "\n\n" if text.strip() else "") + block + "\n"
    return new


def upsert_env(path: Path, me: str) -> str:
    text = path.read_text() if path.is_file() else ""
    line = f"HERMES_HOST={me}"
    if re.search(r"^\s*(?:export\s+)?HERMES_HOST\s*=", text, flags=re.M):
        return re.sub(r"^\s*(?:export\s+)?HERMES_HOST\s*=.*$", line, text, count=1, flags=re.M)
    return (text.rstrip("\n") + "\n" if text.strip() else "") + line + "\n"


def env_name(host: str) -> str:
    return re.sub(r"[^A-Z0-9]", "_", host.upper())


def render_ssh_config(data: dict, env_path: Path) -> str:
    lines = ["# Written by hermes_installer (scripts/render_host_context.py) from config/hosts.yaml and",
             "# the SSH_<NAME>_* lines of HERMES_HOME/.env. Edit those, not this file.", ""]
    for name, h in data["hosts"].items():
        n = env_name(name)
        user = env_file_value(env_path, f"SSH_{n}_USER")
        if not user or not h.get("ip"):
            continue
        key = env_file_value(env_path, f"SSH_{n}_KEY")
        port = env_file_value(env_path, f"SSH_{n}_PORT") or "22"
        lines += [f"Host {name}", f"  HostName {h['ip']}", f"  User {user}", f"  Port {port}"]
        if key:
            lines.append(f"  IdentityFile {key}")
            lines.append("  IdentitiesOnly yes")
        elif env_file_value(env_path, f"SSH_{n}_PASSWORD"):
            lines.append(f"  # password login: SSH_{n}_PASSWORD in .env (sshpass -e)")
        if h.get("kind") == "fortigate":
            lines.append("  # FortiOS CLI, not a Linux shell")
        lines.append("  StrictHostKeyChecking accept-new")
        lines.append("")
    return "\n".join(lines)


def write_ssh_config(data: dict, env_path: Path, dry_run: bool) -> None:
    ssh_dir = Path.home() / ".ssh"
    cfg_dir = ssh_dir / "config.d"
    target = cfg_dir / "hermes_hosts"
    content = render_ssh_config(data, env_path)
    main_cfg = ssh_dir / "config"
    main_text = main_cfg.read_text() if main_cfg.is_file() else ""
    include_line = "Include config.d/*"
    needs_include = not re.search(r"^\s*Include\s+config\.d/\*\s*$", main_text, flags=re.M)
    hosts_written = [l.split()[1] for l in content.splitlines() if l.startswith("Host ")]
    if dry_run:
        print(f"--- {target} ---\n{content}")
        if needs_include:
            print(f"--- {main_cfg}: would prepend '{include_line}' ---")
        return
    cfg_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(ssh_dir, 0o700)
    target.write_text(content)
    os.chmod(target, 0o600)
    if needs_include:
        main_cfg.write_text(f"{include_line}\n\n{main_text}" if main_text else f"{include_line}\n")
        os.chmod(main_cfg, 0o600)
    print(f"ssh aliases: {', '.join(hosts_written) or 'none (no SSH_<NAME>_USER in .env)'} -> {target}"
          + (f"; added '{include_line}' to {main_cfg}" if needs_include else ""))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--hosts", default=str(REPO / "config" / "hosts.yaml"))
    ap.add_argument("--hermes-home", default=os.environ.get("HERMES_HOME") or str(Path.home() / ".hermes"))
    ap.add_argument("--host", help="host name from hosts.yaml; overrides HERMES_HOST and auto-detection")
    ap.add_argument("--tokens-file", default=str(REPO / "tokens.env") if (REPO / "tokens.env").is_file() else "")
    ap.add_argument("--check", action="store_true", help="print the resolved host and exit")
    ap.add_argument("--dry-run", action="store_true", help="print what would be written, write nothing")
    ap.add_argument("--no-env", action="store_true", help="do not write HERMES_HOST into HERMES_HOME/.env")
    ap.add_argument("--write-ssh-config", action="store_true", help="also write ~/.ssh/config.d/hermes_hosts from .env")
    args = ap.parse_args()

    data = load_yaml(Path(args.hosts).read_text())
    hosts = data.get("hosts") or {}
    if not hosts:
        sys.exit(f"!! no hosts in {args.hosts}")
    for t, v in (data.get("tasks") or {}).items():
        if v.get("host") not in hosts:
            sys.exit(f"!! task {t} points at unknown host {v.get('host')}")
    hermes_home = Path(args.hermes_home).expanduser()

    me, source = resolve(args, hosts, hermes_home)
    print(f"host: {me}  (from {source})")
    if args.check:
        return

    host_md = render_host_md(me, data, source)
    soul = upsert_soul(hermes_home / "SOUL.md", soul_block(me, hosts[me], hermes_home))
    env = None if args.no_env else upsert_env(hermes_home / ".env", me)

    if args.dry_run:
        print(f"\n--- {hermes_home}/HOST.md ---\n{host_md}")
        print(f"--- {hermes_home}/SOUL.md (managed block) ---\n{soul_block(me, hosts[me], hermes_home)}\n")
        if env is not None:
            print(f"--- {hermes_home}/.env ---\nHERMES_HOST={me}")
        if args.write_ssh_config:
            write_ssh_config(data, hermes_home / ".env", dry_run=True)
        return

    hermes_home.mkdir(parents=True, exist_ok=True)
    (hermes_home / "HOST.md").write_text(host_md)
    (hermes_home / "SOUL.md").write_text(soul)
    if env is not None:
        p = hermes_home / ".env"
        p.write_text(env)
        os.chmod(p, 0o600)
    print(f"wrote {hermes_home}/HOST.md, SOUL.md block" + ("" if env is None else ", .env HERMES_HOST"))
    if args.write_ssh_config:
        write_ssh_config(data, hermes_home / ".env", dry_run=False)


if __name__ == "__main__":
    main()
