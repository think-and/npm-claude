"""
Shared config loader + API client for Bryonics hooks and commands.

Config: ~/.bryonics/config.json
Session state: ~/.bryonics/sessions/{project_hash}.json
Project→profile bindings: ~/.bryonics/project-profiles.json
Rate-limited unbound-cwd warnings: ~/.bryonics/warnings/
"""

import json
import os
import hashlib
import subprocess
import sys
import time
import urllib.request
from typing import Optional, Dict, List, Any


CONFIG_PATH = os.path.expanduser("~/.bryonics/config.json")
SESSIONS_DIR = os.path.expanduser("~/.bryonics/sessions")
PROJECT_PROFILES_PATH = os.path.expanduser("~/.bryonics/project-profiles.json")
WARNINGS_DIR = os.path.expanduser("~/.bryonics/warnings")

# Sentinel value returned by resolve_profile() when cwd is explicitly ignored.
# `is` comparison only — do not serialize.
IGNORE = object()

# Rate-limit window for "unbound repo" warnings: one warning per cwd per 6 hours.
_UNBOUND_WARN_INTERVAL_S = 6 * 3600


def _load_config_raw() -> dict:
    """Load the raw config file. Returns {} if missing or malformed."""
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def load_config_raw() -> dict:
    """Public accessor for the raw config dict (used by profile_cli)."""
    return _load_config_raw()


def save_config_raw(cfg: dict):
    """Atomic write of the raw config file."""
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=2)
    os.rename(tmp, CONFIG_PATH)


def is_legacy_config(raw: dict) -> bool:
    """True if the config is still in the flat single-identity format."""
    if not raw:
        return False
    return "profiles" not in raw and "api_key" in raw


def load_config() -> dict:
    """Load config, backward-compatible for existing callers.

    Returns a flat-shaped dict with {api_url, api_key, user_id, team_id, ...}:

    - Legacy flat configs: returned verbatim.
    - Multi-profile configs: resolves the profile for the current cwd and
      returns its credentials merged with api_url. If the cwd is unbound,
      returns {} — existing callers that check `if not cfg: return` will
      exit silently, which is the intended "do nothing on unbound" behavior.
    - Ignored cwd (__ignore__): also returns {}.

    Hooks that care about the distinction between "unbound" and "ignored"
    should call resolve_profile() directly.
    """
    raw = _load_config_raw()
    if not raw:
        return {}
    if "profiles" not in raw:
        return raw  # legacy flat — return verbatim

    profile = resolve_profile()
    if profile is None or profile is IGNORE:
        return {}

    return {
        "api_url": raw.get("api_url", ""),
        "api_key": profile.get("api_key", ""),
        "user_id": profile.get("user_id", ""),
        "team_id": profile.get("team_id", ""),
        "profile_id": profile.get("id", ""),
        "profile_slug": profile.get("slug", ""),
        "profile_kind": profile.get("kind", "org"),
    }


