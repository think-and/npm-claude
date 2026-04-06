#!/usr/bin/env python3
"""Helper for /team-invite: generate an invite key for your team."""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bryonics_client import load_config, api_request


def main():
    cfg = load_config()
    if not cfg:
        print("No config found. Run: npx @thinkand/claude@latest install")
        return

    result = api_request(cfg, "POST", "/v1/org/invite", {}, timeout=5.0)
    if not result:
        print("Error: Could not reach API.")
        return

    if "error" in result:
        print("Error: {}".format(result["error"]))
        return

    invite_key = result.get("invite_key", "")
    print("Invite key: {}".format(invite_key))
    print("")
    print("Share with your teammate:")
    print("  npx @thinkand/claude@latest install")
    print("  → Choose 'Join a team' → paste: {}".format(invite_key))


if __name__ == "__main__":
    main()
