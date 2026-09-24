"""mcp-ask-first: ask before MCP tools that change shared systems.

Hermes can only gate MCP tools per server (``trust: untrusted`` asks before every
tool without a ``readOnlyHint`` annotation, and the Mantis, Jenkins and Log
Intelligence servers annotate none), so this hook does it per tool instead. The
listed tools go through Hermes' human-approval gate: a prompt in the CLI, a
permission request in VS Code (ACP).

Runs with nobody to answer are blocked outright: ``hermes -z`` turns every
approval off (HERMES_YOLO_MODE=1), so an "approve" there would just run the tool.
Same for ``hermes chat -q`` and cron.

Hermes names MCP tools ``mcp__<server>__<tool>`` with ``-`` and ``.`` turned into ``_``.
Keep this list in step with the ``"ask"`` rules of the opencode config.
"""

import json
import os

ASK_FIRST_TOOLS = {
    "mcp__mantis_tools__create_mantis": "file a new Mantis bug",
    "mcp__mantis_tools__mantis_add_note": "add a note to a Mantis bug",
    "mcp__mantis_tools__send_email": "send an email",
    "mcp__jenkins__triggerBuild": "start a Jenkins build",
    "mcp__jenkins__rebuildBuild": "re-run a Jenkins build",
}

ARGUMENT_PREVIEW_CHARACTERS = 300
NO_HUMAN_SESSION_MARKERS = ("HERMES_SINGLE_QUERY_SESSION", "HERMES_CRON_SESSION")


def nobody_can_answer():
    """True in one-shot (-z), single-query (-q) and cron runs."""
    try:
        from tools.approval_context import _is_cron_approval_context, _is_single_query_approval_context
        return _is_single_query_approval_context() or _is_cron_approval_context()
    except ImportError:  # the helpers moved in a newer Hermes: read the same markers directly
        return any(os.environ.get(name, "").strip().lower() in ("1", "true", "yes") for name in NO_HUMAN_SESSION_MARKERS)


def ask_before_shared_system_change(tool_name="", args=None, **_):
    action = ASK_FIRST_TOOLS.get(tool_name)
    if action is None:
        return None
    if nobody_can_answer():
        return {"action": "block",
                "message": f"Not run: '{tool_name}' would {action}, which needs the user's approval, and this "
                           "run (hermes -z / chat -q / cron) has nobody to ask. Tell the user to run the request "
                           "in an interactive Hermes session (terminal or VS Code)."}
    argument_preview = json.dumps(args or {}, ensure_ascii=False)
    if len(argument_preview) > ARGUMENT_PREVIEW_CHARACTERS:
        argument_preview = argument_preview[:ARGUMENT_PREVIEW_CHARACTERS] + "..."
    # rule_key = tool name, so "allow for this session" covers later calls of the same tool.
    return {"action": "approve", "message": f"{action}: {argument_preview}", "rule_key": tool_name}


def register(ctx):
    ctx.register_hook("pre_tool_call", ask_before_shared_system_change)
