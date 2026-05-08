"""Local IDE launcher detection for the Koder harness."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any


@dataclass(frozen=True)
class IDEDefinition:
    key: str
    name: str
    commands: tuple[str, ...]
    mac_apps: tuple[str, ...] = ()


@dataclass(frozen=True)
class IDELauncher:
    key: str
    name: str
    mode: str
    executable: str
    label: str

    def command_for(self, target: Path) -> list[str]:
        if self.mode == "mac-app":
            return ["open", "-a", self.executable, str(target)]
        return [self.executable, str(target)]


IDE_DEFINITIONS: tuple[IDEDefinition, ...] = (
    IDEDefinition("vscode", "Visual Studio Code", ("code",), ("Visual Studio Code",)),
    IDEDefinition("cursor", "Cursor", ("cursor",), ("Cursor",)),
    IDEDefinition("windsurf", "Windsurf", ("windsurf",), ("Windsurf",)),
    IDEDefinition("zed", "Zed", ("zed",), ("Zed",)),
    IDEDefinition("sublime", "Sublime Text", ("subl",), ("Sublime Text",)),
    IDEDefinition("textmate", "TextMate", ("mate",), ("TextMate",)),
    IDEDefinition("bbedit", "BBEdit", ("bbedit",), ("BBEdit",)),
    IDEDefinition("intellij", "IntelliJ IDEA", ("idea",), ("IntelliJ IDEA",)),
    IDEDefinition("pycharm", "PyCharm", ("pycharm",), ("PyCharm",)),
    IDEDefinition("webstorm", "WebStorm", ("webstorm",), ("WebStorm",)),
    IDEDefinition("phpstorm", "PhpStorm", ("phpstorm",), ("PhpStorm",)),
    IDEDefinition("rubymine", "RubyMine", ("rubymine",), ("RubyMine",)),
    IDEDefinition("clion", "CLion", ("clion",), ("CLion",)),
    IDEDefinition("goland", "GoLand", ("goland",), ("GoLand",)),
    IDEDefinition("rider", "Rider", ("rider",), ("Rider",)),
    IDEDefinition("datagrip", "DataGrip", ("datagrip",), ("DataGrip",)),
    IDEDefinition("androidstudio", "Android Studio", ("android-studio",), ("Android Studio",)),
)

IDE_HINT_ENV_VARS: tuple[str, ...] = (
    "TERM_PROGRAM",
    "VSCODE_CWD",
    "VSCODE_PID",
    "VSCODE_IPC_HOOK_CLI",
    "CURSOR_TRACE_ID",
    "JETBRAINS_IDE",
    "INTELLIJ_ENVIRONMENT_READER",
)


def collect_terminal_ide_hints(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    env = environ or os.environ
    hints: dict[str, str] = {}
    for name in IDE_HINT_ENV_VARS:
        value = env.get(name)
        if value:
            hints[name] = value
    return hints


def _mac_app_paths(app_name: str, home: Path) -> tuple[Path, ...]:
    return (
        Path("/Applications") / f"{app_name}.app",
        home / "Applications" / f"{app_name}.app",
    )


def detect_ide_launchers(
    *,
    which: Callable[[str], str | None] = shutil.which,
    app_exists: Callable[[Path], bool] | None = None,
    platform: str = sys.platform,
    home: Path | None = None,
) -> list[IDELauncher]:
    """Return installed local IDE launchers in stable preference order."""
    exists = app_exists or (lambda path: path.exists())
    user_home = home or Path.home()
    launchers: list[IDELauncher] = []
    seen: set[str] = set()
    for definition in IDE_DEFINITIONS:
        for command in definition.commands:
            executable = which(command)
            if executable:
                launchers.append(
                    IDELauncher(
                        key=definition.key,
                        name=definition.name,
                        mode="cli",
                        executable=executable,
                        label=command,
                    )
                )
                seen.add(definition.key)
                break
        if definition.key in seen or platform != "darwin":
            continue
        for app_name in definition.mac_apps:
            if any(exists(path) for path in _mac_app_paths(app_name, user_home)):
                launchers.append(
                    IDELauncher(
                        key=definition.key,
                        name=definition.name,
                        mode="mac-app",
                        executable=app_name,
                        label=f"open -a {app_name}",
                    )
                )
                seen.add(definition.key)
                break
    return launchers


def _format_launcher_list(launchers: Sequence[IDELauncher]) -> list[str]:
    if not launchers:
        return ["launchers: none"]
    lines = ["launchers:"]
    for launcher in launchers:
        lines.append(f"- {launcher.key}: {launcher.name} ({launcher.mode}, {launcher.label})")
    return lines


def render_ide_status(
    *,
    cwd: Path | None = None,
    launchers: Sequence[IDELauncher] | None = None,
    environ: Mapping[str, str] | None = None,
) -> str:
    target = (cwd or Path.cwd()).resolve()
    detected = list(launchers) if launchers is not None else detect_ide_launchers()
    hints = collect_terminal_ide_hints(environ)
    lines = [
        "ide:",
        f"target: {target}",
        "integration_scope: local launcher/status",
        f"detected_launchers: {len(detected)}",
    ]
    lines.extend(_format_launcher_list(detected))
    if hints:
        lines.append("terminal_hints:")
        for name in sorted(hints):
            lines.append(f"- {name}: {hints[name]}")
    else:
        lines.append("terminal_hints: none")
    lines.append("open_command: /ide open <launcher> [path]")
    return "\n".join(lines)


def _match_launcher(launchers: Sequence[IDELauncher], selector: str) -> IDELauncher | None:
    normalized = selector.strip().lower()
    for launcher in launchers:
        if normalized in {
            launcher.key.lower(),
            launcher.name.lower(),
            launcher.label.lower(),
            Path(launcher.executable).name.lower(),
        }:
            return launcher
    return None


def open_ide_target(
    *,
    launcher_selector: str | None,
    target: Path | None = None,
    launchers: Sequence[IDELauncher] | None = None,
    runner: Callable[..., Any] = subprocess.run,
) -> str:
    resolved_target = (target or Path.cwd()).expanduser().resolve()
    detected = list(launchers) if launchers is not None else detect_ide_launchers()
    if not detected:
        return (
            "ide: no launchers detected\n"
            f"target: {resolved_target}\n"
            "open_manually: use your IDE's open-project command for the target path"
        )
    if not launcher_selector:
        lines = ["ide: select a launcher", f"target: {resolved_target}"]
        lines.extend(_format_launcher_list(detected))
        lines.append("usage: /ide open <launcher> [path]")
        return "\n".join(lines)

    launcher = _match_launcher(detected, launcher_selector)
    if launcher is None:
        lines = [f"ide: unknown launcher: {launcher_selector}", f"target: {resolved_target}"]
        lines.extend(_format_launcher_list(detected))
        return "\n".join(lines)

    command = launcher.command_for(resolved_target)
    try:
        completed = runner(command, capture_output=True, text=True, timeout=10)
    except FileNotFoundError:
        completed = SimpleNamespace(returncode=127, stdout="", stderr="launcher not found")
    except subprocess.TimeoutExpired:
        completed = SimpleNamespace(returncode=124, stdout="", stderr="launcher timed out")
    except Exception as exc:  # pragma: no cover - defensive shell boundary
        completed = SimpleNamespace(returncode=1, stdout="", stderr=str(exc))

    if completed.returncode == 0:
        return (
            "ide: open\n"
            "status: launched\n"
            f"launcher: {launcher.key}\n"
            f"name: {launcher.name}\n"
            f"target: {resolved_target}\n"
            f"command: {shlex.join(command)}"
        )

    stderr = (completed.stderr or completed.stdout or "unknown error").strip()
    return (
        "ide: open\n"
        "status: failed\n"
        f"launcher: {launcher.key}\n"
        f"target: {resolved_target}\n"
        f"command: {shlex.join(command)}\n"
        f"error: {stderr}"
    )
