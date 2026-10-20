"""chain-adapt: dynamic model detection and switch for Hermes.

Hermes already walks ``fallback_providers`` when a call fails. What it does not do is
move ``model.default`` off a model the gateway retired: every new session would try it
first until the next ``install.sh --adapt`` (cron, every 15 min). This plugin runs that
re-measure at once when a call fails for good — HTTP 401/403/404, or a message with
"does not permit" / "unknown model" — using the command the installer wrote into
``HERMES_HOME/adapt-status.json``, detached and debounced (one run per 5 min however many
sessions see the refusal). A 429 "no budget" or a 5xx is transient: the fallback chain
handles it and nothing is re-measured. Only our gateways count (the base URLs of the
measured chain); a model of some other provider the user added by hand is ignored.

At session start it prints one notice (stderr, interactive terminals only, never into
the ACP / gateway streams) when the measured chain changed since the last notice: the
chain in use, what is refused right now, and the models the gateway lists that nobody
adopted yet — adopting one stays a human decision (prefer list in
config/model-preferences.yaml, then install.sh --adapt).
"""

import json
import os
import subprocess
import sys
import time

PERMANENT_STATUSES = {401, 403, 404}
PERMANENT_PATTERNS = ("does not permit", "not permitted", "unknown model", "model not found",
                      "no such model", "unauthorized", "forbidden")
DEBOUNCE_SECONDS = 300
NOTICE_PLATFORMS = ("cli", "")   # the CLI only: an ACP or gateway session has no terminal to print to


def hermes_home():
    return os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")


def status_path():
    return os.path.join(hermes_home(), "adapt-status.json")


def read_json(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def write_json(path, value):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path + ".tmp", "w", encoding="utf-8") as handle:
            json.dump(value, handle)
        os.replace(path + ".tmp", path)
    except OSError:
        pass


def is_permanent(status_code, error):
    """A refusal that will not go away by itself (the model is gone for this key)."""
    if status_code in PERMANENT_STATUSES:
        return True
    message = str(error.get("message") or "") if isinstance(error, dict) else str(error or "")
    lowered = message.lower()
    return any(pattern in lowered for pattern in PERMANENT_PATTERNS)


def managed_base_url(base_url, status):
    bases = {str(link.get("base_url", "")).rstrip("/") for link in status.get("chain") or [] if isinstance(link, dict)}
    return bool(base_url) and str(base_url).rstrip("/") in bases


def start_adapt(status, reason):
    """Run the installer's re-measure detached; False when nothing started (no command / debounced)."""
    command = status.get("adapt_command")
    if not isinstance(command, list) or not command or not os.path.exists(command[0]):
        return False
    stamp_path = os.path.join(hermes_home(), "adapt-trigger.json")
    stamp = read_json(stamp_path) or {}
    now = time.time()
    if now - float(stamp.get("at") or 0) < DEBOUNCE_SECONDS:
        return False
    write_json(stamp_path, {"at": now, "reason": reason})
    log_path = os.path.join(hermes_home(), "logs", "hermes_installer_adapt.log")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as log:
        log.write(f"\n==> re-measure triggered by plugin chain-adapt ({time.strftime('%Y-%m-%dT%H:%M:%S')}): {reason}\n")
        log.flush()
        subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                         env={**os.environ, "HERMES_HOME": hermes_home()}, start_new_session=True, close_fds=True)
    return True


def on_api_request_error(status_code=None, error=None, model="", base_url="", **_):
    """Observer: never raises, never changes the turn (Hermes walks the fallback chain as before)."""
    try:
        status = read_json(status_path()) or {}
        if not status or not managed_base_url(base_url, status) or not is_permanent(status_code, error):
            return None
        start_adapt(status, f"{model or 'model'} refused{f' (HTTP {status_code})' if status_code else ''} on {base_url}")
    except Exception:  # noqa: BLE001 — an observer must not break the turn
        pass
    return None


def on_session_start(platform="", **_):
    """One stderr notice per re-measured chain, in interactive CLI sessions only."""
    try:
        if platform not in NOTICE_PLATFORMS or not sys.stderr.isatty():
            return None
        status = read_json(status_path()) or {}
        changed_at = status.get("changed_at")
        if not changed_at:
            return None
        seen_path = os.path.join(hermes_home(), "adapt-status.seen")
        if (read_json(seen_path) or {}).get("changed_at") == changed_at:
            return None
        write_json(seen_path, {"changed_at": changed_at})
        chain = " -> ".join(str(link.get("model")) for link in status.get("chain") or [] if isinstance(link, dict))
        lines = [f"[chain-adapt] model chain measured {status.get('measured_at', '')}: {chain or 'empty'}"]
        refused = status.get("refused") or {}
        if refused:
            lines.append("[chain-adapt] refused right now: " + "; ".join(f"{key} ({why})" for key, why in refused.items()))
        new_models = status.get("new_models") or {}
        if new_models:
            seen = "; ".join(f"{key}: {', '.join(models)}" for key, models in new_models.items())
            lines.append("[chain-adapt] new models seen, not used until added to config/model-preferences.yaml "
                         f"(then install.sh --adapt): {seen}")
        print("\n".join(lines), file=sys.stderr)
    except Exception:  # noqa: BLE001
        pass
    return None


def register(ctx):
    ctx.register_hook("api_request_error", on_api_request_error)
    ctx.register_hook("on_session_start", on_session_start)
