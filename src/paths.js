"use strict";

const os = require("os");
const path = require("path");

const HOME = os.homedir();

module.exports = {
  HOME,
  BRYONICS_DIR: path.join(HOME, ".bryonics"),
  RELEASES_DIR: path.join(HOME, ".bryonics", "releases"),
  CURRENT_LINK: path.join(HOME, ".bryonics", "current"),
  CONFIG_PATH: path.join(HOME, ".bryonics", "config.json"),
  INSTALL_STATE: path.join(HOME, ".bryonics", "install-state.json"),
  SESSIONS_DIR: path.join(HOME, ".bryonics", "sessions"),
  CLAUDE_DIR: path.join(HOME, ".claude"),
  SETTINGS_PATH: path.join(HOME, ".claude", "settings.json"),
  COMMANDS_DIR: path.join(HOME, ".claude", "commands"),

  RECALL_CMD: "python3 ~/.bryonics/current/hooks/recall.py",
  CAPTURE_CMD: "python3 ~/.bryonics/current/hooks/capture.py",
};
