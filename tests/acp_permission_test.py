"""Drive `hermes acp` like the VS Code ACP client does and answer its permission requests.

Prompt 1: the gated tool is requested -> we answer "deny".
Prompt 2: same request -> we answer "allow_once".
Prints every permission request, the answer given, and the agent's final text per prompt.
Usage: HERMES_HOME=... python acp_permission_test.py WORKDIR PROMPT
"""
import json
import os
import select
import subprocess
import sys
import time

workdir, prompt_text = sys.argv[1], sys.argv[2]
agent = subprocess.Popen([os.path.expanduser("~/.local/bin/hermes"), "acp"], cwd=workdir,
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=open(os.path.join(workdir, "acp-stderr.log"), "w"),
                         text=True, bufsize=1)
next_id = [0]
buffer = [""]


def send(message):
    agent.stdin.write(json.dumps(message) + "\n")
    agent.stdin.flush()


def request(method, params):
    next_id[0] += 1
    send({"jsonrpc": "2.0", "id": next_id[0], "method": method, "params": params})
    return next_id[0]


def read_message(timeout):
    deadline = time.time() + timeout
    while "\n" not in buffer[0]:
        remaining = deadline - time.time()
        if remaining <= 0:
            return None
        ready, _, _ = select.select([agent.stdout], [], [], remaining)
        if not ready:
            return None
        chunk = os.read(agent.stdout.fileno(), 65536).decode()
        if not chunk:
            return None
        buffer[0] += chunk
    line, buffer[0] = buffer[0].split("\n", 1)
    return json.loads(line) if line.strip() else read_message(timeout)


def wait_for_response(request_id, permission_answer=None, timeout=300):
    text_parts, permissions = [], []
    deadline = time.time() + timeout
    while time.time() < deadline:
        message = read_message(deadline - time.time())
        if message is None:
            break
        if message.get("method") == "session/request_permission":
            params = message["params"]
            options = [option["optionId"] for option in params.get("options", [])]
            permissions.append({"title": params.get("toolCall", {}).get("title"), "options": options,
                                "answer": permission_answer})
            send({"jsonrpc": "2.0", "id": message["id"],
                  "result": {"outcome": {"outcome": "selected", "optionId": permission_answer}}})
        elif message.get("method") == "session/update":
            update = message["params"].get("update", {})
            if update.get("sessionUpdate") == "agent_message_chunk":
                content = update.get("content", {})
                if content.get("type") == "text":
                    text_parts.append(content.get("text", ""))
        elif "method" in message and "id" in message:  # any other client request: not supported
            send({"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32601, "message": "not supported"}})
        elif message.get("id") == request_id:
            return message, "".join(text_parts), permissions
    return None, "".join(text_parts), permissions


initialize_id = request("initialize", {"protocolVersion": 1, "clientCapabilities": {
    "fs": {"readTextFile": False, "writeTextFile": False}, "terminal": False}})
print("initialize:", json.dumps(wait_for_response(initialize_id)[0].get("result", {}).get("agentInfo")))
time.sleep(15)  # let the background MCP discovery finish, as it has by the time a person types
new_session_id = request("session/new", {"cwd": workdir, "mcpServers": []})
session_id = wait_for_response(new_session_id)[0]["result"]["sessionId"]

for answer in ("deny", "allow_once"):
    prompt_id = request("session/prompt", {"sessionId": session_id, "prompt": [{"type": "text", "text": prompt_text}]})
    response, text, permissions = wait_for_response(prompt_id, permission_answer=answer)
    print(f"--- answer to permission requests: {answer}")
    print("    permission requests:", json.dumps(permissions))
    print("    stop reason:", (response or {}).get("result", {}).get("stopReason"))
    print("    agent text:", text.strip()[:500].replace("\n", " "))

agent.stdin.close()
agent.terminate()
agent.wait(timeout=20)
