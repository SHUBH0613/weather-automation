"""
whatsapp_notifier.py
Handles sending weather briefings and PowerPoint attachments to WhatsApp via Twilio.
"""

import os
import requests
from typing import Optional, Dict, Any, List

def get_twilio_credentials() -> Dict[str, str]:
    return {
        "account_sid": os.environ.get("TWILIO_ACCOUNT_SID", "").strip(),
        "auth_token": os.environ.get("TWILIO_AUTH_TOKEN", "").strip(),
        "from_number": os.environ.get("TWILIO_WHATSAPP_NUMBER", "whatsapp:+14155238886").strip(),
        "target_number": os.environ.get("TARGET_PHONE_NUMBER", "+917761866811").strip()
    }

def format_whatsapp_summary(result_data: Dict[str, Any]) -> str:
    """Format a clean, readable WhatsApp weather card with emojis."""
    date_str = result_data.get("date_str", "TODAY")
    weather_data = result_data.get("weather_data", [])

    lines = [
        f"🌤️ *DAILY WEATHER BRIEFING — {date_str}*",
        "━━━━━━━━━━━━━━━━━━━━━━"
    ]

    for item in weather_data:
        loc = item.get("location", "").upper()
        w_rain = item.get("windy_rain")
        w_rain_str = f"{w_rain}mm" if w_rain is not None else "N/A"
        a_rain = item.get("accu_rain_prob")
        a_rain_str = f"{int(a_rain)}%" if a_rain is not None else "N/A"

        w_cloud = item.get("windy_cloud")
        w_cloud_str = f"{int(w_cloud)}%" if w_cloud is not None else "N/A"
        a_cloud = item.get("accu_cloud")
        a_cloud_str = f"{int(a_cloud)}%" if a_cloud is not None else "N/A"

        w_rmk = item.get("windy_remark", "GO")
        a_rmk = item.get("accu_remark", "GO")

        def _badge(rmk: str) -> str:
            if "NO GO" in rmk: return "🔴 *NO GO*"
            if "LTD GO" in rmk: return "🟡 *LTD GO*"
            return "🟢 *GO*"

        lines.append(f"\n📍 *{loc}*")
        lines.append(f"  • *Rain*: Windy {w_rain_str} | Accu {a_rain_str}")
        lines.append(f"  • *Cloud*: Windy {w_cloud_str} | Accu {a_cloud_str}")
        lines.append(f"  • *Status*: Windy {_badge(w_rmk)} | Accu {_badge(a_rmk)}")

    lines.append("\n━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("📊 Full presentation attached below.")
    return "\n".join(lines)

def send_whatsapp_message(
    to_number: str,
    body_text: str,
    media_url: Optional[str] = None
) -> Dict[str, Any]:
    """Send text message and optional media URL to a WhatsApp recipient using Twilio REST API."""
    creds = get_twilio_credentials()
    account_sid = creds["account_sid"]
    auth_token = creds["auth_token"]
    from_num = creds["from_number"]

    if not account_sid or not auth_token:
        print("[WhatsApp] Twilio credentials missing (TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN). Message not sent.")
        return {"error": "Missing Twilio credentials"}

    if not to_number.startswith("whatsapp:"):
        to_number = f"whatsapp:{to_number}"

    if not from_num.startswith("whatsapp:"):
        from_num = f"whatsapp:{from_num}"

    api_url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"
    payload = {
        "From": from_num,
        "To": to_number,
        "Body": body_text
    }
    if media_url:
        payload["MediaUrl"] = media_url

    try:
        resp = requests.post(api_url, data=payload, auth=(account_sid, auth_token), timeout=20)
        res_json = resp.json()
        if resp.status_code in [200, 201]:
            print(f"[WhatsApp] Message sent successfully to {to_number} (SID: {res_json.get('sid')})")
        else:
            print(f"[WhatsApp] Twilio error {resp.status_code}: {res_json}")
        return res_json
    except Exception as e:
        print(f"[WhatsApp] Network/HTTP Exception: {e}")
        return {"error": str(e)}
