"use strict";

const fs = require("fs");
const path = require("path");
const P = require("./paths");

function pin(args) {
  const version = args[0];
  if (!version) {
    console.log("Usage: npx @bryonics/claude pin <version>");
    console.log("\nAvailable versions:");
    if (fs.existsSync(P.RELEASES_DIR)) {
      for (const v of fs.readdirSync(P.RELEASES_DIR).sort()) {
        const isCurrent = fs.existsSync(P.CURRENT_LINK) &&
          fs.realpathSync(P.CURRENT_LINK) === path.join(P.RELEASES_DIR, v);
        console.log(`  ${v}${isCurrent ? " (current)" : ""}`);
      }
    }
    return;
  }

  const targetDir = path.join(P.RELEASES_DIR, version);
  if (!fs.existsSync(targetDir)) {
    console.log(`  Version ${version} not found in ~/.bryonics/releases/`);
    return;
  }

  // Atomic symlink swap
  const tempLink = P.CURRENT_LINK + ".new";
  try { fs.unlinkSync(tempLink); } catch (e) {}
  fs.symlinkSync(targetDir, tempLink);
  fs.renameSync(tempLink, P.CURRENT_LINK);

  // Update install state
  if (fs.existsSync(P.INSTALL_STATE)) {
    const state = JSON.parse(fs.readFileSync(P.INSTALL_STATE, "utf8"));
    state.installed_version = version;
    state.installed_at = new Date().toISOString();
    fs.writeFileSync(P.INSTALL_STATE, JSON.stringify(state, null, 2) + "\n");
  }

  // Verify command symlinks
  if (fs.existsSync(P.INSTALL_STATE)) {
    const state = JSON.parse(fs.readFileSync(P.INSTALL_STATE, "utf8"));
    for (const s of state.managed_symlinks || []) {
      if (!fs.existsSync(s)) {
        const cmdName = path.basename(s);
        const source = path.join(targetDir, "commands", cmdName);
        if (fs.existsSync(source)) {
          fs.symlinkSync(source, s);
        }
      }
    }
  }

  console.log(`  Pinned to v${version}`);
}

module.exports = pin;
