"""Regression tests for project-level hook trust gate (C1+C4).

Covers:
- Untrusted project hooks are blocked before approval
- Approved project hooks execute normally
- Hook env does not contain API keys for project hooks
- User hooks get full env
- passFullEnv opt-in works
"""

from __future__ import annotations

import json
import multiprocessing
import os
import sys
import types
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import pytest

# Stub litellm
if "litellm" not in sys.modules:
    litellm_stub = types.ModuleType("litellm")
    litellm_stub.model_cost = {}
    sys.modules["litellm"] = litellm_stub

project_root = Path(__file__).resolve().parents[3]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from koder_agent.harness.hooks.project_approval import (
    HookApprovalStorageError,
    approve_project_hooks,
    canonical_project_hooks_payload,
    is_project_hooks_allowed,
    load_project_hook_settings,
    project_hooks_digest,
    revoke_project_hooks,
)


def _write_project_settings(project: Path, settings: dict) -> Path:
    settings_path = project / ".koder" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    return settings_path


def _executable_settings(marker: Path | None = None) -> dict:
    command = "printf approved"
    if marker is not None:
        command = f"printf approved > {marker}"
    return {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "run_shell",
                    "hooks": [
                        {
                            "type": "command",
                            "command": command,
                            "if": "run_shell(git *)",
                            "timeout": 5,
                            "shell": "sh",
                            "async": False,
                            "once": False,
                            "passFullEnv": False,
                        },
                        {
                            "type": "http",
                            "url": "https://example.test/hook",
                            "headers": {"Authorization": "Bearer ${HOOK_TOKEN}"},
                            "allowedEnvVars": ["HOOK_TOKEN"],
                            "timeout": 7,
                        },
                        {
                            "type": "prompt",
                            "prompt": "Decide whether this is safe",
                            "model": "small-model",
                        },
                        {
                            "type": "agent",
                            "prompt": "Inspect this operation",
                            "model": "agent-model",
                        },
                    ],
                }
            ]
        }
    }


def _approval_process_worker(
    approvals_path: str,
    project_path: str,
    action: str,
    start_event,
    result_queue,
) -> None:
    from koder_agent.harness.hooks import project_approval

    project_approval._approvals_path = lambda: Path(approvals_path)
    project_approval._invalidate_cache()
    try:
        if not start_event.wait(timeout=10):
            raise RuntimeError("process start barrier timed out")
        if action == "approve":
            project_approval.approve_project_hooks(Path(project_path))
        elif action == "revoke":
            project_approval.revoke_project_hooks(Path(project_path))
        else:
            raise ValueError(f"unknown action: {action}")
        result_queue.put(None)
    except BaseException as exc:
        result_queue.put(repr(exc))
        raise


def _run_process_actions(
    approvals_path: Path,
    actions: list[tuple[str, Path]],
) -> None:
    context = multiprocessing.get_context("spawn")
    start_event = context.Event()
    result_queue = context.Queue()
    processes = [
        context.Process(
            target=_approval_process_worker,
            args=(
                str(approvals_path),
                str(project),
                action,
                start_event,
                result_queue,
            ),
        )
        for action, project in actions
    ]
    for process in processes:
        process.start()
    start_event.set()
    for process in processes:
        process.join(timeout=20)
        assert process.exitcode == 0
    assert [result_queue.get(timeout=5) for _ in processes] == [None] * len(processes)


def _descriptor_lock_process_worker(
    approvals_path: str,
    role: str,
    descriptor_opened_event,
    begin_acquire_event,
    entered_event,
    release_event,
    result_queue,
) -> None:
    from koder_agent.harness.hooks import project_approval

    project_approval._approvals_path = lambda: Path(approvals_path)
    original_acquire = project_approval._acquire_descriptor_lock
    if role == "waiter":

        def gated_acquire(fd: int, timeout: float) -> None:
            descriptor_opened_event.set()
            if not begin_acquire_event.wait(timeout=10):
                raise RuntimeError("waiter acquisition barrier timed out")
            original_acquire(fd, timeout)

        project_approval._acquire_descriptor_lock = gated_acquire

    try:
        with project_approval._locked_approval_storage() as validate_lock_identity:
            validate_lock_identity()
            entered_event.set()
            if not release_event.wait(timeout=10):
                raise RuntimeError(f"{role} release barrier timed out")
        result_queue.put((role, "completed"))
    except project_approval.HookApprovalStorageError:
        result_queue.put((role, "rejected"))
    except BaseException as exc:
        result_queue.put((role, repr(exc)))
        raise


