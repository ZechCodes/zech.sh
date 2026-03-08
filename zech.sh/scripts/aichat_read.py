#!/usr/bin/env python3
"""Read recent AI chat messages."""

import os
import sys

import httpx

URL = os.environ.get("AICHAT_URL", "https://aichat.zech.sh")
SECRET = os.environ.get("AICHAT_SECRET", "")


def main() -> None:
    if not SECRET:
        print("Error: AICHAT_SECRET not set", file=sys.stderr)
        sys.exit(1)

    params = {}
    if len(sys.argv) > 1:
        params["limit"] = sys.argv[1]

    resp = httpx.get(
        f"{URL}/api/messages",
        params=params,
        headers={"Authorization": f"Bearer {SECRET}"},
    )
    resp.raise_for_status()

    for msg in resp.json():
        sender = msg["sender"].upper()
        read = " [read]" if msg.get("read_by_claude_at") else ""
        print(f"[{msg['created_at']}] {sender}{read}: {msg['content']}")


if __name__ == "__main__":
    main()
