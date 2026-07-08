"""Regression tests for project-level hook trust gate (C1+C4).

Covers:
- Untrusted project hooks are blocked before approval
- Approved project hooks execute normally
- Hook env does not contain API keys for project hooks
- User hooks get full env
- passFullEnv opt-in works
"""

from __future__ import annotations

import os
import sys
import types
from pathlib import Path
from unittest.mock import patch

# Stub litellm
if "litellm" not in sys.modules:
    litellm_stub = types.ModuleType("litellm")
    litellm_stub.model_cost = {}
    sys.modules["litellm"] = litellm_stub

project_root = Path(__file__).resolve().parents[3]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from koder_agent.harness.hooks.project_approval import (
    approve_project_hooks,
    is_project_hooks_allowed,
    revoke_project_hooks,
)


class TestProjectHookApproval:
    def test_unapproved_project_returns_false(self, tmp_path):
        with patch(
            "koder_agent.harness.hooks.project_approval._approvals_path",
            return_value=tmp_path / "approvals.json",
        ):
            assert is_project_hooks_allowed(tmp_path / "evil-repo") is False

    def test_approved_project_returns_true(self, tmp_path):
        approvals_file = tmp_path / "approvals.json"
        with patch(
            "koder_agent.harness.hooks.project_approval._approvals_path",
            return_value=approvals_file,
        ):
            project = tmp_path / "my-project"
            approve_project_hooks(project)
            assert is_project_hooks_allowed(project) is True

    def test_revoke_removes_approval(self, tmp_path):
        approvals_file = tmp_path / "approvals.json"
        with patch(
            "koder_agent.harness.hooks.project_approval._approvals_path",
            return_value=approvals_file,
        ):
            project = tmp_path / "my-project"
            approve_project_hooks(project)
            revoke_project_hooks(project)
            assert is_project_hooks_allowed(project) is False

    def test_corrupt_file_returns_false(self, tmp_path):
        approvals_file = tmp_path / "approvals.json"
        approvals_file.write_text("not json", encoding="utf-8")
        with patch(
            "koder_agent.harness.hooks.project_approval._approvals_path",
            return_value=approvals_file,
        ):
            assert is_project_hooks_allowed(tmp_path / "project") is False

    def test_multiple_projects_independent(self, tmp_path):
        approvals_file = tmp_path / "approvals.json"
        with patch(
            "koder_agent.harness.hooks.project_approval._approvals_path",
            return_value=approvals_file,
        ):
            proj_a = tmp_path / "project-a"
            proj_b = tmp_path / "project-b"
            approve_project_hooks(proj_a)
            assert is_project_hooks_allowed(proj_a) is True
            assert is_project_hooks_allowed(proj_b) is False


class TestHookEnvScrubbing:
    def test_project_hook_env_excludes_api_keys(self):
        """Project-source hooks must not see API keys in their env."""
        from koder_agent.harness.hooks.runtime import HookScope, _build_hook_env

        scope = HookScope(
            source="project_settings",
            file_path=Path("/fake/.koder/settings.json"),
            hooks={},
        )

        fake_env = {
            "PATH": "/usr/bin",
            "HOME": "/home/user",
            "OPENAI_API_KEY": "sk-secret",
            "ANTHROPIC_API_KEY": "sk-ant-secret",
            "KODER_API_KEY": "secret",
            "CUSTOM_VAR": "safe",
        }
        with patch.dict(os.environ, fake_env, clear=True):
            env = _build_hook_env({}, scope)

        assert "PATH" in env
        assert "HOME" in env
        assert "CUSTOM_VAR" in env
        assert "OPENAI_API_KEY" not in env
        assert "ANTHROPIC_API_KEY" not in env
        assert "KODER_API_KEY" not in env

    def test_plugin_hook_env_excludes_api_keys(self):
        """Plugin-source hooks must not see API keys either."""
        from koder_agent.harness.hooks.runtime import HookScope, _build_hook_env

        scope = HookScope(
            source="plugin",
            file_path=Path("/fake/plugin/hooks.json"),
            hooks={},
            skill_root=Path("/fake/plugin"),
        )

        fake_env = {
            "PATH": "/usr/bin",
            "OPENAI_API_KEY": "sk-secret",
            "SAFE_THING": "ok",
        }
        with patch.dict(os.environ, fake_env, clear=True):
            env = _build_hook_env({}, scope)

        assert "PATH" in env
        assert "SAFE_THING" in env
        assert "OPENAI_API_KEY" not in env

    def test_user_hook_gets_full_env(self):
        """User-settings hooks get full environment (trusted)."""
        from koder_agent.harness.hooks.runtime import HookScope, _build_hook_env

        scope = HookScope(
            source="user_settings",
            file_path=Path("/home/user/.koder/settings.json"),
            hooks={},
        )

        fake_env = {
            "PATH": "/usr/bin",
            "OPENAI_API_KEY": "sk-secret",
        }
        with patch.dict(os.environ, fake_env, clear=True):
            env = _build_hook_env({}, scope)

        assert "OPENAI_API_KEY" in env

    def test_pass_full_env_opt_in(self):
        """Any hook with passFullEnv=true gets full env regardless of source."""
        from koder_agent.harness.hooks.runtime import HookScope, _build_hook_env

        scope = HookScope(
            source="project_settings",
            file_path=Path("/fake/.koder/settings.json"),
            hooks={},
        )

        fake_env = {
            "PATH": "/usr/bin",
            "OPENAI_API_KEY": "sk-secret",
        }
        with patch.dict(os.environ, fake_env, clear=True):
            env = _build_hook_env({"passFullEnv": True}, scope)

        assert "OPENAI_API_KEY" in env

    def test_lc_vars_pass_through(self):
        """LC_* locale vars are always allowed for project hooks."""
        from koder_agent.harness.hooks.runtime import HookScope, _build_hook_env

        scope = HookScope(
            source="project_settings",
            file_path=Path("/fake/.koder/settings.json"),
            hooks={},
        )

        fake_env = {
            "LC_ALL": "en_US.UTF-8",
            "LC_CTYPE": "UTF-8",
            "GITHUB_TOKEN": "ghp_secret",
        }
        with patch.dict(os.environ, fake_env, clear=True):
            env = _build_hook_env({}, scope)

        assert "LC_ALL" in env
        assert "LC_CTYPE" in env
        assert "GITHUB_TOKEN" not in env
