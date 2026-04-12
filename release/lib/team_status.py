#!/usr/bin/env python3
"""Helper for /team command: show what your team is doing.

Shows per-person activity summaries with timestamps.
Prefers recent live captures over old GitHub ingestion.
"""

import json
import sys
import os
import time
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bryonics_client import load_config, search_memories, api_request


def relative_time(timestamp_str):
    """Convert ISO timestamp to relative time like '2 hours ago'."""
    if not timestamp_str:
        return ""
    try:
        # Parse ISO format
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
            m = secs // 60
            return "{} min ago".format(m)
        elif secs < 86400:
            h = secs // 3600
            return "{} hour{} ago".format(h, "s" if h > 1 else "")
        elif secs < 604800:
            d = secs // 86400
            return "{} day{} ago".format(d, "s" if d > 1 else "")
        else:
            return timestamp_str[:10]
    except Exception:
        return timestamp_str[:16] if len(timestamp_str) > 16 else timestamp_str


def summarize_actions(items):
    """Summarize a person's actions into a short description."""
    files = set()
    tools = set()
    commands = []
    for item in items:
        meta = item.get("metadata", {})
        fp = meta.get("file_path", "")
        if fp:
            files.add(os.path.basename(fp))
        tool = meta.get("tool", "")
        if tool:
            tools.add(tool)
        cmd = meta.get("command", "")
        if cmd:
            commands.append(cmd[:60])

    parts = []
    if files:
        file_list = sorted(files)[:4]
        parts.append("edited {}".format(", ".join(file_list)))
    if commands:
        parts.append("ran {}".format(commands[0][:40]))

    return "; ".join(parts) if parts else ""


def main():
    cfg = load_config()
    team_id = cfg.get("team_id")
    if not team_id:
        print("No team configured. Add team_id to ~/.bryonics/config.json")
        return

    # Search for recent activity — broader query to catch live captures
    results = search_memories(cfg, query="recent edit write bash work changes",
                               team_id=team_id, limit=60)

    # Filter out code_chunks — raw code in team view is noise
    if results:
        results = [r for r in results
                   if r.get("metadata", {}).get("memory_type") != "code_chunk"]

    if not results:
        print("No team activity found.")
        return

    # Group by person, track timestamps
    by_person = defaultdict(list)
    for r in results:
        meta = r.get("metadata", {})
        person = meta.get("created_by", r.get("created_by", "unknown"))
        source = meta.get("source", "")

        # Skip GitHub ingestion for "what are they doing" view
        if source in ("github", "github_pr"):
            continue

        by_person[person].append({
            "memory": r.get("memory", "")[:150],
            "project": meta.get("project", ""),
            "branch": meta.get("branch", ""),
            "timestamp": meta.get("timestamp", r.get("created_at", "")),
            "metadata": meta,
        })

    if not by_person:
        # Fall back to all results including github
        for r in results:
            meta = r.get("metadata", {})
            person = meta.get("created_by", r.get("created_by", "unknown"))
            by_person[person].append({
                "memory": r.get("memory", "")[:150],
                "project": meta.get("project", ""),
                "branch": meta.get("branch", ""),
                "timestamp": meta.get("timestamp", r.get("created_at", "")),
                "metadata": meta,
            })

    # Get org team-status for real last_conversation_at timestamps
    org_status = {}
    try:
        result = api_request(cfg, "GET", "/v1/org/team-status", timeout=3.0)
        if result and "members" in result:
            for m in result["members"]:
                org_status[m["user_id"]] = {
                    "last_conversation_at": m.get("last_conversation_at"),
                    "uninstalled_at": m.get("uninstalled_at"),
                }
    except Exception:
        pass

    print("Team activity (team: {}):".format(team_id))
    print("")

    for person, items in by_person.items():
        # Sort by timestamp (most recent first)
        items.sort(key=lambda x: x.get("timestamp", ""), reverse=True)

        # Prefer org last_conversation_at over memory timestamps
        org_info = org_status.get(person, {})
        latest_ts = org_info.get("last_conversation_at") or (items[0].get("timestamp", "") if items else "")
        time_str = relative_time(latest_ts)

        # Per-person summary
        summary = summarize_actions(items[:5])

        # Header
        header = "  {}".format(person)
        uninstalled = org_info.get("uninstalled_at")
        if uninstalled:
            header += " — uninstalled {}".format(relative_time(uninstalled))
            if time_str:
                header += " (last seen {})".format(time_str)
        elif time_str:
            header += " — last seen {}".format(time_str)
        print(header)

        if summary:
            print("    Summary: {}".format(summary))

        # Recent actions (top 3)
        for item in items[:3]:
            ts = relative_time(item.get("timestamp", ""))
            loc = ""
            if item["project"]:
                loc = " ({}{})".format(item["project"],
                    "/" + item["branch"] if item["branch"] else "")
            ts_prefix = "[{}] ".format(ts) if ts else ""
            print("    - {}{}{}".format(ts_prefix, item["memory"][:100], loc))

        if len(items) > 3:
            print("    ... and {} more".format(len(items) - 3))
        print("")

    # Show org members with no captures yet
    if org_status:
        for user_id, status in org_status.items():
            if user_id not in by_person:
                ts = status.get("last_conversation_at")
                uninstalled = status.get("uninstalled_at")
                header = "  {}".format(user_id)
                if uninstalled:
                    header += " — uninstalled {}".format(relative_time(uninstalled))
                elif ts:
                    header += " — last seen {}".format(relative_time(ts))
                else:
                    header += " — joined, no activity yet"
                print(header)
                print("")


if __name__ == "__main__":
    main()
