#!/usr/bin/env python3
"""Helper for /week-team command: team weekly summary."""

import sys
import os
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bryonics_client import load_config, search_memories


def main():
    cfg = load_config()
    team_id = cfg.get("team_id")
    if not team_id:
        print("No team configured.")
        return

    results = search_memories(cfg, query="this week coding work changes",
                               team_id=team_id, limit=50)

    if not results:
        print("No team activity this week.")
        return

    by_person = defaultdict(list)
    for r in results:
        meta = r.get("metadata", {})
        person = meta.get("created_by", "?")
        by_person[person].append(r.get("memory", "")[:150])

    print("Team Weekly Summary (team: {})".format(team_id))
    print("=" * 40)
    for person, items in by_person.items():
        print("")
        print("{}:".format(person))
        for item in items[:10]:
            print("  - {}".format(item))
    print("")


if __name__ == "__main__":
    main()
