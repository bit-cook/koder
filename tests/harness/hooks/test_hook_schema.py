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


class TestAsyncRewake:
    def test_async_rewake_default_false(self):
        from koder_agent.harness.hooks.runtime import HookConfig

        cfg = HookConfig(type="command", command="echo test", async_hook=True)
        assert cfg.async_rewake is False

    def test_async_rewake_can_be_enabled(self):
        from koder_agent.harness.hooks.runtime import HookConfig

        cfg = HookConfig(type="command", command="echo test", async_hook=True, async_rewake=True)
        assert cfg.async_rewake is True


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


class TestStatusMessage:
    def test_status_message_default_empty(self):
        from koder_agent.harness.hooks.runtime import HookConfig

        cfg = HookConfig(type="command", command="echo test")
        assert cfg.status_message == ""

    def test_status_message_can_be_set(self):
        from koder_agent.harness.hooks.runtime import HookConfig

        cfg = HookConfig(type="command", command="lint", status_message="Running linter...")
        assert cfg.status_message == "Running linter..."
