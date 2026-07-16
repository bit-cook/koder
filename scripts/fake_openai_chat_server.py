#!/usr/bin/env python3
"""Serve deterministic OpenAI-compatible chat completions for tmux scenarios."""

from __future__ import annotations

import argparse
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

_TOOL_SCENARIOS = (
    "streaming_tool_queue",
    "streaming_tool_error",
    "sandbox_shell_tool",
)
_SCENARIOS = ("single", *_TOOL_SCENARIOS)


class _ReusableHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True


class _Handler(BaseHTTPRequestHandler):
    response_text: str = "fixture response"
    log_file: Path | None = None
    scenario: str = "single"
    stream_delay: float = 0.0
    stream_lines: int = 2

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        length = int(self.headers.get("content-length") or 0)
        raw_body = self.rfile.read(length).decode("utf-8") if length else "{}"
        try:
            body: dict[str, Any] = json.loads(raw_body)
        except json.JSONDecodeError:
            body = {"raw_body": raw_body}

        self._write_log(body)

        if self.scenario in _TOOL_SCENARIOS:
            if not body.get("tools"):
                self._send_json(self._chat_completion(body, self.response_text))
                return
            if self.scenario == "streaming_tool_error" and self._request_has_tool_output(body):
                self._send_error(self.response_text)
                return
            if body.get("stream"):
                if self._request_has_tool_output(body):
                    self._send_text_stream(body)
                else:
                    self._send_tool_call_stream(body)
                return
            if self._request_has_tool_output(body):
                self._send_json(self._chat_completion(body, self.response_text))
            else:
                self._send_json(self._tool_call_completion(body))
            return

        self._send_json(self._chat_completion(body, self.response_text))

    def _write_log(self, body: dict[str, Any]) -> None:
        if self.log_file is None:
            return
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        with self.log_file.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "path": self.path,
                        "body": body,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
                + "\n"
            )

    def _request_has_tool_output(self, body: dict[str, Any]) -> bool:
        messages = body.get("messages")
        if not isinstance(messages, list):
            return False

        last_user_index = -1
        for index, message in enumerate(messages):
            if isinstance(message, dict) and message.get("role") == "user":
                last_user_index = index

        for message in messages[last_user_index + 1 :]:
            if not isinstance(message, dict):
                continue
            if message.get("role") == "tool":
                return True
            if "Queued user input" in json.dumps(message, ensure_ascii=False):
                return True
        return False

    def _chat_completion(self, body: dict[str, Any], content: str) -> dict[str, Any]:
        payload = {
            "id": "chatcmpl-koder-fixture",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": body.get("model") or "gpt-4.1",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": self.response_text},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
        }
        payload["choices"][0]["message"]["content"] = content
        return payload

    def _tool_call_completion(self, body: dict[str, Any]) -> dict[str, Any]:
        payload = self._chat_completion(body, "")
        payload["choices"][0]["message"] = {
            "role": "assistant",
            "content": None,
            "tool_calls": [self._tool_call_payload()],
        }
        payload["choices"][0]["finish_reason"] = "tool_calls"
        return payload

    def _tool_call_payload(self) -> dict[str, Any]:
        if self.scenario == "sandbox_shell_tool":
            return {
                "id": "call_koder_sandbox_fixture",
                "type": "function",
                "function": {
                    "name": "run_shell",
                    "arguments": json.dumps({"command": "touch model-tool-created.txt"}),
                },
            }
        return {
            "id": "call_koder_queue_fixture",
            "type": "function",
            "function": {
                "name": "read_file",
                "arguments": json.dumps({"path": "sample.txt"}),
            },
        }

    def _stream_chunk(self, body: dict[str, Any], delta: dict[str, Any], finish_reason=None):
        return {
            "id": "chatcmpl-koder-fixture",
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": body.get("model") or "gpt-4.1",
            "choices": [
                {
                    "index": 0,
                    "delta": delta,
                    "finish_reason": finish_reason,
                }
            ],
        }

    def _send_sse(self, chunks: list[dict[str, Any]]) -> None:
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("cache-control", "no-cache")
        self.end_headers()
        for chunk in chunks:
            encoded = f"data: {json.dumps(chunk)}\n\n".encode("utf-8")
            self.wfile.write(encoded)
            self.wfile.flush()
            if self.stream_delay:
                time.sleep(self.stream_delay)
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def _send_tool_call_stream(self, body: dict[str, Any]) -> None:
        tool_payload = self._tool_call_payload()
        function_payload = tool_payload["function"]
        argument_json = function_payload["arguments"]
        if self.stream_lines <= 2:
            content_chunks = [
                self._stream_chunk(body, {"content": "streaming fixture line 1\n"}),
                self._stream_chunk(body, {"content": "streaming fixture line 2\n"}),
            ]
        else:
            content_chunks = [
                self._stream_chunk(
                    body,
                    {"content": f"streaming fixture long line {line_number:03d}\n"},
                )
                for line_number in range(1, self.stream_lines + 1)
            ]
        chunks = [
            self._stream_chunk(body, {"role": "assistant"}),
            *content_chunks,
            self._stream_chunk(
                body,
                {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": tool_payload["id"],
                            "type": "function",
                            "function": {
                                "name": function_payload["name"],
                                "arguments": "",
                            },
                        }
                    ]
                },
            ),
            self._stream_chunk(
                body,
                {"tool_calls": [{"index": 0, "function": {"arguments": argument_json}}]},
            ),
            self._stream_chunk(body, {}, "tool_calls"),
        ]
        self._send_sse(chunks)

    def _send_text_stream(self, body: dict[str, Any]) -> None:
        chunks = [
            self._stream_chunk(body, {"role": "assistant"}),
            self._stream_chunk(body, {"content": self.response_text}),
            self._stream_chunk(body, {}, "stop"),
        ]
        self._send_sse(chunks)

    def _send_json(self, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _send_error(self, message: str) -> None:
        encoded = json.dumps(
            {
                "error": {
                    "message": message,
                    "type": "invalid_request_error",
                    "param": "input",
                    "code": None,
                }
            }
        ).encode("utf-8")
        self.send_response(400)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, _format: str, *_args: Any) -> None:
        return


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--response", required=True)
    parser.add_argument("--log-file", type=Path, required=True)
    parser.add_argument("--ready-file", type=Path, required=True)
    parser.add_argument(
        "--scenario",
        default="single",
        choices=_SCENARIOS,
    )
    parser.add_argument("--stream-delay", type=float, default=0.0)
    parser.add_argument("--stream-lines", type=int, default=2)
    args = parser.parse_args()

    _Handler.response_text = args.response
    _Handler.log_file = args.log_file
    _Handler.scenario = args.scenario
    _Handler.stream_delay = max(0.0, args.stream_delay)
    _Handler.stream_lines = max(1, args.stream_lines)

    server = _ReusableHTTPServer(("127.0.0.1", args.port), _Handler)
    args.ready_file.parent.mkdir(parents=True, exist_ok=True)
    args.ready_file.write_text(f"ready http://127.0.0.1:{args.port}/v1\n", encoding="utf-8")
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