def _git_root_of(path):
    """Get git root for a directory. Returns string or empty."""
    if not path or not os.path.isdir(path):
        return ""
    try:
        result = subprocess.run(
            ["git", "-C", path, "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=2
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except:
        return ""


def resolve_active_repo() -> str:
    """Resolve active repo from recent file activity, not just cwd.

    Priority:
    1. Git root of last_file (most recent edit)
    2. Git root of first recent_files entry
    3. Git root of cwd (fallback)
    """
    # Try last_file
    try:
        session_path = os.path.join(SESSIONS_DIR, _quick_project_hash() + ".json")
        if os.path.exists(session_path):
            with open(session_path) as f:
                session = json.load(f)

            last_file = session.get("last_file", "")
            if last_file and os.path.exists(os.path.dirname(last_file)):
                root = _git_root_of(os.path.dirname(last_file))
                if root:
                    return root

            for rf in session.get("recent_files", []):
                full = os.path.join(os.getcwd(), rf)
                if os.path.exists(os.path.dirname(full)):
                    root = _git_root_of(os.path.dirname(full))
                    if root:
                        return root
    except:
        pass

    # Fallback: cwd
    return _git_root_of(os.getcwd()) or os.getcwd()


def canonical_cwd(path: str = "") -> str:
    """Canonicalize a path to its git-root (if any), else realpath.

    Always returns an absolute path with no trailing slash. Used as the
    stable key for project→profile bindings and for purge-by-cwd on the
    server. Symlink-resolved so /tmp/link == /var/foo.
    """
    try:
        real = os.path.realpath(path) if path else os.path.realpath(os.getcwd())
    except Exception:
        real = path or os.getcwd()
    try:
        result = subprocess.run(
            ["git", "-C", real, "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=2
        )
        if result.returncode == 0:
            root = result.stdout.strip()
            if root:
                real = os.path.realpath(root)
    except Exception:
        pass
    return real.rstrip("/")


def load_project_profiles() -> dict:
    """Load the cwd→profile binding map."""
    try:
        with open(PROJECT_PROFILES_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_project_profiles(bindings: dict):
    """Atomic write of the cwd→profile binding map."""
    os.makedirs(os.path.dirname(PROJECT_PROFILES_PATH), exist_ok=True)
    tmp = PROJECT_PROFILES_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(bindings, f, indent=2)
    os.rename(tmp, PROJECT_PROFILES_PATH)


def find_profile_by_id(cfg: dict, profile_id: str) -> Optional[dict]:
    """Find a profile in cfg['profiles'] by its id. None if not found."""
    for p in cfg.get("profiles", []):
        if p.get("id") == profile_id:
            return p
    return None


def find_profile_by_slug(cfg: dict, slug: str) -> Optional[dict]:
    """Find a profile in cfg['profiles'] by its slug. None if not found."""
    for p in cfg.get("profiles", []):
        if p.get("slug") == slug:
            return p
    return None


def _warn_unbound_rate_limited(cwd: str, msg: str = None):
    """Print an unbound-cwd warning to stderr, at most once per 6h per cwd.

    Uses a marker file under ~/.bryonics/warnings/ keyed by hash(cwd). Silent
    after the first warn in any 6-hour window.
    """
    try:
        os.makedirs(WARNINGS_DIR, exist_ok=True)
    except OSError:
        return
    marker = os.path.join(
        WARNINGS_DIR, "unbound-" + hashlib.md5(cwd.encode()).hexdigest()[:12])
    try:
        last = os.path.getmtime(marker)
    except (FileNotFoundError, OSError):
        last = 0
    if time.time() - last < _UNBOUND_WARN_INTERVAL_S:
        return
    try:
        if os.path.exists(marker):
            os.utime(marker, None)
        else:
            open(marker, "w").close()
    except OSError:
        pass
    text = msg or (
        f"think&: unbound repo ({cwd}). "
        f"Run /profile use <slug> to sync, or /profile ignore to silence."
    )
    print(text, file=sys.stderr)


def resolve_profile(cwd: str = None) -> Optional[dict]:
    """Resolve which profile to use for the given cwd.

    Resolution order:
      1. BRYONICS_PROFILE env var (by slug) — wins if set.
      2. project-profiles.json binding for canonical_cwd(cwd).
      3. Unbound → return None. Caller decides to warn + skip.
      4. Bound to "__ignore__" → return IGNORE sentinel. Caller exits silently.

    Legacy flat configs: returned as a synthetic profile with
    {id: team_id, slug: team_id, kind: inferred, ...} so callers can treat
    the result uniformly.
    """
    raw = _load_config_raw()
    if not raw:
        return None

    # Legacy: synthesize a single profile from the flat config
    if "profiles" not in raw:
        team_id = raw.get("team_id", "")
        if not raw.get("api_key"):
            return None
        return {
            "id": team_id or "legacy",
            "slug": team_id or "legacy",
            "name": "Legacy (pre-migration)",
            "kind": "personal" if team_id.startswith("personal-") else "org",
            "api_key": raw.get("api_key", ""),
            "user_id": raw.get("user_id", ""),
            "team_id": team_id,
        }

    # Multi-profile path
    # 1. BRYONICS_PROFILE env var
    env_slug = os.environ.get("BRYONICS_PROFILE", "").strip()
    if env_slug:
        p = find_profile_by_slug(raw, env_slug)
        if p:
            return p
        _warn_unbound_rate_limited(
            canonical_cwd(),
            f"think&: BRYONICS_PROFILE={env_slug!r} does not match any profile.",
        )
        return None

    # 2. Binding lookup
    ccwd = canonical_cwd(cwd)
    bindings = load_project_profiles()
    entry = bindings.get(ccwd)

    if entry is None:
        return None

    profile_id = entry.get("profile_id", "")
    if profile_id == "__ignore__":
        return IGNORE

    profile = find_profile_by_id(raw, profile_id)
    if profile is None:
        # Stale binding — profile was removed. Treat as unbound.
        _warn_unbound_rate_limited(
            ccwd,
            f"think&: binding for {ccwd} points to missing profile "
            f"{profile_id!r}. Run /profile use <slug> to rebind.",
        )
        return None
    return profile


def _quick_project_hash() -> str:
    """Hash of cwd git root for finding session file. No resolve_active_repo to avoid recursion."""
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


def get_project_hash() -> str:
    """MD5 hash of the active repo root. Used for per-project session files."""
    root = resolve_active_repo()
    return hashlib.md5(root.encode()).hexdigest()[:12]


def get_project_name() -> str:
    """Current project name (basename of active repo root)."""
    return os.path.basename(resolve_active_repo())


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


# ── Profile management API helpers ──

def _api_post_no_auth(api_url: str, path: str, body: dict,
                      timeout: float = 10.0) -> Optional[dict]:
    """POST to an unauthenticated endpoint (onboarding only)."""
    url = (api_url or "").rstrip("/") + path
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        try:
            return {"error": json.loads(e.read()).get("detail", str(e))}
        except Exception:
            return {"error": str(e)}
    except Exception as e:
        return {"error": str(e)}


def create_personal_profile(api_url: str, user_name: str) -> dict:
    """Call POST /v1/profile/personal. Returns the server response dict."""
    return _api_post_no_auth(
        api_url, "/v1/profile/personal", {"user_name": user_name},
    ) or {"error": "no response"}


def create_team(api_url: str, team_name: str, user_name: str) -> dict:
    """Call POST /v1/org/create. Returns the server response dict."""
    return _api_post_no_auth(
        api_url, "/v1/org/create",
        {"team_name": team_name, "user_name": user_name},
    ) or {"error": "no response"}


def join_team(api_url: str, invite_key: str, user_name: str) -> dict:
    """Call POST /v1/org/join. Returns the server response dict."""
    return _api_post_no_auth(
        api_url, "/v1/org/join",
        {"invite_key": invite_key, "user_name": user_name},
    ) or {"error": "no response"}


def purge_memories_remote(api_url: str, api_key: str, scope: str,
                          cwd_prefix: str = None, confirm: str = "") -> dict:
    """Call POST /v1/memories/purge. Returns the server response dict.

    Raises no exceptions — returns {"error": ...} on any failure.
    """
    url = (api_url or "").rstrip("/") + "/v1/memories/purge"
    body = {"scope": scope, "confirm": confirm}
    if cwd_prefix:
        body["cwd_prefix"] = cwd_prefix
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + api_key,
        },
    )
    try:
        resp = urllib.request.urlopen(req, timeout=30.0)
        return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read()).get("detail", str(e))
        except Exception:
            detail = str(e)
        return {"error": detail, "status": e.code}
    except Exception as e:
        return {"error": str(e)}


def migrate_legacy_config(raw: dict) -> dict:
    """Convert a legacy flat config to the multi-profile schema.

    Wraps the existing api_key/user_id/team_id into profiles[0] with slug
    "legacy". Leaves project-profiles.json untouched (caller decides whether
    to seed bindings). Idempotent: returns the input unchanged if already
    in multi-profile form.
    """
    if not raw or "profiles" in raw:
        return raw
    if not raw.get("api_key"):
        return raw  # nothing to migrate

    team_id = raw.get("team_id", "")
    kind = "personal" if team_id.startswith("personal-") else "org"

    new_cfg = {
        "api_url": raw.get("api_url", ""),
        "profiles": [
            {
                "id": team_id or "legacy",
                "slug": "legacy",
                "name": "Legacy (pre-migration)",
                "kind": kind,
                "api_key": raw.get("api_key", ""),
                "user_id": raw.get("user_id", ""),
                "team_id": team_id,
            }
        ],
    }
    return new_cfg


def add_profile_to_config(raw: dict, new_profile: dict) -> dict:
    """Append a profile to cfg['profiles'], ensuring unique slug."""
    if "profiles" not in raw:
        raw["profiles"] = []
    existing_slugs = {p.get("slug") for p in raw["profiles"]}
    slug = new_profile.get("slug", "")
    base = slug
    i = 2
    while slug in existing_slugs:
        slug = f"{base}-{i}"
        i += 1
    new_profile["slug"] = slug
    raw["profiles"].append(new_profile)
    return raw
