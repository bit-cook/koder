#!/usr/bin/env python3
"""Validate and run scenario-based tmux coverage for Koder TUI features."""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = PROJECT_ROOT / "tests" / "e2e" / "tui_feature_scenarios.json"
VALIDATION_LEVELS = {"smoke", "workflow", "acceptance"}
TURN_ASSERTION_KEYS = (
    "expect_any",
    "expect_all",
    "expect_regex",
    "expect_not",
    "expect_session_dead",
    "expect_tmux_panes_min",
    "expect_tmux_any_pane_any",
    "expect_tmux_any_pane_all",
)
POST_ASSERTION_KEYS = {"path_exists", "path_not_exists", "file_contains", "file_not_contains"}
POST_ASSERTION_KEYS.update({"path_glob_exists", "file_glob_contains", "file_glob_not_contains"})
POST_ASSERTION_KEYS.add("sqlite_contains")


@dataclass(frozen=True)
class ScenarioRef:
    suite: str
    name: str
    payload: dict[str, Any]


def _run(cmd: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None) -> None:
    subprocess.run(cmd, cwd=cwd, env=env, text=True, capture_output=True, check=True, timeout=30)


def _tmux(
    *args: str, check: bool = False, timeout: float = 30.0
) -> subprocess.CompletedProcess[str]:
    tmux = shutil.which("tmux")
    if tmux is None:
        raise RuntimeError("tmux is not available")
    return subprocess.run(
        [tmux, *args], text=True, capture_output=True, check=check, timeout=timeout
    )


def _load_manifest(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _harness_command_names() -> set[str]:
    sys.path.insert(0, str(PROJECT_ROOT))
    from koder_agent.harness.commands.interactive import HarnessInteractiveCommandHandler

    handler = HarnessInteractiveCommandHandler(emit_console=False)
    return set(handler.commands)


def _scenario_refs(manifest: dict[str, Any]) -> list[ScenarioRef]:
    refs: list[ScenarioRef] = []
    for suite_name in ("slash_commands", "agents", "teams", "skills", "features"):
        suite = manifest.get(suite_name, {})
        if not isinstance(suite, dict):
            continue
        for name, payload in suite.items():
            if isinstance(payload, dict):
                refs.append(ScenarioRef(suite=suite_name, name=name, payload=payload))
    return refs


def _validation_level(payload: dict[str, Any]) -> str:
    level = payload.get("validation_level", "smoke")
    return level if isinstance(level, str) else ""


def _non_empty_string_list(value: Any) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(isinstance(item, str) and bool(item.strip()) for item in value)
    )


def _validate_acceptance_metadata(ref: ScenarioRef) -> list[str]:
    if _validation_level(ref.payload) != "acceptance":
        return []

    errors: list[str] = []
    criteria = ref.payload.get("acceptance_criteria")
    artifacts = ref.payload.get("acceptance_artifacts")
    turns = ref.payload.get("turns", [])
    if not _non_empty_string_list(criteria):
        errors.append(f"{ref.suite}/{ref.name}: acceptance_criteria is required")
    if not _non_empty_string_list(artifacts):
        errors.append(f"{ref.suite}/{ref.name}: acceptance_artifacts is required")
    has_exact_visible_assertion = any(
        isinstance(turn, dict) and (turn.get("expect_all") or turn.get("expect_regex"))
        for turn in turns
    )
    if not has_exact_visible_assertion:
        errors.append(f"{ref.suite}/{ref.name}: acceptance needs exact visible assertions")
    has_durable_or_external_assertion = bool(ref.payload.get("post_assertions")) or any(
        isinstance(turn, dict)
        and (
            turn.get("expect_session_dead")
            or turn.get("expect_tmux_panes_min")
            or turn.get("capture") == "visible"
        )
        for turn in turns
    )
    if not has_durable_or_external_assertion:
        errors.append(
            f"{ref.suite}/{ref.name}: acceptance needs durable, external, or pane evidence"
        )
    return errors


