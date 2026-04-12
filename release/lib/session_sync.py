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
from bryonics_client import (
    load_config, load_config_raw, api_request,
    resolve_profile, IGNORE, canonical_cwd,
)

# ── Constants ──

CLAUDE_PROJECTS_DIR = os.path.expanduser("~/.claude/projects")
SYNC_STATE_PATH = os.path.expanduser("~/.bryonics/synced_sessions.json")

SKIP_PROMPTS = {
    "yes", "ok", "go ahead", "sure", "y", "n", "no", "continue",
    "thanks", "great", "cool", "got it", "perfect", "good", "nice",
    "do it", "proceed", "show me", "go", "yep", "yeah",
}

# Prompts starting with one of these tokens are slash-command invocations,
# not real user intent. They should never be uploaded as prompt memories
# because they contain the user's search queries / bind targets / etc.
# Dropping them at extract time is the second layer of the /ask-team leak
# fix (layer 1 is per-transcript profile routing in main()).
COMMAND_PREFIXES = (
    "/ask-team", "/team", "/profile", "/quiz", "/quiz-status",
    "/quiz-cancel", "/quiz-submit", "/quiz-open", "/week-team",
    "/sync", "/code-sync", "/architecture", "/team-invite",
)

DECISION_MARKERS = [
    "chose", "decided", "because", "trade-off", "instead of",
    "rather than", "the reason", "better to", "approach was",
]

PLANNING_PREFIXES = [
    "let me", "now let me", "i'll start by", "first i'll",
    "let me start", "i'll begin", "starting with", "now i'll",
]

GOAL_SHIFT_MARKERS = [
    "now let's", "next thing", "switch to", "moving on to",
    "new task", "different thing", "instead let's", "actually let's",
    "forget that", "change of plans", "ok now", "alright now",
    "next step", "next up", "on to",
]

TRADEOFF_MARKERS = [
    "instead of", "rather than", "trade-off", "downside",
    "alternative", "could have", "opted for", "went with",
    "pros and cons", "versus",
]

OUTCOME_MARKERS = [
    "done", "shipped", "merged", "passing", "fixed",
    "implemented", "completed", "working now", "tests pass",
    "all green", "committed",
]

OPEN_QUESTION_MARKERS = [
    "todo", "later", "revisit", "not sure about", "might need",
    "open question", "tbd", "for now", "temporary", "hack",
    "workaround", "tech debt",
]

NEXT_STEP_MARKERS = [
    "next step", "next we", "then we", "after that",
    "remaining", "still need to", "left to do", "upcoming",
    "plan is to", "will need to",
]

BLOCKED_MARKERS = [
    "blocked", "waiting on", "depends on", "can't proceed",
    "need to first", "prerequisite", "before we can",
    "blocker", "requires",
]

FAILED_ATTEMPT_MARKERS = [
    "didn't work", "that failed", "reverted", "rolled back",
    "that broke", "caused a regression", "wrong approach",
    "backed out", "tried but", "had to undo",
]

MAX_PROMPTS = 3
MAX_DECISIONS = 3
MAX_ERRORS = 3
MAX_TOTAL_PER_RUN = 100
MIN_SESSION_ENTRIES = 10
TIME_GAP_SECONDS = 900  # 15 min = episode boundary
MIN_EPISODE_ENTRIES = 4


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


def find_transcript_files():
    """Find all main session JSONL files (skip subagents)."""
    if not os.path.exists(CLAUDE_PROJECTS_DIR):
        return []

    files = []
    for jsonl in glob.glob(os.path.join(CLAUDE_PROJECTS_DIR, "*", "*.jsonl")):
        # Skip subagent files
        if "/subagents/" in jsonl or "agent-" in os.path.basename(jsonl):
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


# ── Episode Segmentation ──

def parse_iso_timestamp(ts_str):
    """Parse ISO 8601 timestamp to epoch seconds. Returns 0 on failure."""
    if not ts_str:
        return 0
    try:
        ts_str = ts_str.rstrip("Z").split(".")[0]
        return time.mktime(time.strptime(ts_str, "%Y-%m-%dT%H:%M:%S"))
    except (ValueError, OverflowError):
        return 0


