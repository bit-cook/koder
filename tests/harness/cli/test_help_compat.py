import subprocess


def test_koder_help_preserves_legacy_argparse_output():
    proc = subprocess.run(
        ["uv", "run", "koder", "--help"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0
    assert "usage: koder" in proc.stdout
    assert "--session" in proc.stdout
    assert "--resume" in proc.stdout
    assert "--no-stream" in proc.stdout
    assert "--bare" in proc.stdout
    assert "--allowedTools" in proc.stdout


def test_koder_help_lists_available_subcommands():
    proc = subprocess.run(
        ["uv", "run", "koder", "--help"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0
    assert "Commands:" in proc.stdout
    assert "mcp" in proc.stdout
    assert "config" in proc.stdout
    assert "auth" in proc.stdout
    assert "agents" in proc.stdout
    assert "plugin" in proc.stdout


def test_koder_help_lists_common_subcommand_actions():
    proc = subprocess.run(
        ["uv", "run", "koder", "--help"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0
    assert "Use `koder <command> --help` for subcommand details." in proc.stdout
    assert "mcp <add|add-json|list|get|remove|reset-project-choices|serve>" in proc.stdout
    assert "config <show|list|path|edit|init|set|validate|export|import>" in proc.stdout
    assert "auth <login|list|revoke|status>" in proc.stdout
    assert "plugin <list|install|uninstall|enable|disable|validate|marketplace>" in proc.stdout
    assert "doctor [--json]" in proc.stdout
    assert "review [--base <ref>] [--uncommitted] [#PR]" in proc.stdout
    assert "completion <bash|zsh|fish>" in proc.stdout
