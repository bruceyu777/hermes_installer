"""Discover MCP tools the way a Hermes session does and print what the model would see.

Run with the Hermes venv Python and HERMES_HOME pointing at the home to inspect.
"""
import os
import sys

sys.path.insert(0, os.path.expanduser("~/.hermes/hermes-agent"))

from hermes_cli.env_loader import load_hermes_dotenv

load_hermes_dotenv()

from tools.mcp_tool_discovery import discover_mcp_tools
from tools.registry import registry

registered = discover_mcp_tools()
by_server = {}
for tool_name in sorted(registered):
    server = tool_name.split("__")[1] if tool_name.startswith("mcp__") else "?"
    by_server.setdefault(server, []).append(tool_name.split("__", 2)[-1])
for server, tools in by_server.items():
    print(f"{server}: {len(tools)} tools")
    print("    " + " ".join(tools))
all_names = registry.get_all_tool_names()
print("registry has", len([name for name in all_names if name.startswith("mcp__")]), "mcp__ tools")
