#!/usr/bin/env python3
"""Helper for /ask-team command: search team knowledge."""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bryonics_client import load_config, search_memories


def main():
    cfg = load_config()
    team_id = cfg.get("team_id")
    if not team_id:
        print("No team configured.")
        return

    query = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else ""
    if not query:
        print("Usage: team_search.py <question>")
        return

    results = search_memories(cfg, query=query, team_id=team_id, limit=10)

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
        score = r.get("score", 0)
        print("  [{}] ({}): {}".format(person, project, memory))
    print("")


if __name__ == "__main__":
    main()
