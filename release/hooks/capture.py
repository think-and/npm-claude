#!/usr/bin/env python3
"""
PostToolUse hook: capture what this team member did.

Structured capture with dedup and rate limiting.
Only fires on successful Edit/Write/Bash.
Stores to team namespace with attribution.
If no team_id configured, exits silently.
"""

import json
import sys
import os
import time

# Add lib to path
sys.path.insert(0, os.path.expanduser("~/.bryonics/current/lib"))
from bryonics_client import (
    load_config, load_session, save_session,
    get_project_name, get_branch, store_memory, content_hash,
)

# Trivial commands to skip
TRIVIAL_COMMANDS = {"git status", "git diff", "ls", "pwd", "cat", "head", "tail", "echo"}


def repo_relative_path(file_path):
    """Canonicalize file path to repo-relative. Strips git root prefix."""
    if not file_path:
        return ""
    try:
        import subprocess
        root = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=2
        ).stdout.strip()
        if root and file_path.startswith(root):
            rel = file_path[len(root):].lstrip("/")
            return rel if rel else file_path
    except:
        pass
    return file_path


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
        tool = hook_input.get("tool_name", "")
        tool_input = hook_input.get("tool_input", {})
    except:
        sys.exit(0)

    if tool not in ("Edit", "Write", "Bash"):
        sys.exit(0)

    # Load session state
    session = load_session()

    # Rate limit: max 30 captures per session (resets daily or on branch change)
    max_captures = int(os.environ.get("BRYONICS_MAX_CAPTURES", "30"))
    if session.get("capture_count", 0) >= max_captures:
        sys.exit(0)

    # Cooldown: skip if last capture was <5s ago
    now = time.time()
    if now - session.get("last_capture_time", 0) < 5:
        sys.exit(0)

    # Build structured memory
    user = cfg.get("user_id", os.environ.get("USER", "unknown"))
    project = get_project_name()
    branch = get_branch()

    file_path = ""
    command = ""
    exit_status = None

    if tool in ("Edit", "Write"):
        file_path = tool_input.get("file_path", "unknown")
        memory = "[{}] {} {} ({}/{})".format(user, tool, file_path, project, branch)
    elif tool == "Bash":
        command = (tool_input.get("command", "") or "")[:200]
        exit_status = tool_input.get("exit_code")

        # Skip trivial commands
        cmd_base = command.strip().split()[0] if command.strip() else ""
        cmd_full = command.strip()
        if cmd_base in TRIVIAL_COMMANDS or cmd_full in TRIVIAL_COMMANDS:
            sys.exit(0)

        memory = "[{}] Ran: {} ({}/{})".format(user, command[:100], project, branch)
        if exit_status is not None and exit_status != 0:
            memory += " [exit:{}]".format(exit_status)
    else:
        sys.exit(0)

    # Dedup: skip if same hash as last capture
    h = content_hash(tool + (file_path or command[:100]))
    if h == session.get("last_capture_hash"):
        sys.exit(0)

    # Canonicalize file path to repo-relative
    rel_path = repo_relative_path(file_path) if file_path else ""

    # Store to team namespace
    metadata = {
        "project": project,
        "branch": branch,
        "file_path": rel_path,
        "tool": tool,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if command:
        metadata["command"] = command[:200]
    if exit_status is not None:
        metadata["exit_status"] = exit_status

    store_memory(cfg, memory, team_id=team_id, metadata=metadata)

    # Update session state
    session["last_capture_hash"] = h
    session["last_capture_time"] = now
    session["capture_count"] = session.get("capture_count", 0) + 1
    if file_path:
        session["last_file"] = file_path

    # Track recent files (max 10, repo-relative, most recent first)
    if rel_path:
        recent_files = session.get("recent_files", [])
        if rel_path in recent_files:
            recent_files.remove(rel_path)
        recent_files.insert(0, rel_path)
        session["recent_files"] = recent_files[:10]

    # Track recent commands (max 5, most recent first)
    if command:
        recent_commands = session.get("recent_commands", [])
        recent_commands.insert(0, command[:100])
        session["recent_commands"] = recent_commands[:5]

    session["project"] = project
    session["branch"] = branch
    save_session(session)


if __name__ == "__main__":
    main()
