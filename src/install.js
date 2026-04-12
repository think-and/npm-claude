"use strict";

const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const http = require("http");
const https = require("https");
const readline = require("readline");
const P = require("./paths");
const { mergeHooks } = require("./settings-merge");

function prompt(question, defaultVal) {
  return new Promise((resolve) => {
    const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
    const q = defaultVal ? `${question} [${defaultVal}]: ` : `${question}: `;
    rl.question(q, (answer) => {
      rl.close();
      resolve(answer.trim() || defaultVal || "");
    });
  });
}

function sha256(filePath) {
  const data = fs.readFileSync(filePath);
  return crypto.createHash("sha256").update(data).digest("hex");
}

function apiPost(baseUrl, path, body) {
  return new Promise((resolve, reject) => {
    const url = new URL(path, baseUrl);
    const mod = url.protocol === "https:" ? https : http;
    const data = JSON.stringify(body);
    const req = mod.request(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(data) },
      timeout: 10000,
    }, (res) => {
      let body = "";
      res.on("data", (d) => body += d);
      res.on("end", () => {
        try { resolve({ status: res.statusCode, data: JSON.parse(body) }); }
        catch (e) { resolve({ status: res.statusCode, data: { error: body } }); }
      });
    });
    req.on("error", (e) => reject(e));
    req.write(data);
    req.end();
  });
}

async function install(args) {
  const force = args.includes("--force");

  console.log("think& — Team Knowledge Layer for Claude Code");
  console.log("==============================================\n");

  // Check for existing config
  let config = {};
  if (fs.existsSync(P.CONFIG_PATH)) {
    try {
      config = JSON.parse(fs.readFileSync(P.CONFIG_PATH, "utf8"));
      if (config.api_key) {
        console.log(`  Existing config found.`);
        console.log(`  User: ${config.user_id}, Team: ${config.team_id || "(none)"}\n`);
        const reconfigure = await prompt("Reconfigure? (y/N)", "n");
        if (reconfigure.toLowerCase() !== "y") {
          return installRelease(config, force);
        }
      }
    } catch (e) {
      config = {};
    }
  }

  // API URL
  const apiUrl = await prompt("API URL", config.api_url || "http://64.23.139.13:8000");

  // Choose: create, join, or personal
  console.log("\n  1. Create a new organization");
  console.log("  2. Join an existing organization");
  console.log("  3. Personal only (no team — just for you)\n");
  const choice = await prompt("Choose (1, 2, or 3)", "");

  if (choice === "1") {
    // Create team
    const teamName = await prompt("Team name");
    const userName = await prompt("Your name", process.env.USER || "");

    console.log("\n  Creating team...");
    try {
      const resp = await apiPost(apiUrl, "/v1/org/create", {
        team_name: teamName,
        user_name: userName,
      });

      if (resp.status !== 200 || resp.data.error) {
        console.log(`  Error: ${resp.data.error || resp.data.detail || "Unknown error"}`);
        return;
      }

      config = {
        api_url: apiUrl,
        api_key: resp.data.api_key,
        user_id: resp.data.user_id,
        team_id: resp.data.team_id,
      };

      console.log(`  ✓ Team "${resp.data.team_id}" created`);
      console.log(`  ✓ Your API key: ${resp.data.api_key.slice(0, 20)}...`);
      console.log(`  ✓ Invite key: ${resp.data.invite_key}`);
      console.log(`\n  Share this invite key with teammates:`);
      console.log(`    npx @thinkand/claude@latest install`);
      console.log(`    → Choose "Join a team" → paste: ${resp.data.invite_key}\n`);
    } catch (e) {
      console.log(`  Error: Could not reach API at ${apiUrl}`);
      console.log(`  ${e.message}`);
      return;
    }

  } else if (choice === "2") {
    // Join team
    const inviteKey = await prompt("Invite key");
    const userName = await prompt("Your name", process.env.USER || "");

    console.log("\n  Joining team...");
    try {
      const resp = await apiPost(apiUrl, "/v1/org/join", {
        invite_key: inviteKey,
        user_name: userName,
      });

      if (resp.status !== 200 || resp.data.error) {
        console.log(`  Error: ${resp.data.error || resp.data.detail || "Unknown error"}`);
        return;
      }

      config = {
        api_url: apiUrl,
        api_key: resp.data.api_key,
        user_id: resp.data.user_id,
        team_id: resp.data.team_id,
      };

      console.log(`  ✓ Joined team "${resp.data.team_id}"`);
      console.log(`  ✓ Your API key: ${resp.data.api_key.slice(0, 20)}...`);
      console.log("");
    } catch (e) {
      console.log(`  Error: Could not reach API at ${apiUrl}`);
      console.log(`  ${e.message}`);
      return;
    }

  } else if (choice === "3") {
    // Personal profile — team-of-one
    const userName = await prompt("Your name", process.env.USER || "");

    console.log("\n  Creating personal profile...");
    try {
      const resp = await apiPost(apiUrl, "/v1/profile/personal", {
        user_name: userName,
      });

      if (resp.status !== 200 || resp.data.error) {
        console.log(`  Error: ${resp.data.error || resp.data.detail || "Unknown error"}`);
        return;
      }

      config = {
        api_url: apiUrl,
        api_key: resp.data.api_key,
        user_id: resp.data.user_id,
        team_id: resp.data.team_id,
        profile_kind: "personal",
      };

      console.log(`  ✓ Personal profile "${resp.data.profile_id}" created`);
      console.log(`  ✓ Your API key: ${resp.data.api_key.slice(0, 20)}...`);
      console.log("");
      console.log(`  This profile is just for you. No teammates can see it.`);
      console.log(`  Memories stay in your private namespace on the server.`);
      console.log("");
    } catch (e) {
      console.log(`  Error: Could not reach API at ${apiUrl}`);
      console.log(`  ${e.message}`);
      return;
    }

  } else {
    console.log("  Invalid choice.");
    return;
  }

  return installRelease(config, force);
}