def extract_entry_files(entry, git_root=""):
    """Extract file paths from tool_use blocks in an entry."""
    files = set()
    msg = entry.get("message", {})
    content = msg.get("content")
    if not isinstance(content, list):
        return files
    for block in content:
        if block.get("type") != "tool_use":
            continue
        name = block.get("name", "")
        if name in ("Edit", "Write", "Read"):
            fp = block.get("input", {}).get("file_path", "")
            if fp:
                files.add(repo_relative_path(fp, git_root))
    return files


def is_goal_shift(entry):
    """Check if user entry signals a new goal/topic."""
    if entry.get("type") != "user":
        return False
    msg = entry.get("message", {})
    content = msg.get("content")
    if not isinstance(content, str):
        return False
    text = content.strip().lower()
    if len(text) < 15:
        return False
    return any(marker in text for marker in GOAL_SHIFT_MARKERS)


def segment_episodes(entries, git_root=""):
    """Split transcript entries into episodes.

    Boundaries:
    1. Time gap > 15 min between consecutive entries
    2. Topic shift: file cluster changes entirely
    3. Explicit goal change from user

    Returns list of dicts: {entries, start_ts, end_ts, files}
    """
    if not entries:
        return []

    episodes = []
    current = {"entries": [], "start_ts": 0, "end_ts": 0, "files": set()}
    prev_ts = 0

    for entry in entries:
        ts = parse_iso_timestamp(entry.get("timestamp", ""))
        entry_files = extract_entry_files(entry, git_root)

        should_split = False

        # 1. Time gap boundary
        if prev_ts and ts and (ts - prev_ts) > TIME_GAP_SECONDS:
            should_split = True

        # 2. Topic shift: new files have zero overlap with episode files
        if (not should_split and len(current["entries"]) >= 6
                and entry_files and current["files"]
                and not (entry_files & current["files"])):
            should_split = True

        # 3. Explicit goal shift from user
        if not should_split and is_goal_shift(entry):
            if len(current["entries"]) >= MIN_EPISODE_ENTRIES:
                should_split = True

        if should_split and current["entries"]:
            episodes.append(current)
            current = {"entries": [], "start_ts": 0, "end_ts": 0, "files": set()}

        current["entries"].append(entry)
        if ts:
            if not current["start_ts"]:
                current["start_ts"] = ts
            current["end_ts"] = ts
            prev_ts = ts
        current["files"].update(entry_files)

    if current["entries"]:
        episodes.append(current)

    # Merge pass: absorb small episodes (< 8 entries) into previous
    # unless there's a large time gap (> 30 min)
    if len(episodes) <= 1:
        return episodes

    merged = [episodes[0]]
    for ep in episodes[1:]:
        prev = merged[-1]
        gap = (ep["start_ts"] - prev["end_ts"]) if (ep["start_ts"] and prev["end_ts"]) else 0
        small = len(ep["entries"]) < 8

        if small and gap < TIME_GAP_SECONDS:  # merge only within same time window
            prev["entries"].extend(ep["entries"])
            prev["end_ts"] = ep["end_ts"] or prev["end_ts"]
            prev["files"].update(ep["files"])
        else:
            merged.append(ep)

    return merged


# ── Per-Episode Extraction ──

def _scan_assistant_paragraphs(episode, markers, min_len=60):
    """Scan assistant text blocks for paragraphs matching any marker."""
    candidates = []
    for entry in episode["entries"]:
        if entry.get("type") != "assistant":
            continue
        msg = entry.get("message", {})
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if block.get("type") != "text":
                continue
            for para in block.get("text", "").split("\n\n"):
                para = para.strip()
                if len(para) < min_len:
                    continue
                lower = para.lower()
                if any(m in lower for m in markers):
                    candidates.append(para)
    candidates.sort(key=len, reverse=True)
    return candidates


