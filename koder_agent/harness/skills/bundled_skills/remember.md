---
name: remember
description: Save an important fact, preference, or correction to persistent memory in .koder/memory/ so future sessions can use it. Use when the user says "remember this", states a lasting preference, or corrects a mistake worth not repeating.
argument_hint: "<what to remember>"
---

Save the following to persistent memory: $ARGUMENTS
(if empty, ask what should be remembered before writing anything.)

## Memory contract

Memories live in `.koder/memory/` (project-scoped; use `~/.koder/memory/` only for facts
about the user that apply across all projects). One fact per markdown file, with YAML
frontmatter that Koder's retrieval layer parses:

```markdown
---
type: user | feedback | project | reference
description: one-line summary used for retrieval and the index
---
The memory body: the fact itself, plus context needed to apply it.
```

- **user** — lasting preferences of this user ("prefers pytest over unittest").
- **feedback** — a correction the user gave; record it so the mistake is not repeated.
- **project** — a durable fact about this project not written down anywhere in the repo.
- **reference** — external knowledge worth keeping (an API quirk, a doc link that mattered).

For **feedback** and **project** types, the body must include two lines: `Why:` (the reason
this is true or was corrected) and `Apply:` (how to act on it next time). A bare rule
without its rationale gets misapplied.

## Procedure

1. **Check existing memories first.** list_directory on `.koder/memory/` and read `MEMORY.md`
   if present; grep_search the directory for keywords from the new fact. If an existing
   memory covers the same topic, **update that file** with edit_file rather than creating a
   duplicate. If an existing memory is now wrong, delete it (`rm` via run_shell) and remove
   its index line — a wrong memory is worse than no memory.
2. **Filter: is this worth persisting?** Do NOT save what the repo already records:
   code structure (readable via glob_search/grep_search), git history (git_command),
   anything already stated in AGENTS.md, or transient session state ("the tests are
   currently failing"). If the fact belongs in AGENTS.md (team-visible project convention),
   suggest putting it there instead and stop.
3. **Write the file.** Kebab-case descriptive filename, e.g.
   `.koder/memory/prefers-uv-over-pip.md`, with the frontmatter above and a body of a few
   sentences at most. One fact per file — split compound requests into separate files.
4. **Update the index.** Append one line per new memory to `.koder/memory/MEMORY.md`
   (create it if missing), format: `- <description>: <relative path>`. When updating or
   deleting a memory, fix its index line too so the index never drifts from the files.
5. **Confirm.** Report the file path, type, and description, and one line on when this
   memory will change future behavior.

## Anti-patterns

- Duplicating: writing a second file about a topic an existing memory already covers.
- Hoarding: saving observations nobody asked to keep and that the repo already encodes.
- Vague descriptions ("notes about testing") — the description is the retrieval key;
  make it specific ("run integration tests with `make itest`, not pytest directly").
- Editing `MEMORY.md` without touching the memory files, or vice versa.
