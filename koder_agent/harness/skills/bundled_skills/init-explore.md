---
name: init-explore
description: Analyze the codebase and generate or improve an AGENTS.md project guide by exploring build files, CI, and architecture. Use when asked to initialize project docs for AI agents, create AGENTS.md from the actual codebase, or refresh a stale AGENTS.md.
argument_hint: "[extra focus]"
disable_model_invocation: true
---

Analyze this codebase and create an AGENTS.md file, which will be given to future Koder
instances operating in this repository. Extra focus requested by the user: $ARGUMENTS
(if empty, ignore).

What to add — exactly two kinds of content:

1. **Commonly used commands**: how to build, lint, and run tests — and especially how to
   run a SINGLE test. Discover every command from real files (package.json,
   pyproject.toml, Makefile, CI workflow configs, README); do not guess or invent
   commands, and only ship ones you found evidence for.
2. **Big-picture architecture**: the structure that requires reading multiple files to
   understand — how the major pieces connect, where control flows, which modules own
   which responsibilities. Skip anything visible from a single file.

Usage notes:
- If AGENTS.md already exists, improve it against the actual codebase instead of
  regenerating it: fix stale commands, prune dead sections, add what is missing.
- If other AI-agent config files exist (.cursorrules, .cursor/rules/,
  .github/copilot-instructions.md, CLAUDE.md), read them and keep the important
  project facts. If there is a README.md, include the important parts.
- Do not repeat what is obvious from the directory listing, and do not list every file
  or component.
- Do not include generic advice ("write tests", "handle errors gracefully", "never
  commit secrets").
- Do not make up sections ("Common Development Tasks", "Tips for Development") unless
  the content comes from files you actually read.
- Keep it short: AGENTS.md is read on every session, so every line costs context.
- Prefix the file with:

  ```
  # AGENTS.md

  This file provides guidance to Koder and other AI agents when working with this repository.
  ```

When done, show the user a brief summary of what you wrote or changed and why, naming
the source file each command came from.
