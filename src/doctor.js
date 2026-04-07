"use strict";

const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const { execSync } = require("child_process");
const P = require("./paths");

function check(label, fn) {
  try {
    const result = fn();
    if (result === true) {
      console.log(`  ✓ ${label}`);
      return true;
    }
    console.log(`  ✗ ${label}: ${result}`);
    return false;
  } catch (e) {
    console.log(`  ✗ ${label}: ${e.message}`);
    return false;
  }
}

function doctor() {
  console.log("Bryonics Doctor\n");
  let passed = 0;
  let total = 0;

  let installState = {};
  try {
    installState = JSON.parse(fs.readFileSync(P.INSTALL_STATE, "utf8"));
  } catch (e) {}

  // 1. Config
  total++;
  if (check("Config exists and is valid JSON", () => {
    if (!fs.existsSync(P.CONFIG_PATH)) return "~/.bryonics/config.json not found";
    JSON.parse(fs.readFileSync(P.CONFIG_PATH, "utf8"));
    return true;
  })) passed++;

  // 2. Current symlink
  total++;
  if (check("current symlink resolves", () => {
    if (!fs.existsSync(P.CURRENT_LINK)) return "~/.bryonics/current not found";
    const target = fs.realpathSync(P.CURRENT_LINK);
    if (!fs.existsSync(target)) return `target ${target} does not exist`;
    return true;
  })) passed++;

  // 3. Manifest checksums
  total++;
  if (check("Release files match manifest checksums", () => {
    const manifestPath = path.join(P.CURRENT_LINK, "manifest.json");
    if (!fs.existsSync(manifestPath)) return "manifest.json not found";
    const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
    let bad = 0;
    for (const [relPath, info] of Object.entries(manifest.files || {})) {
      const fullPath = path.join(P.CURRENT_LINK, relPath);
      if (!fs.existsSync(fullPath)) { bad++; continue; }
      if (info.sha256) {
        const actual = crypto.createHash("sha256")
          .update(fs.readFileSync(fullPath)).digest("hex");
        if (actual !== info.sha256) bad++;
      }
    }
    if (bad > 0) return `${bad} file(s) corrupted or missing`;
    return true;
  })) passed++;

  // 4. Hooks in settings.json
  const settingsPath = installState.settings_path || P.SETTINGS_PATH;

  total++;
  if (check(`Hooks configured in ${settingsPath}`, () => {
    if (!fs.existsSync(settingsPath)) return `${settingsPath} not found`;
    const settings = JSON.parse(fs.readFileSync(settingsPath, "utf8"));
    const hooks = settings.hooks || {};
    const hasRecall = (hooks.UserPromptSubmit || []).some(
      (h) => h.hooks && h.hooks.some((hh) => hh.command === P.RECALL_CMD)
    );
    const hasCapture = (hooks.PostToolUse || []).some(
      (h) => h.hooks && h.hooks.some((hh) => hh.command === P.CAPTURE_CMD)
    );
    if (!hasRecall) return "recall hook not found";
    if (!hasCapture) return "capture hook not found";
    return true;
  })) passed++;

  // 5. Command symlinks
  total++;
  if (check("Command symlinks resolve", () => {
    if (!installState.installed_version) return "install-state.json not found";
    const broken = (installState.managed_symlinks || []).filter(
      (s) => !fs.existsSync(s)
    );
    if (broken.length > 0) return `${broken.length} broken symlink(s)`;
    return true;
  })) passed++;

  // 6. API reachable
  total++;
  if (check("API reachable", () => {
    try {
      const config = JSON.parse(fs.readFileSync(P.CONFIG_PATH, "utf8"));
      const url = (config.api_url || "").replace(/\/$/, "") + "/v1/health";
      execSync(`curl -s --max-time 3 "${url}" > /dev/null 2>&1`);
      return true;
    } catch (e) {
      return "API not reachable";
    }
  })) passed++;

  // 7. Python 3
  total++;
  if (check("Python 3 available", () => {
    try {
      execSync("python3 --version", { stdio: "pipe" });
      return true;
    } catch (e) {
      return "python3 not found in PATH";
    }
  })) passed++;

  console.log(`\n  ${passed}/${total} checks passed.\n`);
}

module.exports = doctor;
