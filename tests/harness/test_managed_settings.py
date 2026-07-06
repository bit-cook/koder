import json

from koder_agent.harness.managed_settings import load_managed_settings
from koder_agent.harness.sandbox_settings import resolve_sandbox_settings


def test_managed_settings_status_reports_local_policy_file(tmp_path):
    policy = tmp_path / "managed-settings.json"
    policy.write_text(
        json.dumps(
            {
                "disableAllHooks": False,
                "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo ok"}]}]},
                "sandbox": {"enabled": True, "backend": "unix-local"},
            }
        ),
        encoding="utf-8",
    )

    state = load_managed_settings(policy)

    assert state.exists is True
    assert state.valid is True
    assert state.checksum is not None
    assert state.data.get("sandbox", {}).get("enabled") is True


def test_managed_settings_status_reports_invalid_file(tmp_path):
    policy = tmp_path / "managed-settings.json"
    policy.write_text("[", encoding="utf-8")

    state = load_managed_settings(policy)

    assert state.exists is True
    assert state.valid is False
    assert state.error


def test_managed_settings_lock_sandbox_policy(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".koder").mkdir(parents=True)
    (tmp_path / ".koder" / "managed-settings.json").write_text(
        json.dumps({"sandbox": {"enabled": True, "backend": "unix-local"}}),
        encoding="utf-8",
    )
    (tmp_path / ".koder" / "settings.local.json").write_text(
        json.dumps({"sandbox": {"enabled": False, "backend": "docker"}}),
        encoding="utf-8",
    )

    state = resolve_sandbox_settings(tmp_path)

    assert state.enabled is True
    assert state.backend == "unix-local"
    assert state.policy_locked is True


def test_sandbox_settings_parse_backend_mode_and_filesystem_policy(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    project = tmp_path / "project"
    (project / ".koder").mkdir(parents=True)
    (project / ".koder" / "settings.local.json").write_text(
        json.dumps(
            {
                "sandbox": {
                    "enabled": True,
                    "mode": "workspace-write",
                    "backend": "e2b",
                    "networkAccess": True,
                    "writableRoots": ["build"],
                    "allowRead": ["."],
                    "denyRead": ["secrets"],
                    "allowWrite": ["build"],
                    "denyWrite": [".env"],
                    "protectedPaths": [".git", ".koder"],
                }
            }
        ),
        encoding="utf-8",
    )

    state = resolve_sandbox_settings(project)

    assert state.enabled is True
    assert state.policy_mode == "workspace-write"
    assert state.backend == "e2b"
    assert state.network_access is True
    assert state.writable_roots == ("build",)
    assert state.allow_read == (".",)
    assert state.deny_read == ("secrets",)
    assert state.allow_write == ("build",)
    assert state.deny_write == (".env",)
    assert state.protected_paths == (".git", ".koder")
    assert state.policy is not None
