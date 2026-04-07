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
  console.log("think& Uninstall\n");

  // 0. Notify server (best effort)
  if (fs.existsSync(P.CONFIG_PATH)) {
    try {
      const config = JSON.parse(fs.readFileSync(P.CONFIG_PATH, "utf8"));
      if (config.api_key && config.api_url) {
        const http = require("http");
        const https = require("https");
        const url = new URL("/v1/org/uninstall", config.api_url);
        const mod = url.protocol === "https:" ? https : http;
        const req = mod.request(url, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "Authorization": `Bearer ${config.api_key}`,
          },
          timeout: 3000,
        });
        req.write("{}");
        req.end();
        console.log("  Notified server.");
      }
    } catch (e) {}
  }

  // 1. Read install state
  let state = {};
  if (fs.existsSync(P.INSTALL_STATE)) {
    try {
      state = JSON.parse(fs.readFileSync(P.INSTALL_STATE, "utf8"));
    } catch (e) {}
  }

  // 2. Remove hooks from settings.json using exact commands and path from state
  const managedHooks = state.managed_hooks || [P.RECALL_CMD, P.CAPTURE_CMD];
  const settingsPath = state.settings_path || P.SETTINGS_PATH;
  removeHooks(managedHooks, settingsPath);
  console.log(`  Removed hooks from ${settingsPath}`);

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
