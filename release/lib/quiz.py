#!/usr/bin/env python3
"""
Helper for /quiz command: generate and take PR comprehension quizzes.

Usage:
    python3 quiz.py <pr_number> [--new] [--repo owner/repo]
    python3 quiz.py owner/repo#42 [--new]
"""

import json
import sys
import os
import time
import re

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bryonics_client import load_config, api_request


def parse_pr_ref(arg):
    """Parse PR reference: '42' or 'owner/repo#42'."""
    m = re.match(r'^([^#]+)#(\d+)$', arg)
    if m:
        return m.group(1), int(m.group(2))
    try:
        return None, int(arg)
    except ValueError:
        return None, None


def get_current_repo():
    """Try to detect repo from git remote."""
    import subprocess
    try:
        url = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=2
        ).stdout.strip()
        # Parse github.com/owner/repo from URL
        m = re.search(r'github\.com[/:]([^/]+/[^/.]+)', url)
        if m:
            return m.group(1).rstrip('.git')
    except Exception:
        pass
    return None


def get_git_diff(base="main"):
    """Get diff of current branch vs base."""
    import subprocess
    try:
        diff = subprocess.run(
            ["git", "diff", "{}...HEAD".format(base)],
            capture_output=True, text=True, timeout=10
        ).stdout
        if not diff.strip():
            # Try without merge-base (uncommitted changes)
            diff = subprocess.run(
                ["git", "diff", base],
                capture_output=True, text=True, timeout=10
            ).stdout
        return diff
    except Exception:
        return None


def get_git_branch():
    """Get current git branch name."""
    import subprocess
    try:
        return subprocess.run(
            ["git", "branch", "--show-current"],
            capture_output=True, text=True, timeout=2
        ).stdout.strip()
    except Exception:
        return None


def get_head_sha():
    """Get current HEAD SHA."""
    import subprocess
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=2
        ).stdout.strip()
    except Exception:
        return None


def main():
    cfg = load_config()
    if not cfg:
        print("No Bryonics config found. Run install.sh first.")
        return

    args = sys.argv[1:]
    if not args:
        print("Usage: /quiz <PR#> [--new]")
        print("       /quiz owner/repo#42 [--new]")
        print("       /quiz --branch [--new]")
        print("       /quiz --branch feat/auth [--new]")
        return

    force_new = "--new" in args
    args = [a for a in args if a != "--new"]

    # ── Branch mode ──
    if "--branch" in args:
        args = [a for a in args if a != "--branch"]
        branch = get_git_branch()
        if not branch:
            print("Not in a git repository.")
            return

        # Optional: specify base branch to diff against
        base = args[0] if args else "main"

        print("Getting diff for branch '{}' vs '{}'...".format(branch, base))
        diff = get_git_diff(base)
        if not diff or not diff.strip():
            print("No changes found on branch '{}' vs '{}'.".format(branch, base))
            return

        repo = get_current_repo() or "local"
        sha = get_head_sha() or "0000"

        # Count files changed
        file_count = len(set(re.findall(r'^\+\+\+ b/(.+)$', diff, re.MULTILINE)))
        title = "{}: {} files changed".format(branch, file_count)

        print("Branch: {} | SHA: {} | {} files | {} chars diff".format(
            branch, sha[:8], file_count, len(diff)))
        print("Generating quiz...")

        result = api_request(cfg, "POST", "/v1/quiz/generate", {
            "repo": repo,
            "branch": branch,
            "head_sha": sha,
            "branch_title": title,
            "diff_text": diff[:50000],  # cap at 50k chars
            "num_questions": 5,
            "force_new": force_new,
        }, timeout=15.0)

        if not result:
            print("Error: Could not reach quiz API.")
            return
        return handle_result(cfg, result, repo, 0)

    # ── PR mode ──
    repo_arg = None
    for a in args[:]:
        if a.startswith("--repo="):
            repo_arg = a.split("=", 1)[1]
            args.remove(a)
            break

    if not args:
        print("Missing PR number.")
        return

    repo, pr_number = parse_pr_ref(args[0])
    if repo is None:
        repo = repo_arg or get_current_repo()
    if not repo:
        print("Could not detect repo. Use: /quiz owner/repo#42 or /quiz --branch")
        return
    if pr_number is None:
        print("Invalid PR number: {}".format(args[0]))
        return

    print("Generating quiz for {}#{}...".format(repo, pr_number))
    result = api_request(cfg, "POST", "/v1/quiz/generate", {
        "repo": repo,
        "pr_number": pr_number,
        "num_questions": 5,
        "force_new": force_new,
    }, timeout=10.0)

    if not result:
        print("Error: Could not reach quiz API.")
        return
    handle_result(cfg, result, repo, pr_number)


LAST_QUIZ_PATH = os.path.expanduser("~/.bryonics/last_quiz.json")


def save_last_quiz_id(quiz_id):
    """Save last quiz ID so /quiz-submit can find it."""
    with open(LAST_QUIZ_PATH, "w") as f:
        json.dump({"quiz_id": quiz_id}, f)


def load_last_quiz_id():
    """Load last quiz ID."""
    try:
        with open(LAST_QUIZ_PATH) as f:
            return json.load(f).get("quiz_id")
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def handle_result(cfg, result, repo, pr_number):
    """Handle generate response: cache hit, poll job, or show error."""
    if result.get("status") == "failed":
        print("Error: {}".format(result.get("error", "unknown")))
        return

    quiz_id = result.get("quiz_id")
    job_id = result.get("job_id")

    if result.get("cached") and quiz_id:
        print("Found cached quiz.")
        show_quiz(cfg, quiz_id, repo, pr_number)
        return

    if job_id:
        print("Job {} started. Waiting for generation...".format(job_id))
        for i in range(120):
            time.sleep(2)
            status = api_request(cfg, "GET", "/v1/quiz/jobs/{}".format(job_id), timeout=5.0)
            if not status:
                continue
            s = status.get("status", "")
            if s == "completed":
                quiz_id = status.get("quiz_id")
                print("Quiz ready!")
                break
            elif s in ("failed", "cancelled"):
                print("Generation {}: {}".format(s, status.get("error", "")))
                return
            elif i % 5 == 0:
                print("  Status: {}...".format(s))
        else:
            print("Timed out waiting for quiz generation.")
            return

    if quiz_id:
        show_quiz(cfg, quiz_id, repo, pr_number)


def show_quiz(cfg, quiz_id, repo, pr_number):
    """Fetch and display quiz questions."""
    quiz = api_request(cfg, "GET", "/v1/quiz/{}".format(quiz_id), timeout=5.0)
    if not quiz:
        print("Error: Could not fetch quiz.")
        return

    # Save last quiz ID to session so /quiz-submit doesn't need it
    save_last_quiz_id(quiz_id)

    print("")
    print("=" * 60)
    print("PR #{}: {}".format(quiz.get("pr_number", pr_number), quiz.get("pr_title", "")))
    print("Repo: {}  |  SHA: {}".format(quiz.get("repo", repo), quiz.get("head_sha", "")[:8]))
    print("=" * 60)
    print("")

    questions = quiz.get("questions", [])
    for q in questions:
        print("Q{}: {}".format(q["id"], q["question"]))
        for letter, text in sorted(q.get("options", {}).items()):
            print("  {}: {}".format(letter, text))
        print("")

    print("=" * 60)
    print("")
    print("To submit, run:")
    print("  /quiz-submit 1:A 2:B 3:C 4:D 5:B")
    print("")
    print("Replace the letters with your answers.")


if __name__ == "__main__":
    main()
