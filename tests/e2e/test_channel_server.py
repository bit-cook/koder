#!/usr/bin/env python3
"""Minimal channel MCP server for E2E testing — raw JSON-RPC over stdio.

Declares ``claude/channel`` experimental capability.  Listens on HTTP
for POST requests and pushes each body as a ``notifications/claude/channel``
JSON-RPC notification over stdout (which is the MCP stdio transport).

Exposes a ``reply`` MCP tool so the agent can send messages back.

Usage::

    python tests/e2e/test_channel_server.py
    CHANNEL_PORT=9999 python tests/e2e/test_channel_server.py
"""

from __future__ import annotations

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = int(os.environ.get("CHANNEL_PORT", "18787"))

# Collected replies for test assertion
_replies: list[dict] = []

# Lock for stdout writes (JSON-RPC messages must not interleave)
_write_lock = threading.Lock()


def _write_jsonrpc(obj: dict) -> None:
    """Write a JSON-RPC message to stdout with Content-Length header."""
    body = json.dumps(obj)
    with _write_lock:
        sys.stdout.write(body + "\n")
        sys.stdout.flush()


def _send_notification(method: str, params: dict | None = None) -> None:
    """Send a JSON-RPC notification (no id field)."""
    msg: dict = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        msg["params"] = params
    _write_jsonrpc(msg)


def _send_response(req_id, result: dict) -> None:
    """Send a JSON-RPC response."""
    _write_jsonrpc({"jsonrpc": "2.0", "id": req_id, "result": result})


# ── HTTP handler ────────────────────────────────────────────────────────


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8") if length else ""
        sender = self.headers.get("X-Sender", "test")

        # Push channel notification over MCP stdout
        _send_notification(
            "notifications/claude/channel",
            {
                "content": body,
                "meta": {"sender": sender, "chat_id": "test"},
            },
        )

        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"ok")

    def do_GET(self):  # noqa: N802
        """Return collected replies as JSON."""
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(_replies).encode())

    def log_message(self, format, *args):  # noqa: A002
        pass  # Silence HTTP logs on stderr


# ── JSON-RPC request handler ───────────────────────────────────────────


def _handle_request(msg: dict) -> None:
    """Handle an incoming JSON-RPC request from the MCP client."""
    method = msg.get("method", "")
    req_id = msg.get("id")
    params = msg.get("params", {})

    if method == "initialize":
        _send_response(
            req_id,
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {},
                    "experimental": {"claude/channel": {}},
                },
                "serverInfo": {"name": "test-channel", "version": "0.1.0"},
                "instructions": (
                    "Messages from the test channel arrive as "
                    '<channel source="test-channel" sender="..." chat_id="test">. '
                    "Reply with the reply tool."
                ),
            },
        )
    elif method == "tools/list":
        _send_response(
            req_id,
            {
                "tools": [
                    {
                        "name": "reply",
                        "description": "Send a message back through the test channel",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "chat_id": {"type": "string"},
                                "text": {"type": "string"},
                            },
                            "required": ["text"],
                        },
                    }
                ]
            },
        )
    elif method == "tools/call":
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})
        if tool_name == "reply":
            _replies.append(arguments)
            sys.stderr.write(f"REPLY: {arguments.get('text', '')}\n")
            _send_response(
                req_id,
                {"content": [{"type": "text", "text": "sent"}]},
            )
        else:
            _send_response(
                req_id,
                {
                    "content": [{"type": "text", "text": f"unknown tool: {tool_name}"}],
                    "isError": True,
                },
            )
    elif method == "notifications/initialized":
        pass  # Notification, no response needed
    elif method == "ping":
        _send_response(req_id, {})
    elif method == "prompts/list":
        _send_response(req_id, {"prompts": []})
    elif method == "resources/list":
        _send_response(req_id, {"resources": []})
    else:
        # Unknown method — respond with empty result
        if req_id is not None:
            _send_response(req_id, {})


# ── Main loop ──────────────────────────────────────────────────────────


def main() -> None:
    # Start HTTP listener in background
    http_thread = threading.Thread(
        target=lambda: HTTPServer(("127.0.0.1", PORT), _Handler).serve_forever(),
        daemon=True,
    )
    http_thread.start()
    sys.stderr.write(f"test-channel: http://localhost:{PORT}\n")

    # Read JSON-RPC messages from stdin (MCP stdio transport)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue

        msg_id = msg.get("id")

        if msg_id is not None:
            # Request — needs a response
            _handle_request(msg)
        else:
            # Notification — no response
            _handle_request(msg)


if __name__ == "__main__":
    main()
