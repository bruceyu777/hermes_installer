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
A shared lab node has no entry of its own: the `lab_nodes` class of hosts.yaml gives all-in-one-node16 the name
node16 (also valid for --host / HERMES_HOST, on that machine only), without ssh aliases and without touching ~/.ssh.
A configured name that disagrees with auto-detection is used, with a warning (a tokens.env copied
from another machine is the usual cause).

Routing is by capability: a task in hosts.yaml either names its `host` or says what it `needs` (a
capability); a skill says what it needs in its frontmatter (`metadata: {requires: [capability, ...]}`).
HOST.md resolves each need to `here`, `ssh <alias>` (a host that has it and that this host reaches),
or "not reachable from here". When a host moves or a second host gains a capability, only hosts.yaml
changes.

Other runtimes (Claude Code, opencode) get the same HOST.md with --host-md PATH, which writes only that
file (no SOUL.md, no .env, no ssh config). An unknown host gets a fail-closed HOST.md: no identity,
no delegation.

Usage:
  scripts/render_host_context.py                       # HERMES_HOME from env or ~/.hermes
  scripts/render_host_context.py --host cdn --dry-run  # show what would be written
  scripts/render_host_context.py --check               # print the resolved host only, write nothing
  scripts/render_host_context.py --host-md ~/.claude/HOST.md --skills-dir ~/.claude/skills
  scripts/render_host_context.py --where fortistack-node-login   # one capability or task: here / ssh <alias> / none (exit 2)
  scripts/render_host_context.py --probe               # measure tcp/ssh reach from here vs `reaches`; writes nothing
