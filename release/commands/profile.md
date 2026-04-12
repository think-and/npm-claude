---
description: Manage think& profiles (personal vs org, per-folder binding, purge)
---

Run `python3 ~/.bryonics/current/lib/profile_cli.py $ARGUMENTS` and display the output verbatim.

Examples the user might pass:
  /profile                      — show the active profile for the current folder
  /profile list                 — list all profiles and their folder bindings
  /profile use <slug>           — bind the current folder to a profile
  /profile ignore               — mark the current folder as never-upload
  /profile unbind               — remove the current folder's binding
  /profile add personal <name>  — create a new personal profile
  /profile add org <team> <name>  — create a new org
  /profile add join <invite> <name>  — join an existing org with an invite key
  /profile purge-local [slug]   — locally flip all bindings of a profile to ignore
  /profile purge-remote <slug> --all        — delete all memories for a profile on the server
  /profile purge-remote <slug> --cwd <path> — delete memories from one folder

If `$ARGUMENTS` is empty, the command shows the active profile for the current folder.
