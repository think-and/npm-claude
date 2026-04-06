"use strict";

/**
 * Build script: copies release files from bryonics-claude source
 * and generates manifest.json with checksums.
 *
 * Usage: node src/build-manifest.js [source_dir]
 */

const fs = require("fs");
const path = require("path");
const crypto = require("crypto");

const sourceDir = process.argv[2] || path.join(__dirname, "..", "..");
const releaseDir = path.join(__dirname, "..", "release");
const pkg = JSON.parse(fs.readFileSync(path.join(__dirname, "..", "package.json"), "utf8"));

// Files to include in release
const filesToCopy = {
  "hooks/recall.py": path.join(sourceDir, "hooks", "recall.py"),
  "hooks/capture.py": path.join(sourceDir, "hooks", "capture.py"),
  "lib/bryonics_client.py": path.join(sourceDir, "lib", "bryonics_client.py"),
  "lib/quiz.py": path.join(sourceDir, "lib", "quiz.py"),
  "lib/quiz_submit.py": path.join(sourceDir, "lib", "quiz_submit.py"),
  "lib/session_sync.py": path.join(sourceDir, "lib", "session_sync.py"),
  "lib/team_search.py": path.join(sourceDir, "lib", "team_search.py"),
  "lib/team_status.py": path.join(sourceDir, "lib", "team_status.py"),
  "lib/week_summary.py": path.join(sourceDir, "lib", "week_summary.py"),
  "commands/quiz.md": path.join(sourceDir, "commands", "quiz.md"),
  "commands/quiz-submit.md": path.join(sourceDir, "commands", "quiz-submit.md"),
  "commands/quiz-status.md": path.join(sourceDir, "commands", "quiz-status.md"),
  "commands/quiz-cancel.md": path.join(sourceDir, "commands", "quiz-cancel.md"),
  "commands/quiz-open.md": path.join(sourceDir, "commands", "quiz-open.md"),
  "commands/sync.md": path.join(sourceDir, "commands", "sync.md"),
  "commands/team.md": path.join(sourceDir, "commands", "team.md"),
  "commands/ask-team.md": path.join(sourceDir, "commands", "ask-team.md"),
  "commands/week-team.md": path.join(sourceDir, "commands", "week-team.md"),
};

// Clean and recreate release dir
if (fs.existsSync(releaseDir)) {
  fs.rmSync(releaseDir, { recursive: true });
}

// Copy files and compute checksums
const manifest = {
  version: pkg.version,
  released_at: new Date().toISOString(),
  files: {},
  min_python: "3.8",
  min_claude_code: "2.0.0",
};

for (const [relPath, srcPath] of Object.entries(filesToCopy)) {
  const destPath = path.join(releaseDir, relPath);
  fs.mkdirSync(path.dirname(destPath), { recursive: true });

  if (!fs.existsSync(srcPath)) {
    console.log(`  Warning: source not found: ${srcPath}`);
    continue;
  }

  fs.copyFileSync(srcPath, destPath);

  const hash = crypto.createHash("sha256").update(fs.readFileSync(destPath)).digest("hex");
  manifest.files[relPath] = { sha256: hash };
}

// Write manifest
fs.writeFileSync(
  path.join(releaseDir, "manifest.json"),
  JSON.stringify(manifest, null, 2) + "\n"
);

console.log(`Built release v${manifest.version} with ${Object.keys(manifest.files).length} files.`);
