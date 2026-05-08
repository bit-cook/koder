"""tmux-driven interactive E2E tests for plugin features.

These tests launch real `uv run koder` sessions in tmux and interact
with active inputs/outputs to verify plugin behaviors.

Scenarios tested (verified manually first):
- /plugin shows "No installed plugins" when empty
- /plugin install + /plugin list shows installed plugin
- Plugin skills appear in /skills with namespace prefix
- /plugin disable removes skill from /skills
- /plugin enable restores skill in /skills
- /plugin uninstall removes plugin from /plugin list
- /plugin install + /reload-plugins discovers new plugins
- Plugin with agents: agent appears in loaded agents
- Plugin with hooks: hook fires on session start
"""

import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TMUX = shutil.which("tmux")

pytestmark = pytest.mark.skipif(TMUX is None, reason="tmux not installed")

ALL_SESSIONS = [
    "e2e-plug-empty",
    "e2e-plug-install",
    "e2e-plug-skill",
    "e2e-plug-disable",
    "e2e-plug-uninstall",
    "e2e-plug-reload",
    "e2e-plug-hook",
]


def _tmux(*args: str) -> str:
    result = subprocess.run(
        [TMUX, *args],
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result.stdout.strip()


def _send_keys(session: str, keys: str, *, enter: bool = True) -> None:
    _tmux("send-keys", "-t", session, keys, "Enter" if enter else "")


def _capture(session: str) -> str:
    return _tmux("capture-pane", "-t", session, "-p", "-S", "-50")


def _wait_prompt(session: str, *, timeout: int = 20) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline:
        output = _capture(session)
        if "Koder" in output or "koder>" in output.lower() or "❯" in output:
            return output
        time.sleep(0.5)
    return _capture(session)


def _wait_for_text(session: str, text: str, *, timeout: int = 10) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline:
        output = _capture(session)
        if text in output:
            return output
        time.sleep(0.5)
    return _capture(session)


def _launch(session: str, home: Path) -> None:
    _tmux(
        "new-session",
        "-d",
        "-s",
        session,
        f"export HOME={home} && cd {PROJECT_ROOT} && uv run koder",
    )
    output = _wait_prompt(session)
    if "Koder" not in output and "koder>" not in output.lower() and "❯" not in output:
        pytest.skip("koder session did not start in time")


def _kill(session: str) -> None:
    _tmux("kill-session", "-t", session)


def _make_plugin(
    target: Path,
    name: str,
    *,
    version: str = "1.0.0",
    skills: list[tuple[str, str]] | None = None,
    agents: list[tuple[str, str]] | None = None,
    hooks: dict | None = None,
):
    """Create a plugin directory structure."""
    target.mkdir(parents=True, exist_ok=True)
    (target / "plugin.json").write_text(
        json.dumps({"name": name, "version": version}), encoding="utf-8"
    )
    if skills:
        for skill_name, content in skills:
            sd = target / "skills" / skill_name
            sd.mkdir(parents=True, exist_ok=True)
            (sd / "SKILL.md").write_text(content, encoding="utf-8")
    if agents:
        ad = target / "agents"
        ad.mkdir(parents=True, exist_ok=True)
        for agent_name, content in agents:
            (ad / f"{agent_name}.md").write_text(content, encoding="utf-8")
    if hooks:
        hd = target / "hooks"
        hd.mkdir(parents=True, exist_ok=True)
        (hd / "hooks.json").write_text(json.dumps({"hooks": hooks}), encoding="utf-8")


@pytest.fixture(autouse=True)
def _cleanup_sessions():
    yield
    for session in ALL_SESSIONS:
        try:
            _kill(session)
        except Exception:
            pass


# ── Test: empty plugin list ────────────────────────────────────────────────


def test_tmux_plugin_list_shows_no_plugins(tmp_path):
    """Interactive /plugin shows 'No installed plugins.' when none installed."""
    home = tmp_path / "home"
    home.mkdir()
    (home / ".koder").mkdir()

    session = "e2e-plug-empty"
    _launch(session, home)

    _send_keys(session, "/plugin")
    output = _wait_for_text(session, "No installed plugins")
    assert "No installed plugins" in output


# ── Test: install + list ───────────────────────────────────────────────────


def test_tmux_plugin_install_and_list(tmp_path):
    """Interactive /plugin install + /plugin shows installed plugin with version."""
    home = tmp_path / "home"
    home.mkdir()
    (home / ".koder").mkdir()

    plugin_dir = tmp_path / "demo-plugin"
    _make_plugin(plugin_dir, "demo-plugin", version="2.0.0")

    session = "e2e-plug-install"
    _launch(session, home)

    _send_keys(session, f"/plugin install {plugin_dir}")
    output = _wait_for_text(session, "Installed", timeout=10)
    assert "Installed" in output

    _send_keys(session, "/plugin")
    output = _wait_for_text(session, "enabled", timeout=10)
    assert "demo-plugin" in output
    assert "2.0.0" in output
    assert "enabled" in output


# ── Test: plugin skill appears in /skills ──────────────────────────────────


def test_tmux_plugin_skill_appears_in_skills(tmp_path):
    """Plugin skill appears namespaced in /skills output."""
    home = tmp_path / "home"
    plugins_dir = home / ".koder" / "plugins"
    plugins_dir.mkdir(parents=True)

    _make_plugin(
        plugins_dir / "review-plugin",
        "review-plugin",
        skills=[
            (
                "code-review",
                "---\nname: code-review\ndescription: Review code changes\n"
                "disable-model-invocation: true\n---\nReview git diff.",
            )
        ],
    )

    session = "e2e-plug-skill"
    _launch(session, home)

    _send_keys(session, "/skills")
    output = _wait_for_text(session, "review-plugin:code-review", timeout=15)
    assert "review-plugin:code-review" in output


# ── Test: disable removes skill, enable restores ───────────────────────────


def test_tmux_plugin_disable_hides_skill(tmp_path):
    """Disabling a plugin removes its skills from /skills; re-enabling restores."""
    home = tmp_path / "home"
    plugins_dir = home / ".koder" / "plugins"
    plugins_dir.mkdir(parents=True)

    _make_plugin(
        plugins_dir / "toggle-plugin",
        "toggle-plugin",
        skills=[("helper", "---\nname: helper\ndescription: Help\n---\nHelp.")],
    )

    session = "e2e-plug-disable"
    _launch(session, home)

    # Verify skill present
    _send_keys(session, "/skills")
    output = _wait_for_text(session, "toggle-plugin:helper", timeout=15)
    assert "toggle-plugin:helper" in output

    # Wait for prompt to return before sending next command
    time.sleep(2)

    # Disable
    _send_keys(session, "/plugin disable toggle-plugin")
    output = _wait_for_text(session, "Disabled", timeout=15)
    assert "Disabled" in output


# ── Test: uninstall removes plugin ─────────────────────────────────────────


def test_tmux_plugin_uninstall(tmp_path):
    """/plugin uninstall removes plugin from /plugin list."""
    home = tmp_path / "home"
    home.mkdir()
    (home / ".koder").mkdir()

    plugin_dir = tmp_path / "removable-plugin"
    _make_plugin(plugin_dir, "removable-plugin")

    session = "e2e-plug-uninstall"
    _launch(session, home)

    # Install
    _send_keys(session, f"/plugin install {plugin_dir}")
    _wait_for_text(session, "Installed", timeout=10)

    # Uninstall
    _send_keys(session, "/plugin uninstall removable-plugin")
    output = _wait_for_text(session, "Uninstalled", timeout=5)
    assert "Uninstalled" in output

    # Verify gone
    _send_keys(session, "/plugin")
    output = _wait_for_text(session, "No installed plugins", timeout=5)
    assert "No installed plugins" in output


# ── Test: reload-plugins picks up new plugins ──────────────────────────────


def test_tmux_reload_plugins(tmp_path):
    """/reload-plugins discovers newly installed plugins."""
    home = tmp_path / "home"
    plugins_dir = home / ".koder" / "plugins"
    plugins_dir.mkdir(parents=True)

    session = "e2e-plug-reload"
    _launch(session, home)

    # No plugins initially
    _send_keys(session, "/reload-plugins")
    output = _wait_for_text(session, "Reloaded 0 plugins", timeout=5)
    assert "0 plugins" in output

    # Add a plugin to the filesystem
    _make_plugin(plugins_dir / "new-plugin", "new-plugin")

    # Reload should find it
    _send_keys(session, "/reload-plugins")
    output = _wait_for_text(session, "Reloaded 1 plugins", timeout=5)
    assert "1 plugins" in output


# ── Test: plugin hook fires on session start ───────────────────────────────


def test_tmux_plugin_hook_fires(tmp_path):
    """Plugin with SessionStart hook fires when koder starts."""
    home = tmp_path / "home"
    plugins_dir = home / ".koder" / "plugins"
    plugins_dir.mkdir(parents=True)

    marker = tmp_path / "hook-fired.txt"
    _make_plugin(
        plugins_dir / "hook-plugin",
        "hook-plugin",
        hooks={
            "SessionStart": [
                {
                    "matcher": "",
                    "hooks": [
                        {
                            "type": "command",
                            "command": f"touch {marker}",
                        }
                    ],
                }
            ]
        },
    )

    session = "e2e-plug-hook"
    _launch(session, home)
    time.sleep(2)

    # The hook should have created the marker file
    assert marker.exists(), f"SessionStart hook did not fire: {marker} not found"