def _clean_goal(text):
    """Clean a raw user prompt into a concise goal string."""
    # Strip URLs
    text = re.sub(r'https?://\S+', '', text).strip()
    # Take first sentence or first line (whichever is shorter)
    first_line = text.split("\n")[0].strip()
    for sep in [". ", "? ", "! "]:
        idx = first_line.find(sep)
        if idx > 10:
            first_line = first_line[:idx + 1]
            break
    # Cap length
    if len(first_line) > 120:
        first_line = first_line[:120].rsplit(" ", 1)[0] + "..."
    return first_line


def ep_goal(episode):
    """First meaningful user prompt in episode — cleaned to a concise goal."""
    for entry in episode["entries"]:
        if entry.get("type") != "user":
            continue
        msg = entry.get("message", {})
        content = msg.get("content")
        if not isinstance(content, str):
            continue
        text = content.strip()
        if len(text) < 20 or text.lower() in SKIP_PROMPTS:
            continue
        if text.startswith("{") or text.startswith("<"):
            continue
        return _clean_goal(text)
    return ""


def ep_why(episode):
    """Why this approach — decisions with rationale."""
    hits = _scan_assistant_paragraphs(episode, DECISION_MARKERS, min_len=80)
    # Exclude planning prefixes
    filtered = []
    for h in hits:
        if not any(h.lower().startswith(p) for p in PLANNING_PREFIXES):
            filtered.append(h[:300])
        if len(filtered) >= 2:
            break
    return filtered


def ep_decisions(episode):
    """Key decisions made (same as why but broader)."""
    return ep_why(episode)


def ep_failed_attempts(episode):
    """Approaches that didn't work."""
    hits = _scan_assistant_paragraphs(episode, FAILED_ATTEMPT_MARKERS, min_len=40)
    return [h[:200] for h in hits[:2]]


def ep_errors(episode):
    """Errors from tool_result blocks."""
    candidates = []
    for entry in episode["entries"]:
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
    candidates.sort(key=len, reverse=True)
    return candidates[:2]


def ep_outcome(episode):
    """Outcome signals from the tail of the episode."""
    tail = episode["entries"][-10:]
    for entry in reversed(tail):
        if entry.get("type") != "assistant":
            continue
        msg = entry.get("message", {})
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if block.get("type") != "text":
                continue
            for para in block.get("text", "").split("\n\n"):
                para = para.strip()
                if any(m in para.lower() for m in OUTCOME_MARKERS) and len(para) > 20:
                    return para[:200]
    return ""


def ep_open_questions(episode):
    """Unresolved TODOs, hacks, "for now" items."""
    hits = _scan_assistant_paragraphs(episode, OPEN_QUESTION_MARKERS, min_len=30)
    return [h[:200] for h in hits[:2]]


def ep_next_step(episode):
    """What was planned next — from tail of episode."""
    hits = _scan_assistant_paragraphs(
        {"entries": episode["entries"][-8:]}, NEXT_STEP_MARKERS, min_len=30
    )
    return hits[0][:200] if hits else ""


def ep_blocked_on(episode):
    """Blockers mentioned."""
    hits = _scan_assistant_paragraphs(episode, BLOCKED_MARKERS, min_len=30)
    return hits[0][:200] if hits else ""


def ep_subsystem(files):
    """Derive subsystem/component from file paths."""
    dir_counts = {}
    for f in files:
        parts = f.split("/")
        if len(parts) > 1:
            d = parts[0]
            if d not in (".", ".."):
                dir_counts[d] = dir_counts.get(d, 0) + 1
    return max(dir_counts, key=dir_counts.get) if dir_counts else ""


def ep_label(files, goal):
    """Derive short label from dominant directory + goal."""
    top_dir = ep_subsystem(files)
    short_goal = goal.split("\n")[0][:60] if goal else ""
    if short_goal and len(short_goal) == 60:
        short_goal = short_goal.rsplit(" ", 1)[0] + "..."
    if top_dir and short_goal:
        return "{}: {}".format(top_dir, short_goal)
    if short_goal:
        return short_goal
    if files:
        # No goal — label from file basenames
        basenames = [os.path.basename(f) for f in sorted(files)[:3]]
        prefix = "{}: ".format(top_dir) if top_dir else ""
        return prefix + ", ".join(basenames)
    return "unknown"