def _validate_post_assertions(ref: ScenarioRef) -> list[str]:
    post_assertions = ref.payload.get("post_assertions", [])
    if post_assertions is None:
        return []
    if not isinstance(post_assertions, list):
        return [f"{ref.suite}/{ref.name}: post_assertions must be a list"]

    errors: list[str] = []
    for index, assertion in enumerate(post_assertions, start=1):
        if not isinstance(assertion, dict):
            errors.append(f"{ref.suite}/{ref.name}: post_assertion {index} must be an object")
            continue
        keys = [key for key in assertion if key in POST_ASSERTION_KEYS]
        if len(keys) != 1:
            errors.append(
                f"{ref.suite}/{ref.name}: post_assertion {index} needs exactly one of "
                + ", ".join(sorted(POST_ASSERTION_KEYS))
            )
            continue
        value = assertion[keys[0]]
        if keys[0] in {"path_exists", "path_not_exists", "path_glob_exists"}:
            if not isinstance(value, str) or not value.strip():
                errors.append(
                    f"{ref.suite}/{ref.name}: post_assertion {index} {keys[0]} needs a path"
                )
        elif keys[0] == "sqlite_contains":
            if (
                not isinstance(value, list)
                or len(value) != 3
                or not isinstance(value[0], str)
                or not value[0].strip()
                or not isinstance(value[1], str)
                or not value[1].strip()
                or not (
                    isinstance(value[2], str)
                    or (
                        isinstance(value[2], list)
                        and value[2]
                        and all(isinstance(item, str) for item in value[2])
                    )
                )
            ):
                errors.append(
                    f"{ref.suite}/{ref.name}: post_assertion {index} sqlite_contains needs "
                    "[path, select-query, text-or-text-list]"
                )
            elif not value[1].lstrip().lower().startswith("select"):
                errors.append(
                    f"{ref.suite}/{ref.name}: post_assertion {index} sqlite_contains query "
                    "must be SELECT-only"
                )
        else:
            if (
                not isinstance(value, list)
                or len(value) != 2
                or not isinstance(value[0], str)
                or not value[0].strip()
                or not (
                    isinstance(value[1], str)
                    or (
                        isinstance(value[1], list)
                        and value[1]
                        and all(isinstance(item, str) for item in value[1])
                    )
                )
            ):
                errors.append(
                    f"{ref.suite}/{ref.name}: post_assertion {index} {keys[0]} needs "
                    "[path, text-or-text-list]"
                )
    return errors


