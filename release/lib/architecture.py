#!/usr/bin/env python3
"""
Architecture Analysis — Level 3 (Files) + Level 4 (Functions/Classes).

Deterministic repo scan + symbol/import extraction + cached artifact.
Cache key: project + head_sha. Includes imports, imported-by, docstrings.

Usage:
    python3 architecture.py
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
    ".cs", ".php", ".sh", ".bash", ".zsh", ".sql", ".graphql",
    ".proto", ".sol", ".vy", ".move",
}
CONFIG_EXTENSIONS = {".yaml", ".yml", ".toml", ".json", ".md"}

CACHE_DIR = os.path.expanduser("~/.bryonics/architecture")


# ── Repo helpers ──

def get_git_root():
    """Get active repo root. Uses resolve_active_repo if available."""
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from bryonics_client import resolve_active_repo
        return resolve_active_repo()
    except ImportError:
        pass
    try:
        return subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=2
        ).stdout.strip()
    except Exception:
        return os.getcwd()


def get_head_sha():
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=2
        ).stdout.strip()
    except Exception:
        return "0000"


def scan_files(root):
    """Walk repo, collect code files. Returns list of (rel_path, file_type)."""
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        rel_dir = os.path.relpath(dirpath, root)
        if rel_dir == ".":
            rel_dir = ""

        for fname in sorted(filenames):
            ext = os.path.splitext(fname)[1].lower()
            if ext in SKIP_EXTENSIONS:
                continue

            file_type = "code"
            if ext in CONFIG_EXTENSIONS:
                file_type = "config"
            elif ext not in CODE_EXTENSIONS:
                continue

            rel_path = os.path.join(rel_dir, fname) if rel_dir else fname
            files.append((rel_path, file_type))

            if len(files) >= MAX_FILES:
                return files
    return files


# ── Python extraction (AST) ──

def extract_python(filepath):
    """Extract symbols, imports, docstring from Python file."""
    try:
        with open(filepath, "r") as f:
            source = f.read()
        tree = ast.parse(source)
    except (SyntaxError, UnicodeDecodeError, FileNotFoundError):
        return [], [], ""

    symbols = []
    raw_imports = []

    # Docstring
    docstring = ast.get_docstring(tree) or ""
    if docstring:
        docstring = docstring.split("\n")[0].strip()[:120]

    for node in ast.iter_child_nodes(tree):
        # Symbols
        if isinstance(node, ast.ClassDef):
            methods = [
                n.name for n in ast.iter_child_nodes(node)
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                and not n.name.startswith("_")
            ]
            symbols.append({
                "type": "class", "name": node.name,
                "line": node.lineno, "methods": methods[:10],
            })
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not node.name.startswith("_"):
                args = [a.arg for a in node.args.args if a.arg != "self"][:5]
                symbols.append({
                    "type": "function", "name": node.name,
                    "line": node.lineno, "args": args,
                })
        # Imports
        elif isinstance(node, ast.Import):
            for alias in node.names:
                raw_imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                raw_imports.append(node.module)

    return symbols, raw_imports, docstring


# ── JS/TS extraction (regex) ──

def extract_js(filepath):
    """Extract symbols, imports, docstring from JS/TS file."""
    try:
        with open(filepath, "r") as f:
            source = f.read()
    except (UnicodeDecodeError, FileNotFoundError):
        return [], [], ""

    symbols = []
    raw_imports = []

    # Imports
    for m in re.finditer(r"(?:import\s+.*?from\s+['\"]([^'\"]+)['\"]|require\s*\(\s*['\"]([^'\"]+)['\"]\s*\))", source):
        mod = m.group(1) or m.group(2)
        if mod:
            raw_imports.append(mod)

    # Exports/symbols
    for m in re.finditer(r'export\s+(?:default\s+)?(?:async\s+)?function\s+(\w+)', source):
        symbols.append({"type": "function", "name": m.group(1), "exported": True})
    for m in re.finditer(r'export\s+(?:default\s+)?class\s+(\w+)', source):
        symbols.append({"type": "class", "name": m.group(1), "exported": True})
    for m in re.finditer(r'module\.exports\s*=\s*\{([^}]+)\}', source):
        for name in re.findall(r'(\w+)', m.group(1))[:10]:
            symbols.append({"type": "export", "name": name})

    if not symbols:
        for m in re.finditer(r'^(?:async\s+)?function\s+(\w+)', source, re.MULTILINE):
            symbols.append({"type": "function", "name": m.group(1)})
        for m in re.finditer(r'^class\s+(\w+)', source, re.MULTILINE):
            symbols.append({"type": "class", "name": m.group(1)})

    # Docstring: first /* */ block
    docstring = ""
    doc_match = re.match(r'\s*/\*\*(.*?)\*/', source, re.DOTALL)
    if doc_match:
        docstring = " ".join(doc_match.group(1).split())[:120]

    return symbols[:15], raw_imports, docstring


# ── Go extraction (regex) ──

def extract_go(filepath):
    """Extract symbols, imports, docstring from Go file."""
    try:
        with open(filepath, "r") as f:
            source = f.read()
    except (UnicodeDecodeError, FileNotFoundError):
        return [], [], ""

    symbols = []
    raw_imports = []

    # Imports
    for m in re.finditer(r'import\s*\(\s*(.*?)\s*\)', source, re.DOTALL):
        for imp in re.findall(r'"([^"]+)"', m.group(1)):
            raw_imports.append(imp)
    for m in re.finditer(r'import\s+"([^"]+)"', source):
        raw_imports.append(m.group(1))

    # Symbols
    for m in re.finditer(r'^func\s+(?:\([^)]+\)\s+)?(\w+)', source, re.MULTILINE):
        symbols.append({"type": "function", "name": m.group(1)})
    for m in re.finditer(r'^type\s+(\w+)\s+struct', source, re.MULTILINE):
        symbols.append({"type": "struct", "name": m.group(1)})
    for m in re.finditer(r'^type\s+(\w+)\s+interface', source, re.MULTILINE):
        symbols.append({"type": "interface", "name": m.group(1)})

    # Docstring: first // comment block
    docstring = ""
    lines = source.split("\n")
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("//"):
            docstring = stripped.lstrip("/ ").strip()[:120]
            break
        elif stripped and not stripped.startswith("package"):
            break

    return symbols[:15], raw_imports, docstring


# ── Unified extraction ──

def extract_file(root, rel_path):
    """Extract symbols, imports, docstring based on extension."""
    filepath = os.path.join(root, rel_path)
    ext = os.path.splitext(rel_path)[1].lower()

    if ext == ".py":
        return extract_python(filepath)
    elif ext in (".js", ".ts", ".tsx", ".jsx", ".mjs"):
        return extract_js(filepath)
    elif ext == ".go":
        return extract_go(filepath)
    return [], [], ""


# ── Import resolution ──

def resolve_imports(file_entries, root):
    """Resolve raw imports to repo-relative file paths. Best-effort heuristic."""
    # Build a map of known files for resolution
    known_files = {}
    for entry in file_entries:
        path = entry["path"]
        # Map module-style names to paths: "api.auth" → "api/auth.py"
        base = os.path.splitext(path)[0]
        module_name = base.replace(os.sep, ".")
        known_files[module_name] = path
        known_files[path] = path
        # Also basename without extension
        known_files[os.path.basename(base)] = path

    for entry in file_entries:
        resolved = []
        for raw in entry.get("raw_imports", []):
            # Try direct module match
            if raw in known_files:
                resolved.append(known_files[raw])
            # Try with dots → slashes
            dotpath = raw.replace(".", os.sep)
            for ext in (".py", ".js", ".ts", ".go", ""):
                candidate = dotpath + ext
                if candidate in known_files:
                    resolved.append(known_files[candidate])
                    break
        entry["resolved_imports"] = sorted(set(resolved))

    # Build imported_by (reverse index)
    imported_by = {}
    for entry in file_entries:
        for dep in entry.get("resolved_imports", []):
            if dep not in imported_by:
                imported_by[dep] = []
            imported_by[dep].append(entry["path"])

    for entry in file_entries:
        entry["imported_by"] = sorted(imported_by.get(entry["path"], []))


# ── Summary generation ──

def generate_summary(entry):
    """Deterministic summary from docstring + symbols."""
    docstring = entry.get("docstring", "")
    if docstring:
        return docstring

    symbols = entry.get("symbols", [])
    if not symbols:
        return ""

    classes = [s["name"] for s in symbols if s.get("type") == "class"]
    funcs = [s["name"] for s in symbols if s.get("type") in ("function", "struct", "interface")]

    parts = []
    if classes:
        parts.append("{} class{}".format(len(classes), "es" if len(classes) > 1 else ""))
    if funcs:
        parts.append("{} function{}".format(len(funcs), "s" if len(funcs) > 1 else ""))

    names = [s["name"] for s in symbols[:4]]
    if parts:
        return "{}: {}".format(", ".join(parts), ", ".join(names))
    return ", ".join(names)


# ── Directory tree ──

def build_directory_tree(files):
    dirs = {}
    for rel_path, _ in files:
        parts = rel_path.split(os.sep)
        dir_name = os.sep.join(parts[:-1]) if len(parts) > 1 else "."
        if dir_name not in dirs:
            dirs[dir_name] = []
        dirs[dir_name].append(parts[-1])
    return dirs


# ── Cache ──

def get_cache_dir(project):
    d = os.path.join(CACHE_DIR, project)
    os.makedirs(d, exist_ok=True)
    return d


def save_artifact(project, head_sha, artifact):
    """Save artifact with atomic latest.json pointer."""
    d = get_cache_dir(project)
    artifact_path = os.path.join(d, "{}.json".format(head_sha))
    with open(artifact_path, "w") as f:
        json.dump(artifact, f, indent=2)

    # Atomic latest.json
    pointer = {"head_sha": head_sha, "path": artifact_path}
    tmp = os.path.join(d, ".tmp_latest_{}.json".format(os.getpid()))
    with open(tmp, "w") as f:
        json.dump(pointer, f)
    os.rename(tmp, os.path.join(d, "latest.json"))

    return artifact_path


# ── Main ──

def main():
    root = get_git_root()
    project = os.path.basename(root)
    head_sha = get_head_sha()

    print("Scanning {}... (HEAD: {})".format(project, head_sha))

    # Scan files
    files = scan_files(root)
    print("  {} files found".format(len(files)))

    # Extract symbols + imports + docstrings
    file_entries = []
    total_symbols = 0
    total_imports = 0

    for rel_path, file_type in files:
        symbols, raw_imports, docstring = extract_file(root, rel_path)
        total_symbols += len(symbols)
        total_imports += len(raw_imports)

        try:
            size = os.path.getsize(os.path.join(root, rel_path))
        except OSError:
            size = 0

        entry = {
            "path": rel_path,
            "size": size,
            "file_type": file_type,
            "language": os.path.splitext(rel_path)[1].lstrip("."),
            "docstring": docstring,
            "symbols": symbols,
            "raw_imports": raw_imports,
        }
        file_entries.append(entry)

    # Resolve imports + build imported_by
    resolve_imports(file_entries, root)

    # Generate deterministic summaries
    for entry in file_entries:
        entry["summary"] = generate_summary(entry)

    print("  {} symbols, {} imports extracted".format(total_symbols, total_imports))

    # Directory tree
    dir_tree = build_directory_tree(files)

    # Build artifact
    artifact = {
        "project": project,
        "root": root,
        "head_sha": head_sha,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "stats": {
            "files": len(files),
            "symbols": total_symbols,
            "imports": total_imports,
            "directories": len(dir_tree),
        },
        "directories": dir_tree,
        "files": file_entries,
    }

    # Save with head_sha cache key
    cache_path = save_artifact(project, head_sha, artifact)

    # Print summary
    print("")
    print("=" * 60)
    print("  {} — Architecture (Level 3-4)  HEAD: {}".format(project, head_sha))
    print("=" * 60)
    print("")

    print("  Directories:")
    for dir_name in sorted(dir_tree.keys()):
        print("    {}/  ({} files)".format(dir_name, len(dir_tree[dir_name])))

    print("")
    print("  Key files:")
    by_symbols = sorted(file_entries, key=lambda e: len(e.get("symbols", [])), reverse=True)
    for entry in by_symbols[:30]:
        if not entry.get("symbols"):
            continue
        summary = entry.get("summary", "")
        imports_from = entry.get("resolved_imports", [])[:3]
        imported_by = entry.get("imported_by", [])[:3]

        print("    {} — {}".format(entry["path"], summary))
        if imports_from:
            print("      Imports: {}".format(", ".join(imports_from)))
        if imported_by:
            print("      Imported by: {}".format(", ".join(imported_by)))

    print("")
    print("  Cached at: {}".format(cache_path))
    print("  {} files, {} symbols, {} imports, {} directories".format(
        len(files), total_symbols, total_imports, len(dir_tree)))


if __name__ == "__main__":
    main()
