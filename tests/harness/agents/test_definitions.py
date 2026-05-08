from __future__ import annotations

import json
from pathlib import Path

from koder_agent.harness.agents.definitions import (
    BUILTIN_AGENT_DEFINITIONS,
    build_agent_system_prompt,
    filter_tools_for_agent_definition,
    get_active_agents_from_list,
    get_agent_definitions,
    parse_agent_from_json,
    parse_agent_markdown_file,
    resolve_agent_mcp_server_configs,
)


def _write_agent_file(path: Path, *, name: str, description: str, body: str, **frontmatter) -> None:
    lines = ["---", f"name: {name}", f"description: {description}"]
    for key, value in frontmatter.items():
        if isinstance(value, list):
            lines.append(f"{key}:")
            lines.extend(f"  - {item}" for item in value)
        elif isinstance(value, dict):
            lines.append(f"{key}: {json.dumps(value)}")
        else:
            lines.append(f"{key}: {value}")
    lines.extend(["---", body])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_parse_agent_markdown_file_reads_claude_style_frontmatter(tmp_path):
    agent_file = tmp_path / ".koder" / "agents" / "code-reviewer.md"
    _write_agent_file(
        agent_file,
        name="code-reviewer",
        description="Reviews code for correctness",
        body="You are a code reviewer.",
        tools=["Read", "Glob", "Grep"],
        disallowedTools=["Edit"],
        model="sonnet",
        permissionMode="plan",
        maxTurns=12,
        skills=["skill-a", "skill-b"],
        background=True,
        memory="user",
        isolation="worktree",
    )

    agent = parse_agent_markdown_file(
        agent_file,
        base_dir=agent_file.parent,
        source="projectSettings",
    )

    assert agent is not None
    assert agent.agent_type == "code-reviewer"
    assert agent.when_to_use == "Reviews code for correctness"
    assert agent.system_prompt == "You are a code reviewer."
    assert agent.tools == ["Read", "Glob", "Grep"]
    assert agent.disallowed_tools == ["Edit"]
    assert agent.model == "sonnet"
    assert agent.permission_mode == "plan"
    assert agent.max_turns == 12
    assert agent.skills == ["skill-a", "skill-b"]
    assert agent.background is True
    assert agent.memory == "user"
    assert agent.isolation == "worktree"
    assert agent.filename == "code-reviewer"
    assert agent.base_dir == str(agent_file.parent)
    assert agent.source == "projectSettings"


def test_parse_agent_from_json_supports_flag_defined_agents():
    agent = parse_agent_from_json(
        "debugger",
        {
            "description": "Debugs test failures",
            "prompt": "You are a debugger.",
            "tools": ["Read", "Bash"],
            "model": "haiku",
            "permissionMode": "default",
        },
    )

    assert agent is not None
    assert agent.agent_type == "debugger"
    assert agent.when_to_use == "Debugs test failures"
    assert agent.system_prompt == "You are a debugger."
    assert agent.tools == ["Read", "Bash"]
    assert agent.model == "haiku"
    assert agent.permission_mode == "default"
    assert agent.source == "flagSettings"


def test_builtin_subagents_default_to_inherit_main_model():
    builtin_by_type = {agent.agent_type: agent for agent in BUILTIN_AGENT_DEFINITIONS}

    assert builtin_by_type["Explore"].model == "inherit"
    assert builtin_by_type["Plan"].model == "inherit"
    assert builtin_by_type["statusline-setup"].model == "inherit"
    assert builtin_by_type["verification"].model == "inherit"