def validate_manifest(manifest: dict[str, Any], *, strict_acceptance: bool = False) -> list[str]:
    errors: list[str] = []
    command_names = _harness_command_names()
    slash_suite = manifest.get("slash_commands", {})
    if not isinstance(slash_suite, dict):
        return ["slash_commands must be an object"]

    scenario_commands = set(slash_suite)
    missing = sorted(command_names - scenario_commands)
    extra = sorted(scenario_commands - command_names)
    if missing:
        errors.append("missing slash command scenarios: " + ", ".join(missing))
    if extra:
        errors.append("unknown slash command scenarios: " + ", ".join(extra))

    required_suites = {"agents", "teams", "skills", "features"}
    for suite in required_suites:
        if not manifest.get(suite):
            errors.append(f"missing scenario suite: {suite}")

    for ref in _scenario_refs(manifest):
        payload = ref.payload
        level = payload.get("validation_level", "smoke")
        if not isinstance(level, str) or level not in VALIDATION_LEVELS:
            errors.append(
                f"{ref.suite}/{ref.name}: validation_level must be one of "
                + ", ".join(sorted(VALIDATION_LEVELS))
            )
        purpose = payload.get("purpose")
        if not isinstance(purpose, str) or not purpose.strip():
            errors.append(f"{ref.suite}/{ref.name}: purpose is required")
        env = payload.get("env", {})
        if env and (
            not isinstance(env, dict)
            or not all(
                isinstance(key, str)
                and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key)
                and isinstance(value, str)
                for key, value in env.items()
            )
        ):
            errors.append(f"{ref.suite}/{ref.name}: env must map env var names to strings")
        cli_args = payload.get("cli_args", [])
        if cli_args and (
            not isinstance(cli_args, list)
            or not all(isinstance(item, str) and item.strip() for item in cli_args)
        ):
            errors.append(f"{ref.suite}/{ref.name}: cli_args must contain strings")
        prelaunch_files = payload.get("prelaunch_files", [])
        if prelaunch_files and not isinstance(prelaunch_files, list):
            errors.append(f"{ref.suite}/{ref.name}: prelaunch_files must be a list")
        for index, item in enumerate(prelaunch_files, start=1):
            if not isinstance(item, dict):
                errors.append(f"{ref.suite}/{ref.name}: prelaunch_file {index} must be an object")
                continue
            if not isinstance(item.get("path"), str) or not item["path"].strip():
                errors.append(f"{ref.suite}/{ref.name}: prelaunch_file {index} needs a path")
            if not isinstance(item.get("content"), str):
                errors.append(f"{ref.suite}/{ref.name}: prelaunch_file {index} needs content")
        teammate_mode = payload.get("teammate_mode", "tmux")
        if teammate_mode not in {"auto", "in-process", "tmux"}:
            errors.append(
                f"{ref.suite}/{ref.name}: teammate_mode must be auto, in-process, or tmux"
            )
        fake_openai = payload.get("fake_openai")
        if fake_openai is not None:
            if not isinstance(fake_openai, dict):
                errors.append(f"{ref.suite}/{ref.name}: fake_openai must be an object")
            else:
                if not isinstance(fake_openai.get("port"), int):
                    errors.append(f"{ref.suite}/{ref.name}: fake_openai.port must be an integer")
                if (
                    not isinstance(fake_openai.get("response"), str)
                    or not fake_openai["response"].strip()
                ):
                    errors.append(f"{ref.suite}/{ref.name}: fake_openai.response is required")
                for key in ("log_file", "ready_file"):
                    if not isinstance(fake_openai.get(key), str) or not fake_openai[key].strip():
                        errors.append(f"{ref.suite}/{ref.name}: fake_openai.{key} is required")
        turns = payload.get("turns")
        if not isinstance(turns, list) or len(turns) < 2:
            errors.append(f"{ref.suite}/{ref.name}: at least two interactive turns are required")
            continue
        if ref.suite == "slash_commands" and not any(
            isinstance(turn, dict)
            and isinstance(turn.get("send"), str)
            and turn["send"].split()[0] in {f"/{ref.name}", f"/{ref.name.replace('_', '-')}"}
            for turn in turns
        ):
            errors.append(f"{ref.suite}/{ref.name}: no turn sends /{ref.name}")
        for index, turn in enumerate(turns, start=1):
            if not isinstance(turn, dict):
                errors.append(f"{ref.suite}/{ref.name}: turn {index} must be an object")
                continue
            has_send = isinstance(turn.get("send"), str) and bool(turn["send"].strip())
            has_type = isinstance(turn.get("type"), str) and bool(turn["type"].strip())
            has_keys = isinstance(turn.get("keys"), list) and bool(turn["keys"])
            has_wait = isinstance(turn.get("wait"), (int, float))
            has_resize = isinstance(turn.get("resize"), dict)
            has_kill_tmux_pane = isinstance(turn.get("kill_tmux_pane_matching"), str) and bool(
                turn["kill_tmux_pane_matching"].strip()
            )
            if not any(
                [
                    has_send,
                    has_type,
                    has_keys,
                    has_wait,
                    has_resize,
                    has_kill_tmux_pane,
                    turn.get("expect_session_dead"),
                ]
            ):
                errors.append(
                    f"{ref.suite}/{ref.name}: turn {index} needs an action or session-dead check"
                )
            if has_resize:
                resize = turn["resize"]
                if not isinstance(resize.get("width"), int) or not isinstance(
                    resize.get("height"), int
                ):
                    errors.append(
                        f"{ref.suite}/{ref.name}: turn {index} resize needs integer width/height"
                    )
            if "kill_tmux_pane_matching" in turn and not has_kill_tmux_pane:
                errors.append(
                    f"{ref.suite}/{ref.name}: turn {index} kill_tmux_pane_matching needs text"
                )
            expect_any = turn.get("expect_any", [])
            expect_all = turn.get("expect_all", [])
            expect_regex = turn.get("expect_regex", [])
            expect_not = turn.get("expect_not", [])
            for field_name, field_value in (
                ("expect_any", expect_any),
                ("expect_all", expect_all),
                ("expect_regex", expect_regex),
                ("expect_not", expect_not),
            ):
                if field_value and (
                    not isinstance(field_value, list)
                    or not all(isinstance(item, str) for item in field_value)
                ):
                    errors.append(
                        f"{ref.suite}/{ref.name}: turn {index} {field_name} must contain strings"
                    )
            for pattern in expect_regex:
                if isinstance(pattern, str):
                    try:
                        re.compile(pattern)
                    except re.error as exc:
                        errors.append(
                            f"{ref.suite}/{ref.name}: turn {index} invalid expect_regex "
                            f"{pattern!r}: {exc}"
                        )
            has_tmux_pane_assertion = any(
                key in turn
                for key in (
                    "expect_tmux_panes_min",
                    "expect_tmux_any_pane_any",
                    "expect_tmux_any_pane_all",
                )
            )
            if (
                not turn.get("expect_session_dead")
                and not expect_any
                and not expect_all
                and not expect_regex
                and not expect_not
                and not has_tmux_pane_assertion
            ):
                errors.append(f"{ref.suite}/{ref.name}: turn {index} needs an assertion")
        if strict_acceptance:
            errors.extend(_validate_acceptance_metadata(ref))
        errors.extend(_validate_post_assertions(ref))
    return errors


