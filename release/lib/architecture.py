#!/usr/bin/env python3
"""
Architecture Analysis — Level 3 (Files) + Level 4 (Functions/Classes).

Deterministic repo scan + symbol extraction + optional Claude summarization.
Output: cached artifact at .bryonics/architecture/{project}.json

Usage:
    python3 architecture.py [--summarize]
"""

import ast
import json
import os
import re
import subprocess
import sys
import time

# ── Config ──

MAX_FILES = 200
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
    ".cs", ".php", ".sh", ".bash", ".zsh", ".yaml", ".yml",
    ".toml", ".json", ".md", ".sql", ".graphql", ".proto",
    ".sol", ".vy", ".move",
}

CACHE_DIR = os.path.expanduser("~/.bryonics/architecture")


# ── Repo scan ──

def get_git_root():
    try:
        return subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=2
        ).stdout.strip()
    except Exception:
        return os.getcwd()


def scan_files(root):
    """Walk repo, collect code files. Returns list of relative paths."""
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        # Skip ignored dirs
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]

        rel_dir = os.path.relpath(dirpath, root)
        if rel_dir == ".":
            rel_dir = ""

        for fname in sorted(filenames):
            ext = os.path.splitext(fname)[1].lower()
            if ext in SKIP_EXTENSIONS:
                continue
            if ext not in CODE_EXTENSIONS:
                continue

            rel_path = os.path.join(rel_dir, fname) if rel_dir else fname
            files.append(rel_path)

            if len(files) >= MAX_FILES:
                return files
    return files


# ── Symbol extraction ──

def extract_python_symbols(filepath):
    """Extract classes, functions, and key variables from Python files."""
    try:
        with open(filepath, "r") as f:
            source = f.read()
        tree = ast.parse(source)
    except (SyntaxError, UnicodeDecodeError, FileNotFoundError):
        return []

    symbols = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ClassDef):
            methods = [
                n.name for n in ast.iter_child_nodes(node)
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                and not n.name.startswith("_")
            ]
            symbols.append({
                "type": "class",
                "name": node.name,
                "line": node.lineno,
                "methods": methods[:10],
            })
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not node.name.startswith("_"):
                args = [a.arg for a in node.args.args if a.arg != "self"][:5]
                symbols.append({
                    "type": "function",
                    "name": node.name,
                    "line": node.lineno,
                    "args": args,
                })
    return symbols


def extract_js_symbols(filepath):
    """Extract exports, functions, classes from JS/TS files using regex."""
    try:
        with open(filepath, "r") as f:
            source = f.read()
    except (UnicodeDecodeError, FileNotFoundError):
        return []

    symbols = []

    # Export patterns
    for m in re.finditer(r'export\s+(?:default\s+)?(?:async\s+)?function\s+(\w+)', source):
        symbols.append({"type": "function", "name": m.group(1), "exported": True})
    for m in re.finditer(r'export\s+(?:default\s+)?class\s+(\w+)', source):
        symbols.append({"type": "class", "name": m.group(1), "exported": True})
    for m in re.finditer(r'module\.exports\s*=\s*\{([^}]+)\}', source):
        names = re.findall(r'(\w+)', m.group(1))
        for name in names[:10]:
            symbols.append({"type": "export", "name": name})

    # Top-level function/class (not exported)
    if not symbols:
        for m in re.finditer(r'^(?:async\s+)?function\s+(\w+)', source, re.MULTILINE):
            symbols.append({"type": "function", "name": m.group(1)})
        for m in re.finditer(r'^class\s+(\w+)', source, re.MULTILINE):
            symbols.append({"type": "class", "name": m.group(1)})

    return symbols[:15]


