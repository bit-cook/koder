import asyncio
import json
from pathlib import Path

from koder_agent.config.models import SkillsConfig
from koder_agent.harness.commands.interactive import HarnessInteractiveCommandHandler
from koder_agent.harness.permissions.service import PermissionService


class _DummyConfig:
    def __init__(self, user_dir: Path, project_dir: Path):
        self.skills = SkillsConfig(
            enabled=True,
            user_skills_dir=str(user_dir),
            project_skills_dir=str(project_dir),
        )
        self.model = type("_M", (), {"name": "test-model", "provider": "test"})()
        self.cli = type("_C", (), {"stream": False, "session": None})()


async def _run_skills(handler: HarnessInteractiveCommandHandler) -> str:
    return await handler.handle_slash_input("/skills", scheduler=None)


def test_skills_command_lists_available_skills(tmp_path, monkeypatch):
    user_dir = tmp_path / "user-skills" / "demo-skill"
    project_dir = tmp_path / "project-skills" / "proj-skill"
    user_dir.mkdir(parents=True)
    project_dir.mkdir(parents=True)
    (user_dir / "SKILL.md").write_text(
        "---\nname: demo-skill\ndescription: demo user skill\n---\ncontent",
        encoding="utf-8",
    )
    (project_dir / "SKILL.md").write_text(
        "---\nname: proj-skill\ndescription: demo project skill\n---\ncontent",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "koder_agent.harness.commands.interactive.get_config",
        lambda: _DummyConfig(user_dir.parent, project_dir.parent),
    )
    handler = HarnessInteractiveCommandHandler()
    import asyncio

    result = asyncio.run(_run_skills(handler))
    assert "batch" in result
    assert "debug" in result
    assert "loop" in result
    assert "simplify" in result
    assert "demo-skill" in result
    assert "proj-skill" in result


def test_skills_command_hides_non_user_invocable_skills(tmp_path, monkeypatch):
    project_dir = tmp_path / "project-skills" / "hidden-skill"
    project_dir.mkdir(parents=True)
    (project_dir / "SKILL.md").write_text(
        "---\nname: hidden-skill\ndescription: hidden\nuser-invocable: false\n---\ncontent",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "koder_agent.harness.commands.interactive.get_config",
        lambda: _DummyConfig(tmp_path / "user-skills", project_dir.parent),
    )
    handler = HarnessInteractiveCommandHandler()
    import asyncio

    result = asyncio.run(_run_skills(handler))
    assert "hidden-skill" not in result


def test_direct_skill_invocation_executes_inline_skill(tmp_path, monkeypatch):
    project_dir = tmp_path / "project-skills" / "explain-code"
    project_dir.mkdir(parents=True)
    (project_dir / "SKILL.md").write_text(
        "---\nname: explain-code\ndescription: explain code\nargument-hint: [path]\n---\nExplain $ARGUMENTS in ${KODER_SESSION_ID}",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "koder_agent.harness.commands.interactive.get_config",
        lambda: _DummyConfig(tmp_path / "user-skills", project_dir.parent),
    )

    class _Session:
        session_id = "skill-session"

    class _Scheduler:
        session = _Session()

        async def handle(
            self, prompt: str, render_output: bool = True, multimodal_input=None
        ) -> str:
            return prompt

    handler = HarnessInteractiveCommandHandler()
    import asyncio

    result = asyncio.run(
        handler.handle_slash_input("/explain-code src/auth.py", scheduler=_Scheduler())
    )
    assert "Explain src/auth.py in skill-session" in result


def test_direct_manual_skill_invocation_renders_without_model(tmp_path, monkeypatch):
    project_dir = tmp_path / "project-skills" / "manual-check"
    project_dir.mkdir(parents=True)
    (project_dir / "SKILL.md").write_text(
        "---\nname: manual-check\ndescription: manual check\ndisable-model-invocation: true\n---\nManual $ARGUMENTS",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "koder_agent.harness.commands.interactive.get_config",
        lambda: _DummyConfig(tmp_path / "user-skills", project_dir.parent),
    )

    class _Scheduler:
        session = type("Session", (), {"session_id": "skill-session"})()

        async def handle(
            self, prompt: str, render_output: bool = True, multimodal_input=None
        ) -> str:
            raise AssertionError("manual skill should not call the model scheduler")

    handler = HarnessInteractiveCommandHandler()
    result = asyncio.run(
        handler.handle_slash_input("/manual-check fixture", scheduler=_Scheduler())
    )

    assert result == "Manual fixture"