def build_episode_memories(episodes, user, project, branch, git_root):
    """Build structured episode memories for upload.

    Each episode becomes one memory with rich metadata.
    """
    memories = []
    total = len(episodes)

    for i, ep in enumerate(episodes):
        goal = ep_goal(ep)
        why = ep_why(ep)
        decisions = ep_decisions(ep)
        failed = ep_failed_attempts(ep)
        errors = ep_errors(ep)
        outcome = ep_outcome(ep)
        open_qs = ep_open_questions(ep)
        next_s = ep_next_step(ep)
        blocked = ep_blocked_on(ep)
        files = sorted(ep["files"])[:15]
        subsystem = ep_subsystem(ep["files"])
        label = ep_label(ep["files"], goal)

        # Quality gate: skip episodes with no goal AND no files
        if not goal and not files:
            continue

        # Build narrative text
        parts = ["[{}] Episode {}/{} ({}/{}): {}".format(
            user, i + 1, total, project, branch, label
        )]
        if goal:
            parts.append("Goal: " + goal[:200])
        if why:
            parts.append("Why: " + "; ".join(why))
        if files:
            parts.append("Files: " + ", ".join(files[:8]))
        if decisions:
            parts.append("Decisions: " + "; ".join(decisions))
        if failed:
            parts.append("Failed: " + "; ".join(failed))
        if errors:
            parts.append("Errors: " + "; ".join(errors))
        if outcome:
            parts.append("Outcome: " + outcome)
        if open_qs:
            parts.append("Open: " + "; ".join(open_qs))
        if next_s:
            parts.append("Next: " + next_s)
        if blocked:
            parts.append("Blocked: " + blocked)

        text = "\n".join(parts)

        memories.append({
            "text": text,
            "type": "episode",
            "metadata_extra": {
                "episode_index": i,
                "episode_total": total,
                "episode_label": label,
                "episode_goal": goal[:200] if goal else "",
                "episode_subsystem": subsystem,
                "episode_files": files,
                "episode_outcome": outcome[:200] if outcome else "",
                "episode_next_step": next_s,
                "episode_blocked_on": blocked,
                "has_errors": len(errors) > 0,
                "has_failed_attempts": len(failed) > 0,
                "has_open_questions": len(open_qs) > 0,
                "start_ts": time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ", time.gmtime(ep["start_ts"])
                ) if ep["start_ts"] else "",
                "end_ts": time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ", time.gmtime(ep["end_ts"])
                ) if ep["end_ts"] else "",
            }
        })

    return memories


# ── Legacy Extractors (flat, pre-episode) ──

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
        # Drop slash-command invocations. These are commands, not intent,
        # and their arguments frequently contain the exact queries /
        # bind targets / search strings the user most wants kept private.
        # See session_sync.py:COMMAND_PREFIXES and the /ask-team leak fix.
        first_token = text.split(None, 1)[0].lower() if text.split() else ""
        if first_token in COMMAND_PREFIXES:
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

def _has_timestamps(entries):
    """Check if entries have usable timestamps for episode segmentation."""
    for entry in entries[:5]:
        if entry.get("timestamp"):
            return True
    return False


def _upload_memories(memories, cfg, user, team_id, session_id, project, branch, cwd):
    """Upload a list of memories. Returns count of successful uploads."""
    uploaded = 0
    for mem in memories:
        metadata = {
            "source": "claude_code_session",
            "session_id": session_id,
            "project": project,
            "branch": branch,
            "memory_type": mem["type"],
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "cwd": cwd,
        }
        # Merge episode-specific metadata
        if "metadata_extra" in mem:
            metadata.update(mem["metadata_extra"])

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

    return uploaded


