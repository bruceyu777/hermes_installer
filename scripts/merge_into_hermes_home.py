#!/usr/bin/env python3
"""install.sh helper: put the tokens and this repo's config into a Hermes home.

    merge_into_hermes_home.py tokens --tokens-file FILE --hermes-home DIR
    merge_into_hermes_home.py config --fragment config/config.yaml --hermes-home DIR
    merge_into_hermes_home.py adapt  --preferences config/model-preferences.yaml --fragment config/config.yaml
                                     --hermes-home DIR [--dry-run]
    merge_into_hermes_home.py chain  --hermes-home DIR    (prints "KEY_NAME MODEL BASE_URL" per link in use)

adapt probes every key with a one-token chat call and rebuilds the model chain from the
prefer lists (see config/model-preferences.yaml), then merges the config like `config`.

Run it with the Python of the Hermes venv: the config step needs ruamel.yaml, which
keeps the comments and layout of your config.yaml (same settings Hermes itself uses).
Files are only rewritten when something changes, after a copy to <name>.bak-<time>.
No secret is ever printed.
"""

import argparse
import base64
import copy
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

PLACEHOLDER = "NOT_SET"

# tokens.env name -> ~/.hermes/.env name. Jenkins is stored ready for an HTTP Basic header.
ENV_NAME_OF_TOKEN = {
    "FOS_AI_API_KEY": "FOS_AI_API_KEY",
    "FOS_AI_FALLBACK_API_KEY": "FOS_AI_FALLBACK_API_KEY",
    "LOCAL_QWEN_API_KEY": "LOCAL_QWEN_API_KEY",
    "MANTIS_MCP_TOKEN": "MANTIS_MCP_TOKEN",
    "JENKINS_MCP_TOKEN": "JENKINS_MCP_BASIC",
    "LOGINTEL_MCP_KEY": "LOGINTEL_MCP_KEY",
}
ENV_BLOCK_HEADER = "# ── hermes_installer tokens (written by install.sh from tokens.env) ──"

# MCP server -> the .env name its Authorization header needs. OAuth servers are on when
# `hermes mcp login <server>` has stored a token under mcp-tokens/.
TOKEN_OF_MCP_SERVER = {"logintel": "LOGINTEL_MCP_KEY", "mantis-tools": "MANTIS_MCP_TOKEN", "jenkins": "JENKINS_MCP_BASIC"}
OAUTH_MCP_SERVERS = ("microsoft365",)
ENV_REFERENCE = re.compile(r"^\$\{(\w+)\}$")


def read_env_file(path):
    """NAME -> value for the KEY=VALUE lines of a dotenv-style file (quotes stripped)."""
    values = {}
    if not os.path.exists(path):
        return values
    with open(path, encoding="utf-8") as env_file:
        for line in env_file:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, value = line.split("=", 1)
            name = name.strip()
            if name.startswith("export "):
                name = name[len("export "):].strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            values[name] = value
    return values


def has_value(values, name):
    return values.get(name, "") not in ("", PLACEHOLDER)


