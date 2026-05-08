import json
import os
import socketserver
import sys
import threading
import time
import types
from pathlib import Path
from unittest.mock import MagicMock

if "litellm" not in sys.modules:
    litellm_stub = types.ModuleType("litellm")
    litellm_stub.model_cost = {}
    sys.modules["litellm"] = litellm_stub

project_root = Path(__file__).resolve().parents[3]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from agents import ToolInputGuardrailData

from koder_agent.agentic.hook_guardrail import hook_pretool_guardrail
from koder_agent.harness.hooks.runtime import (
    dispatch_command_hooks,
    list_configured_hooks,
    poll_file_change_hooks,
    update_watch_paths,
)


def test_list_configured_hooks_reads_koder_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    project = tmp_path / "project"
    (project / ".koder").mkdir(parents=True)
    (project / ".koder" / "settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "PostToolUse": [
                        {
                            "matcher": "Edit|Write",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "echo ok",
                                }
                            ],
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    listings = list_configured_hooks(project)
    assert len(listings) == 1
    assert listings[0].event == "PostToolUse"
    assert listings[0].matcher == "Edit|Write"
    assert listings[0].source == "project_settings"


def test_list_configured_hooks_includes_policy_and_plugin_sources(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    project = tmp_path / "project"
    (project / ".koder").mkdir(parents=True)
    (tmp_path / ".koder").mkdir(parents=True)
    (tmp_path / ".koder" / "managed-settings.json").write_text(
        json.dumps(
            {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo managed"}]}]}}
        ),
        encoding="utf-8",
    )
    plugin_hooks = tmp_path / ".koder" / "plugins" / "demo" / "hooks"
    plugin_hooks.mkdir(parents=True)
    (plugin_hooks.parent / "plugin.json").write_text(
        json.dumps({"name": "demo", "version": "1.0.0"}),
        encoding="utf-8",
    )
    (plugin_hooks / "hooks.json").write_text(
        json.dumps(
            {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo plugin"}]}]}}
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(project)

    listings = list_configured_hooks(project)
    sources = {listing.source for listing in listings}
    assert "policy_settings" in sources
    assert "plugin" in sources


def test_dispatch_command_hooks_blocks_user_prompt_submit_on_exit_code_2(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    project = tmp_path / "project"
    (project / ".koder").mkdir(parents=True)
    (project / ".koder" / "settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "UserPromptSubmit": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "python -c \"import sys; print('blocked'); raise SystemExit(2)\"",
                                }
                            ]
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    result = dispatch_command_hooks(
        cwd=project,
        event_name="UserPromptSubmit",
        match_value=None,
        payload={"event": "UserPromptSubmit", "prompt": "hello"},
    )

    assert result.blocked is True


def test_hook_pretool_guardrail_rejects_blocked_command(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    project = tmp_path / "project"
    (project / ".koder").mkdir(parents=True)
    (project / ".koder" / "settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "run_shell",
                            "hooks": [
                                {
                                    "type": "command",
                                    "if": "run_shell(rm *)",
                                    "command": 'python -c "import sys; print(\'{\\"decision\\": \\"block\\", \\"reason\\": \\"rm blocked\\"}\')"',
                                }
                            ],
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(project)

    mock_context = MagicMock()
    mock_context.tool_name = "run_shell"
    mock_context.tool_arguments = json.dumps({"command": "rm -rf build"})
    data = MagicMock(spec=ToolInputGuardrailData)
    data.context = mock_context

    result = hook_pretool_guardrail(data)
    assert result.behavior["type"] == "reject_content"


def test_dispatch_command_hooks_runs_session_start_command(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    project = tmp_path / "project"
    marker = tmp_path / "session-start.json"
    (project / ".koder").mkdir(parents=True)
    (project / ".koder" / "settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "SessionStart": [
                        {
                            "matcher": "startup",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": f"python -c \"import sys, pathlib; pathlib.Path(r'{marker}').write_text(sys.stdin.read())\"",
                                }
                            ],
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    result = dispatch_command_hooks(
        cwd=project,
        event_name="SessionStart",
        match_value="startup",
        payload={"event": "SessionStart", "source": "startup"},
    )

    assert result.matched_hooks == 1
    assert json.loads(marker.read_text(encoding="utf-8"))["event"] == "SessionStart"


def test_dispatch_command_hooks_supports_http_prompt_agent_and_async_handlers(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HOME", str(tmp_path))
    project = tmp_path / "project"
    async_marker = tmp_path / "async.txt"
    (project / ".koder").mkdir(parents=True)

    class _Handler(socketserver.BaseRequestHandler):
        def handle(self):
            data = self.request.recv(4096).decode("utf-8")
            body = data.split("\r\n\r\n", 1)[1]
            payload = json.loads(body)
            response = "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n" + json.dumps(
                {"hookSpecificOutput": {"additionalContext": payload["event"]}}
            )
            self.request.sendall(response.encode("utf-8"))

    server = socketserver.TCPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}"

    (project / ".koder" / "settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "Stop": [
                        {
                            "hooks": [
                                {
                                    "type": "http",
                                    "url": url,
                                },
                                {
                                    "type": "prompt",
                                    "prompt": "Decide",
                                },
                                {
                                    "type": "agent",
                                    "prompt": "Investigate",
                                },
                                {
                                    "type": "command",
                                    "command": f"python -c \"import pathlib,time; time.sleep(0.1); pathlib.Path(r'{async_marker}').write_text('done')\"",
                                    "async": True,
                                },
                            ]
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "koder_agent.harness.hooks.runtime._run_prompt_hook",
        lambda **_kwargs: json.dumps({"hookSpecificOutput": {"additionalContext": "prompt"}}),
    )
    monkeypatch.setattr(
        "koder_agent.harness.hooks.runtime._run_agent_hook",
        lambda **_kwargs: json.dumps({"hookSpecificOutput": {"additionalContext": "agent"}}),
    )

    result = dispatch_command_hooks(
        cwd=project,
        event_name="Stop",
        match_value=None,
        payload={"event": "Stop"},
    )

    server.shutdown()
    thread.join(timeout=2)

    assert result.matched_hooks == 4
    time.sleep(0.2)
    assert async_marker.read_text(encoding="utf-8") == "done"


def test_post_tool_use_failure_hook_dispatches_for_error_results(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    project = tmp_path / "project"
    marker = tmp_path / "post-tool-use-failure.json"
    (project / ".koder").mkdir(parents=True)
    (project / ".koder" / "settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "PostToolUseFailure": [
                        {
                            "matcher": "read_file",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": f"python -c \"import sys, pathlib; pathlib.Path(r'{marker}').write_text(sys.stdin.read())\"",
                                }
                            ],
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(project)

    from koder_agent.harness.permissions.service import PermissionService
    from koder_agent.harness.tools.registry import ToolRegistry, ToolSpec

    async def _error_tool(_arguments):
        return {"tool": "read_file", "status": "error", "content": "boom"}

    registry = ToolRegistry.with_permission_service(PermissionService.default())
    registry.register(ToolSpec(name="read_file", invoke=_error_tool))
    result = __import__("asyncio").run(registry.get("read_file").invoke({}))

    assert result["status"] == "error"
    payload = json.loads(marker.read_text(encoding="utf-8"))
    assert payload["event"] == "PostToolUseFailure"


def test_post_tool_use_hook_can_block_successful_tool_result(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    project = tmp_path / "project"
    (project / ".koder").mkdir(parents=True)
    (project / ".koder" / "settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "PostToolUse": [
                        {
                            "matcher": "read_file",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": 'python -c "print(\'{\\"decision\\":\\"block\\",\\"reason\\":\\"bad output\\"}\')"',
                                }
                            ],
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(project)

    from koder_agent.harness.permissions.service import PermissionService
    from koder_agent.harness.tools.registry import ToolRegistry, ToolSpec

    async def _ok_tool(_arguments):
        return {"tool": "read_file", "status": "success", "content": "ok"}

    registry = ToolRegistry.with_permission_service(PermissionService.default())
    registry.register(ToolSpec(name="read_file", invoke=_ok_tool))
    result = __import__("asyncio").run(registry.get("read_file").invoke({}))

    assert result["status"] == "error"
    assert result["content"] == "bad output"


def test_post_tool_use_failure_hook_can_override_error_message(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    project = tmp_path / "project"
    (project / ".koder").mkdir(parents=True)
    (project / ".koder" / "settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "PostToolUseFailure": [
                        {
                            "matcher": "read_file",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": 'python -c "print(\'{\\"decision\\":\\"block\\",\\"reason\\":\\"override failure\\"}\')"',
                                }
                            ],
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(project)

    from koder_agent.harness.permissions.service import PermissionService
    from koder_agent.harness.tools.registry import ToolRegistry, ToolSpec

    async def _error_tool(_arguments):
        return {"tool": "read_file", "status": "error", "content": "boom"}

    registry = ToolRegistry.with_permission_service(PermissionService.default())
    registry.register(ToolSpec(name="read_file", invoke=_error_tool))
    result = __import__("asyncio").run(registry.get("read_file").invoke({}))

    assert result["status"] == "error"
    assert result["content"] == "override failure"


def test_file_changed_hooks_fire_for_watched_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    project = tmp_path / "project"
    watched = tmp_path / "watched.txt"
    marker = tmp_path / "file-changed.json"
    watched.write_text("one", encoding="utf-8")
    (project / ".koder").mkdir(parents=True)
    (project / ".koder" / "settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "SessionStart": [
                        {
                            "matcher": "startup",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": f'python -c "print(\'{{\\"hookSpecificOutput\\":{{\\"watchPaths\\":[\\"{watched}\\"]}}}}\')"',
                                }
                            ],
                        }
                    ],
                    "FileChanged": [
                        {
                            "matcher": watched.name,
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": f"python -c \"import sys, pathlib; pathlib.Path(r'{marker}').write_text(sys.stdin.read())\"",
                                }
                            ],
                        }
                    ],
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(project)

    session_start = dispatch_command_hooks(
        cwd=project,
        event_name="SessionStart",
        match_value="startup",
        payload={"event": "SessionStart", "source": "startup"},
    )
    update_watch_paths(session_start.watch_paths)
    watched.write_text("two", encoding="utf-8")
    fired = poll_file_change_hooks(project)

    assert fired == 1
    payload = json.loads(marker.read_text(encoding="utf-8"))
    assert payload["event"] == "FileChanged"


def test_session_start_hooks_can_persist_environment_variables(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    project = tmp_path / "project"
    (project / ".koder").mkdir(parents=True)
    (project / ".koder" / "settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "SessionStart": [
                        {
                            "matcher": "startup",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "python -c \"import os, pathlib; pathlib.Path(os.environ['KODER_ENV_FILE']).write_text('export DEMO_ENV=hooked\\n')\"",
                                }
                            ],
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(project)
    monkeypatch.delenv("DEMO_ENV", raising=False)

    dispatch_command_hooks(
        cwd=project,
        event_name="SessionStart",
        match_value="startup",
        payload={"event": "SessionStart", "source": "startup", "session_id": "demo-session"},
    )

    assert os.environ["DEMO_ENV"] == "hooked"


def test_mcp_tool_matcher_supports_prefixed_tool_names(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    project = tmp_path / "project"
    marker = tmp_path / "mcp.json"
    (project / ".koder").mkdir(parents=True)
    (project / ".koder" / "settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "mcp__.*",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": f"python -c \"import sys, pathlib; pathlib.Path(r'{marker}').write_text(sys.stdin.read())\"",
                                }
                            ],
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(project)

    dispatch_command_hooks(
        cwd=project,
        event_name="PreToolUse",
        match_value="mcp__server__tool",
        payload={"event": "PreToolUse", "tool_name": "mcp__server__tool", "tool_input": {}},
    )

    assert json.loads(marker.read_text(encoding="utf-8"))["tool_name"] == "mcp__server__tool"


def test_elicitation_events_support_structured_output(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    project = tmp_path / "project"
    marker = tmp_path / "elicitation.json"
    (project / ".koder").mkdir(parents=True)
    (project / ".koder" / "settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "Elicitation": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": 'python -c "print(\'{\\"hookSpecificOutput\\":{\\"action\\":\\"accept\\",\\"content\\":{\\"answer\\":\\"ok\\"}}}\')"',
                                }
                            ]
                        }
                    ],
                    "ElicitationResult": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": f"python -c \"import sys, pathlib; pathlib.Path(r'{marker}').write_text(sys.stdin.read())\"",
                                }
                            ]
                        }
                    ],
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(project)

    elicitation = dispatch_command_hooks(
        cwd=project,
        event_name="Elicitation",
        match_value="demo-server",
        payload={"event": "Elicitation", "server_name": "demo-server"},
    )
    dispatch_command_hooks(
        cwd=project,
        event_name="ElicitationResult",
        match_value="demo-server",
        payload={"event": "ElicitationResult", "server_name": "demo-server"},
    )

    assert elicitation.elicitation_action == "accept"
    assert elicitation.elicitation_content == {"answer": "ok"}
    assert json.loads(marker.read_text(encoding="utf-8"))["event"] == "ElicitationResult"