def main():
    # Per-transcript profile routing is the core leak fix. Each transcript
    # picks its profile from *its own* cwd via resolve_profile, not from
    # the ambient process cwd. Personal transcripts → personal profile,
    # team transcripts → team profile, unbound transcripts → skipped.
    raw = load_config_raw()
    if not raw:
        print("No Bryonics config found. Run install.sh first.")
        return

    # api_url is profile-independent; read once.
    api_url = raw.get("api_url", "")

    transcript_files = find_transcript_files()
    if not transcript_files:
        print("No Claude Code session transcripts found.")
        return

    sync_state = load_sync_state()
    total_synced = 0
    total_memories = 0
    sessions_processed = 0
    skipped_unbound = 0
    skipped_ignored = 0

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

        # Detect project context — scan first 20 entries for cwd/gitBranch
        # (entries[0] after byte offset resume may be an assistant entry)
        session_cwd = ""
        branch = ""
        for e in entries[:20]:
            if not session_cwd and e.get("cwd"):
                session_cwd = e["cwd"]
            if not branch and e.get("gitBranch"):
                branch = e["gitBranch"]
            if session_cwd and branch:
                break
        project = os.path.basename(session_cwd) if session_cwd else "unknown"
        git_root = detect_git_root(session_cwd) if session_cwd else ""

        # Resolve profile for THIS transcript's cwd. Load-bearing: if the
        # transcript came from a personal folder, its memories go to the
        # personal profile; if it came from a team folder, they go to the
        # team profile. Unbound folders are skipped silently.
        profile = resolve_profile(session_cwd) if session_cwd else None
        if profile is IGNORE:
            skipped_ignored += 1
            sync_state[session_id] = {
                "synced_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "memories_extracted": session_state.get("memories_extracted", 0),
                "last_byte_offset": new_offset,
            }
            continue
        if profile is None:
            skipped_unbound += 1
            # Advance the offset so we don't re-scan this transcript every
            # run. The user can bind the folder later and future transcript
            # content will flow; historical content stays unbound.
            sync_state[session_id] = {
                "synced_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "memories_extracted": session_state.get("memories_extracted", 0),
                "last_byte_offset": new_offset,
            }
            continue

        # Per-transcript identity — comes from the resolved profile, not
        # from ambient config. This is what stops cross-profile uploads.
        user = profile.get("user_id") or os.environ.get("USER", "unknown")
        team_id = profile.get("team_id")
        transcript_cfg = {
            "api_url": api_url,
            "api_key": profile.get("api_key", ""),
        }

        # Extract memories — episode-based if timestamps available, legacy fallback
        memories = []
        if _has_timestamps(entries):
            episodes = segment_episodes(entries, git_root)
            if episodes:
                memories = build_episode_memories(
                    episodes, user, project, branch, git_root
                )
                # Always include session-level file summary
                memories.extend(
                    extract_file_summary(entries, user, project, branch, git_root)
                )
        else:
            # Legacy flat extraction
            memories.extend(extract_prompts(entries, user, project, branch))
            memories.extend(extract_decisions(entries, user, project))
            memories.extend(extract_errors(entries, user, project))
            memories.extend(extract_file_summary(entries, user, project, branch, git_root))

        if not memories:
            sync_state[session_id] = {
                "synced_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "memories_extracted": session_state.get("memories_extracted", 0),
                "last_byte_offset": new_offset,
            }
            continue

        # Upload to THIS transcript's profile — not the global one.
        uploaded = _upload_memories(
            memories[:MAX_TOTAL_PER_RUN - total_memories],
            transcript_cfg, user, team_id, session_id, project, branch,
            canonical_cwd(session_cwd),
        )

        if uploaded > 0:
            sync_state[session_id] = {
                "synced_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "memories_extracted": session_state.get("memories_extracted", 0) + uploaded,
                "last_byte_offset": new_offset,
            }
            sessions_processed += 1
            total_synced += uploaded
            total_memories += uploaded

    save_sync_state(sync_state)

    parts = []
    if total_synced > 0:
        parts.append("Synced {} sessions, {} memories uploaded.".format(
            sessions_processed, total_synced))
    else:
        parts.append("No new memories to sync.")
    if skipped_unbound:
        parts.append(f"Skipped {skipped_unbound} unbound transcript(s) — "
                     f"run /profile use in those folders to enable sync.")
    if skipped_ignored:
        parts.append(f"Skipped {skipped_ignored} ignored transcript(s).")
    print(" ".join(parts))


if __name__ == "__main__":
    main()
