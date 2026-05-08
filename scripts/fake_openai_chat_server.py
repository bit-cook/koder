#!/usr/bin/env python3
"""Serve deterministic OpenAI-compatible chat completions for tmux scenarios."""

from __future__ import annotations

import argparse
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


class _ReusableHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True


class _Handler(BaseHTTPRequestHandler):
    response_text: str = "fixture response"
    log_file: Path | None = None

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        length = int(self.headers.get("content-length") or 0)
        raw_body = self.rfile.read(length).decode("utf-8") if length else "{}"
        try:
            body: dict[str, Any] = json.loads(raw_body)
        except json.JSONDecodeError:
            body = {"raw_body": raw_body}

        if self.log_file is not None:
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
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(200)
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
    args = parser.parse_args()

    _Handler.response_text = args.response
    _Handler.log_file = args.log_file

    server = _ReusableHTTPServer(("127.0.0.1", args.port), _Handler)
    args.ready_file.parent.mkdir(parents=True, exist_ok=True)
    args.ready_file.write_text(f"ready http://127.0.0.1:{args.port}/v1\n", encoding="utf-8")
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
