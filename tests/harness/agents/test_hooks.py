# ruff: noqa: E402

import asyncio
import json
import sys
import types
from pathlib import Path

if "litellm" not in sys.modules:
    litellm_stub = types.ModuleType("litellm")
    litellm_stub.model_cost = {}
    sys.modules["litellm"] = litellm_stub

project_root = Path(__file__).resolve().parents[3]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from koder_agent.harness.agents.definitions import AgentDefinition
from koder_agent.harness.agents.hooks import SubagentLifecycleHooks
from koder_agent.harness.permissions.service import PermissionService


def test_subagent_frontmatter_hooks_run_for_matching_tools(tmp_path):
    pre_path = tmp_path / "pre.json"
    post_path = tmp_path / "post.json"
    agent = AgentDefinition(
        agent_type="reviewer",
        when_to_use="Reviews code",
        system_prompt="You are a reviewer.",
        source="projectSettings",
        hooks={
            "PreToolUse": [
                {
                    "matcher": "read_file",
                    "hooks": [
                        {
                            "type": "command",
                            "command": f"python -c \"import sys, pathlib; pathlib.Path(r'{pre_path}').write_text(sys.stdin.read())\"",
                        }
                    ],
                }
            ],
            "PostToolUse": [
                {
                    "matcher": "read_file",
                    "hooks": [
                        {
                            "type": "command",
                            "command": f"python -c \"import sys, pathlib; pathlib.Path(r'{post_path}').write_text(sys.stdin.read())\"",
                        }
                    ],
                }
            ],
        },
    )
    hooks = SubagentLifecycleHooks(agent_definition=agent, cwd=tmp_path)

    class _Agent:
        name = "reviewer"

    class _Tool:
        name = "read_file"

    asyncio.run(hooks.on_tool_start(None, _Agent(), _Tool()))
    asyncio.run(hooks.on_tool_end(None, _Agent(), _Tool(), "ok"))

    pre_payload = json.loads(pre_path.read_text(encoding="utf-8"))
    post_payload = json.loads(post_path.read_text(encoding="utf-8"))
    assert pre_payload["event"] == "PreToolUse"
    assert pre_payload["tool_name"] == "read_file"
    assert post_payload["event"] == "PostToolUse"
    assert post_payload["tool_name"] == "read_file"


def test_project_subagent_start_and_stop_hooks_run_from_settings(tmp_path):
    start_path = tmp_path / "start.json"
    stop_path = tmp_path / "stop.json"
    (tmp_path / ".koder").mkdir(parents=True)
    (tmp_path / ".koder" / "settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "SubagentStart": [
                        {
                            "matcher": "reviewer",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": f"python -c \"import sys, pathlib; pathlib.Path(r'{start_path}').write_text(sys.stdin.read())\"",
                                }
                            ],
                        }
                    ],
                    "SubagentStop": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": f"python -c \"import sys, pathlib; pathlib.Path(r'{stop_path}').write_text(sys.stdin.read())\"",
                                }
                            ],
                        }
                    ],
                }
            }
        ),
        encoding="utf-8",
    )

    agent = AgentDefinition(
        agent_type="reviewer",
        when_to_use="Reviews code",
        system_prompt="You are a reviewer.",
        source="projectSettings",
    )
    hooks = SubagentLifecycleHooks(agent_definition=agent, cwd=tmp_path)

    class _Agent:
        name = "reviewer"

    asyncio.run(hooks.on_agent_start(None, _Agent()))
    asyncio.run(hooks.on_agent_end(None, _Agent(), "done"))

    start_payload = json.loads(start_path.read_text(encoding="utf-8"))
    stop_payload = json.loads(stop_path.read_text(encoding="utf-8"))
    assert start_payload["event"] == "SubagentStart"
    assert stop_payload["event"] == "SubagentStop"


def test_subagent_shell_preflight_does_not_fail_without_tool_arguments(tmp_path):
    agent = AgentDefinition(
        agent_type="reviewer",
        when_to_use="Reviews code",
        system_prompt="You are a reviewer.",
        source="projectSettings",
    )
    hooks = SubagentLifecycleHooks(
        agent_definition=agent,
        cwd=tmp_path,
        permission_service=PermissionService.default(workspace_root=tmp_path),
    )

    class _Agent:
        name = "reviewer"

    class _Tool:
        name = "run_shell"

    asyncio.run(hooks.on_tool_start(None, _Agent(), _Tool()))
