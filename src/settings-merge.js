"use strict";

const fs = require("fs");
const { SETTINGS_PATH, RECALL_CMD, CAPTURE_CMD } = require("./paths");

function mergeHooks() {
  let settings = {};
  if (fs.existsSync(SETTINGS_PATH)) {
    try {
      settings = JSON.parse(fs.readFileSync(SETTINGS_PATH, "utf8"));
    } catch (e) {
      console.log(`  Warning: ${SETTINGS_PATH} is invalid JSON. Backing up and starting fresh.`);
      fs.copyFileSync(SETTINGS_PATH, SETTINGS_PATH + ".invalid.bak");
      settings = {};
    }
    // Backup valid file before modifying
    fs.copyFileSync(SETTINGS_PATH, SETTINGS_PATH + ".bak");
  }

  settings.hooks = settings.hooks || {};

  // UserPromptSubmit — recall hook
  const ups = settings.hooks.UserPromptSubmit || [];
  const bryonicsRecall = {
    hooks: [{ type: "command", command: RECALL_CMD, timeout: 10 }],
  };
  // Match current OR old-style bryonics hooks
  const isRecallHook = (cmd) => cmd === RECALL_CMD || cmd === "python3 ~/.bryonics/hooks/recall.py";
  const isCaptureHook = (cmd) => cmd === CAPTURE_CMD || cmd === "python3 ~/.bryonics/hooks/capture.py";

  // Remove all old bryonics recall hooks, then add current
  const filteredUps = ups.filter(
    (h) => !h.hooks || !h.hooks.some((hh) => isRecallHook(hh.command))
  );
  filteredUps.push(bryonicsRecall);
  settings.hooks.UserPromptSubmit = filteredUps;

  // PostToolUse — same: remove old, add current
  const ptu = settings.hooks.PostToolUse || [];
  const bryonicsCapture = {
    matcher: "Edit|Write|Bash",
    hooks: [{ type: "command", command: CAPTURE_CMD }],
  };
  const filteredPtu = ptu.filter(
    (h) => !h.hooks || !h.hooks.some((hh) => isCaptureHook(hh.command))
  );
  filteredPtu.push(bryonicsCapture);
  settings.hooks.PostToolUse = filteredPtu;

  fs.writeFileSync(SETTINGS_PATH, JSON.stringify(settings, null, 2) + "\n");
  return true;
}

function removeHooks(managedHooks) {
  if (!fs.existsSync(SETTINGS_PATH)) return;

  let settings;
  try {
    settings = JSON.parse(fs.readFileSync(SETTINGS_PATH, "utf8"));
  } catch (e) {
    return;
  }

  if (!settings.hooks) return;
  fs.copyFileSync(SETTINGS_PATH, SETTINGS_PATH + ".bak");

  const hookCmds = new Set(managedHooks || [RECALL_CMD, CAPTURE_CMD]);

  for (const event of ["UserPromptSubmit", "PostToolUse"]) {
    if (!Array.isArray(settings.hooks[event])) continue;
    settings.hooks[event] = settings.hooks[event].filter(
      (h) => !h.hooks || !h.hooks.some((hh) => hookCmds.has(hh.command))
    );
    if (settings.hooks[event].length === 0) {
      delete settings.hooks[event];
    }
  }

  if (Object.keys(settings.hooks).length === 0) {
    delete settings.hooks;
  }

  fs.writeFileSync(SETTINGS_PATH, JSON.stringify(settings, null, 2) + "\n");
}

module.exports = { mergeHooks, removeHooks };
