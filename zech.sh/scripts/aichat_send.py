#!/usr/bin/env python3
"""Send a message as Claude to AI chat."""

import os
import sys

import httpx

URL = os.environ.get("AICHAT_URL", "https://aichat.zech.sh")
SECRET = os.environ.get("AICHAT_SECRET", "")


def main() -> None:
    if not SECRET:
        print("Error: AICHAT_SECRET not set", file=sys.stderr)
        sys.exit(1)

    if len(sys.argv) < 2:
        print("Usage: aichat_send.py <message>", file=sys.stderr)
        sys.exit(1)

    content = " ".join(sys.argv[1:])

    resp = httpx.post(
        f"{URL}/api/messages",
        json={"content": content},
        headers={"Authorization": f"Bearer {SECRET}"},
    )
    resp.raise_for_status()
    print(f"Sent: {resp.json()}")


if __name__ == "__main__":
    main()
