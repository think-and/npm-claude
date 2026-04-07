#!/usr/bin/env python3
"""
Session Sync v0 — extract structured memories from Claude Code transcripts.

Conservative extractor: max 10 memories per session.
- 3 prompts (longest user prompts)
- 3 decisions (assistant reasoning with decision markers)
- 3 errors (from tool_result blocks)
- 1 file summary (all files edited in session)

Skips subagent transcripts. Uses byte offset for incremental sync.
"""

import json
import os
import sys
import glob
import time
import re
import subprocess

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bryonics_client import load_config, save_config, api_request

# ── Constants ──

CLAUDE_PROJECTS_DIR = os.path.expanduser("~/.claude/projects")
SYNC_STATE_PATH = os.path.expanduser("~/.bryonics/synced_sessions.json")

SKIP_PROMPTS = {
    "yes", "ok", "go ahead", "sure", "y", "n", "no", "continue",
    "thanks", "great", "cool", "got it", "perfect", "good", "nice",
    "do it", "proceed", "show me", "go", "yep", "yeah",
}

DECISION_MARKERS = [
    "chose", "decided", "because", "trade-off", "instead of",
    "rather than", "the reason", "better to", "approach was",
]

PLANNING_PREFIXES = [
    "let me", "now let me", "i'll start by", "first i'll",
    "let me start", "i'll begin", "starting with", "now i'll",
]

MAX_PROMPTS = 3
MAX_DECISIONS = 3
MAX_ERRORS = 3
MAX_TOTAL_PER_RUN = 100
MIN_SESSION_ENTRIES = 10
VALID_MODES = ("all", "folder")


# ── Sync state ──

