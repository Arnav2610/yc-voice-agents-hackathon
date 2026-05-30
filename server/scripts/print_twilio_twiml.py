#!/usr/bin/env python3
"""Print TwiML to paste into a Twilio TwiML Bin or Studio HTTP widget."""

from __future__ import annotations

import os
import sys

from dotenv import load_dotenv

load_dotenv(override=True)


def main() -> None:
    host = (
        (sys.argv[1] if len(sys.argv) > 1 else None)
        or os.getenv("TWILIO_PUBLIC_URL")
        or os.getenv("NGROK_DOMAIN")
    )
    if not host:
        print(
            "Usage: uv run python scripts/print_twilio_twiml.py YOUR_SUBDOMAIN.ngrok-free.app\n"
            "Or set TWILIO_PUBLIC_URL in .env",
            file=sys.stderr,
        )
        sys.exit(1)
    host = host.removeprefix("https://").removeprefix("http://").rstrip("/")
    print(
        f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Connect>
    <Stream url="wss://{host}/ws"></Stream>
  </Connect>
  <Pause length="40"/>
</Response>"""
    )
    print(f"\n# Webhook URL for Twilio voice: https://{host}/", file=sys.stderr)
    print(f"# Media stream WebSocket: wss://{host}/ws", file=sys.stderr)


if __name__ == "__main__":
    main()
