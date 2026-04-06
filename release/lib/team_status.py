#!/usr/bin/env python3
"""Helper for /team command: show what your team has been working on."""

import json
import sys
import os
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bryonics_client import load_config, search_memories


def main():
    cfg = load_config()
    team_id = cfg.get("team_id")
    if not team_id:
        print("No team configured. Add team_id to ~/.bryonics/config.json")
        return

    results = search_memories(cfg, query="recent work changes edits",
                               team_id=team_id, limit=30)

    if not results:
        print("No team activity found.")
        return

    # Group by person
    by_person = defaultdict(list)
    for r in results:
        meta = r.get("metadata", {})
        person = meta.get("created_by", r.get("created_by", "unknown"))
        by_person[person].append({
            "memory": r.get("memory", "")[:150],
            "project": meta.get("project", ""),
            "branch": meta.get("branch", ""),
        })

    print("Team activity (team: {}):".format(team_id))
    print("")
    for person, items in by_person.items():
        print("  {}:".format(person))
        for item in items[:5]:
            loc = ""
            if item["project"]:
                loc = " ({}{})".format(item["project"],
                    "/" + item["branch"] if item["branch"] else "")
            print("    - {}{}".format(item["memory"][:120], loc))
        if len(items) > 5:
            print("    ... and {} more".format(len(items) - 5))
        print("")


if __name__ == "__main__":
    main()