"""
import argparse
import ipaddress
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


def add_lab_node(data: dict) -> None:
    """On a shared lab node (hosts.yaml `lab_nodes`), add this machine to data["hosts"]; `lab_node: True` marks it."""
    hosts, cls = data["hosts"], dict(data.get("lab_nodes") or {})
    prefix, name_prefix = cls.pop("hostname_prefix", ""), cls.pop("name_prefix", "")
    hostname = socket.gethostname()
    m = re.fullmatch(re.escape(prefix) + r"(\d+)", hostname, flags=re.I) if prefix and name_prefix else None
    if not m or autodetect(hosts) or name_prefix + m.group(1) in hosts:   # an entry of its own comes first
        return
    ips = local_ips()   # docker and libvirt bridges are in there too: take the one in the lab network
    cidr = ((data.get("networks") or {}).get(cls.get("network")) or {}).get("cidr")
    lab_ips = [ip for ip in ips if cidr and "." in ip and ipaddress.ip_address(ip) in ipaddress.ip_network(cidr)]
    cls.update(hostname=hostname, lab_node=True)
    ip = next(iter(lab_ips + ips), "")
    if ip:
        cls["ip"] = ip
    hosts[name_prefix + m.group(1)] = cls


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


def caps(h: dict) -> list:
    return h.get("capabilities") or []


def task_hosts(task: dict, hosts: dict) -> list:
    """The hosts a task may run on: its `host`, or every host whose capabilities include its `needs`."""
    if task.get("host"):
        return [task["host"]]
    return [k for k, h in hosts.items() if task.get("needs") in caps(h)]


def route(me: str, hosts: dict, need: str = "", candidates=None) -> tuple:
    """('here', [me]) | ('ssh', [aliases this host reaches]) | ('none', [hosts that have it])."""
    if candidates is None:
        candidates = [k for k, h in hosts.items() if need in caps(h)]
    if me in candidates:
        return "here", [me]
    near = [c for c in candidates if c in (hosts[me].get("reaches") or [])]
    return ("ssh", near) if near else ("none", candidates)


def fmt_route(kind: str, where: list) -> str:
    if kind == "here":
        return "**here**"
    if kind == "ssh":
        return " or ".join(f"`ssh {w}`" for w in where)
    return "not reachable from here: hand it to the user" + (f" (has it: {', '.join(where)})" if where else " (no host has it)")


def skill_requirements(dirs: list) -> list:
    """(skill, [capability, ...]) for every <dir>/<skill>/SKILL.md whose frontmatter has metadata.requires."""
    found = {}
    for d in dirs:
        for f in sorted(Path(d).expanduser().glob("*/SKILL.md")):
            m = re.match(r"^---\n(.*?)\n---\n", f.read_text(encoding="utf-8", errors="replace"), re.S)
            try:
                fm = load_yaml(m.group(1)) if m else None
            except Exception:
                continue
            meta = fm.get("metadata") if isinstance(fm, dict) else None
            req = meta.get("requires") if isinstance(meta, dict) else None
            if isinstance(req, str):
                req = [r.strip() for r in req.split(",") if r.strip()]
            if req:
                found.setdefault(fm.get("name") or f.parent.name, [str(r) for r in req])
    return sorted(found.items())


def unknown_host_md(hosts_file: str, hosts: dict) -> str:
    return "\n".join([
        "# This host is not in the host list",
        "",
        f"hostname {socket.gethostname()}, ips {' '.join(local_ips())} match no entry in {hosts_file}.",
        "This host has no identity: do not delegate anything to another host and do not assume any capability.",
        "Work only with what is on this machine and say so. Fix: add the host to hosts.yaml",
        f"(known: {', '.join(hosts)}) or re-render with --host NAME.",
        ""])


def ssh_target(alias: str) -> tuple:
    """(hostname, port) that `ssh <alias>` would use, from `ssh -G` (no connection is made)."""
    try:
        out = subprocess.run(["ssh", "-G", alias], capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return alias, 22
    cfg = dict(l.split(" ", 1) for l in out.splitlines() if " " in l)
    return cfg.get("hostname", alias), int(cfg.get("port", "22"))


def ssh_aliases() -> set:
    names = set()
    ssh_dir = Path.home() / ".ssh"
    for f in [ssh_dir / "config", *sorted((ssh_dir / "config.d").glob("*"))]:
        if f.is_file():
            for l in f.read_text(errors="replace").splitlines():
                parts = l.split()
                if len(parts) >= 2 and parts[0].lower() == "host":
                    names.update(p for p in parts[1:] if "*" not in p and "?" not in p)
    return names


def tcp_open(host: str, port: int, timeout: float = 3.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def probe(me: str, hosts: dict) -> None:
    """Measure, from this host, port reach and key login to every other host; compare with `reaches`. Read-only."""
    declared = set(hosts[me].get("reaches") or [])
    aliases = ssh_aliases()
    rows, notes = [], []
    for k, h in hosts.items():
        if k == me:
            continue
        target, port = ssh_target(k) if k in aliases else (h.get("ip") or k, 22)
        tcp = tcp_open(target, port)
        if h.get("kind") == "fortigate":
            login = "n/a (FortiOS)"
        elif k not in aliases:
            login = "no ssh alias"
        elif not tcp:
            login = "—"
        else:
            r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", k, "true"],
                               stdin=subprocess.DEVNULL, capture_output=True, timeout=20)
            login = "ok" if r.returncode == 0 else "no key login"
        rows.append((k, f"{target}:{port}", "yes" if k in declared else "no", "open" if tcp else "closed", login))
        if k in declared and not tcp:
            notes.append(f"{k}: listed in reaches, but {target}:{port} is closed from here")
        if k not in declared and tcp and login == "ok":
            notes.append(f"{k}: key login works but it is not in reaches")
    print(f"probe from {me} ({socket.gethostname()})")
    print(f"{'host':18} {'target':22} {'in reaches':10} {'port':7} login")
    for r in rows:
        print(f"{r[0]:18} {r[1]:22} {r[2]:10} {r[3]:7} {r[4]}")
    for n in notes:
        print(f"!! {n}")


def render_host_md(me: str, data: dict, source: str, skills=()) -> str:
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
    routes = {t: route(me, hosts, candidates=task_hosts(v, hosts)) for t, v in tasks.items()}
    here = [(t, v) for t, v in tasks.items() if routes[t][0] == "here"]
    lines += [f"- `{t}` — {v.get('why', '')}" for t, v in here] or ["- none"]
    lines += ["", "## Tasks that must run elsewhere", "",
              "Run them with `ssh <alias> '<command>'` (alias = host name below; credentials come from",
              "`SSH_<NAME>_*` in `.env` through `~/.ssh/config.d/hermes_hosts`). Do not copy credentials",
              "or data back to this host; report a summary and the remote path. Say on which host each command ran.",
              "",
              "Delegate commands, not agents: you stay in charge here, run each command over ssh and read its",
              "output here. A write (trigger, migration, delete, restart, config change) is approved by the user",
              "here before the ssh call; never start an unattended agent (`claude -p`, `hermes -z`, `opencode run`)",
              "on the other host to do it.",
              "", "| task | needs | run it with | why |", "|---|---|---|---|"]
    away = [(t, v) for t, v in tasks.items() if routes[t][0] != "here"]
    lines += [f"| `{t}` | {v.get('needs') or 'host ' + str(v.get('host'))} | {fmt_route(*routes[t])} | {v.get('why', '')} |"
              for t, v in away] or ["| — | — | — | — |"]
    if skills:
        lines += ["", "## Skills and where their work runs", "",
                  "Each skill's `metadata.requires` (capabilities) matched against the hosts. `here` = run it on this",
                  "host; `ssh <alias>` = run that part's commands on that host, under the rules above.",
                  "", "| skill | needs → run it with |", "|---|---|"]
        for name, reqs in skills:
            cells = "; ".join(f"{r} → {fmt_route(*route(me, hosts, need=r))}" for r in reqs)
            lines.append(f"| `{name}` | {cells} |")
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
    lines += ["", "_Rendered by hermes_installer from config/hosts.yaml (and the skills' metadata.requires); edit those,"
                  " not this file, then re-render: `./install.sh` (Hermes) or `make host` (cc_skills)._", ""]
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
    ap.add_argument("--skills-dir", action="append", default=[],
                    help="directory of <skill>/SKILL.md whose metadata.requires go into HOST.md (repeatable)")
    ap.add_argument("--host-md", metavar="PATH",
                    help="write only HOST.md to PATH (Claude Code, opencode): no SOUL.md, .env or ssh config")
    ap.add_argument("--where", metavar="NEED", help="resolve one capability or task name from this host and exit")
    ap.add_argument("--probe", action="store_true", help="measure tcp/ssh reach from this host vs `reaches`; writes nothing")
    args = ap.parse_args()

    data = load_yaml(Path(args.hosts).read_text())
    hosts = data.get("hosts") or {}   # the same dict as data["hosts"]: add_lab_node adds to it
    if not hosts:
        sys.exit(f"!! no hosts in {args.hosts}")
    for t, v in (data.get("tasks") or {}).items():
        if v.get("host"):
            if v["host"] not in hosts:
                sys.exit(f"!! task {t} points at unknown host {v['host']}")
        elif v.get("needs"):
            if not task_hosts(v, hosts):
                print(f"!! warning: task {t} needs '{v['needs']}' but no host has that capability", file=sys.stderr)
        else:
            sys.exit(f"!! task {t} has neither `host` nor `needs`")
    hermes_home = Path(args.hermes_home).expanduser()
    add_lab_node(data)

    try:
        me, source = resolve(args, hosts, hermes_home)
    except SystemExit as unresolved:
        if args.host_md and not args.dry_run:   # fail closed: the runtime still loads a HOST.md, and it says "no identity"
            out = Path(args.host_md).expanduser()
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(unknown_host_md(args.hosts, hosts))
            print(f"wrote fail-closed {out}", file=sys.stderr)
        raise unresolved
    print(f"host: {me}  (from {source})")
    if args.check:
        return
    if args.probe:
        probe(me, hosts)
        return
    if args.where:
        task = (data.get("tasks") or {}).get(args.where)
        kind, where = route(me, hosts, candidates=task_hosts(task, hosts)) if task else route(me, hosts, need=args.where)
        print(f"{args.where}: {kind}" + ("" if kind == "here" else f" {' '.join(where) or '-'}"))
        sys.exit(0 if kind != "none" else 2)

    skills = skill_requirements(args.skills_dir)
    host_md = render_host_md(me, data, source, skills)
    if args.host_md:
        out = Path(args.host_md).expanduser()
        if args.dry_run:
            print(f"\n--- {out} ---\n{host_md}")
            return
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(host_md)
        print(f"wrote {out}" + (f" ({len(skills)} skills with metadata.requires)" if args.skills_dir else ""))
        return

    if args.write_ssh_config and hosts[me].get("lab_node"):
        args.write_ssh_config = False
        print("ssh aliases: none on a shared lab node (~/.ssh is everybody's, left alone)")
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