def load_sync_state():
    try:
        with open(SYNC_STATE_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_sync_state(state):
    os.makedirs(os.path.dirname(SYNC_STATE_PATH), exist_ok=True)
    with open(SYNC_STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)


# ── Helpers ──

def repo_relative_path(file_path, git_root):
    """Canonicalize to repo-relative path."""
    if not file_path:
        return ""
    if git_root and file_path.startswith(git_root):
        rel = file_path[len(git_root):].lstrip("/")
        return rel if rel else file_path
    return file_path


def detect_git_root(cwd):
    """Detect git root from a working directory."""
    try:
        result = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=2
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except Exception:
        return ""


def find_transcript_files(mode="folder"):
    """Find session JSONL files (skip subagents).

    mode="folder": scan only the current project's folder (derived from git root / cwd).
    mode="all": scan all project folders under ~/.claude/projects.
    """
    if not os.path.exists(CLAUDE_PROJECTS_DIR):
        return []

    if mode == "all":
        search_dirs = [
            d for d in glob.glob(os.path.join(CLAUDE_PROJECTS_DIR, "*"))
            if os.path.isdir(d)
        ]
    else:
        cwd = os.getcwd()
        git_root = detect_git_root(cwd)
        project_path = git_root if git_root else cwd
        # Claude Code folder convention: /Users/foo/bar -> -Users-foo-bar
        project_folder = project_path.replace("/", "-")
        search_dirs = [os.path.join(CLAUDE_PROJECTS_DIR, project_folder)]

    files = []
    for project_dir in search_dirs:
        if not os.path.isdir(project_dir):
            continue
        for jsonl in glob.glob(os.path.join(project_dir, "*.jsonl")):
            # Skip subagent files
            if "agent-" in os.path.basename(jsonl):
                continue
            # Must be a UUID-style filename
            basename = os.path.basename(jsonl).replace(".jsonl", "")
            if len(basename) >= 30 and "-" in basename:
                files.append(jsonl)
    return files


def parse_transcript(filepath, byte_offset=0):
    """Parse JSONL transcript from byte offset. Returns (entries, new_offset)."""
    file_size = os.path.getsize(filepath)

    # Reset if file shrunk (truncated/rotated)
    if byte_offset > file_size:
        byte_offset = 0

    entries = []
    new_offset = byte_offset

    with open(filepath, "r") as f:
        f.seek(byte_offset)
        while True:
            line_start = f.tell()
            line = f.readline()
            if not line:
                break
            # Don't advance past incomplete trailing line
            if not line.endswith("\n"):
                # Partial line — don't parse, leave offset here
                break
            line = line.strip()
            if not line:
                new_offset = f.tell()
                continue
            try:
                entry = json.loads(line)
                entries.append(entry)
                new_offset = f.tell()
            except json.JSONDecodeError:
                new_offset = f.tell()
                continue

    return entries, new_offset


# ── Extractors ──

def extract_prompts(entries, user, project, branch):
    """Extract top 3 user prompts by length."""
    candidates = []
    for entry in entries:
        if entry.get("type") != "user":
            continue
        msg = entry.get("message", {})
        content = msg.get("content")
        if not isinstance(content, str):
            continue
        text = content.strip()
        if len(text) < 30:
            continue
        if text.lower() in SKIP_PROMPTS:
            continue
        # Skip system/hook messages
        if text.startswith("{") or text.startswith("<"):
            continue
        candidates.append(text)

    # Top 3 by length
    candidates.sort(key=len, reverse=True)
    memories = []
    for text in candidates[:MAX_PROMPTS]:
        memories.append({
            "text": "[{}] Session prompt ({}/{}): {}".format(user, project, branch, text[:200]),
            "type": "prompt",
        })
    return memories


def extract_decisions(entries, user, project):
    """Extract top 3 decisions from assistant text."""
    candidates = []
    for entry in entries:
        if entry.get("type") != "assistant":
            continue
        msg = entry.get("message", {})
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if block.get("type") != "text":
                continue
            text = block.get("text", "")
            for para in text.split("\n\n"):
                para_stripped = para.strip()
                if len(para_stripped) < 80:
                    continue
                para_lower = para_stripped.lower()
                # Check decision markers
                if not any(m in para_lower for m in DECISION_MARKERS):
                    continue
                # Exclude generic planning
                if any(para_lower.startswith(p) for p in PLANNING_PREFIXES):
                    continue
                candidates.append(para_stripped)

    # Top 3 by length
    candidates.sort(key=len, reverse=True)
    memories = []
    for text in candidates[:MAX_DECISIONS]:
        memories.append({
            "text": "[{}] Decision ({}): {}".format(user, project, text[:300]),
            "type": "decision",
        })
    return memories


def extract_errors(entries, user, project):
    """Extract top 3 errors from tool_result blocks."""
    candidates = []
    for entry in entries:
        if entry.get("type") != "user":
            continue
        msg = entry.get("message", {})
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if block.get("type") != "tool_result":
                continue
            text = str(block.get("content", ""))
            is_error = block.get("is_error", False)
            if is_error or ("error" in text.lower() and len(text) > 20):
                candidates.append(text[:200])

    # Top 3 by length (longest errors = most informative)
    candidates.sort(key=len, reverse=True)
    memories = []
    for text in candidates[:MAX_ERRORS]:
        memories.append({
            "text": "[{}] Error ({}): {}".format(user, project, text),
            "type": "error",
        })
    return memories


def extract_file_summary(entries, user, project, branch, git_root):
    """Extract 1 file summary per session."""
    files_touched = set()
    for entry in entries:
        if entry.get("type") != "assistant":
            continue
        msg = entry.get("message", {})
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if block.get("type") != "tool_use":
                continue
            if block.get("name") not in ("Edit", "Write"):
                continue
            fp = block.get("input", {}).get("file_path", "")
            if fp:
                rel = repo_relative_path(fp, git_root)
                files_touched.add(rel)

    if not files_touched:
        return []

    sorted_files = sorted(files_touched)[:15]
    text = "[{}] Session ({}/{}): edited {} files: {}".format(
        user, project, branch, len(files_touched), ", ".join(sorted_files)
    )
    return [{"text": text, "type": "file_summary"}]


# ── Main ──

def main():
    cfg = load_config()
    if not cfg:
        print("No Bryonics config found. Run install.sh first.")
        return

    # ── CLI arg parsing ──
    arg = sys.argv[1] if len(sys.argv) > 1 else None

    if arg == "set-default":
        value = sys.argv[2] if len(sys.argv) > 2 else None
        if value not in VALID_MODES:
            print("Usage: /sync set-default <all|folder>")
            return
        save_config({"sync_default": value})
        print("Default sync mode set to '{}'.".format(value))
        return

    # Resolve mode
    if arg in VALID_MODES:
        mode = arg
    elif arg:
        print("Unknown mode '{}'. Use: all, folder, or set-default <mode>.".format(arg))
        return
    else:
        mode = cfg.get("sync_default", None)

    show_hint = mode is None
    if mode is None:
        mode = "folder"

    team_id = cfg.get("team_id")
    user = cfg.get("user_id", os.environ.get("USER", "unknown"))

    transcript_files = find_transcript_files(mode)
    if not transcript_files:
        label = "any project" if mode == "all" else "this project"
        print("No Claude Code session transcripts found for {}.".format(label))
        return

    sync_state = load_sync_state()
    total_synced = 0
    total_memories = 0
    sessions_processed = 0

    for filepath in transcript_files:
        if total_memories >= MAX_TOTAL_PER_RUN:
            break

        # Session ID from filename
        session_id = os.path.basename(filepath).replace(".jsonl", "")
        session_state = sync_state.get(session_id, {})
        byte_offset = session_state.get("last_byte_offset", 0)

        # Parse new entries from offset
        entries, new_offset = parse_transcript(filepath, byte_offset)

        if not entries:
            continue

        # Skip tiny sessions
        if byte_offset == 0 and len(entries) < MIN_SESSION_ENTRIES:
            continue

        # Detect project context from first entry
        first_entry = entries[0] if entries else {}
        session_cwd = first_entry.get("cwd", "")
        project = os.path.basename(session_cwd) if session_cwd else "unknown"
        branch = first_entry.get("gitBranch", "")
        git_root = detect_git_root(session_cwd) if session_cwd else ""

        # Extract memories
        memories = []
        memories.extend(extract_prompts(entries, user, project, branch))
        memories.extend(extract_decisions(entries, user, project))
        memories.extend(extract_errors(entries, user, project))
        memories.extend(extract_file_summary(entries, user, project, branch, git_root))

        if not memories:
            # Still advance offset even if no memories extracted
            sync_state[session_id] = {
                "synced_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "memories_extracted": session_state.get("memories_extracted", 0),
                "last_byte_offset": new_offset,
            }
            continue

        # Upload memories (advance offset only after successful uploads)
        uploaded = 0
        for mem in memories:
            if total_memories >= MAX_TOTAL_PER_RUN:
                break

            metadata = {
                "source": "claude_code_session",
                "session_id": session_id,
                "project": project,
                "branch": branch,
                "memory_type": mem["type"],
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "cwd": session_cwd,
            }

            body = {
                "messages": [{"role": "user", "content": mem["text"]}],
                "user_id": user,
                "metadata": metadata,
            }
            if team_id:
                body["team_id"] = team_id

            result = api_request(cfg, "POST", "/v1/memories", body, timeout=5.0)
            if result:
                uploaded += 1
                total_memories += 1

        # Only advance offset after successful uploads
        if uploaded > 0:
            sync_state[session_id] = {
                "synced_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "memories_extracted": session_state.get("memories_extracted", 0) + uploaded,
                "last_byte_offset": new_offset,
            }
            sessions_processed += 1
            total_synced += uploaded

    save_sync_state(sync_state)

    if total_synced > 0:
        print("Synced {} sessions, {} memories extracted.".format(
            sessions_processed, total_synced))
    else:
        print("No new memories to sync.")

    if show_hint:
        print("Synced current project only. Use /sync all for all projects, or /sync set-default <mode>.")


if __name__ == "__main__":
    main()
