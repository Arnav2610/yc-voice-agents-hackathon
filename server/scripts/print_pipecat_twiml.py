#!/usr/bin/env python3
"""Print TwiML for Twilio → Pipecat Cloud (chronos-911 agent)."""

from __future__ import annotations

import os
import sys

from dotenv import load_dotenv

load_dotenv(override=True)

AGENT_NAME = "chronos-911"
DEFAULT_WS = "wss://api.pipecat.daily.co/ws/twilio"


def main() -> None:
    org = (sys.argv[1] if len(sys.argv) > 1 else None) or os.getenv("PIPECAT_ORG", "").strip()
    if not org:
        print(
            "Usage: uv run python scripts/print_pipecat_twiml.py YOUR_ORG_NAME\n"
            "  e.g. uv run python scripts/print_pipecat_twiml.py industrious-purple-cat-12345\n"
            "Or set PIPECAT_ORG in .env\n"
            "Find org name: uv run pipecatcloud organizations list",
            file=sys.stderr,
        )
        sys.exit(1)

    host = f"{AGENT_NAME}.{org}"
    print(
        f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Connect>
    <Stream url="{DEFAULT_WS}">
      <Parameter name="_pipecatCloudServiceHost" value="{host}"/>
    </Stream>
  </Connect>
</Response>"""
    )
    print(f"\n# Pipecat service host: {host}", file=sys.stderr)
    print("# Paste the XML above into a Twilio TwiML Bin, then assign it to your phone number.", file=sys.stderr)


if __name__ == "__main__":
    main()