def extract_go_symbols(filepath):
    """Extract Go functions, types, structs using regex."""
    try:
        with open(filepath, "r") as f:
            source = f.read()
    except (UnicodeDecodeError, FileNotFoundError):
        return []

    symbols = []
    for m in re.finditer(r'^func\s+(?:\([^)]+\)\s+)?(\w+)', source, re.MULTILINE):
        symbols.append({"type": "function", "name": m.group(1)})
    for m in re.finditer(r'^type\s+(\w+)\s+struct', source, re.MULTILINE):
        symbols.append({"type": "struct", "name": m.group(1)})
    for m in re.finditer(r'^type\s+(\w+)\s+interface', source, re.MULTILINE):
        symbols.append({"type": "interface", "name": m.group(1)})
    return symbols[:15]


def extract_symbols(root, rel_path):
    """Extract symbols based on file extension."""
    filepath = os.path.join(root, rel_path)
    ext = os.path.splitext(rel_path)[1].lower()

    if ext == ".py":
        return extract_python_symbols(filepath)
    elif ext in (".js", ".ts", ".tsx", ".jsx", ".mjs"):
        return extract_js_symbols(filepath)
    elif ext == ".go":
        return extract_go_symbols(filepath)
    return []


# ── Directory structure ──

def build_directory_tree(files):
    """Group files by directory, compute stats."""
    dirs = {}
    for f in files:
        parts = f.split(os.sep)
        if len(parts) > 1:
            dir_name = os.sep.join(parts[:-1])
        else:
            dir_name = "."
        if dir_name not in dirs:
            dirs[dir_name] = []
        dirs[dir_name].append(parts[-1])
    return dirs


# ── Main ──

def main():
    root = get_git_root()
    project = os.path.basename(root)

    print("Scanning {}...".format(project))

    # Level 3: File map
    files = scan_files(root)
    print("  {} code files found".format(len(files)))

    # Level 4: Symbol extraction
    file_entries = []
    total_symbols = 0
    for rel_path in files:
        symbols = extract_symbols(root, rel_path)
        total_symbols += len(symbols)

        # Get file size
        try:
            size = os.path.getsize(os.path.join(root, rel_path))
        except OSError:
            size = 0

        file_entries.append({
            "path": rel_path,
            "size": size,
            "symbols": symbols,
        })

    print("  {} symbols extracted".format(total_symbols))

    # Directory tree
    dir_tree = build_directory_tree(files)

    # Build artifact
    artifact = {
        "project": project,
        "root": root,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "stats": {
            "files": len(files),
            "symbols": total_symbols,
            "directories": len(dir_tree),
        },
        "directories": dir_tree,
        "files": file_entries,
    }

    # Cache
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache_path = os.path.join(CACHE_DIR, "{}.json".format(project))
    with open(cache_path, "w") as f:
        json.dump(artifact, f, indent=2)

    # Print summary
    print("")
    print("=" * 60)
    print("  {} — Architecture (Level 3-4)".format(project))
    print("=" * 60)
    print("")

    # Print directory structure with file counts
    print("  Directories:")
    for dir_name in sorted(dir_tree.keys()):
        file_list = dir_tree[dir_name]
        print("    {}/  ({} files)".format(dir_name, len(file_list)))

    print("")

    # Print files with symbols
    print("  Key files:")
    # Sort by symbol count (most interesting first)
    by_symbols = sorted(file_entries, key=lambda e: len(e["symbols"]), reverse=True)
    for entry in by_symbols[:30]:
        if not entry["symbols"]:
            continue
        syms = entry["symbols"]
        sym_names = [s["name"] for s in syms[:5]]
        sym_types = set(s["type"] for s in syms)
        type_str = "/".join(sorted(sym_types))
        print("    {} ({}: {})".format(
            entry["path"],
            type_str,
            ", ".join(sym_names) + ("..." if len(syms) > 5 else ""),
        ))

    print("")
    print("  Cached at: {}".format(cache_path))
    print("  {} files, {} symbols, {} directories".format(
        len(files), total_symbols, len(dir_tree)))


if __name__ == "__main__":
    main()
