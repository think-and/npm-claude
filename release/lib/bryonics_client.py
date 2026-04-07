"""
Shared config loader + API client for Bryonics hooks and commands.

Config: ~/.bryonics/config.json
Session state: ~/.bryonics/sessions/{project_hash}.json
"""

import json
import os
import hashlib
import subprocess
import time
import urllib.request
from typing import Optional, Dict, List, Any


CONFIG_PATH = os.path.expanduser("~/.bryonics/config.json")
SESSIONS_DIR = os.path.expanduser("~/.bryonics/sessions")


def load_config() -> dict:
    """Load ~/.bryonics/config.json. Returns empty dict if missing."""
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_config(cfg: dict):
    """Merge cfg into ~/.bryonics/config.json and write back."""
    existing = load_config()
    existing.update(cfg)
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        json.dump(existing, f, indent=2)


def get_project_hash() -> str:
    """MD5 hash of the git root or cwd. Used for per-project session files."""
    try:
        root = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=2
        ).stdout.strip()
    except:
        root = ""
    if not root:
        root = os.getcwd()
    return hashlib.md5(root.encode()).hexdigest()[:12]


def get_project_name() -> str:
    """Current project name (basename of git root or cwd)."""
    try:
        root = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=2
        ).stdout.strip()
        if root:
            return os.path.basename(root)
    except:
        pass
    return os.path.basename(os.getcwd())


def get_branch() -> str:
    """Current git branch."""
    try:
        return subprocess.run(
            ["git", "branch", "--show-current"],
            capture_output=True, text=True, timeout=2
        ).stdout.strip()
    except:
        return ""


def load_session() -> dict:
    """Load per-project session state."""
    os.makedirs(SESSIONS_DIR, exist_ok=True)
    path = os.path.join(SESSIONS_DIR, get_project_hash() + ".json")
    try:
        with open(path) as f:
            session = json.load(f)
        # Reset capture_count daily or on project/branch change
        project = get_project_name()
        branch = get_branch()
        today = time.strftime("%Y-%m-%d")
        if (session.get("date") != today or
            session.get("project") != project or
            session.get("branch") != branch):
            session["capture_count"] = 0
            session["date"] = today
            session["project"] = project
            session["branch"] = branch
        return session
    except (FileNotFoundError, json.JSONDecodeError):
        return {
            "project": get_project_name(),
            "branch": get_branch(),
            "date": time.strftime("%Y-%m-%d"),
            "capture_count": 0,
        }


def save_session(session: dict):
    """Save per-project session state."""
    os.makedirs(SESSIONS_DIR, exist_ok=True)
    path = os.path.join(SESSIONS_DIR, get_project_hash() + ".json")
    with open(path, "w") as f:
        json.dump(session, f)


def api_request(cfg: dict, method: str, path: str, body: dict = None,
                timeout: float = 2.0) -> Optional[Any]:
    """Make an API request to Bryonics. Returns parsed JSON or None."""
    url = cfg.get("api_url", "").rstrip("/") + path
    headers = {"Content-Type": "application/json"}
    api_key = cfg.get("api_key", "")
    if api_key:
        headers["Authorization"] = "Bearer " + api_key

    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers)

    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        return json.loads(resp.read())
    except:
        return None


def store_memory(cfg: dict, memory_text: str, team_id: str = None,
                  metadata: dict = None) -> Optional[dict]:
    """Store a memory to the Bryonics API."""
    body = {
        "messages": [{"role": "user", "content": memory_text}],
        "user_id": cfg.get("user_id", "unknown"),
    }
    if team_id:
        body["team_id"] = team_id
    if metadata:
        body["metadata"] = metadata
    return api_request(cfg, "POST", "/v1/memories", body)


def search_memories(cfg: dict, query: str, team_id: str = None,
                     exclude_user: str = None, filters: dict = None,
                     context: dict = None, limit: int = 5) -> list:
    """Search memories. Returns list of results or empty list."""
    body = {"query": query, "limit": limit}
    if team_id:
        body["team_id"] = team_id
    if exclude_user:
        body["exclude_user"] = exclude_user
    if filters:
        body["filters"] = filters
    if context:
        body["context"] = context
    # user_id still needed for personal searches
    body["user_id"] = cfg.get("user_id", "unknown")

    result = api_request(cfg, "POST", "/v1/memories/search", body, timeout=2.0)
    if isinstance(result, list):
        return result
    return []


def content_hash(text: str) -> str:
    """Hash for dedup."""
    return hashlib.md5(text[:200].encode()).hexdigest()[:12]
