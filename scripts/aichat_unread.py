#!/usr/bin/env python3
"""Read unread AI chat messages from the user."""

import os
import sys

import httpx

URL = os.environ.get("AICHAT_URL", "https://aichat.zech.sh")
SECRET = os.environ.get("AICHAT_SECRET", "")


def main() -> None:
    if not SECRET:
        print("Error: AICHAT_SECRET not set", file=sys.stderr)
        sys.exit(1)

    resp = httpx.get(
        f"{URL}/api/messages/unread",
        headers={"Authorization": f"Bearer {SECRET}"},
    )
    resp.raise_for_status()

    messages = resp.json()
    if not messages:
        print("No unread messages.")
        return

    for msg in messages:
        print(f"[{msg['created_at']}] {msg['content']}")


if __name__ == "__main__":
    main()
