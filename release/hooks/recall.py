#!/usr/bin/env python3
"""
UserPromptSubmit hook: recall TEAM knowledge before Claude responds.

Queries team namespace with structured filters.
Only injects if score exceeds threshold.
Excludes own memories (Claude Code auto memory handles those).
Outputs structured JSON via hookSpecificOutput.additionalContext.
If no team_id configured, exits silently.
"""

import json
import sys
import os

# Add lib to path
sys.path.insert(0, os.path.expanduser("~/.bryonics/current/lib"))
from bryonics_client import (
    load_config, load_session, save_session,
    get_project_name, get_branch, search_memories,
)


SCORE_THRESHOLD = float(os.environ.get("BRYONICS_SCORE_THRESHOLD", "0.3"))
MAX_RESULTS = 3
MIN_PROMPT_LENGTH = 15


def main():
    cfg = load_config()
    if not cfg:
        sys.exit(0)

    # Must have team_id — personal memory is Claude Code's job
    team_id = cfg.get("team_id")
    if not team_id:
        sys.exit(0)

    # Read hook input from stdin
    try:
        hook_input = json.load(sys.stdin)
        prompt = hook_input.get("prompt", hook_input.get("message", ""))
    except:
        sys.exit(0)

    if not prompt or len(prompt) < MIN_PROMPT_LENGTH:
        sys.exit(0)

    # Load session for context enrichment + anti-spam
    session = load_session()
    project = get_project_name()
    branch = get_branch()

    # Build structured search request
    user_id = cfg.get("user_id", os.environ.get("USER", "unknown"))

    # Context for soft boosts — no hard filters
    context = {
        "project": project,
        "branch": branch,
        "current_file": session.get("last_file"),
        "recent_files": session.get("recent_files", []),
    }

    results = search_memories(
        cfg,
        query=prompt[:500],
        team_id=team_id,
        exclude_user=user_id,
        context=context,
        limit=MAX_RESULTS + 2,  # fetch extra, filter client-side
    )

    if not results:
        sys.exit(0)

    # Score threshold: only inject if top result is relevant enough
    top_score = results[0].get("score", 0) if results else 0
    if top_score < SCORE_THRESHOLD:
        sys.exit(0)

    # Anti-spam: skip memories already shown this session
    last_recalled = set(session.get("last_recalled_ids", []))
    fresh_results = [r for r in results if r.get("id", "") not in last_recalled]

    if not fresh_results:
        sys.exit(0)

    # Take top 2-3
    to_inject = fresh_results[:MAX_RESULTS]

    # Format attributed context
    lines = []
    for r in to_inject:
        meta = r.get("metadata", {})
        created_by = meta.get("created_by", r.get("created_by", "teammate"))
        proj = meta.get("project", "")
        br = meta.get("branch", "")
        memory = r.get("memory", "")[:200]

        location = ""
        if proj and br:
            location = " ({}/{})".format(proj, br)
        elif proj:
            location = " ({})".format(proj)

        lines.append("[{}]{}: {}".format(created_by, location, memory))

    if not lines:
        sys.exit(0)

    # Output structured JSON for Claude Code
    context = "Team context from Bryonics:\n" + "\n".join(lines)
    output = {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": context
        }
    }
    json.dump(output, sys.stdout)

    # Update session: track what we recalled
    recalled_ids = [r.get("id", "") for r in to_inject]
    session["last_recalled_ids"] = list(last_recalled | set(recalled_ids))
    save_session(session)


if __name__ == "__main__":
    main()
