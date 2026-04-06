#!/usr/bin/env node
"use strict";

const command = process.argv[2];
const args = process.argv.slice(3);

switch (command) {
  case "install":
    require("../src/install")(args);
    break;
  case "update":
    require("../src/update")(args);
    break;
  case "doctor":
    require("../src/doctor")(args);
    break;
  case "uninstall":
    require("../src/uninstall")(args);
    break;
  case "pin":
    require("../src/pin")(args);
    break;
  default:
    console.log("think& — Team Knowledge Layer for Claude Code");
    console.log("");
    console.log("Usage: npx @thinkand/claude@latest <command>");
    console.log("");
    console.log("Commands:");
    console.log("  install      Install think& hooks and commands");
    console.log("  update       Update to the latest version");
    console.log("  doctor       Check installation health");
    console.log("  uninstall    Remove think& from Claude Code");
    console.log("  pin <ver>    Pin to a specific version");
    process.exit(command ? 1 : 0);
}
