"""Static shell-completion script generation for the `koder` CLI."""

from __future__ import annotations

SUPPORTED_SHELLS = ("bash", "zsh", "fish")

# Top-level subcommands offered for completion. Kept in sync with the argparse
# subparsers registered in ``koder_agent/cli.py``.
_SUBCOMMANDS = (
    "auth",
    "mcp",
    "config",
    "agents",
    "plugin",
    "plugins",
    "doctor",
    "review",
    "completion",
    "upgrade",
)

# Commonly used top-level flags.
_FLAGS = (
    "--help",
    "--version",
    "--session",
    "--continue",
    "--resume",
    "--print",
    "--output-format",
    "--system-prompt",
    "--append-system-prompt",
    "--bare",
    "--verbose",
    "--debug",
    "--no-stream",
    "--agent",
    "--agents",
)


_BASH_TEMPLATE = """# koder bash completion
_koder_completion() {{
    local cur prev words cword
    _init_completion 2>/dev/null || {{
        cur="${{COMP_WORDS[COMP_CWORD]}}"
        prev="${{COMP_WORDS[COMP_CWORD-1]}}"
    }}
    local subcommands="{subcommands}"
    local flags="{flags}"
    if [ "$COMP_CWORD" -eq 1 ]; then
        COMPREPLY=( $(compgen -W "${{subcommands}} ${{flags}}" -- "$cur") )
        return 0
    fi
    case "$prev" in
        completion)
            COMPREPLY=( $(compgen -W "bash zsh fish" -- "$cur") )
            return 0
            ;;
        auth)
            COMPREPLY=( $(compgen -W "login list revoke status" -- "$cur") )
            return 0
            ;;
        config)
            COMPREPLY=( $(compgen -W "show list path edit init set validate export import" -- "$cur") )
            return 0
            ;;
        mcp)
            COMPREPLY=( $(compgen -W "add add-json list get remove reset-project-choices serve" -- "$cur") )
            return 0
            ;;
        plugin|plugins)
            COMPREPLY=( $(compgen -W "list install uninstall enable disable validate marketplace" -- "$cur") )
            return 0
            ;;
    esac
    COMPREPLY=( $(compgen -W "${{flags}}" -- "$cur") )
    return 0
}}
complete -F _koder_completion koder
"""


_ZSH_TEMPLATE = """#compdef koder
# koder zsh completion
_koder() {{
    local -a subcommands flags
    subcommands=({subcommands})
    flags=({flags})
    if (( CURRENT == 2 )); then
        _describe 'command' subcommands
        _describe 'flag' flags
        return
    fi
    case "${{words[2]}}" in
        completion)
            compadd bash zsh fish
            ;;
        auth)
            compadd login list revoke status
            ;;
        config)
            compadd show list path edit init set validate export import
            ;;
        mcp)
            compadd add add-json list get remove reset-project-choices serve
            ;;
        plugin|plugins)
            compadd list install uninstall enable disable validate marketplace
            ;;
        *)
            _describe 'flag' flags
            ;;
    esac
}}
compdef _koder koder
"""


_FISH_TEMPLATE = """# koder fish completion
complete -c koder -f
# Top-level subcommands (only when no subcommand yet)
{subcommand_lines}
# Common flags
{flag_lines}
# Nested subcommands
complete -c koder -n '__fish_seen_subcommand_from completion' -a 'bash zsh fish'
complete -c koder -n '__fish_seen_subcommand_from auth' -a 'login list revoke status'
complete -c koder -n '__fish_seen_subcommand_from config' -a 'show list path edit init set validate export import'
complete -c koder -n '__fish_seen_subcommand_from mcp' -a 'add add-json list get remove reset-project-choices serve'
complete -c koder -n '__fish_seen_subcommand_from plugin plugins' -a 'list install uninstall enable disable validate marketplace'
"""


def render_completion_script(shell: str) -> str:
    """Return the static completion script for the given shell.

    Raises:
        ValueError: If ``shell`` is not one of the supported shells.
    """
    normalized = shell.strip().lower()
    if normalized not in SUPPORTED_SHELLS:
        raise ValueError(
            f"Unsupported shell '{shell}'. Supported shells: {', '.join(SUPPORTED_SHELLS)}."
        )

    if normalized == "bash":
        return _BASH_TEMPLATE.format(
            subcommands=" ".join(_SUBCOMMANDS),
            flags=" ".join(_FLAGS),
        )
    if normalized == "zsh":
        return _ZSH_TEMPLATE.format(
            subcommands=" ".join(_SUBCOMMANDS),
            flags=" ".join(_FLAGS),
        )
    # fish
    subcommand_lines = "\n".join(
        f"complete -c koder -n '__fish_use_subcommand' -a '{cmd}'" for cmd in _SUBCOMMANDS
    )
    flag_lines = "\n".join(f"complete -c koder -l '{flag.lstrip('-')}'" for flag in _FLAGS)
    return _FISH_TEMPLATE.format(
        subcommand_lines=subcommand_lines,
        flag_lines=flag_lines,
    )
