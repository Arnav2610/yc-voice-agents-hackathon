#!/usr/bin/env python
"""Start the Chronos text-WS bridge for Cekura text simulations.

Then expose it with ngrok:  ngrok http $CHRONOS_WS_PORT  (default 8970)
and use the wss:// URL as `websocket_url` in run_scenarios_text.

Usage:  uv run python scripts/run_text_ws.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(override=True)

from chronos.text_ws_server import serve  # noqa: E402

if __name__ == "__main__":
    try:
        asyncio.run(serve())
    except KeyboardInterrupt:
        pass