def _expand_scenario_path(value: str, *, home: Path, repo: Path) -> Path:
    expanded = value.replace("$HOME", str(home)).replace("$REPO", str(repo))
    return Path(expanded).expanduser()


def _expand_scenario_glob(value: str, *, home: Path, repo: Path) -> list[Path]:
    expanded = value.replace("$HOME", str(home)).replace("$REPO", str(repo))
    return [Path(path) for path in sorted(glob.glob(str(Path(expanded).expanduser())))]


def _expand_scenario_env_value(value: str, *, home: Path, repo: Path) -> str:
    return (
        value.replace("$HOME", str(home))
        .replace("$REPO", str(repo))
        .replace("$PATH", os.environ.get("PATH", ""))
    )


def _expected_strings(value: str | list[str]) -> list[str]:
    return [value] if isinstance(value, str) else value


def _run_post_assertions(scenario: ScenarioRef, *, home: Path, repo: Path) -> list[str]:
    failures: list[str] = []
    for index, assertion in enumerate(scenario.payload.get("post_assertions", []), start=1):
        if "path_exists" in assertion:
            path = _expand_scenario_path(assertion["path_exists"], home=home, repo=repo)
            if not path.exists():
                failures.append(f"post_assertion {index}: expected path to exist: {path}")
            continue
        if "path_not_exists" in assertion:
            path = _expand_scenario_path(assertion["path_not_exists"], home=home, repo=repo)
            if path.exists():
                failures.append(f"post_assertion {index}: expected path not to exist: {path}")
            continue
        if "file_contains" in assertion:
            raw_path, expected = assertion["file_contains"]
            path = _expand_scenario_path(raw_path, home=home, repo=repo)
            if not path.exists():
                failures.append(f"post_assertion {index}: expected file to exist: {path}")
                continue
            content = path.read_text(encoding="utf-8")
            missing = [item for item in _expected_strings(expected) if item not in content]
            if missing:
                failures.append(
                    f"post_assertion {index}: {path} missing "
                    + ", ".join(repr(item) for item in missing)
                )
            continue
        if "path_glob_exists" in assertion:
            matches = _expand_scenario_glob(assertion["path_glob_exists"], home=home, repo=repo)
            if not matches:
                failures.append(
                    f"post_assertion {index}: expected glob to match: "
                    f"{assertion['path_glob_exists']}"
                )
            continue
        if "file_glob_contains" in assertion:
            raw_pattern, expected = assertion["file_glob_contains"]
            matches = _expand_scenario_glob(raw_pattern, home=home, repo=repo)
            if not matches:
                failures.append(f"post_assertion {index}: expected glob to match: {raw_pattern}")
                continue
            missing = []
            for item in _expected_strings(expected):
                if not any(item in path.read_text(encoding="utf-8") for path in matches):
                    missing.append(item)
            if missing:
                failures.append(
                    f"post_assertion {index}: {raw_pattern} missing "
                    + ", ".join(repr(item) for item in missing)
                )
            continue
        if "file_glob_not_contains" in assertion:
            raw_pattern, forbidden = assertion["file_glob_not_contains"]
            matches = _expand_scenario_glob(raw_pattern, home=home, repo=repo)
            present = []
            for item in _expected_strings(forbidden):
                if any(item in path.read_text(encoding="utf-8") for path in matches):
                    present.append(item)
            if present:
                failures.append(
                    f"post_assertion {index}: {raw_pattern} unexpectedly contains "
                    + ", ".join(repr(item) for item in present)
                )
            continue
        if "file_not_contains" in assertion:
            raw_path, forbidden = assertion["file_not_contains"]
            path = _expand_scenario_path(raw_path, home=home, repo=repo)
            if not path.exists():
                continue
            content = path.read_text(encoding="utf-8")
            present = [item for item in _expected_strings(forbidden) if item in content]
            if present:
                failures.append(
                    f"post_assertion {index}: {path} unexpectedly contains "
                    + ", ".join(repr(item) for item in present)
                )
            continue
        if "sqlite_contains" in assertion:
            raw_path, query, expected = assertion["sqlite_contains"]
            path = _expand_scenario_path(raw_path, home=home, repo=repo)
            if not path.exists():
                failures.append(f"post_assertion {index}: expected database to exist: {path}")
                continue
            try:
                with sqlite3.connect(path) as conn:
                    rows = conn.execute(query).fetchall()
            except sqlite3.Error as exc:
                failures.append(f"post_assertion {index}: sqlite query failed for {path}: {exc}")
                continue
            content = "\n".join(
                "\t".join("" if value is None else str(value) for value in row) for row in rows
            )
            missing = [item for item in _expected_strings(expected) if item not in content]
            if missing:
                failures.append(
                    f"post_assertion {index}: sqlite result from {path} missing "
                    + ", ".join(repr(item) for item in missing)
                )
    return failures


