Generate a code structure map of the current repository (Level 3: Files, Level 4: Functions/Classes).

Run `python3 ~/.bryonics/current/lib/architecture.py` and display the results.

This scans the repo deterministically, extracts symbols (functions, classes, exports) via AST/regex, and caches the result. No LLM call — pure code analysis.

Output: directory tree, key files with their symbols, cached at ~/.bryonics/architecture/{project}.json
