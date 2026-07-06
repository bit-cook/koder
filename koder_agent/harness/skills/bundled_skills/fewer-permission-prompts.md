---
name: fewer-permission-prompts
description: Scan session history for frequently used read-only run_shell commands, then propose a prioritized permissions.allow list for .koder/settings.json to reduce approval prompts. Use when asked to reduce permission prompts, stop asking for approval on safe commands, or tune the permission allowlist.
---

Scan the user's session history for tool calls, build a prioritized list of
read-only permission rules, get the user's confirmation, then merge them into
`.koder/settings.json` under `permissions.allow`. $ARGUMENTS

The rule format is `tool_name(content)`, e.g. `run_shell(git status *)` or an
exact `run_shell(uv run pytest --collect-only)`. A trailing ` *` (with the
space) matches both the bare command and any arguments.

## Steps

1. **Extract command frequencies from the session DB.** History lives in
   SQLite at `~/.koder/koder.db`, table `agent_messages(session_id,
   message_data, created_at)`; `message_data` is JSON. Tool calls have
   `{"type": "function_call", "name": "<tool>", "arguments": "<json-string>"}`,
   and for `run_shell` the arguments JSON contains a `"command"` field. Run a
   read-only query via run_shell, for example:

   ```bash
   python3 -c "
   import json, re, sqlite3, collections, os
   db = os.path.expanduser('~/.koder/koder.db')
   conn = sqlite3.connect(f'file:{db}?mode=ro', uri=True)
   rows = conn.execute(\"SELECT message_data FROM agent_messages WHERE message_data LIKE '%function_call%'\")
   shell = collections.Counter()
   WRAPPERS = {'sudo','timeout','env','nohup','nice','command'}
   for (raw,) in rows:
       try: m = json.loads(raw)
       except Exception: continue
       if m.get('type') != 'function_call' or m.get('name') != 'run_shell': continue
       try: cmd = json.loads(m.get('arguments') or '{}').get('command','')
       except Exception: continue
       for part in re.split(r'&&|\|\||;|\|', cmd):
           toks = part.strip().split()
           while toks and (toks[0] in WRAPPERS or '=' in toks[0]): toks.pop(0)
           if toks:
               sub = toks[1] if len(toks) > 1 and re.fullmatch(r'[a-z][a-z0-9-]*', toks[1]) else ''
               shell[(toks[0] + (' ' + sub if sub else ''))] += 1
   for k, v in shell.most_common(30): print(v, k)
   "
   ```

   Note the token handling: strip wrapper prefixes (`sudo`, `timeout`,
   `env VAR=x ...`), split compound commands on `&&`, `;`, `|`, and count each
   part's leading command + first subcommand. Discard junk tokens that are
   obviously heredoc or script content rather than commands.

2. **Filter to READ-ONLY commands only.** Allow-candidates: `git status`,
   `git log`, `git diff`, `git show`, `git branch`, `ls`, `rg`, `grep`,
   `find`, `wc`, `which`, `file`, `head`, `tail`, `cat`, `stat`, `tree`,
   `du`, `df`, `ps`, `npm ls`, `uv run pytest --collect-only`, and similar
   pure-inspection commands. Disqualifiers: anything that writes, deletes,
   moves, installs, deploys, or sends network traffic (`rm`, `mv`, `curl`,
   `git push`, `npm install`, `pip install`, `docker run`, ...). Test runs
   that execute project code are not read-only. **When in doubt, leave it
   out.**

3. **HARD SECURITY RULE — never allowlist arbitrary code execution.** A rule
   that lets any code run unattended defeats the permission system entirely.
   Never propose:
   - Interpreters: `python`, `python3`, `node`, `ruby`, `perl`, `sh`,
     `bash -c`, `zsh -c`, `eval`, `exec`
   - Unpinned wildcards: `run_shell(*)`, `run_shell(git *)`, or any pattern
     whose wildcard spans the subcommand
   - Package runners with wildcards: `npx *`, `uvx *`, `uv run *`
   - Commands that evaluate their arguments: `xargs sh -c`, `find -exec`,
     `awk` with `system()`, `ssh <host> <cmd>`
   An entry must be at least as narrow as `run_shell(git status *)` — command
   plus subcommand pinned — or an exact full command with no wildcard.

4. **Stay inside the rule engine's scope.** Permission rules only match tools
   whose call carries a matchable target: `run_shell`/`run_powershell`
   commands, file tools (`file_path`/`path` argument), skill invocations, and
   tools with a `uri`/`url` argument. Do NOT propose rules for MCP or
   extension tools: they carry no matchable target, so an entry like
   `some_mcp_tool(*)` (or a bare tool name, which the parser silently drops)
   would sit inert in the settings file and never suppress a prompt. If the
   user asks about MCP tool prompts, explain this limitation instead of
   writing a rule.

5. **Rank and present BEFORE writing anything.** Sort by count descending,
   drop entries seen fewer than ~3 times, cap at ~20. Show the user a table:

   | # | Rule | Count | Why safe |
   |---|------|-------|----------|
   | 1 | `run_shell(git log *)` | 42 | reads commit history only |
   | 2 | `run_shell(rg *)` | 31 | text search, no writes |

   Do not modify any file until the user confirms (they may also trim the
   list or choose a different scope).

6. **On confirmation, merge into `.koder/settings.json`.** Read the file
   first with read_file; if it does not exist, create it with only the
   `permissions.allow` key. Merge, never replace: preserve every existing
   key and every existing entry in `permissions.allow`, de-duplicate against
   what is already there, and never remove or reorder unrelated fields. If
   the user prefers not to commit the rules to the repo, use
   `.koder/settings.local.json` (gitignored) instead; it is loaded with the
   same priority hierarchy. Verify whichever file you wrote is valid JSON
   (`python3 -c "import json; json.load(open('.koder/settings.json'))"`).

7. **Report and remind.** Summarize what was added, what was already present,
   and what you skipped and why. Remind the user that `permissions.deny`
   rules at any scope (user `~/.koder/settings.json`, project, or local)
   always beat allow rules, and that a restart or new session may be needed
   for the updated permissions to take effect.

Do not add anything to `permissions.deny`. Do not touch any other settings
key. Do not modify the session database — all queries must be read-only.
