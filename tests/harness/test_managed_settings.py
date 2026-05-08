import json

from koder_agent.harness.managed_settings import (
    load_managed_settings,
    render_managed_settings_status,
)
from koder_agent.harness.sandbox_settings import resolve_sandbox_settings


def test_managed_settings_status_reports_local_policy_file(tmp_path):
    policy = tmp_path / "managed-settings.json"
    policy.write_text(
        json.dumps(
            {
                "disableAllHooks": False,
                "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo ok"}]}]},
                "sandbox": {"enabled": True, "allowUnsandboxedCommands": False},
            }
        ),
        encoding="utf-8",
    )

    state = load_managed_settings(policy)
    output = render_managed_settings_status(policy)

    assert state.exists is True
    assert state.valid is True
    assert state.checksum is not None
    assert "exists: true" in output
    assert "valid: true" in output
    assert "hooks_events: 1" in output
    assert "hooks_groups: 1" in output
    assert "sandbox_policy_locked: true" in output
    assert "allowUnsandboxedCommands" in output


def test_managed_settings_status_reports_invalid_file(tmp_path):
    policy = tmp_path / "managed-settings.json"
    policy.write_text("[", encoding="utf-8")

    state = load_managed_settings(policy)
    output = render_managed_settings_status(policy)

    assert state.exists is True
    assert state.valid is False
    assert "valid: false" in output
    assert "error:" in output


def test_managed_settings_lock_sandbox_policy(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".koder").mkdir(parents=True)
    (tmp_path / ".koder" / "managed-settings.json").write_text(
        json.dumps({"sandbox": {"enabled": True, "allowUnsandboxedCommands": False}}),
        encoding="utf-8",
    )
    (tmp_path / ".koder" / "settings.local.json").write_text(
        json.dumps({"sandbox": {"enabled": False, "allowUnsandboxedCommands": True}}),
        encoding="utf-8",
    )

    state = resolve_sandbox_settings(tmp_path)

    assert state.enabled is True
    assert state.allow_unsandboxed_commands is False
    assert state.policy_locked is True
