# think&

Team Knowledge Layer for Claude Code.

think& captures what your team builds, surfaces it when relevant, and quizzes you on what shipped — automatically, across Claude Code sessions.

## Install

```bash
npx @thinkand/claude@latest install
```

You'll be asked to **create a team** or **join an existing one**:

```
think& — Team Knowledge Layer for Claude Code
==============================================

  1. Create a new team
  2. Join an existing team

> 1

  Team name: my-team
  Your name: alice

  ✓ Team "my-team" created
  ✓ Invite key: tk_invite_abc123...

  Share this invite key with teammates.
```

To add a teammate:

```bash
# You (admin):
/team-invite
# → gives you: tk_invite_xyz789...

# Teammate:
npx @thinkand/claude@latest install
# → Choose "Join a team" → paste the invite key
```

Restart Claude Code after install.

## What it does

### Proactive Recall

Every prompt you type triggers a team knowledge search. If a teammate already solved something relevant, it surfaces automatically — before you start working.

### Comprehension Quizzes

Quiz yourself on any PR or local branch diff:

```
/quiz hammadtq/openclaw#1       → quiz on a GitHub PR
/quiz --branch                   → quiz on your current branch vs main
/quiz-submit 1:B 2:A 3:C 4:D 5:B
```

Questions focus on architecture, trade-offs, and failure modes — not function names or syntax.

### Session Sync

Extract structured memories from your Claude Code transcripts:

```
/sync
```

Pulls out key prompts, decisions, errors, and file change summaries. Max 10 memories per session, incremental.

### Team Commands

```
/team                → what your team has been working on
/ask-team auth       → search team knowledge for "auth"
/week-team           → weekly team summary
/team-invite         → generate invite key (admin only)
```

## CLI Commands

```bash
npx @thinkand/claude@latest install      # create/join team, install hooks
npx @thinkand/claude@latest update       # update to latest version
npx @thinkand/claude@latest doctor       # check installation health
npx @thinkand/claude@latest uninstall    # clean removal
npx @thinkand/claude@latest pin <ver>    # rollback to specific version
```

## How it works

- **Capture hook** runs after every Edit/Write/Bash in Claude Code — records what you did
- **Recall hook** runs before every prompt — searches team knowledge and injects relevant context
- **Server** stores memories in a brain-inspired retrieval engine (Hopfield networks + BM25 + entity matching)
- **Context boost** — results from your current project/branch/files rank higher
- **Org auth** — per-user API keys, one-time invite keys, no email or OAuth

## What gets installed

```
~/.bryonics/
├── current → releases/0.4.0/    # stable symlink, atomically swapped on update
├── releases/0.4.0/
│   ├── hooks/                    # recall.py, capture.py
│   ├── lib/                      # client library + helpers
│   └── commands/                 # slash command definitions
├── config.json                   # your API key + team (persists across updates)
└── sessions/                     # per-project state (persists)

~/.claude/
├── settings.json                 # hooks auto-merged (backed up before changes)
└── commands/
    ├── quiz.md → current release  # individual symlinks
    ├── sync.md → ...
    └── team.md → ...
```

Updates are a symlink swap — no file copying, no manual reconfiguration.

## Requirements

- Claude Code 2.0+
- Python 3.8+
- Node.js 18+ (for installer only — runtime is Python)

## Privacy

- Sessions and memories are synced to your team's server
- API keys stored in `~/.bryonics/config.json`
- No telemetry, no third-party data sharing