async function installRelease(config, force) {
  // Create directories
  fs.mkdirSync(P.BRYONICS_DIR, { recursive: true });
  fs.mkdirSync(P.RELEASES_DIR, { recursive: true });
  fs.mkdirSync(P.SESSIONS_DIR, { recursive: true });
  fs.mkdirSync(P.COMMANDS_DIR, { recursive: true });

  // Write config
  fs.writeFileSync(P.CONFIG_PATH, JSON.stringify(config, null, 2) + "\n");

  // Copy release files
  const releaseDir = path.join(__dirname, "..", "release");
  const manifest = JSON.parse(fs.readFileSync(path.join(releaseDir, "manifest.json"), "utf8"));
  const version = manifest.version;
  const targetDir = path.join(P.RELEASES_DIR, version);

  console.log(`  Installing think& v${version}...`);

  if (fs.existsSync(targetDir)) {
    fs.rmSync(targetDir, { recursive: true });
  }
  copyDirSync(releaseDir, targetDir);

  // Verify checksums
  let checksumOk = true;
  for (const [relPath, info] of Object.entries(manifest.files || {})) {
    const fullPath = path.join(targetDir, relPath);
    if (!fs.existsSync(fullPath)) { checksumOk = false; continue; }
    const actual = sha256(fullPath);
    if (info.sha256 && actual !== info.sha256) checksumOk = false;
  }
  if (checksumOk) console.log("  ✓ Checksums verified");

  // Atomic symlink
  const tempLink = P.CURRENT_LINK + ".new";
  try { fs.unlinkSync(tempLink); } catch (e) {}
  fs.symlinkSync(targetDir, tempLink);
  fs.renameSync(tempLink, P.CURRENT_LINK);
  console.log(`  ✓ current → releases/${version}`);

  // Command symlinks
  const commandsSource = path.join(targetDir, "commands");
  const managedSymlinks = [];

  if (fs.existsSync(commandsSource)) {
    for (const file of fs.readdirSync(commandsSource)) {
      if (!file.endsWith(".md")) continue;
      const target = path.join(P.COMMANDS_DIR, file);
      const source = path.join(commandsSource, file);

      if (fs.existsSync(target)) {
        const stat = fs.lstatSync(target);
        if (!stat.isSymbolicLink() && !force) {
          console.log(`  ⚠ ${file} exists (not symlink). Use --force.`);
          continue;
        }
        fs.unlinkSync(target);
      }

      fs.symlinkSync(source, target);
      managedSymlinks.push(target);
    }
  }
  console.log(`  ✓ ${managedSymlinks.length} command symlinks`);

  // Merge hooks
  mergeHooks();
  console.log("  ✓ Hooks merged into settings.json");

  // Save install state
  const installState = {
    installed_version: version,
    installed_at: new Date().toISOString(),
    managed_symlinks: managedSymlinks,
    managed_hooks: [P.RECALL_CMD, P.CAPTURE_CMD],
  };
  fs.writeFileSync(P.INSTALL_STATE, JSON.stringify(installState, null, 2) + "\n");

  console.log(`\n  think& v${version} installed!`);
  console.log("  Restart Claude Code to activate.\n");
}

function copyDirSync(src, dest) {
  fs.mkdirSync(dest, { recursive: true });
  for (const entry of fs.readdirSync(src, { withFileTypes: true })) {
    const srcPath = path.join(src, entry.name);
    const destPath = path.join(dest, entry.name);
    if (entry.isDirectory()) copyDirSync(srcPath, destPath);
    else fs.copyFileSync(srcPath, destPath);
  }
}

module.exports = install;
