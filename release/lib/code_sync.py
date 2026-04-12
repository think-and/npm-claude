#!/usr/bin/env python3
"""
Code Sync — upload repo contents to think& server memory.

Two modes:
1. Full sync (/code-sync): scans entire repo, uploads all files
2. Single file sync (from capture.py): uploads one changed file

Per-file: 1 file_summary memory + 1-N code_chunk memories.
Incremental via file_hash. Uses resolve_active_repo().
"""

import ast
import hashlib
import json
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bryonics_client import (
    load_config, api_request, resolve_active_repo,
    get_project_name, get_branch, canonical_cwd,
)

# Reuse scanner config from architecture.py
MAX_FILES = 200
MAX_CHUNKS_PER_FILE = 5
MAX_CHUNK_CHARS = 2000
UPLOAD_THROTTLE = 0.05  # 50ms between uploads

SYNC_STATE_PATH = os.path.expanduser("~/.bryonics/code_sync_state.json")

SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "env",
    ".tox", ".mypy_cache", ".pytest_cache", "dist", "build",
    ".next", ".nuxt", "coverage", ".cache", ".bryonics",
    "vendor", "target", ".idea", ".vscode",
}
SKIP_EXTENSIONS = {
    ".pyc", ".pyo", ".class", ".o", ".so", ".dylib", ".exe",
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".woff",
    ".ttf", ".eot", ".pdf", ".zip", ".tar", ".gz", ".lock",
    ".map", ".min.js", ".min.css",
}
CODE_EXTENSIONS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".rb",
    ".java", ".kt", ".swift", ".c", ".cpp", ".h", ".hpp",
    ".cs", ".php", ".sh", ".bash", ".zsh", ".sql", ".graphql",
    ".proto", ".sol", ".vy", ".move",
}


# ── Hashing ──

def file_hash(filepath):
    """SHA256 of file contents."""
    try:
        with open(filepath, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()[:16]
    except (FileNotFoundError, PermissionError):
        return None


def chunk_hash(text):
    """SHA256 of chunk text."""
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def get_head_sha():
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=2
        ).stdout.strip()
    except Exception:
        return "0000"


# ── Sync state ──