def _prepare_workspace(root: Path) -> tuple[Path, Path]:
    home = root / "home"
    repo = root / "repo"
    home.mkdir(parents=True, exist_ok=True)
    repo.mkdir(parents=True, exist_ok=True)
    (repo / "AGENTS.md").write_text(
        "# Test project\n\nUse deterministic local outputs.\n", encoding="utf-8"
    )
    (repo / "docs").mkdir(parents=True, exist_ok=True)
    (repo / "docs" / "runtime-notes.md").write_text(
        "# MAGIC DOC: Runtime Notes\n\nKeep this fixture current.\n", encoding="utf-8"
    )
    (repo / "sample.txt").write_text("initial\n", encoding="utf-8")
    (repo / ".koder" / "skills" / "demo-skill").mkdir(parents=True, exist_ok=True)
    (repo / ".koder" / "skills" / "demo-skill" / "SKILL.md").write_text(
        "---\nname: demo-skill\ndescription: Demo skill for tmux validation\ndisable-model-invocation: true\n---\nUse deterministic local output.\n",
        encoding="utf-8",
    )
    (repo / ".koder" / "agents").mkdir(parents=True, exist_ok=True)
    (repo / ".koder" / "agents" / "reviewer.md").write_text(
        "---\nname: reviewer\ndescription: Reviews fixture changes\ntools:\n  - Read\n  - Bash\nmodel: sonnet\npermissionMode: plan\n---\nYou review fixture changes.\n",
        encoding="utf-8",
    )
    plugin = home / ".koder" / "plugins" / "demo-plugin"
    (plugin / "skills" / "plugin-skill").mkdir(parents=True, exist_ok=True)
    (plugin / "plugin.json").write_text(
        json.dumps({"name": "demo-plugin", "version": "1.0.0"}), encoding="utf-8"
    )
    (plugin / "skills" / "plugin-skill" / "SKILL.md").write_text(
        "---\nname: plugin-skill\ndescription: Plugin skill fixture\ndisable-model-invocation: true\n---\nPlugin skill body.\n",
        encoding="utf-8",
    )
    _run(["git", "init"], cwd=repo)
    _run(["git", "config", "user.email", "koder@example.invalid"], cwd=repo)
    _run(["git", "config", "user.name", "Koder Test"], cwd=repo)
    _run(["git", "add", "sample.txt"], cwd=repo)
    _run(["git", "commit", "-m", "initial"], cwd=repo)
    (repo / "sample.txt").write_text("initial\nchanged\n", encoding="utf-8")
    return home, repo


def _write_prelaunch_files(scenario: ScenarioRef, *, home: Path, repo: Path) -> None:
    for item in scenario.payload.get("prelaunch_files", []):
        path = _expand_scenario_path(item["path"], home=home, repo=repo)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(item["content"], encoding="utf-8")


