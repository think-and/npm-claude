Sync Claude Code session transcripts to Bryonics team knowledge base.

Run `python3 ~/.bryonics/current/lib/session_sync.py $ARGUMENTS` and display the results.

Modes:
- `/sync` — sync current project only (default)
- `/sync all` — sync all projects
- `/sync set-default <mode>` — persist default mode (`all` or `folder`)

Extracts structured memories from your Claude Code sessions:
- Key prompts (what you asked for)
- Decisions and reasoning
- Errors encountered
- Files changed summary

Max 10 memories per session. Incremental — only syncs new content since last run.