def test_get_active_agents_from_list_prefers_flag_then_project_then_user_then_plugin_then_builtin():
    builtin = parse_agent_from_json(
        "shared",
        {"description": "builtin", "prompt": "builtin"},
        source="built-in",
    )
    plugin = parse_agent_from_json(
        "review-plugin:shared",
        {"description": "plugin", "prompt": "plugin"},
        source="plugin",
    )
    user = parse_agent_from_json(
        "shared",
        {"description": "user", "prompt": "user"},
        source="userSettings",
    )
    project = parse_agent_from_json(
        "shared",
        {"description": "project", "prompt": "project"},
        source="projectSettings",
    )
    flag = parse_agent_from_json(
        "shared",
        {"description": "flag", "prompt": "flag"},
        source="flagSettings",
    )
    plugin_only = parse_agent_from_json(
        "review-plugin:plugin-only",
        {"description": "plugin only", "prompt": "plugin only"},
        source="plugin",
    )

    active = get_active_agents_from_list(
        [builtin, plugin, user, project, flag, plugin_only]  # type: ignore[list-item]
    )
    active_by_type = {agent.agent_type: agent for agent in active}

    assert active_by_type["shared"].source == "flagSettings"
    assert active_by_type["shared"].system_prompt == "flag"
    assert active_by_type["review-plugin:plugin-only"].source == "plugin"


def test_get_agent_definitions_loads_project_user_plugin_and_flag_agents_with_precedence(
    tmp_path, monkeypatch
):
    project_root = tmp_path / "project"
    project_root.mkdir()
    user_home = tmp_path / "home"
    plugin_root = tmp_path / "plugins"

    _write_agent_file(
        project_root / ".koder" / "agents" / "shared.md",
        name="shared",
        description="project version",
        body="project prompt",
    )
    _write_agent_file(
        user_home / ".koder" / "agents" / "shared.md",
        name="shared",
        description="user version",
        body="user prompt",
    )

    plugin_dir = plugin_root / "review-plugin"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.json").write_text(
        json.dumps({"name": "review-plugin", "version": "1.0.0"}),
        encoding="utf-8",
    )
    _write_agent_file(
        plugin_dir / "agents" / "shared.md",
        name="shared",
        description="plugin version",
        body="plugin prompt",
    )

    monkeypatch.setenv("HOME", str(user_home))

    result = get_agent_definitions(
        cwd=project_root,
        plugin_root=plugin_root,
        cli_agents_json={
            "shared": {
                "description": "flag version",
                "prompt": "flag prompt",
            }
        },
    )

    active_by_type = {agent.agent_type: agent for agent in result.active_agents}
    all_shared = [agent for agent in result.all_agents if agent.agent_type == "shared"]

    assert active_by_type["shared"].source == "flagSettings"
    assert active_by_type["shared"].system_prompt == "flag prompt"
    assert {agent.source for agent in all_shared} >= {
        "projectSettings",
        "userSettings",
        "flagSettings",
    }
    assert any(agent.agent_type == "review-plugin:shared" for agent in result.active_agents)


def test_plugin_agents_ignore_permission_mode_hooks_and_mcp_servers(tmp_path):
    agent_file = tmp_path / "agents" / "reviewer.md"
    _write_agent_file(
        agent_file,
        name="reviewer",
        description="Plugin reviewer",
        body="You are a reviewer.",
        permissionMode="plan",
        hooks={"Stop": {"type": "command"}},
        mcpServers=["github"],
    )

    agent = parse_agent_markdown_file(
        agent_file,
        base_dir=tmp_path / "agents",
        source="plugin",
        plugin_name="review-plugin",
    )

    assert agent is not None
    assert agent.permission_mode is None
    assert agent.hooks is None
    assert agent.mcp_servers is None