def write_if_changed(path, new_text, label):
    old_text = None
    if os.path.exists(path):
        with open(path, encoding="utf-8") as existing_file:
            old_text = existing_file.read()
    if old_text == new_text:
        print(f"    {label} unchanged")
        return
    if old_text is not None:
        backup_path = f"{path}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
        with open(os.open(backup_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as backup:
            backup.write(old_text)
        print(f"    previous {label} saved as {os.path.basename(backup_path)}")
    temporary_path = f"{path}.tmp-{os.getpid()}"
    with open(os.open(temporary_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as new_file:
        new_file.write(new_text)
    os.replace(temporary_path, path)
    os.chmod(path, 0o600)
    print(f"    {label} written")


# ── tokens ───────────────────────────────────────────────────────────────────
def merge_tokens(tokens_file, hermes_home):
    """Environment variables first, then non-empty tokens.env values. Empty = keep what .env has."""
    given = {name: os.environ.get(name, "").strip() for name in ENV_NAME_OF_TOKEN}
    if os.path.exists(tokens_file):
        os.chmod(tokens_file, 0o600)
        for name, value in read_env_file(tokens_file).items():
            if name in given and value:
                given[name] = value
        print(f"    tokens read from {tokens_file}")
    else:
        print(f"    !! no {tokens_file}: keeping the tokens already in .env (copy tokens.env.example to create it)")

    for name, value in given.items():
        if value and any(character.isspace() for character in value):
            print(f"    !! {name} contains whitespace; ignored")
            given[name] = ""
    jenkins = given["JENKINS_MCP_TOKEN"]
    if jenkins and ":" not in jenkins:
        print("    !! JENKINS_MCP_TOKEN must be user:apitoken; ignored")
        jenkins = ""
    given["JENKINS_MCP_TOKEN"] = base64.b64encode(jenkins.encode()).decode() if jenkins else ""

    env_path = os.path.join(hermes_home, ".env")
    current = read_env_file(env_path)
    # Fallback key: its own value, else the one already installed, else reuse the personal key.
    if not given["FOS_AI_FALLBACK_API_KEY"] and not has_value(current, "FOS_AI_FALLBACK_API_KEY"):
        given["FOS_AI_FALLBACK_API_KEY"] = given["FOS_AI_API_KEY"] or current.get("FOS_AI_API_KEY", "")

    wanted = {ENV_NAME_OF_TOKEN[name]: value for name, value in given.items() if value}
    lines = []
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as env_file:
            lines = env_file.read().splitlines()
    written = set()
    for index, line in enumerate(lines):
        match = re.match(r"^\s*(?:export\s+)?(\w+)\s*=", line)
        if match and match.group(1) in wanted:
            lines[index] = f"{match.group(1)}={wanted[match.group(1)]}"
            written.add(match.group(1))
    missing_lines = [f"{name}={value}" for name, value in wanted.items() if name not in written]
    if missing_lines:
        if ENV_BLOCK_HEADER not in lines:
            lines += ["", ENV_BLOCK_HEADER]
        lines += missing_lines
    write_if_changed(env_path, "\n".join(lines) + "\n", ".env")

    installed = read_env_file(env_path)
    for token_name, env_name in ENV_NAME_OF_TOKEN.items():
        state = "set" if env_name in wanted else ("kept" if has_value(installed, env_name) else "missing")
        print(f"    {token_name:<24} {state:<8} .env {env_name}")


# ── config ───────────────────────────────────────────────────────────────────
def replace_mapping_in_place(existing, new):
    """Give `existing` exactly the keys of `new`, keeping the comments attached to it."""
    for key in [key for key in existing if key not in new]:
        del existing[key]
    for key, value in new.items():
        existing[key] = value


def round_trip_yaml():
    from ruamel.yaml import YAML

    yaml = YAML(typ="rt")
    yaml.preserve_quotes = True
    yaml.allow_unicode = True
    yaml.default_flow_style = False
    yaml.indent(mapping=2, sequence=4, offset=2)
    yaml.allow_duplicate_keys = True
    return yaml


def model_chain(fragment):
    """The primary plus the fallbacks of the fragment, in order, with the .env name of each key."""
    primary = fragment["model"]
    chain = [{"model": primary["default"], "base_url": primary["base_url"],
              "token": ENV_REFERENCE.match(primary["api_key"]).group(1),
              "extra": {key: value for key, value in primary.items()
                        if key not in ("default", "provider", "base_url", "api_key")}}]
    chain += [{"model": entry["model"], "base_url": entry["base_url"], "token": entry["key_env"], "extra": {}}
              for entry in fragment["fallback_providers"]]
    return chain


def chain_in_config(config):
    """The links of the model chain a config.yaml uses now (empty for a new config)."""
    chain = []
    model = config.get("model") if config else None
    if isinstance(model, dict) and model.get("default") and ENV_REFERENCE.match(str(model.get("api_key", ""))):
        chain.append({"model": model["default"], "base_url": model["base_url"],
                      "token": ENV_REFERENCE.match(model["api_key"]).group(1), "extra": {}})
    for entry in (config or {}).get("fallback_providers") or []:
        if isinstance(entry, dict) and entry.get("key_env"):
            chain.append({"model": entry["model"], "base_url": entry["base_url"], "token": entry["key_env"], "extra": {}})
    return chain


def load_config(hermes_home):
    from ruamel.yaml.comments import CommentedMap

    config_path = os.path.join(hermes_home, "config.yaml")
    config = None
    if os.path.exists(config_path):
        with open(config_path, encoding="utf-8") as config_file:
            config = round_trip_yaml().load(config_file)
    return config if isinstance(config, CommentedMap) else CommentedMap(config or {})


def print_chain(hermes_home):
    for link in chain_in_config(load_config(hermes_home)):
        print(link["token"], link["model"], link["base_url"])


# ── adapt: measure what each key can call now, then choose ───────────────────
def gateway_request(url, key, payload=None, timeout=60):
    """(HTTP status, parsed JSON body or {}); status None when the gateway cannot be reached."""
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(url, data=data, headers={"Authorization": f"Bearer {key}",
                                                              "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        try:
            return error.code, json.loads(error.read() or b"{}")
        except ValueError:
            return error.code, {}
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None, {}


def error_text(body):
    error = body.get("error") if isinstance(body, dict) else None
    return str(error.get("message") if isinstance(error, dict) else error or "")


def probe_model(base_url, key, model):
    """("ok" | "refused" | "unreachable", reason). Only a chat call that answers counts."""
    status, body = gateway_request(f"{base_url}/chat/completions", key, {
        "model": model, "max_tokens": 16, "messages": [{"role": "user", "content": "Reply with exactly: OK"}]})
    if status is None:
        return "unreachable", "no response"
    if status == 200 and not error_text(body):
        return "ok", ""
    reason = error_text(body)[:90] or f"HTTP {status}"
    if status == 429 and "budget" not in reason.lower():
        return "ok", "rate-limited right now, still usable"
    return "refused", f"HTTP {status}: {reason}"


def adapt_chain(preferences_path, fragment, config, env):
    with open(preferences_path, encoding="utf-8") as preferences_file:
        preferences = round_trip_yaml().load(preferences_file)
    known = {model for account in preferences["accounts"] for model in account["prefer"]}
    not_chat = [pattern.lower() for pattern in preferences.get("not_chat_patterns") or []]
    current = chain_in_config(config)
    chosen, used = [], set()
    for account in preferences["accounts"]:
        key_name, base_url, take = account["key"], account["base_url"], int(account.get("take", 1))
        if not has_value(env, key_name):
            print(f"    {key_name}: no key, skipped")
            continue
        status, body = gateway_request(f"{base_url}/models", env[key_name], timeout=20)
        if status is None:
            kept = [link for link in current if link["token"] == key_name]
            print(f"    {key_name}: gateway unreachable, keeping its current links: "
                  f"{', '.join(link['model'] for link in kept) or 'none'}")
            chosen += kept
            used.update((link["base_url"], link["model"]) for link in kept)
            continue
        listed = [item.get("id") for item in (body.get("data") or []) if isinstance(item, dict)]
        picks = []
        for model in account["prefer"]:
            if len(picks) >= take:
                break
            if (base_url, model) in used:   # Hermes skips a second link to the same deployment
                continue
            result, reason = probe_model(base_url, env[key_name], model)
            print(f"    {key_name:<24} {model:<24} {result}{' (' + reason + ')' if reason else ''}")
            if result == "ok":
                picks.append({"model": model, "base_url": base_url, "token": key_name, "extra": {}})
                used.add((base_url, model))
        new_models = [model for model in listed if model not in known
                      and not any(pattern in model.lower() for pattern in not_chat)]
        if new_models:
            print(f"    {key_name}: new models seen, not used until added to a prefer list: {', '.join(new_models)}")
        if not picks:
            print(f"    !! {key_name}: none of its preferred models answers right now")
        chosen += picks
    if not chosen:
        chosen = current or model_chain(fragment)
        print("    !! no model answered: keeping the chain " + ", ".join(link["model"] for link in chosen))
    return chosen


def merge_config(fragment_path, hermes_home, preferences_path=None, dry_run=False):
    from ruamel.yaml.comments import CommentedMap, CommentedSeq

    yaml = round_trip_yaml()
    with open(fragment_path, encoding="utf-8") as fragment_file:
        fragment = yaml.load(fragment_file)
    config_path = os.path.join(hermes_home, "config.yaml")
    config = load_config(hermes_home)
    env = read_env_file(os.path.join(hermes_home, ".env"))

    if preferences_path:
        available = adapt_chain(preferences_path, fragment, config, env)
    else:   # the fixed chain of the fragment, cut down to the links this person has a key for
        chain = model_chain(fragment)
        available = [link for link in chain if has_value(env, link["token"])]
        if not available:
            print("    !! no LLM key in .env at all: Hermes will not be able to answer")
            available = chain
    if dry_run:
        print("    chain (dry run, nothing written): " + " -> ".join(link["model"] for link in available))
        return
    first = available[0]
    model = CommentedMap([("default", first["model"]), ("provider", "custom"), ("base_url", first["base_url"]),
                          ("api_key", "${%s}" % first["token"])])
    model.update(first["extra"])
    if isinstance(config.get("model"), CommentedMap):
        replace_mapping_in_place(config["model"], model)
    else:
        config["model"] = model
    config["fallback_providers"] = CommentedSeq(
        CommentedMap([("provider", "custom"), ("model", link["model"]), ("base_url", link["base_url"]),
                      ("key_env", link["token"])])
        for link in available[1:])

    # MCP servers: this repo's entries are replaced, servers of your own are kept.
    servers = config.get("mcp_servers")
    if not isinstance(servers, CommentedMap):
        servers = CommentedMap()
        config["mcp_servers"] = servers
    for name, server in fragment["mcp_servers"].items():
        server = copy.deepcopy(server)
        if name in TOKEN_OF_MCP_SERVER:
            server.pop("enabled", None)
            if not has_value(env, TOKEN_OF_MCP_SERVER[name]):
                server["enabled"] = False
        elif name in OAUTH_MCP_SERVERS:
            server["enabled"] = os.path.exists(os.path.join(hermes_home, "mcp-tokens", f"{name}.json"))
        servers[name] = server

    # Plugins: enable ours, keep the rest of the list.
    plugins = config.get("plugins")
    if not isinstance(plugins, CommentedMap):
        plugins = CommentedMap()
        config["plugins"] = plugins
    enabled = plugins.get("enabled")
    if not isinstance(enabled, list):
        enabled = CommentedSeq()
        plugins["enabled"] = enabled
    for plugin_name in fragment["plugins"]["enabled"]:
        if plugin_name not in enabled:
            enabled.append(plugin_name)
        disabled = plugins.get("disabled")
        if isinstance(disabled, list) and plugin_name in disabled:
            disabled.remove(plugin_name)

    buffer = io.StringIO()
    yaml.dump(config, buffer)
    write_if_changed(config_path, buffer.getvalue(), "config.yaml")

    print("    chain: " + " -> ".join(f"{link['model']} ({link['token']})" for link in available))
    print("    MCP: " + ", ".join(f"{name}={'on' if servers[name].get('enabled', True) else 'off'}"
                                  for name in fragment["mcp_servers"]))
    print(f"    plugins enabled: {', '.join(str(name) for name in enabled)}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    subcommands = parser.add_subparsers(dest="command", required=True)
    tokens_parser = subcommands.add_parser("tokens")
    tokens_parser.add_argument("--tokens-file", required=True)
    tokens_parser.add_argument("--hermes-home", required=True)
    for name in ("config", "adapt"):
        command_parser = subcommands.add_parser(name)
        command_parser.add_argument("--fragment", required=True)
        command_parser.add_argument("--hermes-home", required=True)
        if name == "adapt":
            command_parser.add_argument("--preferences", required=True)
            command_parser.add_argument("--dry-run", action="store_true")
    chain_parser = subcommands.add_parser("chain")
    chain_parser.add_argument("--hermes-home", required=True)
    arguments = parser.parse_args()
    if arguments.command == "chain":
        print_chain(arguments.hermes_home)
        return
    os.makedirs(arguments.hermes_home, exist_ok=True)
    if arguments.command == "tokens":
        merge_tokens(arguments.tokens_file, arguments.hermes_home)
    elif arguments.command == "config":
        merge_config(arguments.fragment, arguments.hermes_home)
    else:
        merge_config(arguments.fragment, arguments.hermes_home, arguments.preferences, arguments.dry_run)


if __name__ == "__main__":
    sys.exit(main())
