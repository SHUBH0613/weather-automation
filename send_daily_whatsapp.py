"""
send_daily_whatsapp.py
Command-line runner for scheduled daily weather delivery (06:30 AM IST).
Fetches weather data, generates PPT, and dispatches directly to WhatsApp via Twilio.
"""

import asyncio
import os
import sys
from datetime import datetime
import pytz

from weather_automation import run_automation
from ppt_generator import generate_ppt
from whatsapp_notifier import send_whatsapp_message, format_whatsapp_summary

async def main():
    target_phone = os.environ.get("TARGET_PHONE_NUMBER", "+917761866811").strip()
    print(f"[Daily 06:30 AM WX] Starting automation for recipient: {target_phone}")

    async def emit(msg: str):
        print(f"  {msg}", flush=True)

    result = await run_automation(emit=emit)
    ppt_path = generate_ppt(result)
    print(f"[Daily 06:30 AM WX] Presentation generated: {ppt_path}")

    # Build WhatsApp message
    summary_text = format_whatsapp_summary(result)

    # Public download URL if hosted on Render or cloud
    public_base = os.environ.get("RENDER_EXTERNAL_URL") or os.environ.get("APP_URL", "")
    filename = os.path.basename(ppt_path)
    media_url = f"{public_base.rstrip('/')}/download/{filename}" if public_base else None

    if media_url:
        summary_text += f"\n\n🔗 *Direct Download Link*:\n{media_url}"

    print(f"[Daily 06:30 AM WX] Dispatching to {target_phone} via Twilio...")
    res = send_whatsapp_message(target_phone, summary_text, media_url=media_url)
    print(f"[Daily 06:30 AM WX] Delivery result: {res}")

if __name__ == "__main__":
    asyncio.run(main())
