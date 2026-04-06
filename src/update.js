"use strict";

const fs = require("fs");
const path = require("path");
const P = require("./paths");

function update() {
  console.log("Bryonics Update\n");

  // Check current version
  const manifestPath = path.join(P.CURRENT_LINK, "manifest.json");
  if (!fs.existsSync(manifestPath)) {
    console.log("  Bryonics not installed. Run: npx @bryonics/claude@latest install");
    return;
  }

  const current = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
  const currentVersion = current.version;

  // Check bundled version
  const bundledManifest = path.join(__dirname, "..", "release", "manifest.json");
  if (!fs.existsSync(bundledManifest)) {
    console.log("  No bundled release found.");
    return;
  }

  const bundled = JSON.parse(fs.readFileSync(bundledManifest, "utf8"));
  const newVersion = bundled.version;

  if (currentVersion === newVersion) {
    console.log(`  Already on latest version: v${currentVersion}`);
    return;
  }

  console.log(`  Current: v${currentVersion}`);
  console.log(`  Available: v${newVersion}`);
  console.log(`  Updating...`);

  // Copy new release
  const targetDir = path.join(P.RELEASES_DIR, newVersion);
  const releaseDir = path.join(__dirname, "..", "release");

  if (fs.existsSync(targetDir)) {
    fs.rmSync(targetDir, { recursive: true });
  }
  copyDirSync(releaseDir, targetDir);

  // Atomic symlink swap
  const tempLink = P.CURRENT_LINK + ".new";
  try { fs.unlinkSync(tempLink); } catch (e) {}
  fs.symlinkSync(targetDir, tempLink);
  fs.renameSync(tempLink, P.CURRENT_LINK);

  // Verify command symlinks still exist, repair missing
  if (fs.existsSync(P.INSTALL_STATE)) {
    const state = JSON.parse(fs.readFileSync(P.INSTALL_STATE, "utf8"));
    let repaired = 0;
    for (const s of state.managed_symlinks || []) {
      if (!fs.existsSync(s)) {
        // Recreate symlink
        const cmdName = path.basename(s);
        const source = path.join(targetDir, "commands", cmdName);
        if (fs.existsSync(source)) {
          fs.symlinkSync(source, s);
          repaired++;
        }
      }
    }
    if (repaired > 0) {
      console.log(`  Repaired ${repaired} command symlinks.`);
    }

    // Update install state
    state.installed_version = newVersion;
    state.installed_at = new Date().toISOString();
    fs.writeFileSync(P.INSTALL_STATE, JSON.stringify(state, null, 2) + "\n");
  }

  // Clean old releases (keep current + 1 previous)
  const releases = fs.readdirSync(P.RELEASES_DIR).sort();
  while (releases.length > 2) {
    const old = releases.shift();
    if (old !== newVersion && old !== currentVersion) {
      fs.rmSync(path.join(P.RELEASES_DIR, old), { recursive: true, force: true });
      console.log(`  Cleaned up v${old}`);
    }
  }

  console.log(`\n  Updated v${currentVersion} → v${newVersion}`);
  console.log("  Restart Claude Code to activate.\n");
}

function copyDirSync(src, dest) {
  fs.mkdirSync(dest, { recursive: true });
  for (const entry of fs.readdirSync(src, { withFileTypes: true })) {
    const srcPath = path.join(src, entry.name);
    const destPath = path.join(dest, entry.name);
    if (entry.isDirectory()) {
      copyDirSync(srcPath, destPath);
    } else {
      fs.copyFileSync(srcPath, destPath);
    }
  }
}

module.exports = update;