@pytest.fixture
def isolated_approvals(tmp_path, monkeypatch):
    from koder_agent.harness.hooks import project_approval

    approvals_file = tmp_path / "hook-project-approvals.json"
    monkeypatch.setattr(project_approval, "_approvals_path", lambda: approvals_file)
    project_approval._invalidate_cache()
    yield approvals_file
    project_approval._invalidate_cache()


class TestProjectHookApproval:
    def test_unapproved_project_returns_false(self, tmp_path, isolated_approvals):
        assert is_project_hooks_allowed(tmp_path / "evil-repo") is False

    def test_approved_project_returns_true(self, tmp_path, isolated_approvals):
        project = tmp_path / "my-project"
        _write_project_settings(project, _executable_settings())
        approve_project_hooks(project)
        assert is_project_hooks_allowed(project) is True

    def test_revoke_removes_approval(self, tmp_path, isolated_approvals):
        project = tmp_path / "my-project"
        _write_project_settings(project, _executable_settings())
        approve_project_hooks(project)
        revoke_project_hooks(project)
        assert is_project_hooks_allowed(project) is False

    def test_corrupt_file_returns_false(self, tmp_path, isolated_approvals):
        isolated_approvals.write_text("not json", encoding="utf-8")
        assert is_project_hooks_allowed(tmp_path / "project") is False

    def test_malformed_approved_mapping_fails_closed(self, tmp_path, isolated_approvals):
        isolated_approvals.write_text(
            json.dumps({"schema_version": 2, "approved": ["not", "a", "mapping"]}),
            encoding="utf-8",
        )
        assert is_project_hooks_allowed(tmp_path / "project") is False

    def test_multiple_projects_independent(self, tmp_path, isolated_approvals):
        proj_a = tmp_path / "project-a"
        proj_b = tmp_path / "project-b"
        _write_project_settings(proj_a, _executable_settings())
        _write_project_settings(proj_b, _executable_settings())
        approve_project_hooks(proj_a)
        assert is_project_hooks_allowed(proj_a) is True
        assert is_project_hooks_allowed(proj_b) is False

    def test_approval_record_is_auditable(self, tmp_path, isolated_approvals):
        project = tmp_path / "my-project"
        _write_project_settings(project, _executable_settings())

        approve_project_hooks(project)

        stored = json.loads(isolated_approvals.read_text(encoding="utf-8"))
        assert stored["schema_version"] == 2
        record = next(iter(stored["approved"].values()))
        assert record["project_path"] == str(project.resolve())
        assert record["digest_algorithm"] == "sha256"
        assert record["payload_schema_version"] == 1
        assert len(record["executable_digest"]) == 64
        assert record["approved_at"].endswith("Z")

    def test_legacy_path_only_record_fails_closed(self, tmp_path, isolated_approvals):
        project = tmp_path / "legacy-project"
        _write_project_settings(project, _executable_settings())
        approve_project_hooks(project)
        stored = json.loads(isolated_approvals.read_text(encoding="utf-8"))
        key = next(iter(stored["approved"]))
        stored["approved"][key] = str(project.resolve())
        isolated_approvals.write_text(json.dumps(stored), encoding="utf-8")

        assert is_project_hooks_allowed(project) is False

    @pytest.mark.parametrize(
        "mutation",
        [
            lambda data: data["hooks"]["PreToolUse"][0].__setitem__("matcher", "Edit"),
            lambda data: data["hooks"]["PreToolUse"][0]["hooks"][0].__setitem__(
                "command", "printf changed"
            ),
            lambda data: data["hooks"]["PreToolUse"][0]["hooks"][0].__setitem__("type", "agent"),
            lambda data: data["hooks"]["PreToolUse"][0]["hooks"][0].__setitem__(
                "if", "run_shell(rm *)"
            ),
            lambda data: data["hooks"]["PreToolUse"][0]["hooks"][0].__setitem__("timeout", 9),
            lambda data: data["hooks"]["PreToolUse"][0]["hooks"][0].__setitem__("shell", "bash"),
            lambda data: data["hooks"]["PreToolUse"][0]["hooks"][0].__setitem__("async", True),
            lambda data: data["hooks"]["PreToolUse"][0]["hooks"][0].__setitem__("once", True),
            lambda data: data["hooks"]["PreToolUse"][0]["hooks"][0].__setitem__(
                "passFullEnv", True
            ),
            lambda data: data["hooks"]["PreToolUse"][0]["hooks"][1].__setitem__(
                "url", "https://changed.test/hook"
            ),
            lambda data: data["hooks"]["PreToolUse"][0]["hooks"][1].__setitem__(
                "headers", {"X-Changed": "yes"}
            ),
            lambda data: data["hooks"]["PreToolUse"][0]["hooks"][1].__setitem__(
                "allowedEnvVars", ["OTHER_TOKEN"]
            ),
            lambda data: data["hooks"]["PreToolUse"][0]["hooks"][2].__setitem__(
                "prompt", "Changed prompt"
            ),
            lambda data: data["hooks"]["PreToolUse"][0]["hooks"][2].__setitem__(
                "model", "changed-model"
            ),
            lambda data: data["hooks"]["PreToolUse"][0]["hooks"][3].__setitem__(
                "prompt", "Changed agent prompt"
            ),
            lambda data: data.__setitem__("disableAllHooks", True),
            lambda data: data["hooks"].__setitem__("PostToolUse", data["hooks"].pop("PreToolUse")),
        ],
        ids=[
            "matcher",
            "command",
            "type",
            "condition",
            "timeout",
            "shell",
            "async",
            "once",
            "pass-full-env",
            "url",
            "headers",
            "allowed-env-vars",
            "prompt",
            "prompt-model",
            "agent-prompt",
            "disable-all-hooks",
            "event",
        ],
    )
    def test_executable_change_invalidates_approval(self, tmp_path, isolated_approvals, mutation):
        project = tmp_path / "my-project"
        settings = _executable_settings()
        _write_project_settings(project, settings)
        approve_project_hooks(project)

        changed = deepcopy(settings)
        mutation(changed)
        _write_project_settings(project, changed)

        assert is_project_hooks_allowed(project) is False

    def test_unrelated_settings_and_json_presentation_do_not_invalidate(
        self, tmp_path, isolated_approvals
    ):
        project = tmp_path / "my-project"
        settings = _executable_settings()
        _write_project_settings(project, settings)
        approve_project_hooks(project)

        changed = {"theme": "dark", "permissions": {"allow": ["Read"]}, **settings}
        settings_path = project / ".koder" / "settings.json"
        settings_path.write_text(
            json.dumps(changed, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )

        assert is_project_hooks_allowed(project) is True

    def test_approval_binds_the_reviewed_snapshot(self, tmp_path, isolated_approvals):
        project = tmp_path / "my-project"
        settings = _executable_settings()
        _write_project_settings(project, settings)
        reviewed_snapshot = load_project_hook_settings(project)
        settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"] = "printf replaced"
        _write_project_settings(project, settings)

        approve_project_hooks(project, reviewed_snapshot)

        assert is_project_hooks_allowed(project) is False

    def test_expected_digest_prevents_approving_a_different_snapshot(
        self, tmp_path, isolated_approvals
    ):
        project = tmp_path / "my-project"
        settings = _executable_settings()
        _write_project_settings(project, settings)
        expected_digest = project_hooks_digest(load_project_hook_settings(project))
        settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"] = "printf changed"
        _write_project_settings(project, settings)

        with pytest.raises(ValueError, match="digest changed"):
            approve_project_hooks(project, expected_digest=expected_digest)

        assert is_project_hooks_allowed(project) is False

    def test_concurrent_approvals_preserve_both_projects(self, tmp_path, isolated_approvals):
        projects = [tmp_path / "project-a", tmp_path / "project-b"]
        for project in projects:
            _write_project_settings(project, _executable_settings())

        with ThreadPoolExecutor(max_workers=2) as executor:
            list(executor.map(approve_project_hooks, projects))

        assert all(is_project_hooks_allowed(project) for project in projects)
        stored = json.loads(isolated_approvals.read_text(encoding="utf-8"))
        assert len(stored["approved"]) == 2

    def test_process_concurrent_approvals_preserve_all_projects(self, tmp_path, isolated_approvals):
        projects = [tmp_path / f"process-project-{index}" for index in range(4)]
        for project in projects:
            _write_project_settings(project, _executable_settings())

        _run_process_actions(
            isolated_approvals,
            [("approve", project) for project in projects],
        )

        assert all(is_project_hooks_allowed(project) for project in projects)
        stored = json.loads(isolated_approvals.read_text(encoding="utf-8"))
        assert len(stored["approved"]) == len(projects)

    def test_process_concurrent_approve_and_revoke_preserve_unrelated_updates(
        self, tmp_path, isolated_approvals
    ):
        revoked_project = tmp_path / "revoked-project"
        retained_project = tmp_path / "retained-project"
        added_project = tmp_path / "added-project"
        for project in (revoked_project, retained_project, added_project):
            _write_project_settings(project, _executable_settings())
        approve_project_hooks(revoked_project)
        approve_project_hooks(retained_project)

        _run_process_actions(
            isolated_approvals,
            [("revoke", revoked_project), ("approve", added_project)],
        )

        assert is_project_hooks_allowed(revoked_project) is False
        assert is_project_hooks_allowed(retained_project) is True
        assert is_project_hooks_allowed(added_project) is True

    def test_approval_file_is_private_and_atomically_replaced(self, tmp_path, isolated_approvals):
        project = tmp_path / "my-project"
        _write_project_settings(project, _executable_settings())

        approve_project_hooks(project)

        assert isolated_approvals.stat().st_mode & 0o777 == 0o600

    def test_failed_atomic_replace_preserves_previous_approval(
        self, tmp_path, monkeypatch, isolated_approvals
    ):
        from koder_agent.harness.hooks import project_approval

        project = tmp_path / "my-project"
        settings = _executable_settings()
        _write_project_settings(project, settings)
        approve_project_hooks(project)
        original = isolated_approvals.read_bytes()
        settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"] = "printf changed"
        _write_project_settings(project, settings)

        def fail_replace(_source, _destination):
            raise OSError("replace failed")

        monkeypatch.setattr(project_approval.os, "replace", fail_replace)
        with pytest.raises(HookApprovalStorageError, match="replace failed"):
            approve_project_hooks(project)

        assert isolated_approvals.read_bytes() == original
        assert not list(isolated_approvals.parent.glob(".hook-project-approvals.json.*.tmp"))

    def test_symlinked_approval_file_is_rejected_without_touching_target(
        self, tmp_path, monkeypatch, isolated_approvals
    ):
        from koder_agent.harness.hooks import project_approval

        project = tmp_path / "my-project"
        victim = tmp_path / "victim.json"
        victim.write_text("do not replace", encoding="utf-8")
        isolated_approvals.symlink_to(victim)
        _write_project_settings(project, _executable_settings())
        monkeypatch.setattr(project_approval, "_no_follow_flag", lambda: 0)
        monkeypatch.setattr(project_approval, "_running_on_windows", lambda: True)
        monkeypatch.setattr(project_approval, "_windows_handle_is_reparse_point", lambda _fd: False)

        with pytest.raises(
            HookApprovalStorageError, match="unsafe|non-regular|linked|symlink|reparse"
        ):
            approve_project_hooks(project)

        assert victim.read_text(encoding="utf-8") == "do not replace"
        assert is_project_hooks_allowed(project) is False

    def test_symlinked_lock_file_is_rejected(self, tmp_path, monkeypatch, isolated_approvals):
        from koder_agent.harness.hooks import project_approval

        project = tmp_path / "my-project"
        victim = tmp_path / "lock-victim"
        victim.write_text("do not lock", encoding="utf-8")
        lock_path = isolated_approvals.with_name(f"{isolated_approvals.name}.lock")
        lock_path.symlink_to(victim)
        _write_project_settings(project, _executable_settings())
        monkeypatch.setattr(project_approval, "_no_follow_flag", lambda: 0)
        monkeypatch.setattr(project_approval, "_running_on_windows", lambda: True)
        monkeypatch.setattr(project_approval, "_windows_handle_is_reparse_point", lambda _fd: False)

        with pytest.raises(
            HookApprovalStorageError, match="unsafe|non-regular|linked|symlink|reparse"
        ):
            approve_project_hooks(project)

        assert victim.read_text(encoding="utf-8") == "do not lock"

    def test_existing_lock_file_content_is_never_truncated(self, tmp_path, isolated_approvals):
        project = tmp_path / "my-project"
        _write_project_settings(project, _executable_settings())
        lock_path = isolated_approvals.with_name(f"{isolated_approvals.name}.lock")
        original = b"preserve-existing-lock-content\x00byte-for-byte"
        lock_path.write_bytes(original)

        approve_project_hooks(project)

        assert lock_path.read_bytes() == original

    @pytest.mark.skipif(os.name == "nt", reason="hardlink creation privileges vary on Windows")
    def test_hardlinked_lock_file_is_rejected_without_touching_victim(
        self, tmp_path, isolated_approvals
    ):
        project = tmp_path / "my-project"
        _write_project_settings(project, _executable_settings())
        victim = tmp_path / "lock-victim"
        victim_bytes = b"static-hardlink-victim\x00unchanged"
        victim.write_bytes(victim_bytes)
        lock_path = isolated_approvals.with_name(f"{isolated_approvals.name}.lock")
        os.link(victim, lock_path)

        with pytest.raises(HookApprovalStorageError, match="multiply-linked"):
            approve_project_hooks(project)

        assert victim.read_bytes() == victim_bytes

    def test_simulated_windows_reparse_lock_path_is_rejected(
        self, tmp_path, monkeypatch, isolated_approvals
    ):
        from koder_agent.harness.hooks import project_approval

        project = tmp_path / "my-project"
        lock_path = isolated_approvals.with_name(f"{isolated_approvals.name}.lock")
        lock_path.touch()
        _write_project_settings(project, _executable_settings())
        original_check = project_approval._path_is_link_or_reparse
        monkeypatch.setattr(project_approval, "_no_follow_flag", lambda: 0)
        monkeypatch.setattr(project_approval, "_running_on_windows", lambda: True)
        monkeypatch.setattr(
            project_approval,
            "_path_is_link_or_reparse",
            lambda path, file_stat: path == lock_path or original_check(path, file_stat),
        )

        with pytest.raises(HookApprovalStorageError, match="reparse-point"):
            approve_project_hooks(project)

    def test_simulated_windows_reparse_handle_is_rejected(
        self, tmp_path, monkeypatch, isolated_approvals
    ):
        from koder_agent.harness.hooks import project_approval

        project = tmp_path / "my-project"
        _write_project_settings(project, _executable_settings())
        monkeypatch.setattr(project_approval, "_no_follow_flag", lambda: 0)
        monkeypatch.setattr(project_approval, "_running_on_windows", lambda: True)
        monkeypatch.setattr(project_approval, "_windows_handle_is_reparse_point", lambda _fd: True)

        with pytest.raises(HookApprovalStorageError, match="reparse-point"):
            approve_project_hooks(project)

    @pytest.mark.skipif(os.name == "nt", reason="Unix descriptor-lock adversary")
    def test_waiter_keeps_validated_descriptor_across_lock_path_replacement(
        self, isolated_approvals
    ):
        context = multiprocessing.get_context("spawn")
        holder_opened = context.Event()
        holder_begin = context.Event()
        holder_entered = context.Event()
        holder_release = context.Event()
        waiter_opened = context.Event()
        waiter_begin = context.Event()
        waiter_entered = context.Event()
        waiter_release = context.Event()
        result_queue = context.Queue()

        holder = context.Process(
            target=_descriptor_lock_process_worker,
            args=(
                str(isolated_approvals),
                "holder",
                holder_opened,
                holder_begin,
                holder_entered,
                holder_release,
                result_queue,
            ),
        )
        holder.start()
        assert holder_entered.wait(timeout=10)

        waiter = context.Process(
            target=_descriptor_lock_process_worker,
            args=(
                str(isolated_approvals),
                "waiter",
                waiter_opened,
                waiter_begin,
                waiter_entered,
                waiter_release,
                result_queue,
            ),
        )
        waiter.start()
        assert waiter_opened.wait(timeout=10)

        lock_path = isolated_approvals.with_name(f"{isolated_approvals.name}.lock")
        replacement = lock_path.with_suffix(".replacement")
        replacement.write_bytes(b"replacement-lock-content")
        os.replace(replacement, lock_path)
        waiter_begin.set()

        overlap = waiter_entered.wait(timeout=0.25)
        if overlap:
            waiter_release.set()
        holder_release.set()
        holder.join(timeout=10)
        waiter.join(timeout=10)
        assert holder.exitcode == 0
        assert waiter.exitcode == 0
        assert overlap is False
        assert sorted(result_queue.get(timeout=5) for _ in range(2)) == [
            ("holder", "completed"),
            ("waiter", "rejected"),
        ]

    @pytest.mark.skipif(os.name == "nt", reason="Unix hardlink adversary")
    def test_hardlink_substitution_before_descriptor_lock_preserves_victim(
        self, tmp_path, monkeypatch, isolated_approvals
    ):
        from koder_agent.harness.hooks import project_approval

        project = tmp_path / "my-project"
        victim = tmp_path / "lock-victim"
        victim_bytes = b"DO-NOT-TRUNCATE\x00exact-bytes"
        victim.write_bytes(victim_bytes)
        _write_project_settings(project, _executable_settings())
        lock_path = isolated_approvals.with_name(f"{isolated_approvals.name}.lock")
        original_acquire = project_approval._acquire_descriptor_lock

        def substitute_then_acquire(fd: int, timeout: float) -> None:
            lock_path.unlink()
            os.link(victim, lock_path)
            original_acquire(fd, timeout)

        monkeypatch.setattr(project_approval, "_acquire_descriptor_lock", substitute_then_acquire)

        with pytest.raises(HookApprovalStorageError, match="linked|changed|replaced"):
            approve_project_hooks(project)

        assert victim.read_bytes() == victim_bytes

    @pytest.mark.skipif(os.name == "nt", reason="Unix symlink adversary")
    def test_symlink_substitution_before_descriptor_lock_fails_closed(
        self, tmp_path, monkeypatch, isolated_approvals
    ):
        from koder_agent.harness.hooks import project_approval

        project = tmp_path / "my-project"
        victim = tmp_path / "lock-victim"
        victim_bytes = b"unchanged-victim"
        victim.write_bytes(victim_bytes)
        _write_project_settings(project, _executable_settings())
        lock_path = isolated_approvals.with_name(f"{isolated_approvals.name}.lock")
        original_acquire = project_approval._acquire_descriptor_lock

        def substitute_then_acquire(fd: int, timeout: float) -> None:
            lock_path.unlink()
            lock_path.symlink_to(victim)
            original_acquire(fd, timeout)

        monkeypatch.setattr(project_approval, "_acquire_descriptor_lock", substitute_then_acquire)

        with pytest.raises(HookApprovalStorageError, match="symlink|reparse"):
            approve_project_hooks(project)

        assert victim.read_bytes() == victim_bytes

    def test_simulated_windows_reparse_swap_before_descriptor_lock_fails_closed(
        self, tmp_path, monkeypatch, isolated_approvals
    ):
        from koder_agent.harness.hooks import project_approval

        project = tmp_path / "my-project"
        _write_project_settings(project, _executable_settings())
        lock_path = isolated_approvals.with_name(f"{isolated_approvals.name}.lock")
        original_acquire = project_approval._acquire_descriptor_lock
        original_check = project_approval._path_is_link_or_reparse
        swapped = False

        def simulated_reparse(path, file_stat):
            return (swapped and path == lock_path) or original_check(path, file_stat)

        def substitute_then_acquire(fd: int, timeout: float) -> None:
            nonlocal swapped
            lock_path.unlink()
            lock_path.write_bytes(b"simulated-reparse")
            swapped = True
            original_acquire(fd, timeout)

        monkeypatch.setattr(project_approval, "_path_is_link_or_reparse", simulated_reparse)
        monkeypatch.setattr(project_approval, "_acquire_descriptor_lock", substitute_then_acquire)

        with pytest.raises(HookApprovalStorageError, match="reparse-point|non-regular|replaced"):
            approve_project_hooks(project)

    def test_windows_lock_adapter_uses_same_validated_descriptor(
        self, monkeypatch, isolated_approvals
    ):
        from koder_agent.harness.hooks import project_approval

        acquired: list[int] = []
        released: list[int] = []
        monkeypatch.setattr(project_approval, "_running_on_windows", lambda: True)
        monkeypatch.setattr(project_approval, "_no_follow_flag", lambda: 0)
        monkeypatch.setattr(project_approval, "_windows_handle_is_reparse_point", lambda _fd: False)
        monkeypatch.setattr(
            project_approval, "_windows_lock_descriptor", lambda fd: acquired.append(fd)
        )
        monkeypatch.setattr(
            project_approval, "_windows_unlock_descriptor", lambda fd: released.append(fd)
        )

        with project_approval._locked_approval_storage() as validate_lock_identity:
            validate_lock_identity()
            assert acquired
            os.fstat(acquired[0])

        assert acquired == released
        with pytest.raises(OSError):
            os.fstat(acquired[0])

    @pytest.mark.parametrize("failure", [TimeoutError("timeout"), KeyboardInterrupt()])
    def test_failed_or_cancelled_acquisition_closes_descriptor_and_future_locking_works(
        self, tmp_path, monkeypatch, isolated_approvals, failure
    ):
        from koder_agent.harness.hooks import project_approval

        project = tmp_path / "my-project"
        _write_project_settings(project, _executable_settings())
        original_acquire = project_approval._acquire_descriptor_lock
        opened_fds: list[int] = []

        def fail_acquire(fd: int, _timeout: float) -> None:
            opened_fds.append(fd)
            raise failure

        monkeypatch.setattr(project_approval, "_acquire_descriptor_lock", fail_acquire)
        expected = (
            HookApprovalStorageError if isinstance(failure, TimeoutError) else KeyboardInterrupt
        )
        with pytest.raises(expected):
            approve_project_hooks(project)

        assert opened_fds
        with pytest.raises(OSError):
            os.fstat(opened_fds[0])

        monkeypatch.setattr(project_approval, "_acquire_descriptor_lock", original_acquire)
        approve_project_hooks(project)
        assert is_project_hooks_allowed(project) is True

    def test_unlock_error_closes_descriptor_and_future_locking_works(
        self, tmp_path, monkeypatch, isolated_approvals
    ):
        from koder_agent.harness.hooks import project_approval

        project = tmp_path / "my-project"
        _write_project_settings(project, _executable_settings())
        original_unlock = project_approval._unlock_descriptor
        locked_fds: list[int] = []

        def fail_unlock(fd: int) -> None:
            locked_fds.append(fd)
            raise OSError("injected unlock failure")

        monkeypatch.setattr(project_approval, "_unlock_descriptor", fail_unlock)
        with pytest.raises(HookApprovalStorageError, match="release.*injected unlock failure"):
            approve_project_hooks(project)

        assert locked_fds
        with pytest.raises(OSError):
            os.fstat(locked_fds[0])

        monkeypatch.setattr(project_approval, "_unlock_descriptor", original_unlock)
        revoke_project_hooks(project)
        approve_project_hooks(project)
        assert is_project_hooks_allowed(project) is True


class TestCanonicalExecutablePayload:
    def test_serializer_is_deterministic_and_excludes_unrelated_settings(self):
        left = _executable_settings()
        left["theme"] = "dark"
        right = {"permissions": {"deny": ["Bash"]}, **_executable_settings()}

        assert canonical_project_hooks_payload(left) == canonical_project_hooks_payload(right)
        assert project_hooks_digest(left) == project_hooks_digest(right)


class TestProjectHookApprovalDispatch:
    def test_approved_unchanged_payload_runs(self, tmp_path, monkeypatch, isolated_approvals):
        from koder_agent.harness.hooks.runtime import dispatch_command_hooks

        project = tmp_path / "project"
        marker = tmp_path / "approved-marker"
        settings = {
            "hooks": {
                "SessionStart": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": f"printf approved > {marker}",
                            }
                        ]
                    }
                ]
            }
        }
        _write_project_settings(project, settings)
        approve_project_hooks(project)
        monkeypatch.chdir(project)

        result = dispatch_command_hooks(
            cwd=project,
            event_name="SessionStart",
            payload={"event": "SessionStart", "source": "startup"},
        )

        assert result.matched_hooks == 1
        assert marker.read_text(encoding="utf-8") == "approved"

    def test_changed_command_is_denied_at_dispatch(
        self, tmp_path, monkeypatch, isolated_approvals, caplog
    ):
        from koder_agent.harness.hooks.runtime import dispatch_command_hooks

        project = tmp_path / "project"
        approved_marker = tmp_path / "approved-marker"
        changed_marker = tmp_path / "changed-marker"
        settings = {
            "hooks": {
                "SessionStart": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": f"printf approved > {approved_marker}",
                            }
                        ]
                    }
                ]
            }
        }
        _write_project_settings(project, settings)
        approve_project_hooks(project)
        settings["hooks"]["SessionStart"][0]["hooks"][0]["command"] = (
            f"printf changed > {changed_marker}"
        )
        _write_project_settings(project, settings)
        monkeypatch.chdir(project)

        result = dispatch_command_hooks(
            cwd=project,
            event_name="SessionStart",
            payload={"event": "SessionStart", "source": "startup"},
        )

        assert result.matched_hooks == 0
        assert not approved_marker.exists()
        assert not changed_marker.exists()
        assert "executable hook configuration changed" in caplog.text
        assert "review and reapprove current hooks" in caplog.text

    def test_local_hook_changes_share_project_approval_binding(
        self, tmp_path, monkeypatch, isolated_approvals
    ):
        from koder_agent.harness.hooks.runtime import dispatch_command_hooks

        project = tmp_path / "project"
        local_settings_path = project / ".koder" / "settings.local.json"
        local_settings_path.parent.mkdir(parents=True)
        marker = tmp_path / "local-marker"
        changed_marker = tmp_path / "changed-local-marker"
        local_settings = {
            "hooks": {
                "SessionStart": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": f"printf local > {marker}",
                            }
                        ]
                    }
                ]
            }
        }
        local_settings_path.write_text(json.dumps(local_settings), encoding="utf-8")
        approve_project_hooks(project)
        monkeypatch.chdir(project)

        allowed = dispatch_command_hooks(
            cwd=project,
            event_name="SessionStart",
            payload={"event": "SessionStart", "source": "startup"},
        )
        assert allowed.matched_hooks == 1
        assert marker.read_text(encoding="utf-8") == "local"

        local_settings["hooks"]["SessionStart"][0]["hooks"][0]["command"] = (
            f"printf changed > {changed_marker}"
        )
        local_settings_path.write_text(json.dumps(local_settings), encoding="utf-8")
        denied = dispatch_command_hooks(
            cwd=project,
            event_name="SessionStart",
            payload={"event": "SessionStart", "source": "startup"},
        )

        assert denied.matched_hooks == 0
        assert not changed_marker.exists()

    def test_user_hook_does_not_require_project_approval(
        self, tmp_path, monkeypatch, isolated_approvals
    ):
        from koder_agent.harness.hooks.runtime import dispatch_command_hooks

        home = tmp_path / "home"
        project = tmp_path / "project"
        marker = tmp_path / "user-marker"
        (home / ".koder").mkdir(parents=True)
        project.mkdir()
        (home / ".koder" / "settings.json").write_text(
            json.dumps(
                {
                    "hooks": {
                        "SessionStart": [
                            {
                                "hooks": [
                                    {
                                        "type": "command",
                                        "command": f"printf user > {marker}",
                                    }
                                ]
                            }
                        ]
                    }
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.chdir(project)

        result = dispatch_command_hooks(
            cwd=project,
            event_name="SessionStart",
            payload={"event": "SessionStart", "source": "startup"},
        )

        assert result.matched_hooks == 1
        assert marker.read_text(encoding="utf-8") == "user"

    def test_project_settings_are_parsed_once_for_digest_and_execution(
        self, tmp_path, monkeypatch, isolated_approvals
    ):
        from koder_agent.harness.hooks import runtime

        project = tmp_path / "project"
        marker = tmp_path / "approved-marker"
        settings_path = _write_project_settings(
            project,
            {
                "hooks": {
                    "SessionStart": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": f"printf approved > {marker}",
                                }
                            ]
                        }
                    ]
                }
            },
        )
        approve_project_hooks(project)
        monkeypatch.chdir(project)
        original_load = runtime._load_json_file
        project_loads = 0

        def counted_load(path):
            nonlocal project_loads
            if path == settings_path:
                project_loads += 1
            return original_load(path)

        monkeypatch.setattr(runtime, "_load_json_file", counted_load)

        result = runtime.dispatch_command_hooks(
            cwd=project,
            event_name="SessionStart",
            payload={"event": "SessionStart", "source": "startup"},
        )

        assert result.matched_hooks == 1
        assert project_loads == 1


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
