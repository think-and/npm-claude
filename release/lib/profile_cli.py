#!/usr/bin/env python3
"""
/profile slash command backing — manage think& profiles.

Usage (from the shell or via commands/profile.md):
    python3 profile_cli.py                        # show active profile for cwd
    python3 profile_cli.py list                   # list profiles + bindings
    python3 profile_cli.py use <slug>             # bind current cwd to profile
    python3 profile_cli.py ignore                 # bind current cwd to __ignore__
    python3 profile_cli.py unbind                 # remove binding for current cwd
    python3 profile_cli.py add personal <name>    # add new personal profile
    python3 profile_cli.py add org <team> <name>  # create a new org + add
    python3 profile_cli.py add join <invite> <name>  # join existing org + add
    python3 profile_cli.py purge-local [slug]     # flip all bindings of a
                                                    # profile to __ignore__
                                                    # (local-only, instant)
    python3 profile_cli.py purge-remote <slug> --all
                                                  # nuke all memories for a
                                                    # profile on the server
    python3 profile_cli.py purge-remote <slug> --cwd <path>
                                                  # purge one cwd's worth

A "slug" is the short user-facing handle for a profile (e.g. "acme",
"personal", "side-game"). Bindings reference profile_id internally, so
slug renames are safe.
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bryonics_client import (
    IGNORE,
    canonical_cwd,
    resolve_profile,
    load_config_raw,
    save_config_raw,
    load_project_profiles,
    save_project_profiles,
    find_profile_by_id,
    find_profile_by_slug,
    is_legacy_config,
    migrate_legacy_config,
    create_personal_profile,
    create_team,
    join_team,
    purge_memories_remote,
    add_profile_to_config,
)


DEFAULT_API_URL = "http://64.23.139.13:8000"


# ── Output helpers ──

def _err(msg: str):
    print(msg, file=sys.stderr)


def _green(s: str) -> str:
    return s  # color later if we add it


# ── Migration prompt ──

def _maybe_migrate_legacy(raw: dict) -> dict:
    """If the config is legacy-flat, print a warning, migrate to profiles[],
    persist, and return the new dict. Offers an optional server-side purge."""
    if not is_legacy_config(raw):
        return raw

    _err("")
    _err("=" * 62)
    _err("think&: your config is being migrated to multi-profile format.")
    _err("")
    _err("Your existing identity becomes profile slug 'legacy'. Until you")
    _err("explicitly bind projects with  /profile use <slug>  no new memories")
    _err("will be uploaded. This is intentional — it prevents accidental")
    _err("cross-uploads.")
    _err("=" * 62)
    _err("")

    new_cfg = migrate_legacy_config(raw)
    save_config_raw(new_cfg)
    _err(f"  ✓ {len(new_cfg['profiles'])} profile(s) now in config.")
    _err("  Run  /profile use legacy  in each project you want to sync.")
    _err("")

    # Offer one-shot purge. Only prompt when we're attached to a TTY;
    # skip automatically otherwise (captures, session_sync, non-interactive).
    if sys.stdin.isatty():
        _err("Would you also like to DELETE all previously-uploaded memories")
        _err("from the legacy profile on the server? This cannot be undone")
        _err("and cannot recall content teammates may have already seen.")
        _err("")
        _err("Type `delete` to confirm, anything else to skip:")
        try:
            ans = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            ans = ""
        if ans == "delete":
            legacy = new_cfg["profiles"][0]
            resp = purge_memories_remote(
                new_cfg.get("api_url", DEFAULT_API_URL),
                legacy["api_key"],
                scope="all",
                confirm=legacy["slug"],
            )
            if "error" in resp:
                _err(f"  ✗ Purge failed: {resp['error']}")
            else:
                _err(f"  ✓ Purged {resp.get('deleted_count', 0)} memories.")
        else:
            _err("  Skipped server-side purge. Nothing was deleted.")
        _err("")
    return new_cfg


# ── Subcommand handlers ──

def cmd_show():
    raw = load_config_raw()
    raw = _maybe_migrate_legacy(raw)
    if not raw:
        print("No think& config. Run:  npx @thinkand/claude@latest install")
        return 0

    cwd = canonical_cwd()
    print(f"Current folder: {cwd}")

    env_override = os.environ.get("BRYONICS_PROFILE", "").strip()
    if env_override:
        print(f"  → BRYONICS_PROFILE={env_override!r} (env var override)")

    profile = resolve_profile()
    if profile is IGNORE:
        print("  → __ignore__   (no uploads from this folder)")
        return 0
    if profile is None:
        print("  → (unbound — no uploads, no recall)")
        print()
        print("  Bind it:     /profile use <slug>")
        print("  Silence it:  /profile ignore")
        return 0

    print(f"  → {profile.get('slug', '?')}   "
          f"({profile.get('kind', '?')}, team_id={profile.get('team_id', '?')})")
    return 0


def cmd_list():
    raw = load_config_raw()
    raw = _maybe_migrate_legacy(raw)
    if not raw:
        print("No think& config. Run:  npx @thinkand/claude@latest install")
        return 0

    profiles = raw.get("profiles", [])
    if not profiles:
        print("No profiles configured.")
        return 0

    bindings = load_project_profiles()

    # Reverse index: profile_id → list of bound cwds
    by_profile_id = {}
    ignore_cwds = []
    for cwd, entry in bindings.items():
        pid = entry.get("profile_id", "")
        if pid == "__ignore__":
            ignore_cwds.append(cwd)
        else:
            by_profile_id.setdefault(pid, []).append(cwd)

    print(f"Profiles ({len(profiles)}):")
    for p in profiles:
        print(f"  [{p.get('slug', '?')}]   kind={p.get('kind', '?')}   "
              f"team_id={p.get('team_id', '?')}")
        bound = by_profile_id.get(p.get("id"), [])
        if bound:
            for cwd in sorted(bound):
                print(f"      {cwd}")
        else:
            print("      (no bindings)")
    if ignore_cwds:
        print()
        print(f"Ignored ({len(ignore_cwds)}):")
        for cwd in sorted(ignore_cwds):
            print(f"      {cwd}")
    return 0


def cmd_use(argv):
    if not argv:
        _err("usage: /profile use <slug>")
        return 1
    slug = argv[0]

    raw = load_config_raw()
    raw = _maybe_migrate_legacy(raw)
    if not raw:
        _err("No think& config. Run:  npx @thinkand/claude@latest install")
        return 1

    profile = find_profile_by_slug(raw, slug)
    if profile is None:
        _err(f"No profile with slug {slug!r}. Available:")
        for p in raw.get("profiles", []):
            _err(f"    {p.get('slug', '?')}")
        return 1

    cwd = canonical_cwd()
    bindings = load_project_profiles()
    bindings[cwd] = {
        "profile_id": profile.get("id", ""),
        "bound_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    save_project_profiles(bindings)
    print(f"  ✓ Bound {cwd}")
    print(f"          → {slug} ({profile.get('kind', '?')})")
    return 0


def cmd_ignore():
    raw = load_config_raw()
    raw = _maybe_migrate_legacy(raw)
    cwd = canonical_cwd()
    bindings = load_project_profiles()
    bindings[cwd] = {
        "profile_id": "__ignore__",
        "bound_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    save_project_profiles(bindings)
    print(f"  ✓ {cwd} is now ignored. No uploads from here.")
    return 0


def cmd_unbind():
    cwd = canonical_cwd()
    bindings = load_project_profiles()
    if cwd not in bindings:
        print(f"  {cwd} was not bound.")
        return 0
    del bindings[cwd]
    save_project_profiles(bindings)
    print(f"  ✓ Removed binding for {cwd}")
    print("  Next hook fire will warn once about the unbound repo.")
    return 0


def cmd_add(argv):
    if not argv:
        _err("usage: /profile add personal <your-name>")
        _err("       /profile add org <team-name> <your-name>")
        _err("       /profile add join <invite-key> <your-name>")
        return 1

    raw = load_config_raw()
    raw = _maybe_migrate_legacy(raw)
    api_url = raw.get("api_url") or DEFAULT_API_URL

    mode = argv[0]
    if mode == "personal":
        if len(argv) < 2:
            _err("usage: /profile add personal <your-name>")
            return 1
        user_name = argv[1]
        resp = create_personal_profile(api_url, user_name)
        if "error" in resp:
            _err(f"  ✗ {resp['error']}")
            return 1
        profile = {
            "id": resp.get("profile_id", ""),
            "slug": _default_slug(resp.get("profile_id", "personal"), user_name),
            "name": f"Personal ({user_name})",
            "kind": "personal",
            "api_key": resp.get("api_key", ""),
            "user_id": resp.get("user_id", ""),
            "team_id": resp.get("team_id", ""),
        }
    elif mode == "org":
        if len(argv) < 3:
            _err("usage: /profile add org <team-name> <your-name>")
            return 1
        team_name, user_name = argv[1], argv[2]
        resp = create_team(api_url, team_name, user_name)
        if "error" in resp:
            _err(f"  ✗ {resp['error']}")
            return 1
        profile = {
            "id": resp.get("team_id", ""),
            "slug": resp.get("team_id", team_name),
            "name": team_name,
            "kind": "org",
            "api_key": resp.get("api_key", ""),
            "user_id": resp.get("user_id", ""),
            "team_id": resp.get("team_id", ""),
        }
        if resp.get("invite_key"):
            print(f"  Invite key (share with teammates): {resp['invite_key']}")
    elif mode == "join":
        if len(argv) < 3:
            _err("usage: /profile add join <invite-key> <your-name>")
            return 1
        invite, user_name = argv[1], argv[2]
        resp = join_team(api_url, invite, user_name)
        if "error" in resp:
            _err(f"  ✗ {resp['error']}")
            return 1
        profile = {
            "id": resp.get("team_id", ""),
            "slug": resp.get("team_id", "team"),
            "name": resp.get("team_id", "team"),
            "kind": "org",
            "api_key": resp.get("api_key", ""),
            "user_id": resp.get("user_id", ""),
            "team_id": resp.get("team_id", ""),
        }
    else:
        _err(f"Unknown mode {mode!r}. Use 'personal', 'org', or 'join'.")
        return 1

    if not raw.get("api_url"):
        raw["api_url"] = api_url
    raw = add_profile_to_config(raw, profile)
    save_config_raw(raw)
    print(f"  ✓ Added profile {profile['slug']!r} ({profile['kind']})")
    print(f"  Now run in each folder you want to sync:")
    print(f"    /profile use {profile['slug']}")
    return 0


def _default_slug(profile_id: str, user_name: str) -> str:
    """Pick a sensible default slug for a newly-created profile."""
    if profile_id.startswith("personal-"):
        return "personal"
    return profile_id or user_name.lower().replace(" ", "-")


def cmd_purge_local(argv):
    raw = load_config_raw()
    raw = _maybe_migrate_legacy(raw)
    if not raw.get("profiles"):
        _err("No profiles configured.")
        return 1

    target_slug = argv[0] if argv else None
    target_id = None
    if target_slug:
        p = find_profile_by_slug(raw, target_slug)
        if p is None:
            _err(f"No profile with slug {target_slug!r}")
            return 1
        target_id = p.get("id")

    bindings = load_project_profiles()
    flipped = 0
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ")
    for cwd, entry in bindings.items():
        pid = entry.get("profile_id", "")
        if pid == "__ignore__":
            continue
        if target_id and pid != target_id:
            continue
        bindings[cwd] = {"profile_id": "__ignore__", "bound_at": now}
        flipped += 1

    save_project_profiles(bindings)
    scope_desc = f"for profile {target_slug!r}" if target_slug else "(all profiles)"
    print(f"  ✓ Flipped {flipped} binding(s) to __ignore__ {scope_desc}")
    print("  No new uploads will happen from these folders.")
    print("  Existing memories on the server are unchanged.")
    print("  Run /profile purge-remote to delete them from the server.")
    return 0


def cmd_purge_remote(argv):
    if not argv:
        _err("usage: /profile purge-remote <slug> --all")
        _err("       /profile purge-remote <slug> --cwd <path>")
        return 1

    slug = argv[0]
    flags = argv[1:]
    scope = None
    cwd_prefix = None
    if "--all" in flags:
        scope = "all"
    elif "--cwd" in flags:
        i = flags.index("--cwd")
        if i + 1 >= len(flags):
            _err("usage: /profile purge-remote <slug> --cwd <path>")
            return 1
        scope = "cwd_prefix"
        cwd_prefix = canonical_cwd(flags[i + 1])
    else:
        _err("Specify --all or --cwd <path>")
        return 1

    raw = load_config_raw()
    raw = _maybe_migrate_legacy(raw)
    profile = find_profile_by_slug(raw, slug)
    if profile is None:
        _err(f"No profile with slug {slug!r}")
        return 1

    _err("")
    _err("⚠  Server-side purge — this cannot be undone.")
    _err("")
    _err(f"  Profile:     {profile.get('slug')} ({profile.get('kind')})")
    _err(f"  Scope:       {scope}")
    if cwd_prefix:
        _err(f"  cwd prefix:  {cwd_prefix}")
    _err("")
    _err(f"  Type the profile slug ({slug!r}) to confirm, or anything else to abort:")
    try:
        ans = input("> ").strip()
    except (EOFError, KeyboardInterrupt):
        ans = ""
    if ans != slug:
        _err("  Aborted. Nothing deleted.")
        return 0

    api_url = raw.get("api_url") or DEFAULT_API_URL
    resp = purge_memories_remote(
        api_url, profile["api_key"],
        scope=scope, cwd_prefix=cwd_prefix, confirm=slug,
    )
    if "error" in resp:
        _err(f"  ✗ Purge failed: {resp['error']}")
        return 1
    print(f"  ✓ Deleted {resp.get('deleted_count', 0)} memories.")
    print(f"    Tombstoned at {resp.get('tombstoned_at', '?')}")
    print()
    print("  Purge stops NEW leakage.")
    print("  Content any teammate already saw via /ask-team is on their")
    print("  machine — session transcripts, scrollback, API logs — and")
    print("  we cannot recall it. If the data was sensitive, rotate secrets")
    print("  and notify anyone who may have seen it.")
    return 0


# ── Entry point ──

def main(argv):
    if not argv:
        return cmd_show()

    sub = argv[0]
    rest = argv[1:]
    if sub == "list":
        return cmd_list()
    if sub == "use":
        return cmd_use(rest)
    if sub == "ignore":
        return cmd_ignore()
    if sub == "unbind":
        return cmd_unbind()
    if sub == "add":
        return cmd_add(rest)
    if sub == "purge-local":
        return cmd_purge_local(rest)
    if sub == "purge-remote":
        return cmd_purge_remote(rest)
    if sub in ("show", "status", "-h", "--help", "help"):
        if sub in ("-h", "--help", "help"):
            print(__doc__)
            return 0
        return cmd_show()

    _err(f"Unknown subcommand: {sub}")
    _err("Run  /profile help  for usage.")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