def _start_fake_openai(scenario: ScenarioRef, *, home: Path, repo: Path) -> subprocess.Popen | None:
    fake_openai = scenario.payload.get("fake_openai")
    if not isinstance(fake_openai, dict):
        return None

    log_file = _expand_scenario_path(fake_openai["log_file"], home=home, repo=repo)
    ready_file = _expand_scenario_path(fake_openai["ready_file"], home=home, repo=repo)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    ready_file.parent.mkdir(parents=True, exist_ok=True)
    if ready_file.exists():
        ready_file.unlink()

    proc = subprocess.Popen(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "fake_openai_chat_server.py"),
            "--port",
            str(fake_openai["port"]),
            "--response",
            fake_openai["response"],
            "--log-file",
            str(log_file),
            "--ready-file",
            str(ready_file),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    deadline = time.time() + 5.0
    while time.time() < deadline:
        if ready_file.exists():
            return proc
        if proc.poll() is not None:
            raise RuntimeError(f"fake OpenAI provider exited for {scenario.suite}/{scenario.name}")
        time.sleep(0.1)
    proc.terminate()
    raise RuntimeError(
        f"fake OpenAI provider did not become ready for {scenario.suite}/{scenario.name}"
    )


def _stop_fake_openai(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def _launch_session(home: Path, repo: Path, scenario: ScenarioRef) -> str:
    session = f"koder-scenario-{scenario.suite[:3]}-{scenario.name[:18]}-{uuid.uuid4().hex[:6]}"
    env_assignments = {
        "HOME": str(home),
        "PYTHONPATH": str(PROJECT_ROOT),
        "KODER_MODEL": "gpt-4.1",
    }
    env_assignments.update(
        {
            key: _expand_scenario_env_value(value, home=home, repo=repo)
            for key, value in scenario.payload.get("env", {}).items()
        }
    )
    env_prefix = " ".join(f"{key}={shlex.quote(value)}" for key, value in env_assignments.items())
    teammate_mode = scenario.payload.get("teammate_mode", "tmux")
    extra_cli_args = " ".join(shlex.quote(arg) for arg in scenario.payload.get("cli_args", []))
    launch = (
        f"cd {shlex.quote(str(repo))} && "
        f"{env_prefix} "
        f"uv --project {shlex.quote(str(PROJECT_ROOT))} run --no-sync koder "
        f"--teammate-mode {shlex.quote(teammate_mode)}"
        f"{(' ' + extra_cli_args) if extra_cli_args else ''}"
    )
    result = _tmux(
        "new-session",
        "-d",
        "-s",
        session,
        "-x",
        "160",
        "-y",
        "48",
        launch,
        check=False,
        timeout=20,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip())
    startup = _wait_for_prompt(session, timeout=20.0)
    if not _session_exists(session):
        raise RuntimeError(
            f"koder session exited before prompt for {scenario.suite}/{scenario.name}"
        )
    if "| ⚡ Koder |" not in startup or "│>" not in startup:
        raise RuntimeError(f"koder prompt did not appear for {scenario.suite}/{scenario.name}")
    return session


def _capture(session: str) -> str:
    return _tmux("capture-pane", "-p", "-S", "-500", "-t", session, timeout=10).stdout


def _capture_visible(session: str) -> str:
    return _tmux("capture-pane", "-p", "-t", session, timeout=10).stdout


def _capture_for_turn(session: str, turn: dict[str, Any]) -> str:
    if turn.get("capture") == "visible":
        return _capture_visible(session)
    return _capture(session)


def _list_panes(session: str) -> list[str]:
    result = _tmux("list-panes", "-t", session, "-F", "#{pane_id}", timeout=10)
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _capture_pane(pane_id: str) -> str:
    return _tmux("capture-pane", "-p", "-S", "-300", "-t", pane_id, timeout=10).stdout


def _capture_all_panes(session: str) -> dict[str, str]:
    return {pane_id: _capture_pane(pane_id) for pane_id in _list_panes(session)}


def _kill_tmux_pane_matching(session: str, marker: str) -> str | None:
    panes = _list_panes(session)
    pane_outputs = _capture_all_panes(session)
    for pane_id in panes[1:]:
        if marker in pane_outputs.get(pane_id, ""):
            result = _tmux("kill-pane", "-t", pane_id, timeout=10)
            if result.returncode != 0:
                return result.stderr.strip() or f"failed to kill {pane_id}"
            return None
    return f"no non-leader pane matched {marker!r}"


def _wait_for_prompt(session: str, timeout: float) -> str:
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        if not _session_exists(session):
            return last
        last = _capture(session)
        if "| ⚡ Koder |" in last and "│>" in last:
            return last
        time.sleep(0.5)
    return last


def _send(session: str, text: str) -> None:
    _tmux("send-keys", "-t", session, "-l", text, timeout=10)
    time.sleep(0.3)
    _tmux("send-keys", "-t", session, "Enter", timeout=10)


def _type_text(session: str, text: str) -> None:
    _tmux("send-keys", "-t", session, "-l", text, timeout=10)
    time.sleep(0.3)


def _send_key_sequence(session: str, keys: list[str]) -> None:
    for key in keys:
        _tmux("send-keys", "-t", session, key, timeout=10)
        time.sleep(0.2)


def _resize_window(session: str, *, width: int, height: int) -> None:
    _tmux("resize-window", "-t", session, "-x", str(width), "-y", str(height), timeout=10)
    time.sleep(0.6)


def _session_exists(session: str) -> bool:
    return _tmux("has-session", "-t", session, timeout=5).returncode == 0


def _tmux_pane_assertions_pass(session: str, turn: dict[str, Any]) -> tuple[bool, dict[str, str]]:
    expected_min = turn.get("expect_tmux_panes_min")
    expect_any = turn.get("expect_tmux_any_pane_any", [])
    expect_all = turn.get("expect_tmux_any_pane_all", [])
    if expected_min is None and not expect_any and not expect_all:
        return True, {}

    pane_outputs = _capture_all_panes(session)
    if expected_min is not None and len(pane_outputs) < int(expected_min):
        return False, pane_outputs
    if expect_any and not any(
        any(item in output for item in expect_any) for output in pane_outputs.values()
    ):
        return False, pane_outputs
    if expect_all and not any(
        all(item in output for item in expect_all) for output in pane_outputs.values()
    ):
        return False, pane_outputs
    return True, pane_outputs


def _wait_for_assertions(session: str, turn: dict[str, Any], timeout: float) -> tuple[bool, str]:
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        exists = _session_exists(session)
        if turn.get("expect_session_dead"):
            if not exists:
                return True, last
        elif exists:
            last = _capture_for_turn(session, turn)
            expect_all = turn.get("expect_all", [])
            expect_any = turn.get("expect_any", [])
            expect_regex = turn.get("expect_regex", [])
            expect_not = turn.get("expect_not", [])
            all_ok = all(item in last for item in expect_all)
            any_ok = True if not expect_any else any(item in last for item in expect_any)
            regex_ok = all(re.search(pattern, last) for pattern in expect_regex)
            not_ok = all(item not in last for item in expect_not)
            pane_ok, _pane_outputs = _tmux_pane_assertions_pass(session, turn)
            if all_ok and any_ok and regex_ok and not_ok and pane_ok:
                return True, last
        time.sleep(0.5)
    if _session_exists(session):
        last = _capture_for_turn(session, turn)
    return False, last


def run_scenario(scenario: ScenarioRef, *, output_dir: Path) -> bool:
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="koder-scenario-") as tmp:
        home, repo = _prepare_workspace(Path(tmp))
        _write_prelaunch_files(scenario, home=home, repo=repo)
        fake_openai_proc: subprocess.Popen | None = None
        session: str | None = None
        ok = True
        try:
            fake_openai_proc = _start_fake_openai(scenario, home=home, repo=repo)
            session = _launch_session(home, repo, scenario)
            for index, turn in enumerate(scenario.payload["turns"], start=1):
                if turn.get("send"):
                    _send(session, turn["send"])
                if turn.get("type"):
                    _type_text(session, turn["type"])
                if turn.get("keys"):
                    _send_key_sequence(session, turn["keys"])
                if turn.get("resize"):
                    resize = turn["resize"]
                    _resize_window(
                        session,
                        width=int(resize["width"]),
                        height=int(resize["height"]),
                    )
                if turn.get("kill_tmux_pane_matching"):
                    kill_error = _kill_tmux_pane_matching(session, turn["kill_tmux_pane_matching"])
                    if kill_error:
                        print(
                            f"FAIL {scenario.suite}/{scenario.name} turn {index}: {kill_error}",
                            file=sys.stderr,
                        )
                        ok = False
                        break
                if turn.get("wait"):
                    time.sleep(float(turn["wait"]))
                passed, output = _wait_for_assertions(
                    session, turn, float(turn.get("timeout", 12.0))
                )
                capture = output_dir / f"{scenario.suite}-{scenario.name}-turn-{index}.txt"
                capture.write_text(output, encoding="utf-8")
                if any(
                    key in turn
                    for key in (
                        "expect_tmux_panes_min",
                        "expect_tmux_any_pane_any",
                        "expect_tmux_any_pane_all",
                    )
                ):
                    pane_capture = (
                        output_dir / f"{scenario.suite}-{scenario.name}-turn-{index}-panes.txt"
                    )
                    pane_capture.write_text(
                        "\n\n".join(
                            f"## {pane_id}\n{content}"
                            for pane_id, content in _capture_all_panes(session).items()
                        ),
                        encoding="utf-8",
                    )
                if not passed:
                    print(
                        f"FAIL {scenario.suite}/{scenario.name} turn {index}: {turn}",
                        file=sys.stderr,
                    )
                    ok = False
                    break
                time.sleep(0.8)
            if ok:
                post_failures = _run_post_assertions(scenario, home=home, repo=repo)
                if post_failures:
                    post_capture = output_dir / f"{scenario.suite}-{scenario.name}-post.txt"
                    post_capture.write_text("\n".join(post_failures), encoding="utf-8")
                    for failure in post_failures:
                        print(f"FAIL {scenario.suite}/{scenario.name}: {failure}", file=sys.stderr)
                    ok = False
        finally:
            if session is not None:
                _tmux("kill-session", "-t", session, timeout=5)
            _stop_fake_openai(fake_openai_proc)
        return ok


