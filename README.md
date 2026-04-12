# think&

Team Knowledge Layer for Claude Code.

think& captures what your team builds, surfaces it when relevant, and quizzes you on what shipped — automatically, across Claude Code sessions.

**npm:** [npmjs.com/package/@thinkand/claude](https://www.npmjs.com/package/@thinkand/claude)

## Install

```bash
npx @thinkand/claude@latest install
```

You'll be asked to **create an organization**, **join an existing one**, or set up a **personal profile** (team-of-one for your private work):

```
think& — Team Knowledge Layer for Claude Code
==============================================

  1. Create a new organization
  2. Join an existing organization
  3. Personal only (no team — just for you)

> 1

  Team name: my-team
  Your name: alice

  ✓ Team "my-team" created
  ✓ Invite key: tk_invite_abc123...

  Share this invite key with teammates.
```

> **⚠ After install, think& does nothing until you bind your project.**
> Choose a profile, then run `/profile use <slug>` inside each project you want synced. See **[Profiles](#profiles-choose-what-goes-where)** below. This opt-in per folder is the boundary that stops personal code from leaking into the team namespace.

To add a teammate:

```bash
# You (admin):
/team-invite
# → gives you: tk_invite_xyz789...

# Teammate:
npx @thinkand/claude@latest install
# → Choose "Join an existing organization" → paste the invite key
```

Restart Claude Code after install.

## Profiles: choose what goes where

think& identities are split into **profiles**. Each profile is a namespace with its own credentials and its own memories. On one laptop you can hold any combination of:

- **Personal profile** — a team-of-one. Memories are private to you. Use this for side projects, side notes, anything you don't want in a team namespace.
- **Organization profile** — a shared team. Memories are visible to every teammate in the same org via `/ask-team`, `/team`, `/week-team`.

Profiles are **bound per folder**. A hook firing inside a given folder uploads (and recalls) to the profile bound to that folder's git root. **A folder with no binding uploads nothing.** This is the core safety guarantee — if you haven't said "this folder goes to org X," nothing from that folder leaves your laptop.

### Managing profiles

```
/profile                       # show the active profile for the current folder
/profile list                  # list all profiles and their bound folders

/profile use <slug>            # bind the current folder to a profile
/profile ignore                # mark the current folder as never-upload
/profile unbind                # remove the current folder's binding

/profile add personal <name>   # create a new personal profile
/profile add org <team> <name> # create a new organization
/profile add join <invite> <name>   # join an existing org with an invite key

/profile purge-local [slug]    # locally flip all bindings of a profile to ignore
                               # (instant panic button — no server round-trip)
/profile purge-remote <slug> --all          # delete every memory for a profile from the server
/profile purge-remote <slug> --cwd <path>   # delete one folder's memories from the server
```

All command output also works at the shell:
```bash
python3 ~/.bryonics/current/lib/profile_cli.py <subcommand> [args]
```

### Example: one laptop, three profiles

```bash
# Install once, create your org
npx @thinkand/claude@latest install     # → create org acme

# Add a personal profile alongside the org
/profile add personal gaurav            # → slug "personal"

# Bind work projects to acme
cd ~/work/acme-api        && /profile use acme
cd ~/work/acme-dashboard  && /profile use acme

# Bind side projects to personal
cd ~/side/my-game         && /profile use personal
cd ~/notes                && /profile use personal

# Ignore a folder you never want touched
cd ~/secrets              && /profile ignore

# Check which profile a folder resolves to
cd ~/work/acme-api && /profile
# → acme (org, team_id=acme)
```

Now hooks in `~/work/acme-api/` upload to `acme`, hooks in `~/side/my-game/` upload to `personal`, hooks in `~/secrets/` do nothing, and hooks in any other folder silently skip with a once-per-6-hours stderr warning that says the folder is unbound.

### Environment variable override

To override the per-folder binding for a single terminal session (useful for scripted runs or when a folder's binding is wrong):

```bash
export BRYONICS_PROFILE=personal    # overrides any binding for this shell
```

## Migrating from an earlier version (< 0.5.0)

If you installed think& before profiles existed, your config lives in a flat format: `{api_url, api_key, user_id, team_id}`. On first run of the new version, think& automatically migrates it to the multi-profile schema. **No data is lost.** Your existing key is preserved verbatim inside the new `profiles[0]` object with slug `legacy`.

**But all your folders become unbound.** This is intentional — before the migration, every hook in every folder on your laptop was uploading to the same single `team_id`. The migration flips to opt-in-per-folder so you can pick which projects actually belong in the team namespace.

**What you need to do after the migration:**

1. **Bind the projects you want synced back.** For each active project:
   ```bash
   cd ~/path/to/project
   /profile use legacy
   ```
   Or from a shell:
   ```bash
   python3 ~/.bryonics/current/lib/profile_cli.py use legacy
   ```

2. **(Optional) split personal work out from the legacy profile.** If the flat config was actually for team work, you can create a fresh personal profile for side projects:
   ```bash
   /profile add personal gaurav
   cd ~/side/project && /profile use personal
   ```

3. **(Optional) purge already-uploaded accidental leaks.** If your personal projects were unintentionally getting sync'd to the team namespace before the migration, purge them from the server:
   ```bash
   /profile purge-remote legacy --cwd /Users/gaurav/side/project
   ```
   See **[Purge and what "deleted" actually means](#purge-and-what-deleted-actually-means)** below.

4. **Verify what's running where.**
   ```bash
   /profile list
   ```
   Should show every folder you want synced alongside the `legacy` profile.

**If you want to go back.** The migration is reversible — your old config is fully reconstructable from the new `profiles[0]` object. To hand-revert:
```bash
python3 -c "
import json
cfg = json.load(open('$HOME/.bryonics/config.json'))
legacy = cfg['profiles'][0]
flat = {
    'api_url': cfg['api_url'],
    'api_key': legacy['api_key'],
    'user_id': legacy['user_id'],
    'team_id': legacy['team_id'],
}
json.dump(flat, open('$HOME/.bryonics/config.json', 'w'), indent=2)
"
```

(You'd also need to downgrade the installed version, since the new hooks expect the multi-profile shape.)

## What it does

### Proactive Recall

Every prompt you type triggers a server-side recall that surfaces relevant context automatically:

- **Architecture context** — the right files, symbols, and subsystem info for what you're asking about
- **Team activity** — what teammates recently worked on in the same area
- **Related code** — actual code snippets from the codebase when relevant

### Session Resume

When you start a new session or say `continue` / `resume`, think& automatically injects context about where you left off — before any file exploration happens.

The resume packet includes:

- **Current task** — what you were working on
- **Why it matters** — the reasoning and tradeoffs behind the approach
- **Next step** — what was planned next
- **Files to open** — exactly which files to inspect (no broad repo rediscovery)
- **Known constraints** — blockers, failed attempts, things to avoid
- **Open questions** — unresolved TODOs from prior sessions

```
Session resume:
Current task: Add rate limiting to API endpoints
Why: Sliding window rather than token bucket — simpler to reason about
Next step: Add Redis for distributed rate limiting
Files to open: src/middleware.py, src/rate_limit.py
Constraints: Blocked: Waiting on infra team for Redis endpoint
Open questions: Revisit the expiry policy for refresh tokens
Recent episodes:
  - auth fix → Tests pass, auth implemented
  - rate limiting → Add Redis [open]
```

This is powered by **episode segmentation** — each coding session is split into episodes (by time gaps, topic shifts, or explicit goal changes), and each episode captures the goal, decisions, errors, outcome, and what's still open.

**Requires `/sync` first.** Episode memories are extracted from your Claude Code transcripts when you run `/sync`. Without it, there's no episode data for resume to surface. Run `/sync` at the end of a session (or periodically) to keep resume context fresh.

Resume triggers automatically on:
- First prompt of a new session (`capture_count == 0`)
- Short prompts: `continue`, `resume`, `carry on`
- Longer prompts: `where was I`, `what were we doing`, `bring me up to speed`

### 5-Level Architecture Understanding

think& builds a live architecture model of your codebase. Ask questions at any level:

| Level | What it is | Example questions |
|-------|-----------|-------------------|
| **L0 — Features** | Feature history + evolution | "how did recall evolve?" "what changed in auth recently?" |
| **L1 — Components** | Broad system components | "what component handles developer runtime?" "what component owns quiz generation?" |
| **L2 — Subsystems** | Concept-shaped subsystems | "what subsystem owns capture?" "what subsystem owns org auth?" |
| **L3 — Files** | Files with imports + dependencies | "how does capture hook work?" "how does quiz generation work?" |
| **L4 — Symbols** | Functions, classes, exports | "what does repo_relative_path do?" |

Architecture is built from your actual code (uploaded via `/code-sync`) and team activity history.

### Comprehension Quizzes

Quiz yourself on any PR or local branch diff:

```
/quiz hammadtq/openclaw#1       → quiz on a GitHub PR
/quiz --branch                   → quiz on your current branch vs main
/quiz-submit 1:B 2:A 3:C 4:D 5:B
```

Questions focus on architecture, trade-offs, and failure modes — generated by Claude Sonnet. Answer key stays server-side until you submit.

### Session Sync

Extract structured episode memories from your Claude Code transcripts:

```
/sync
```

Segments sessions into episodes and extracts per-episode: goal, why, files touched, decisions, failed attempts, outcome, open questions, next step, and blockers. Incremental — only processes new transcript data since last sync.

### Code Sync

Upload your repo's code to the server for architecture analysis:

```
/architecture    → scan + cache file/symbol/import structure
/code-sync       → upload file contents to server memory
```

Incremental — only uploads changed files on subsequent runs. Capture hook auto-uploads on every Edit/Write.

### Team Commands

```
/team                → what your team has been working on (with timestamps)
/ask-team auth       → search team knowledge for "auth"
/week-team           → weekly team summary
/team-invite         → generate invite key (admin only)
```

### Profile Commands

```
/profile                    → show active profile for the current folder
/profile list               → list all profiles and their folder bindings
/profile use <slug>         → bind current folder to a profile
/profile ignore             → mark current folder as never-upload
/profile unbind             → remove current folder's binding
/profile add personal <name>    → create a new personal profile
/profile add org <team> <name>  → create a new organization
/profile add join <invite> <name>  → join an existing org
/profile purge-local [slug]             → locally flip all bindings of a profile to ignore
/profile purge-remote <slug> --all      → delete all memories for a profile from the server
/profile purge-remote <slug> --cwd <path>  → delete one folder's memories from the server
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

- **Profile resolver** — every hook starts by resolving the current folder to a profile via `~/.bryonics/project-profiles.json`. Unbound folders silently skip. The resolver canonicalizes cwd to the git root so symlinks and nested subdirs always match consistently.
- **Capture hook** runs after every Edit/Write/Bash — resolves the profile, records what you did, uploads changed file contents, and tags every memory with the folder's canonical cwd so purge-by-cwd works precisely later.
- **Recall hook** runs before every prompt — calls `POST /v1/recall` for server-side multi-lane fusion; on resume prompts, also calls `POST /v1/session-resume` for episode-based context.
- **Session sync** — `/sync` iterates transcript files and resolves each transcript's profile independently from the transcript's own cwd. Personal-folder transcripts go to personal profiles; team-folder transcripts go to team profiles; unbound transcripts are skipped.
- **Slash command blocklist** — `/ask-team`, `/profile`, `/team`, `/quiz`, `/sync`, etc. are dropped from prompt-memory extraction. Search queries and bind targets stay off the wire.
- **Architecture builder** derives 5-level architecture (L0-L4) from uploaded code + activity history.
- **Recall fusion** combines architecture + team activity + code chunks, with metadata-first routing for concept queries.
- **Org auth** — per-user API keys, one-time invite keys, no email or OAuth. Each profile has exactly one key; keys rotate independently.

## Purge and what "deleted" actually means

`/profile purge-remote` tombstones memories on the server. Search stops returning them immediately — the tombstone filter runs inside every `POST /v1/memories/search` call. A background compact job removes the underlying rows later.

**What purge does not do:**

- **Does not recall content teammates already saw.** If a teammate ran `/ask-team` yesterday and your accidentally-uploaded personal code came back in the results, that content is now on their machine — in their Claude Code session transcript, their scrollback, possibly in their own session_sync uploads if the content got quoted back to the LLM. The server delete doesn't reach into their laptop.
- **Does not clear Anthropic API logs.** Once prompts + results flow through the Anthropic API, they're subject to Anthropic's retention policy, not ours.

**If the leaked data was sensitive:** rotate secrets, notify anyone who may have seen it, and treat purge as harm reduction rather than full remediation.

**If you just made a typo binding and want to fix it quickly:** `/profile unbind` on the current folder + `/profile use <correct-slug>` is enough. No need to purge — the wrong bind only matters if it actually uploaded anything, and you can check with `/team` or grep recent captures for the folder name.

## What gets installed

```
~/.bryonics/
├── current → releases/0.5.0/    # stable symlink, atomically swapped on update
├── releases/0.5.0/
│   ├── hooks/                    # recall.py, capture.py
│   ├── lib/                      # client library + helpers (profile_cli.py, etc.)
│   └── commands/                 # slash command definitions
├── config.json                   # profiles[] + api_url (persists across updates)
├── project-profiles.json         # cwd → profile bindings (persists)
├── warnings/                     # rate-limit markers for unbound-cwd warnings
└── sessions/                     # per-project session state (persists)

~/.claude/
├── settings.json                 # hooks auto-merged (backed up before changes)
└── commands/
    ├── quiz.md → current release  # individual symlinks
    ├── sync.md → ...
    ├── team.md → ...
    └── profile.md → ...
```

**Two files to know about.**
- `config.json` holds your profiles and their keys. Back it up before any invasive action.
- `project-profiles.json` holds the folder → profile bindings. Safe to delete — it just means every folder becomes unbound again, and you re-run `/profile use` where you want sync.

Updates are a symlink swap — no file copying, no manual reconfiguration. Your profiles and bindings survive updates automatically.

## Requirements

- Claude Code 2.0+
- Python 3.8+
- Node.js 18+ (for installer only — runtime is Python)

## Privacy

- **Per-folder opt-in.** Memories and code chunks only upload from folders you've explicitly bound with `/profile use`. Unbound folders do nothing — no captures, no recall, no file contents.
- **Per-profile isolation.** Each profile is a separate namespace with its own API key. A key for the personal profile cannot read or delete memories in the team profile, and vice versa. The server derives identity from the bearer token, not from request-body fields, so a stolen key can only affect its own profile.
- **Command prompts are excluded.** Slash-command invocations (`/ask-team`, `/profile use`, `/quiz`, `/team`, `/sync`, `/architecture`, etc.) are dropped at extraction time and never become memories. Your search queries don't get indexed as team knowledge.
- **Purge is a first-class operation.** `/profile purge-remote <slug> --cwd <path>` tombstones memories on the server, and the search handler's tombstone filter makes them invisible immediately. See **[Purge and what "deleted" actually means](#purge-and-what-deleted-actually-means)** for what purge can and can't recover.
- **API keys** live in `~/.bryonics/config.json`, one per profile. They never appear in request bodies — only as bearer tokens.
- **No telemetry**, no third-party data sharing. Memories are synced only to the API URL you configured at install time.