def test_get_agent_definitions_respects_project_agent_deny_rules(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / ".koder" / "agents").mkdir(parents=True)
    (project_root / ".koder" / "settings.json").write_text(
        json.dumps({"permissions": {"deny": ["Agent(Explore)"]}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    result = get_agent_definitions(cwd=project_root)
    active_types = {agent.agent_type for agent in result.active_agents}

    assert "Explore" not in active_types
    assert "general-purpose" in active_types


def test_build_agent_system_prompt_includes_preloaded_skill_content_and_memory(
    tmp_path, monkeypatch
):
    project_root = tmp_path / "project"
    project_root.mkdir()
    skill_dir = project_root / ".koder" / "skills" / "api-conventions"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: api-conventions\ndescription: API conventions\n---\nUse typed request models.\n",
        encoding="utf-8",
    )

    memory_dir = project_root / ".koder" / "agent-memory" / "reviewer"
    memory_dir.mkdir(parents=True)
    (memory_dir / "MEMORY.md").write_text("Remember the auth edge cases.\n", encoding="utf-8")

    agent = parse_agent_from_json(
        "reviewer",
        {
            "description": "Reviews code",
            "prompt": "You are a reviewer.",
            "skills": ["api-conventions"],
            "memory": "project",
        },
    )
    assert agent is not None

    prompt = build_agent_system_prompt(agent, cwd=project_root)

    assert "You are a reviewer." in prompt
    assert "Use typed request models." in prompt
    assert "Remember the auth edge cases." in prompt
    assert ".koder/agent-memory/reviewer" in prompt


def test_memory_enabled_agent_auto_adds_file_tools():
    class _Tool:
        def __init__(self, name: str):
            self.name = name

    agent = parse_agent_from_json(
        "reviewer",
        {
            "description": "Reviews code",
            "prompt": "You are a reviewer.",
            "tools": ["Read"],
            "memory": "project",
        },
    )
    assert agent is not None
    filtered = filter_tools_for_agent_definition(
        agent,
        [_Tool("read_file"), _Tool("write_file"), _Tool("append_file"), _Tool("edit_file")],
    )
    names = {tool.name for tool in filtered}
    assert names >= {"read_file", "write_file", "append_file", "edit_file"}


def test_plan_mode_agent_filters_write_tools():
    class _Tool:
        def __init__(self, name: str):
            self.name = name

    agent = parse_agent_from_json(
        "planner",
        {
            "description": "Plans code changes",
            "prompt": "You are a planner.",
            "tools": ["Read", "Write", "Edit"],
            "permissionMode": "plan",
        },
    )
    assert agent is not None
    filtered = filter_tools_for_agent_definition(
        agent,
        [_Tool("read_file"), _Tool("write_file"), _Tool("append_file"), _Tool("edit_file")],
    )
    names = {tool.name for tool in filtered}
    assert names == {"read_file"}


def test_resolve_agent_mcp_server_configs_supports_inline_and_named_servers(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    config_path = home / ".koder" / "config.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        "mcp_servers:\n"
        "  - name: github\n"
        "    transport_type: stdio\n"
        "    command: gh-mcp\n"
        "    args: []\n",
        encoding="utf-8",
    )

    agent = parse_agent_from_json(
        "browser-tester",
        {
            "description": "Uses browser tools",
            "prompt": "You are a browser tester.",
            "mcpServers": [
                "github",
                {
                    "playwright": {
                        "type": "stdio",
                        "command": "npx",
                        "args": ["-y", "@playwright/mcp@latest"],
                    }
                },
            ],
        },
    )
    assert agent is not None

    configs = resolve_agent_mcp_server_configs(agent)

    names = {config.name for config in configs}
    assert names == {"github", "playwright"}
    playwright = next(config for config in configs if config.name == "playwright")
    assert playwright.transport_type.value == "stdio"
    assert playwright.command == "npx"


def test_effort_field_parsed_and_preserved(tmp_path):
    """effort field in agent frontmatter is parsed and preserved."""
    agent_md = tmp_path / "effortful.md"
    agent_md.write_text(
        "---\nname: effortful\ndescription: High effort agent\neffort: high\n---\n\nWork hard.\n",
        encoding="utf-8",
    )
    agent = parse_agent_markdown_file(
        agent_md,
        base_dir=tmp_path,
        source="projectSettings",
    )
    assert agent is not None
    assert agent.effort == "high"

    # Also test numeric effort
    agent_md2 = tmp_path / "numeric.md"
    agent_md2.write_text(
        "---\nname: numeric\ndescription: Numeric effort\neffort: 3\n---\n\nMedium.\n",
        encoding="utf-8",
    )
    agent2 = parse_agent_markdown_file(
        agent_md2,
        base_dir=tmp_path,
        source="projectSettings",
    )
    assert agent2 is not None
    assert agent2.effort == 3