def select_scenarios(
    manifest: dict[str, Any], selectors: list[str], run_all: bool
) -> list[ScenarioRef]:
    refs = _scenario_refs(manifest)
    if run_all:
        return refs
    if not selectors:
        return []
    selected: list[ScenarioRef] = []
    for selector in selectors:
        for ref in refs:
            if selector in {ref.name, f"{ref.suite}/{ref.name}"}:
                selected.append(ref)
    return selected


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate or run tmux feature scenarios")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--check", action="store_true", help="Validate manifest coverage")
    parser.add_argument(
        "--strict-acceptance",
        action="store_true",
        help="Require acceptance scenarios to include acceptance metadata and stronger evidence",
    )
    parser.add_argument("--list", action="store_true", help="List scenario names")
    parser.add_argument(
        "--run", action="append", default=[], help="Run a scenario by name or suite/name"
    )
    parser.add_argument("--run-all", action="store_true", help="Run every scenario")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(tempfile.gettempdir()) / "koder-feature-scenarios",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest = _load_manifest(args.manifest)
    errors = validate_manifest(manifest, strict_acceptance=args.strict_acceptance)
    if args.check or not (args.list or args.run or args.run_all):
        if errors:
            for error in errors:
                print(error, file=sys.stderr)
            return 1
        print("scenario manifest covers all runtime slash commands and required feature suites")
        if args.check and not (args.list or args.run or args.run_all):
            return 0
    if args.list:
        for ref in _scenario_refs(manifest):
            print(
                f"{ref.suite}/{ref.name} [{_validation_level(ref.payload)}]: "
                f"{ref.payload.get('purpose', '')}"
            )
    selected = select_scenarios(manifest, args.run, args.run_all)
    if args.run or args.run_all:
        if errors:
            print("manifest validation failed; refusing to run scenarios", file=sys.stderr)
            return 1
        if not selected:
            print("no scenarios selected", file=sys.stderr)
            return 1
        if shutil.which("tmux") is None:
            print("tmux is not available", file=sys.stderr)
            return 2
        all_ok = True
        for scenario in selected:
            print(f"RUN {scenario.suite}/{scenario.name}")
            all_ok = run_scenario(scenario, output_dir=args.output_dir) and all_ok
        return 0 if all_ok else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
