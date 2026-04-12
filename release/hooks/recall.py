#!/usr/bin/env python3
"""
UserPromptSubmit hook: recall via server-side fusion.

Primary: POST /v1/recall → structured sections (architecture + activity + code)
Fallback: local architecture_lookup.py (server unreachable or 404)
"""

import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.expanduser("~/.bryonics/current/lib"))
from bryonics_client import (
    load_config, load_session, save_session,
    get_project_name, get_branch, api_request,
    resolve_active_repo,
)

MIN_PROMPT_LENGTH = 15
MAX_RECALLED_IDS = 20

RESUME_MARKERS = {
    "continue", "resume", "where was i", "pick up where",
    "what was i working on", "what were we doing", "carry on",
    "what's next", "what was happening", "what happened",
    "bring me up to speed", "catch me up",
}


def get_head_sha():
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=2
        ).stdout.strip()
    except Exception:
        return ""


def main():
    cfg = load_config()
    if not cfg:
        cfg = {}

    # Read hook input
    try:
        hook_input = json.load(sys.stdin)
        prompt = hook_input.get("prompt", hook_input.get("message", ""))
    except Exception:
        sys.exit(0)

    if not prompt:
        sys.exit(0)

    # Check resume markers BEFORE length guard — "resume" and "continue" are short
    prompt_lower = prompt.strip().lower()
    is_resume_prompt = any(marker in prompt_lower for marker in RESUME_MARKERS)

    if len(prompt) < MIN_PROMPT_LENGTH and not is_resume_prompt:
        sys.exit(0)

    # Gather local hints
    session = load_session()
    project = get_project_name()
    branch = get_branch()
    current_file = session.get("last_file", "")
    recent_files = session.get("recent_files", [])
    head_sha = get_head_sha()

    context_parts = []
    used_server = False

    # ── Session resume: first prompt or "continue"-type ──
    is_resume = is_resume_prompt or session.get("capture_count", 0) == 0

    if is_resume and cfg.get("api_url"):
        resume_result = api_request(cfg, "POST", "/v1/session-resume", {
            "project": project,
            "branch": branch,
            "head_sha": head_sha,
            "current_file": current_file,
        }, timeout=3.0)

        if isinstance(resume_result, dict) and resume_result.get("current_task"):
            resume_lines = []

            task = resume_result.get("current_task", "")
            if task:
                resume_lines.append("Current task: " + task[:200])

            why = resume_result.get("why_it_matters", "")
            if why:
                resume_lines.append("Why: " + why[:200])

            next_step = resume_result.get("next_step", "")
            if next_step:
                resume_lines.append("Next step: " + next_step[:200])

            files_to_open = resume_result.get("files_to_open", [])
            if files_to_open:
                resume_lines.append("Files to open: " + ", ".join(files_to_open[:8]))

            constraints = resume_result.get("known_constraints", [])
            if constraints:
                resume_lines.append("Constraints: " + "; ".join(constraints[:3]))

            open_qs = resume_result.get("open_questions", [])
            if open_qs:
                resume_lines.append("Open questions: " + "; ".join(open_qs[:3]))

            # Compact story
            story = resume_result.get("compact_story", [])
            if story:
                story_lines = []
                for ep in story:
                    label = ep.get("label", "")
                    outcome = ep.get("outcome", "")
                    suffix = " → " + outcome if outcome else ""
                    if ep.get("has_open"):
                        suffix += " [open]"
                    story_lines.append("  - " + label + suffix)
                resume_lines.append("Recent episodes:\n" + "\n".join(story_lines))

            if resume_lines:
                context_parts.append("Session resume:\n" + "\n".join(resume_lines))

    # ── Primary: server-side recall ──
    if cfg.get("api_url"):
        result = api_request(cfg, "POST", "/v1/recall", {
            "query": prompt[:500],
            "project": project,
            "branch": branch,
            "head_sha": head_sha,
            "current_file": current_file,
            "recent_files": recent_files,
        }, timeout=3.0)

        # Accept result if valid dict (fallback on None = network error, or 404)
        if isinstance(result, dict) and ("architecture" in result or "team_activity" in result):
            used_server = True

            # Format architecture section — render by level
            arch = result.get("architecture", [])
            if arch:
                lines = []
                for a in arch:
                    level = a.get("level", 0)

                    if level == 0:
                        # Feature: show name, summary, components, timeline, decisions
                        feat_name = a.get("feature_name", a.get("display_name", ""))
                        feat_type = a.get("feature_type", "feature")
                        summary = a.get("summary", "")
                        line = "Feature: {} ({})".format(feat_name, feat_type)
                        if summary:
                            line += "\n    {}".format(summary[:120])
                        comps = a.get("components", [])
                        if comps:
                            line += "\n    Components: {}".format(", ".join(comps))
                        contribs = a.get("contributors", [])
                        if contribs:
                            line += "\n    Contributors: {}".format(", ".join(contribs))
                        timeline = a.get("timeline", [])
                        for t in timeline[:3]:
                            line += "\n    {}: {}".format(t.get("date", ""), t.get("event", ""))
                        decisions = a.get("decisions", [])
                        if decisions:
                            line += "\n    Key decisions:"
                            for d in decisions[:2]:
                                line += "\n      - {}".format(d[:100])
                    elif level == 1:
                        # Component: show name, summary, child subsystems
                        display = a.get("display_name", "")
                        summary = a.get("summary", "")
                        child_subs = a.get("subsystems", [])
                        line = "Component: {}".format(display)
                        if summary:
                            line += "\n    {}".format(summary[:120])
                        if child_subs:
                            line += "\n    Subsystems: {}".format(", ".join(child_subs))
                        interfaces = a.get("interfaces", [])
                        if interfaces:
                            line += "\n    Interfaces: {}".format(", ".join(interfaces[:4]))
                    elif level == 2:
                        # Subsystem: show name, summary
                        display = a.get("display_name", a.get("subsystem", ""))
                        summary = a.get("summary", "")
                        line = "Subsystem: {}".format(display)
                        if summary:
                            line += "\n    {}".format(summary[:120])
                    else:
                        # Level 3/4: show file + symbols
                        path = a.get("path", "")
                        summary = a.get("summary", "")
                        line = "{} — {}".format(path, summary[:100])
                        syms = a.get("symbols", [])[:6]
                        if syms:
                            line += "\n    Symbols: {}".format(", ".join(
                                s if isinstance(s, str) else str(s) for s in syms))
                        sub = a.get("subsystem", "")
                        if sub:
                            line += "\n    Subsystem: {}".format(sub)

                    lines.append(line)
                context_parts.append("Architecture:\n" + "\n".join(lines))

            # Format team activity (with anti-spam via item IDs)
            activity = result.get("team_activity", [])
            last_recalled = set(session.get("last_recalled_ids", []))
            team_lines = []
            new_ids = []

            for a in activity:
                # Use server-provided ID if available, else hash memory text
                item_id = a.get("id", "")
                if not item_id:
                    item_id = str(hash(a.get("memory", "")))
                if item_id in last_recalled:
                    continue

                user = a.get("user", "?")
                proj = a.get("project", "")
                memory = a.get("memory", "")[:150]
                ts = a.get("timestamp", "")

                loc = " ({})".format(proj) if proj else ""
                team_lines.append("[{}]{}: {}".format(user, loc, memory))
                new_ids.append(item_id)

            if team_lines:
                context_parts.append("Team activity:\n" + "\n".join(team_lines[:3]))
                all_recalled = list(last_recalled | set(new_ids))
                session["last_recalled_ids"] = all_recalled[-MAX_RECALLED_IDS:]
                save_session(session)

            # Format code section — at most 1 short snippet
            code = result.get("code", [])
            if code:
                c = code[0]
                content = c.get("content", "")[:300]
                path = c.get("path", "")
                if content.strip():
                    context_parts.append("Related code ({}):\n{}".format(path, content))

    # ── Fallback: local architecture only (server unreachable or 404) ──
    if not used_server:
        try:
            from architecture_lookup import lookup
            git_root = ""
            try:
                git_root = resolve_active_repo()
            except Exception:
                pass

            snippets = lookup(
                project=project,
                current_file=current_file,
                recent_files=recent_files,
                prompt=prompt[:200],
                git_root=git_root,
            )
            if snippets:
                lines = []
                for s in snippets:
                    reason = s.get("reason", "")
                    tag = " [{}]".format(reason) if reason else ""
                    line = "{} — {}{}".format(s["path"], s.get("summary", ""), tag)
                    syms = s.get("symbols", [])[:6]
                    if syms:
                        line += "\n    Symbols: {}".format(", ".join(syms))
                    lines.append(line)
                context_parts.append("Local architecture:\n" + "\n".join(lines))
        except Exception:
            pass

    # ── Output ──
    if not context_parts:
        sys.exit(0)

    output = {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": "\n\n".join(context_parts),
        }
    }
    json.dump(output, sys.stdout)


if __name__ == "__main__":
    main()
