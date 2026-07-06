---
name: loop
description: Run a prompt or slash command on a recurring schedule via the /loop runtime command. Use when the user wants a recurring task, a periodic check, or a scheduled prompt.
argument_hint: "[interval] <prompt>"
disable_model_invocation: true
---

Recurring scheduled prompts are managed by the `/loop` runtime command, not by this skill.
Direct the user to run `/loop` directly. Supported forms:

- `/loop @every 5m <prompt>` - run a prompt every 5 minutes
- `/loop 5m <prompt>` - same interval form without the `@every` word
- `/loop @every:300 <prompt>` - seconds form, only when it aligns to whole minutes
- `/loop 0 9 * * * <prompt>` - run on a 5-field cron schedule (local time)
- `/loop once 30 14 * * 1 <prompt>` - schedule a one-shot run
- `/loop list` or `/loop ls` - list scheduled loop jobs
- `/loop delete <id>` - delete a job (`remove` and `rm` are aliases)

Jobs are stored in `~/.koder/scheduled_tasks.json` by default and fire while Koder is
running. `/loop` stores schedules as 5-field cron expressions, so it rejects `@after-turn`,
sub-minute intervals, and intervals that cannot be represented as 5-field cron (for example
`45m`). If the user described a schedule in $ARGUMENTS, translate it into the matching
`/loop` invocation above and show them the exact command to run.
