#!/usr/bin/env python3
"""Helper for /ask-team command: search team knowledge with timestamps.

Detects activity-shaped queries ("what has X done/fixed") and routes
to structured /v1/activity endpoint instead of generic search.
"""

import re
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bryonics_client import load_config, search_memories, api_request


def relative_time(timestamp_str):
    """Convert ISO timestamp to relative time."""
    if not timestamp_str:
        return ""
    try:
        ts = timestamp_str.replace("Z", "+00:00")
        if "T" not in ts:
            return timestamp_str
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(ts)
        now = datetime.now(timezone.utc)
        diff = now - dt
        secs = int(diff.total_seconds())
        if secs < 60:
            return "just now"
        elif secs < 3600:
            return "{} min ago".format(secs // 60)
        elif secs < 86400:
            return "{} hours ago".format(secs // 3600)
        elif secs < 604800:
            return "{} days ago".format(secs // 86400)
        else:
            return timestamp_str[:10]
    except Exception:
        return timestamp_str[:16] if len(timestamp_str) > 16 else timestamp_str


ACTIVITY_PATTERNS = [
    r"what (?:has|did|is) (\w+) (?:done|fixed|completed|shipped|working on|built|changed)",
    r"what (\w+) (?:has been|was) (?:doing|working on|building)",
    r"show (?:me )?(\w+)'?s? (?:work|activity|changes|fixes)",
    r"(\w+)'?s? recent (?:work|activity|changes|fixes)",
    r"what (?:has|did) (\w+) (?:work|build|fix|ship|change)",
]


def detect_activity_intent(query):
    """Returns (user, mode) if query is activity-shaped, else None."""
    query_lower = query.lower().strip()
    for pattern in ACTIVITY_PATTERNS:
        m = re.search(pattern, query_lower)
        if m:
            user = m.group(1)
            # Skip if "user" is a common word, not a name
            if user in ("the", "this", "that", "my", "our", "your", "it", "we"):
                continue
            done_words = {"fixed", "completed", "shipped", "built", "done", "changed"}
            mode = "done" if any(w in query_lower for w in done_words) else "doing"
            return user, mode
    return None


def print_activity(result):
    """Print structured activity summary."""
    user = result.get("user", "?")
    period = result.get("period", "")
    mode = result.get("mode", "done")
    captures = result.get("capture_count", 0)

    if captures == 0:
        print("No activity found for {} ({}).".format(user, period))
        return

    # Summary first
    summary = result.get("summary", "")
    if summary:
        print("{}: {}".format(user, summary))
    else:
        print("{} — {} ({})".format(user, period, mode))
    print("")

    # Main focus
    main = result.get("main_focus")
    if main:
        feat = main.get("feature_name") or main.get("branch", "")
        dates = "{} — {}".format(main.get("first_seen", ""), main.get("last_seen", ""))
        print("  Main focus: {} ({} files, {})".format(feat, main["files_touched"], dates))
        top_files = main.get("top_files", [])
        if top_files:
            print("    {}".format(", ".join(top_files[:4])))
        print("")

    # Secondary work
    secondary = result.get("secondary", [])
    if secondary:
        label = "Also completed:" if mode == "done" else "Also working on:"
        print("  {}".format(label))
        for b in secondary:
            feat = b.get("feature_name") or b.get("branch", "")
            print("    {} ({} files)".format(feat, b["files_touched"]))
        print("")

    # Key files
    key_files = result.get("key_files", [])
    if key_files:
        print("  Most edited:")
        for f in key_files[:5]:
            print("    {} ({} edits)".format(f["path"], f["edits"]))
        print("")

    # Decisions
    decisions = result.get("key_decisions", [])
    if decisions:
        print("  Key decisions:")
        for d in decisions[:3]:
            print("    - {}".format(d[:120]))
        print("")

    # Tools as secondary detail
    if main:
        tools = main.get("tool_summary", "")
        if tools:
            print("  Tools: {}".format(tools))


def main():
    cfg = load_config()
    team_id = cfg.get("team_id")
    if not team_id:
        print("No team configured.")
        return

    query = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else ""
    if not query:
        print("Usage: /ask-team <question>")
        return

    # Detect activity-shaped queries
    intent = detect_activity_intent(query)
    if intent:
        user, mode = intent
        result = api_request(cfg, "POST", "/v1/activity", {
            "user": user,
            "time_window": "7d",
            "mode": mode,
        }, timeout=5.0)
        if result and result.get("capture_count", 0) > 0:
            print_activity(result)
            return
        # Fall through to generic search if no activity found

    # Generic search
    results = search_memories(cfg, query=query, team_id=team_id, limit=15)

    if results:
        results = [r for r in results
                   if r.get("metadata", {}).get("memory_type") != "code_chunk"]
        results = results[:10]

    if not results:
        print("No team knowledge found for: {}".format(query))
        return

    print("Team knowledge for: {}".format(query))
    print("")
    for r in results:
        meta = r.get("metadata", {})
        person = meta.get("created_by", "?")
        project = meta.get("project", "")
        memory = r.get("memory", "")[:200]
        ts = relative_time(meta.get("timestamp", r.get("created_at", "")))

        ts_str = " [{}]".format(ts) if ts else ""
        print("  [{}] ({}){}: {}".format(person, project, ts_str, memory))
    print("")


if __name__ == "__main__":
    main()
