"""Tests for hook schema features."""


class TestHookShellSelection:
    def test_default_shell_is_empty(self):
        from koder_agent.harness.hooks.runtime import HookConfig

        cfg = HookConfig(type="command", command="echo test")
        assert cfg.shell == ""

    def test_shell_can_be_set(self):
        from koder_agent.harness.hooks.runtime import HookConfig

        cfg = HookConfig(type="command", command="echo test", shell="bash")
        assert cfg.shell == "bash"


class TestHttpHeaders:
    def test_headers_default_none(self):
        from koder_agent.harness.hooks.runtime import HookConfig

        cfg = HookConfig(type="http", url="https://example.com")
        assert cfg.headers is None

    def test_headers_can_be_set(self):
        from koder_agent.harness.hooks.runtime import HookConfig

        cfg = HookConfig(
            type="http",
            url="https://example.com",
            headers={"Authorization": "Bearer token"},
        )
        assert cfg.headers["Authorization"] == "Bearer token"


class TestEnvVarExpansion:
    def test_expand_allowed_env_var(self, monkeypatch):
        from koder_agent.harness.hooks.runtime import _expand_env_vars

        monkeypatch.setenv("MY_TOKEN", "secret123")
        result = _expand_env_vars("Bearer ${MY_TOKEN}", ["MY_TOKEN"])
        assert result == "Bearer secret123"

    def test_expand_disallowed_env_var(self, monkeypatch):
        from koder_agent.harness.hooks.runtime import _expand_env_vars

        monkeypatch.setenv("SECRET", "nope")
        result = _expand_env_vars("Bearer ${SECRET}", ["ALLOWED_ONLY"])
        assert result == "Bearer ${SECRET}"

    def test_expand_missing_env_var(self):
        from koder_agent.harness.hooks.runtime import _expand_env_vars

        result = _expand_env_vars("Bearer ${NONEXISTENT}", ["NONEXISTENT"])
        assert result == "Bearer "


class TestHookConfigHasNoReservedFields:
    def test_every_config_field_is_consumed_by_dispatch(self):
        """HookConfig must not accumulate parsed-but-unused fields again.

        asyncRewake and statusMessage were removed because nothing consumed
        them; this pins the surviving field list.
        """
        from dataclasses import fields

        from koder_agent.harness.hooks.runtime import HookConfig

        assert [field.name for field in fields(HookConfig)] == [
            "type",
            "command",
            "url",
            "prompt",
            "timeout",
            "shell",
            "headers",
            "allowed_env_vars",
            "async_hook",
            "once",
            "matcher",
            "if_condition",
            "model",
        ]