def test_direct_remember_skill_persists_project_memory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    handler = HarnessInteractiveCommandHandler()

    result = asyncio.run(
        handler.handle_slash_input("/remember durable fixture memory", scheduler=None)
    )

    assert "remember: saved" in result
    assert "type: project" in result
    memory_files = list((tmp_path / ".koder" / "memory").glob("*.md"))
    assert any(path.name != "MEMORY.md" for path in memory_files)
    assert "durable fixture memory" in (tmp_path / ".koder" / "memory" / "MEMORY.md").read_text(
        encoding="utf-8"
    )


def test_direct_skill_invocation_runs_forked_skill_via_agent_service(tmp_path, monkeypatch):
    project_dir = tmp_path / "project-skills" / "deploy"
    project_dir.mkdir(parents=True)
    (project_dir / "SKILL.md").write_text(
        "---\nname: deploy\ndescription: deploy app\ncontext: fork\nagent: reviewer\n---\nDeploy $ARGUMENTS",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "koder_agent.harness.commands.interactive.get_config",
        lambda: _DummyConfig(tmp_path / "user-skills", project_dir.parent),
    )
    monkeypatch.setattr(
        "koder_agent.harness.commands.interactive.get_agent_definitions",
        lambda **_kwargs: type(
            "Defs", (), {"active_agents": [type("A", (), {"agent_type": "reviewer"})()]}
        )(),
    )

    class _AgentService:
        async def run_sync(self, *, agent_definition, prompt, seed_items=None, cwd=None):
            return f"{agent_definition.agent_type}: {prompt}"

    handler = HarnessInteractiveCommandHandler(agent_service=_AgentService())
    import asyncio

    result = asyncio.run(handler.handle_slash_input("/deploy production", scheduler=None))
    assert result == "reviewer: Deploy production"


def test_direct_skill_invocation_activates_skill_scoped_hooks(tmp_path, monkeypatch):
    project_dir = tmp_path / "project-skills" / "explain-code"
    marker = tmp_path / "skill-hook.json"
    project_dir.mkdir(parents=True)
    (project_dir / "SKILL.md").write_text(
        "---\nname: explain-code\ndescription: explain code\nhooks:\n  PostToolUse:\n    - hooks:\n        - type: command\n          command: >-\n            python -c \"import sys, pathlib; pathlib.Path(r'"
        + str(marker)
        + "').write_text(sys.stdin.read())\"\n---\nExplain $ARGUMENTS",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "koder_agent.harness.commands.interactive.get_config",
        lambda: _DummyConfig(tmp_path / "user-skills", project_dir.parent),
    )

    class _Scheduler:
        async def handle(
            self, prompt: str, render_output: bool = True, multimodal_input=None
        ) -> str:
            from koder_agent.harness.hooks.runtime import dispatch_command_hooks

            dispatch_command_hooks(
                cwd=tmp_path,
                event_name="PostToolUse",
                match_value="read_file",
                payload={
                    "event": "PostToolUse",
                    "tool_name": "read_file",
                    "tool_input": {"file_path": "demo.txt"},
                    "result": "ok",
                },
            )
            return prompt

        session = type("Session", (), {"session_id": "skill-session"})()

    handler = HarnessInteractiveCommandHandler()
    result = asyncio.run(handler.handle_slash_input("/explain-code demo", scheduler=_Scheduler()))

    assert "Explain demo" in result
    payload = json.loads(marker.read_text(encoding="utf-8"))
    assert payload["event"] == "PostToolUse"


def test_direct_skill_invocation_respects_skill_permission_rules(tmp_path, monkeypatch):
    project_dir = tmp_path / "project-skills" / "deploy"
    project_dir.mkdir(parents=True)
    (project_dir / "SKILL.md").write_text(
        "---\nname: deploy\ndescription: deploy app\n---\nDeploy $ARGUMENTS",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "koder_agent.harness.commands.interactive.get_config",
        lambda: _DummyConfig(tmp_path / "user-skills", project_dir.parent),
    )
    permissions = PermissionService.default()
    permissions.add_rule("Skill", "deny", "Skill(deploy *)")

    handler = HarnessInteractiveCommandHandler(permission_service=permissions)
    result = asyncio.run(handler.handle_slash_input("/deploy production", scheduler=None))

    assert "skills: blocked" in result
    assert "Denied by rule" in result


def test_direct_bundled_skill_invocation_works():
    handler = HarnessInteractiveCommandHandler()
    result = asyncio.run(handler.handle_slash_input("/simplify", scheduler=None))
    assert "improving the **quality** of the changed code" in result