def load_sync_state():
    try:
        with open(SYNC_STATE_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_sync_state(state):
    os.makedirs(os.path.dirname(SYNC_STATE_PATH), exist_ok=True)
    with open(SYNC_STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)


# ── File scanning ──

def scan_code_files(root):
    """Walk repo, return list of repo-relative paths for code files."""
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        rel_dir = os.path.relpath(dirpath, root)
        if rel_dir == ".":
            rel_dir = ""

        for fname in sorted(filenames):
            ext = os.path.splitext(fname)[1].lower()
            if ext in SKIP_EXTENSIONS or ext not in CODE_EXTENSIONS:
                continue
            rel_path = os.path.join(rel_dir, fname) if rel_dir else fname
            files.append(rel_path)
            if len(files) >= MAX_FILES:
                return files
    return files


# ── Chunking ──

def chunk_python(source):
    """Chunk Python file at function/class boundaries. Returns list of strings."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return [source[:MAX_CHUNK_CHARS]]

    lines = source.split("\n")
    boundaries = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            boundaries.append(node.lineno - 1)  # 0-indexed

    if not boundaries:
        return [source[:MAX_CHUNK_CHARS]]

    chunks = []

    # Header chunk: everything before first function/class
    if boundaries[0] > 0:
        header = "\n".join(lines[:boundaries[0]]).strip()
        if header:
            chunks.append(header)

    # Function/class chunks
    for i, start in enumerate(boundaries):
        end = boundaries[i + 1] if i + 1 < len(boundaries) else len(lines)
        chunk_text = "\n".join(lines[start:end]).strip()
        if chunk_text:
            chunks.append(chunk_text[:MAX_CHUNK_CHARS])

    return chunks[:MAX_CHUNKS_PER_FILE]


def chunk_js(source):
    """Chunk JS/TS at function/export/class boundaries."""
    # Find boundaries
    boundary_pattern = re.compile(
        r'^(?:export\s+)?(?:default\s+)?(?:async\s+)?(?:function|class|const\s+\w+\s*=\s*(?:async\s+)?\()',
        re.MULTILINE,
    )
    lines = source.split("\n")
    boundaries = []
    for m in boundary_pattern.finditer(source):
        line_no = source[:m.start()].count("\n")
        boundaries.append(line_no)

    if not boundaries:
        return [source[:MAX_CHUNK_CHARS]]

    chunks = []

    # Header
    if boundaries[0] > 0:
        header = "\n".join(lines[:boundaries[0]]).strip()
        if header:
            chunks.append(header)

    for i, start in enumerate(boundaries):
        end = boundaries[i + 1] if i + 1 < len(boundaries) else len(lines)
        chunk_text = "\n".join(lines[start:end]).strip()
        if chunk_text:
            chunks.append(chunk_text[:MAX_CHUNK_CHARS])

    return chunks[:MAX_CHUNKS_PER_FILE]


def chunk_go(source):
    """Chunk Go at func/type boundaries."""
    boundary_pattern = re.compile(r'^(?:func|type)\s+', re.MULTILINE)
    lines = source.split("\n")
    boundaries = []
    for m in boundary_pattern.finditer(source):
        line_no = source[:m.start()].count("\n")
        boundaries.append(line_no)

    if not boundaries:
        return [source[:MAX_CHUNK_CHARS]]

    chunks = []

    if boundaries[0] > 0:
        header = "\n".join(lines[:boundaries[0]]).strip()
        if header:
            chunks.append(header)

    for i, start in enumerate(boundaries):
        end = boundaries[i + 1] if i + 1 < len(boundaries) else len(lines)
        chunk_text = "\n".join(lines[start:end]).strip()
        if chunk_text:
            chunks.append(chunk_text[:MAX_CHUNK_CHARS])

    return chunks[:MAX_CHUNKS_PER_FILE]


def chunk_file(filepath, language):
    """Chunk a file by language. Returns list of chunk strings."""
    try:
        with open(filepath, "r") as f:
            source = f.read()
    except (FileNotFoundError, UnicodeDecodeError, PermissionError):
        return []

    if not source.strip():
        return []

    # Small files: one chunk
    if len(source) < 500:
        return [source]

    if language == "py":
        return chunk_python(source)
    elif language in ("js", "ts", "tsx", "jsx", "mjs"):
        return chunk_js(source)
    elif language == "go":
        return chunk_go(source)
    else:
        # Generic: just header (first 2000 chars)
        return [source[:MAX_CHUNK_CHARS]]


# ── Extraction (reused from architecture.py) ──

def extract_python_meta(filepath):
    """Extract symbols, imports, docstring from Python."""
    try:
        with open(filepath, "r") as f:
            source = f.read()
        tree = ast.parse(source)
    except (SyntaxError, UnicodeDecodeError, FileNotFoundError):
        return [], [], ""

    symbols = []
    raw_imports = []
    docstring = ast.get_docstring(tree) or ""
    if docstring:
        docstring = docstring.split("\n")[0].strip()[:120]

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ClassDef):
            symbols.append(node.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not node.name.startswith("_"):
                symbols.append(node.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                raw_imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                raw_imports.append(node.module)

    return symbols[:15], raw_imports, docstring


def extract_js_meta(filepath):
    """Extract symbols, imports from JS/TS."""
    try:
        with open(filepath, "r") as f:
            source = f.read()
    except (UnicodeDecodeError, FileNotFoundError):
        return [], [], ""

    symbols = []
    raw_imports = []

    for m in re.finditer(r"(?:import\s+.*?from\s+['\"]([^'\"]+)['\"]|require\s*\(\s*['\"]([^'\"]+)['\"]\s*\))", source):
        mod = m.group(1) or m.group(2)
        if mod:
            raw_imports.append(mod)

    for m in re.finditer(r'export\s+(?:default\s+)?(?:async\s+)?function\s+(\w+)', source):
        symbols.append(m.group(1))
    for m in re.finditer(r'export\s+(?:default\s+)?class\s+(\w+)', source):
        symbols.append(m.group(1))

    docstring = ""
    doc_match = re.match(r'\s*/\*\*(.*?)\*/', source, re.DOTALL)
    if doc_match:
        docstring = " ".join(doc_match.group(1).split())[:120]

    return symbols[:15], raw_imports, docstring


def extract_meta(filepath, language):
    """Extract symbols, imports, docstring."""
    if language == "py":
        return extract_python_meta(filepath)
    elif language in ("js", "ts", "tsx", "jsx", "mjs"):
        return extract_js_meta(filepath)
    return [], [], ""


# ── Upload ──

def upload_memory(cfg, text, metadata):
    """Upload a single memory to the server."""
    body = {
        "messages": [{"role": "user", "content": text}],
        "user_id": cfg.get("user_id", "unknown"),
        "metadata": metadata,
    }
    team_id = cfg.get("team_id")
    if team_id:
        body["team_id"] = team_id
    return api_request(cfg, "POST", "/v1/memories", body, timeout=5.0)


def sync_file(cfg, root, rel_path, repo, branch, head_sha):
    """Sync one file: upload file_summary + code_chunks. Returns (summaries, chunks) count."""
    filepath = os.path.join(root, rel_path)
    language = os.path.splitext(rel_path)[1].lstrip(".").lower()
    fhash = file_hash(filepath)
    if not fhash:
        return 0, 0

    # File stats
    try:
        size = os.path.getsize(filepath)
        with open(filepath, "r") as f:
            line_count = sum(1 for _ in f)
    except (FileNotFoundError, UnicodeDecodeError):
        return 0, 0

    # Extract metadata
    symbols, raw_imports, docstring = extract_meta(filepath, language)

    # Base metadata — every field present, no optionals.
    # cwd is the canonical git-root of the synced repo; required for
    # precise /v1/memories/purge by cwd_prefix.
    base_meta = {
        "source": "code_snapshot",
        "repo": repo,
        "branch": branch,
        "head_sha": head_sha,
        "file_path": rel_path,
        "file_hash": fhash,
        "language": language,
        "cwd": canonical_cwd(root),
    }

    # 1. Upload file_summary
    summary_text = "{} — {}".format(rel_path, docstring or "")
    if symbols:
        summary_text += "\nSymbols: {}".format(", ".join(symbols[:10]))
    if raw_imports:
        summary_text += "\nImports: {}".format(", ".join(raw_imports[:10]))
    summary_text += "\nLanguage: {} | {}B | {} lines".format(language, size, line_count)

    summary_meta = dict(base_meta)
    summary_meta.update({
        "memory_type": "file_summary",
        "chunk_index": -1,
        "chunk_hash": "",
        "symbols": symbols,
        "raw_imports": raw_imports,
        "line_count": line_count,
        "size": size,
    })
    upload_memory(cfg, summary_text, summary_meta)
    time.sleep(UPLOAD_THROTTLE)

    # 2. Upload code_chunks
    chunks = chunk_file(filepath, language)
    chunk_count = 0
    for i, chunk_text in enumerate(chunks):
        chash = chunk_hash(chunk_text)
        chunk_meta = dict(base_meta)
        chunk_meta.update({
            "memory_type": "code_chunk",
            "chunk_index": i,
            "chunk_hash": chash,
        })

        header = "# {} [chunk {}/{}]\n\n".format(rel_path, i + 1, len(chunks))
        upload_memory(cfg, header + chunk_text, chunk_meta)
        chunk_count += 1
        time.sleep(UPLOAD_THROTTLE)

    return 1, chunk_count


# ── Public API ──

def sync_single_file(cfg, filepath, project, branch):
    """Sync a single file (called from capture.py on Edit/Write)."""
    root = resolve_active_repo()
    if not root:
        return

    # Canonicalize to repo-relative
    if filepath.startswith(root):
        rel_path = filepath[len(root):].lstrip("/")
    else:
        rel_path = filepath

    # Check if file is syncable
    ext = os.path.splitext(rel_path)[1].lower()
    if ext not in CODE_EXTENSIONS:
        return

    # Check hash — skip if unchanged
    fhash = file_hash(os.path.join(root, rel_path))
    if not fhash:
        return

    state = load_sync_state()
    project_state = state.get(project, {})
    files_state = project_state.get("files", {})

    if files_state.get(rel_path) == fhash:
        return  # unchanged

    head_sha = get_head_sha()
    repo = project

    summaries, chunks = sync_file(cfg, root, rel_path, repo, branch, head_sha)

    if summaries > 0:
        # Update state
        files_state[rel_path] = fhash
        project_state["files"] = files_state
        project_state["last_sync"] = time.strftime("%Y-%m-%dT%H:%M:%SZ")
        state[project] = project_state
        save_sync_state(state)


def full_sync():
    """Full repo sync. Called by /code-sync command."""
    cfg = load_config()
    if not cfg:
        print("No config found. Run: npx @thinkand/claude@latest install")
        return

    root = resolve_active_repo()
    project = os.path.basename(root)
    branch = get_branch()
    head_sha = get_head_sha()

    print("Code sync: {} (HEAD: {})".format(project, head_sha))

    # Scan files
    files = scan_code_files(root)
    print("  {} code files found".format(len(files)))

    # Load sync state
    state = load_sync_state()
    project_state = state.get(project, {})
    files_state = project_state.get("files", {})

    # Find changed files
    changed = []
    for rel_path in files:
        fhash = file_hash(os.path.join(root, rel_path))
        if not fhash:
            continue
        if files_state.get(rel_path) == fhash:
            continue
        changed.append((rel_path, fhash))

    if not changed:
        print("  No files changed since last sync.")
        return

    print("  {} files changed, syncing...".format(len(changed)))

    total_summaries = 0
    total_chunks = 0

    for rel_path, fhash in changed:
        summaries, chunks = sync_file(cfg, root, rel_path, project, branch, head_sha)
        total_summaries += summaries
        total_chunks += chunks
        files_state[rel_path] = fhash

        if (total_summaries + total_chunks) % 20 == 0:
            print("  ... {} files, {} chunks uploaded".format(total_summaries, total_chunks))

    # Save state
    project_state["files"] = files_state
    project_state["last_sync"] = time.strftime("%Y-%m-%dT%H:%M:%SZ")
    project_state["head_sha"] = head_sha
    state[project] = project_state
    save_sync_state(state)

    print("  Done! {} file summaries + {} code chunks uploaded.".format(
        total_summaries, total_chunks))


if __name__ == "__main__":
    full_sync()
