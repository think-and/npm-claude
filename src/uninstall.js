"use strict";

const fs = require("fs");
const readline = require("readline");
const P = require("./paths");
const { removeHooks } = require("./settings-merge");

function prompt(question) {
  return new Promise((resolve) => {
    const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
    rl.question(question, (answer) => {
      rl.close();
      resolve(answer.trim().toLowerCase());
    });
  });
}

async function uninstall() {
  console.log("Bryonics Uninstall\n");

  // 1. Read install state
  let state = {};
  if (fs.existsSync(P.INSTALL_STATE)) {
    try {
      state = JSON.parse(fs.readFileSync(P.INSTALL_STATE, "utf8"));
    } catch (e) {}
  }

  // 2. Remove hooks from settings.json using exact commands from state
  const managedHooks = state.managed_hooks || [P.RECALL_CMD, P.CAPTURE_CMD];
  removeHooks(managedHooks);
  console.log("  Removed hooks from ~/.claude/settings.json");

  // 3. Remove managed command symlinks
  const symlinks = state.managed_symlinks || [];
  let removed = 0;
  for (const s of symlinks) {
    try {
      if (fs.existsSync(s) && fs.lstatSync(s).isSymbolicLink()) {
        fs.unlinkSync(s);
        removed++;
      }
    } catch (e) {}
  }
  console.log(`  Removed ${removed} command symlinks.`);

  // 4. Remove install state
  try { fs.unlinkSync(P.INSTALL_STATE); } catch (e) {}

  // 5. Remove current symlink
  try { fs.unlinkSync(P.CURRENT_LINK); } catch (e) {}

  // 6. Ask about full removal
  const answer = await prompt("\n  Remove all Bryonics data (~/.bryonics/)? [y/N] ");
  if (answer === "y" || answer === "yes") {
    fs.rmSync(P.BRYONICS_DIR, { recursive: true, force: true });
    console.log("  Removed ~/.bryonics/");
  } else {
    console.log("  Config and data preserved in ~/.bryonics/");
  }

  console.log("\n  Bryonics uninstalled. Restart Claude Code.\n");
}

module.exports = uninstall;
