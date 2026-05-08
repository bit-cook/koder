# Skills, Plugins, And MCP

Koder can be extended with skills, plugins, MCP servers, session channels, and Magic Docs. Use the lightest extension that solves the problem.

## Skills

Skills are local instruction bundles loaded with progressive disclosure. At startup Koder loads skill names and descriptions; full content is loaded only when the skill is invoked or selected by the agent.

Skill search order:

1. `.koder/skills/` in the current project
2. `~/.koder/skills/`
3. Installed plugins
4. Bundled runtime skills

Create a project skill:

```text
.koder/skills/api-review/SKILL.md
```

```markdown
---
name: api-review
description: Review API changes for compatibility and error handling
allowed_tools:
  - read_file
  - grep_search
---

Review public API changes for request shape, response shape, status codes, and migration notes.
```

Inspect available skills:

```bash
/skills
```

Invoke a manual skill by its command name when the skill exposes one, or ask Koder to use it in plain language.

## Verifier Skills

Use `/init-verifiers` to create project-local verifier skills:

```bash
/init-verifiers cli
/init-verifiers web
/init-verifiers api
```

Verifier skills are useful when a project has a repeatable acceptance workflow that should be explained in one place and loaded on demand.

## Plugins

Plugins can contribute skills, commands, MCP servers, channels, and dependencies.

```bash
koder plugin install ./my-plugin --scope project
koder plugin list
koder plugin enable my-plugin
koder plugin disable my-plugin
koder plugin validate ./my-plugin
koder plugin marketplace list
```

Inside the TUI:

```bash
/plugin
/reload-plugins
/skills
```

Use `--plugin-dir` for a session-only plugin directory while developing a plugin locally.

## MCP Servers

MCP servers add external tools to the Koder runtime.

```bash
koder mcp add filesystem "python -m mcp.server.filesystem" --scope project
koder mcp add api --transport http --url http://localhost:8000 --header "Authorization: Bearer token"
koder mcp list
koder mcp get filesystem
koder mcp remove filesystem --scope project
koder mcp reset-project-choices
koder mcp serve
```

MCP configuration can live in user, project, or local scopes depending on the command flags. Use project scope when the server is part of the repository workflow; use user scope for personal tools.

See [Configuration Guide](configuration.md) for the YAML config format.

## Channels

Channels are MCP or plugin-backed session integrations enabled at startup:

```bash
koder --channels server:my-channel
koder --channels plugin:team-chat@local
koder --dangerously-load-development-channels server:dev-channel
```

Inspect active channel entries:

```bash
/channels
/channels help
```

`/channels` is read-only. It reports active entries and the supported startup forms.

## Magic Docs

Magic Docs are markdown files whose first line is:

```markdown
# MAGIC DOC: Project Runtime Notes
```

An optional italic line directly after the header becomes refresh guidance. When Koder reads a Magic Doc through `read_file`, it tracks the file and refreshes a managed `## Koder Session Notes` section after completed turns.

Commands:

```bash
/magic-docs
/magic-docs refresh
```

Use Magic Docs for local project notes that should stay current across a Koder session. The refresh is local and deterministic; it preserves the header and guidance line and replaces only the managed section.
