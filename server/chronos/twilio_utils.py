"""Twilio REST helpers for Chronos telephony."""

from __future__ import annotations

import os

import aiohttp
from loguru import logger


async def get_call_info(call_sid: str) -> dict:
    """Fetch caller metadata from Twilio (From/To numbers, status).

    Returns an empty dict when credentials are missing or the request fails.
    """
    account_sid = os.getenv("TWILIO_ACCOUNT_SID")
    auth_token = os.getenv("TWILIO_AUTH_TOKEN")
    if not account_sid or not auth_token or not call_sid:
        return {}

    url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Calls/{call_sid}.json"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, auth=aiohttp.BasicAuth(account_sid, auth_token)) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.error(f"Twilio API error ({resp.status}): {body[:200]}")
                    return {}
                data = await resp.json()
                return {
                    "call_sid": data.get("sid"),
                    "from_number": data.get("from"),
                    "to_number": data.get("to"),
                    "status": data.get("status"),
                    "direction": data.get("direction"),
                }
    except Exception as e:  # noqa: BLE001
        logger.error(f"Error fetching Twilio call info: {e}")
        return {}
