from pathlib import Path
from types import SimpleNamespace

from koder_agent.harness.ide import (
    IDELauncher,
    collect_terminal_ide_hints,
    detect_ide_launchers,
    open_ide_target,
    render_ide_status,
)


def test_detect_ide_launchers_prefers_cli_launchers(tmp_path):
    def fake_which(command: str) -> str | None:
        if command in {"code", "cursor"}:
            return f"/usr/local/bin/{command}"
        return None

    launchers = detect_ide_launchers(
        which=fake_which,
        app_exists=lambda _path: False,
        platform="darwin",
        home=tmp_path,
    )

    assert [launcher.key for launcher in launchers[:2]] == ["vscode", "cursor"]
    assert launchers[0].mode == "cli"
    assert launchers[0].label == "code"


def test_detect_ide_launchers_falls_back_to_macos_apps(tmp_path):
    def fake_exists(path: Path) -> bool:
        return path == Path("/Applications") / "Visual Studio Code.app"

    launchers = detect_ide_launchers(
        which=lambda _command: None,
        app_exists=fake_exists,
        platform="darwin",
        home=tmp_path,
    )

    assert len(launchers) == 1
    assert launchers[0] == IDELauncher(
        key="vscode",
        name="Visual Studio Code",
        mode="mac-app",
        executable="Visual Studio Code",
        label="open -a Visual Studio Code",
    )


def test_collect_terminal_ide_hints_filters_to_known_values():
    hints = collect_terminal_ide_hints(
        {
            "TERM_PROGRAM": "vscode",
            "VSCODE_PID": "123",
            "UNRELATED": "ignored",
        }
    )

    assert hints == {"TERM_PROGRAM": "vscode", "VSCODE_PID": "123"}


def test_render_ide_status_includes_launchers_and_terminal_hints(tmp_path):
    output = render_ide_status(
        cwd=tmp_path,
        launchers=[
            IDELauncher(
                key="cursor",
                name="Cursor",
                mode="cli",
                executable="/bin/cursor",
                label="cursor",
            )
        ],
        environ={"TERM_PROGRAM": "Apple_Terminal"},
    )

    assert output.startswith("ide:\n")
    assert f"target: {tmp_path.resolve()}" in output
    assert "integration_scope: local launcher/status" in output
    assert "detected_launchers: 1" in output
    assert "- cursor: Cursor (cli, cursor)" in output
    assert "- TERM_PROGRAM: Apple_Terminal" in output


def test_open_ide_target_requires_launcher_when_one_is_available(tmp_path):
    output = open_ide_target(
        launcher_selector=None,
        target=tmp_path,
        launchers=[
            IDELauncher(
                key="vscode",
                name="Visual Studio Code",
                mode="cli",
                executable="/bin/code",
                label="code",
            )
        ],
    )

    assert output.startswith("ide: select a launcher")
    assert "usage: /ide open <launcher> [path]" in output


def test_open_ide_target_runs_selected_launcher(tmp_path):
    captured: dict[str, object] = {}

    def fake_runner(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    output = open_ide_target(
        launcher_selector="code",
        target=tmp_path,
        launchers=[
            IDELauncher(
                key="vscode",
                name="Visual Studio Code",
                mode="cli",
                executable="/bin/code",
                label="code",
            )
        ],
        runner=fake_runner,
    )

    assert captured["command"] == ["/bin/code", str(tmp_path.resolve())]
    assert captured["kwargs"] == {"capture_output": True, "text": True, "timeout": 10}
    assert "status: launched" in output
    assert "launcher: vscode" in output


def test_open_ide_target_reports_failed_launcher(tmp_path):
    def fake_runner(_command, **_kwargs):
        return SimpleNamespace(returncode=2, stdout="", stderr="bad launcher")

    output = open_ide_target(
        launcher_selector="cursor",
        target=tmp_path,
        launchers=[
            IDELauncher(
                key="cursor",
                name="Cursor",
                mode="cli",
                executable="/bin/cursor",
                label="cursor",
            )
        ],
        runner=fake_runner,
    )

    assert "status: failed" in output
    assert "error: bad launcher" in output
