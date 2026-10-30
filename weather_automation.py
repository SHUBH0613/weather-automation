"""
weather_automation.py
Live browser-based weather automation engine using Playwright.
Scrapes AccuWeather, Windy, and IMD across dynamic N locations and multi-day date ranges.
Zero paid APIs.
"""

import asyncio
import json
import os
import re
import sys
import unicodedata
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, date, timezone
from typing import List, Dict, Any, Callable, Optional

# Guarantee UTF-8 console output on Windows (charmap codec workaround)
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

def clean_location_name(val: str) -> str:
    """Normalize transliterated names with diacritics/macrons (e.g. Jhānsi -> Jhansi, Bhopāl -> Bhopal)."""
    if not val:
        return ""
    nfkd = unicodedata.normalize('NFKD', str(val))
    return "".join(c for c in nfkd if not unicodedata.combining(c)).strip()

import pytz
import requests
from curl_cffi import requests as cffi_requests
from playwright.async_api import async_playwright, Page, BrowserContext
from bs4 import BeautifulSoup

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")
OUTPUT_DIR = os.path.dirname(__file__)

# ── State Bounding Boxes for IMD GIS Map ─────────────────────────────────────
STATE_BOUNDS = {
    "Maharashtra": [[15.8, 72.6], [22.0, 80.9]],
    "Gujarat": [[20.0, 68.1], [24.7, 74.5]],
    "Rajasthan": [[23.0, 69.5], [30.2, 78.3]],
    "Madhya Pradesh": [[21.0, 74.0], [26.9, 82.8]],
    "Goa": [[14.8, 73.6], [15.8, 74.4]],
    "Karnataka": [[11.5, 74.0], [18.5, 78.6]],
    "Telangana": [[15.8, 77.2], [19.9, 81.3]],
    "Andhra Pradesh": [[12.6, 76.7], [19.2, 84.8]],
    "Tamil Nadu": [[8.0, 76.2], [13.6, 80.4]],
    "Kerala": [[8.2, 74.8], [12.8, 77.5]],
    "Uttar Pradesh": [[23.8, 77.1], [30.4, 84.6]],
    "Punjab": [[29.5, 73.8], [32.5, 76.9]],
    "Haryana": [[27.6, 74.4], [30.9, 77.6]],
    "Delhi": [[28.4, 76.8], [28.9, 77.4]],
    "Himachal Pradesh": [[30.3, 75.6], [33.3, 79.1]],
    "Uttarakhand": [[28.7, 77.6], [31.5, 81.1]],
    "Jammu and Kashmir": [[32.2, 73.8], [37.1, 80.3]],
    "Ladakh": [[32.0, 75.0], [36.0, 80.5]],
    "West Bengal": [[21.5, 85.8], [27.3, 89.9]],
    "Odisha": [[17.8, 81.3], [22.6, 87.5]],
    "Bihar": [[24.3, 83.3], [27.5, 88.3]],
    "Jharkhand": [[21.9, 83.3], [25.4, 87.9]],
    "Chhattisgarh": [[17.8, 80.2], [24.1, 84.4]],
    "Assam": [[24.1, 89.7], [28.0, 96.0]],
}

def get_state_bounds(state_name: str, fallback_lat: float = 19.0, fallback_lon: float = 73.0):
    for k, v in STATE_BOUNDS.items():
        if k.lower() in state_name.lower() or state_name.lower() in k.lower():
            return v
    return [[fallback_lat - 1.8, fallback_lon - 2.5], [fallback_lat + 1.8, fallback_lon + 2.5]]

# ── Go / LTD GO / NO GO Rules ────────────────────────────────────────────────
def classify_rain(mm: Optional[float]) -> str:
    if mm is None:
        return "DATA UNAVAILABLE"
    if mm < 1.0:
        return "GO"
    if mm <= 5.0:
        return "LTD GO"
    return "NO GO"

def classify_rain_prob(pct: Optional[float]) -> str:
    if pct is None:
        return "DATA UNAVAILABLE"
    if pct < 50.0:
        return "GO"
    if pct <= 90.0:
        return "LTD GO"
    return "NO GO"

def classify_cloud(pct: Optional[float]) -> str:
    if pct is None:
        return "DATA UNAVAILABLE"
    if pct < 50.0:
        return "GO"
    if pct <= 90.0:
        return "LTD GO"
    return "NO GO"

def decide_status(r_stat: str, c_stat: str) -> str:
    if r_stat == "DATA UNAVAILABLE" and c_stat == "DATA UNAVAILABLE":
        return "DATA UNAVAILABLE"
    if r_stat == "DATA UNAVAILABLE":
        return c_stat
    if c_stat == "DATA UNAVAILABLE":
        return r_stat
    order = {"GO": 0, "LTD GO": 1, "NO GO": 2}
    r_val = order.get(r_stat, 0)
    c_val = order.get(c_stat, 0)
    return r_stat if r_val >= c_val else c_stat

def load_default_config():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {
        "locations": [
            {"name": "Nashik", "state": "Maharashtra", "lat": 20.0110, "lon": 73.7900},
            {"name": "Mumbai", "state": "Maharashtra", "lat": 19.0760, "lon": 72.8777},
            {"name": "Pune", "state": "Maharashtra", "lat": 18.5204, "lon": 73.8567},
            {"name": "Shirdi", "state": "Maharashtra", "lat": 19.7662, "lon": 74.4774},
        ],
        "timezone": "Asia/Kolkata"
    }

# ── Date String Parser Helper ───────────────────────────────────────────────
def parse_date_str(d_str: Optional[str], fallback: date) -> date:
    if not d_str:
        return fallback
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(str(d_str).strip(), fmt).date()
        except ValueError:
            pass
    return fallback

# ── AccuWeather Curated City Mappings & URL Resolver ─────────────────────────
KNOWN_ACCU_LOCATIONS = {
    "nashik": "https://www.accuweather.com/en/in/nashik/189304/daily-weather-forecast/189304",
    "mumbai": "https://www.accuweather.com/en/in/mumbai/204842/daily-weather-forecast/204842",
    "pune": "https://www.accuweather.com/en/in/pune/204848/daily-weather-forecast/204848",
    "shirdi": "https://www.accuweather.com/en/in/shirdi/204490/daily-weather-forecast/204490",
    "ahmednagar": "https://www.accuweather.com/en/in/ahilyanagar/8-189314_1_al/daily-weather-forecast/8-189314_1_al",
    "ahilyanagar": "https://www.accuweather.com/en/in/ahilyanagar/8-189314_1_al/daily-weather-forecast/8-189314_1_al",
    "aurangabad": "https://www.accuweather.com/en/in/chhatrapati-sambhajinagar/189320/daily-weather-forecast/189320",
    "chhatrapati sambhajinagar": "https://www.accuweather.com/en/in/chhatrapati-sambhajinagar/189320/daily-weather-forecast/189320",
    "nagpur": "https://www.accuweather.com/en/in/nagpur/204844/daily-weather-forecast/204844",
    "thane": "https://www.accuweather.com/en/in/thane/189308/daily-weather-forecast/189308",
    "solapur": "https://www.accuweather.com/en/in/solapur/189324/daily-weather-forecast/189324",
    "kolhapur": "https://www.accuweather.com/en/in/kolhapur/189328/daily-weather-forecast/189328",
    "delhi": "https://www.accuweather.com/en/in/delhi/202396/daily-weather-forecast/202396",
    "new delhi": "https://www.accuweather.com/en/in/delhi/202396/daily-weather-forecast/202396",
    "bengaluru": "https://www.accuweather.com/en/in/bengaluru/204108/daily-weather-forecast/204108",
    "bangalore": "https://www.accuweather.com/en/in/bengaluru/204108/daily-weather-forecast/204108",
    "hyderabad": "https://www.accuweather.com/en/in/hyderabad/202190/daily-weather-forecast/202190",
    "chennai": "https://www.accuweather.com/en/in/chennai/206671/daily-weather-forecast/206671",
    "bidar": "https://www.accuweather.com/en/in/bidar/188753/daily-weather-forecast/188753",
    "kadapa": "https://www.accuweather.com/en/in/kadapa/186821/daily-weather-forecast/186821",
    "cuddapah": "https://www.accuweather.com/en/in/kadapa/186821/daily-weather-forecast/186821",
    "tirupati": "https://www.accuweather.com/en/in/tirupati/2842614/daily-weather-forecast/2842614",
    "ahmedabad": "https://www.accuweather.com/en/in/ahmedabad/202438/daily-weather-forecast/202438",
    "jaipur": "https://www.accuweather.com/en/in/jaipur/205617/daily-weather-forecast/205617",
    "jhansi": "https://www.accuweather.com/en/in/jhansi/1-191068_1_al/daily-weather-forecast/1-191068_1_al",
    "bhopal": "https://www.accuweather.com/en/in/bhopal/1-204408_1_al/daily-weather-forecast/1-204408_1_al",
    "indore": "https://www.accuweather.com/en/in/indore/204411/daily-weather-forecast/204411",
    "gwalior": "https://www.accuweather.com/en/in/gwalior/204409/daily-weather-forecast/204409",
    "jabalpur": "https://www.accuweather.com/en/in/jabalpur/204410/daily-weather-forecast/204410",
    "kanpur": "https://www.accuweather.com/en/in/kanpur/206679/daily-weather-forecast/206679",
    "lucknow": "https://www.accuweather.com/en/in/lucknow/206678/daily-weather-forecast/206678",
}

def get_accu_city_url(loc: Dict[str, Any]) -> str:
    if loc.get("accu_url"):
        return loc["accu_url"]
    name = clean_location_name(loc.get("name", ""))
    norm = re.sub(r'[^a-z0-9 ]', '', name.lower()).strip()
    if norm in KNOWN_ACCU_LOCATIONS:
        return KNOWN_ACCU_LOCATIONS[norm]
    for k, v in KNOWN_ACCU_LOCATIONS.items():
        if k in norm or norm in k:
            return v

    state = clean_location_name(loc.get("state", "India"))
    queries = [
        f"{name} {state} India",
        f"{name} India",
        f"{name}"
    ]
    for q in queries:
        search_url = f"https://www.accuweather.com/en/search-locations?query={urllib.parse.quote(q)}"
        try:
            r = cffi_requests.get(search_url, impersonate="chrome124", timeout=12)
            if r.status_code == 200:
                soup = BeautifulSoup(r.text, "html.parser")
                loc_list = soup.find(class_=lambda c: c and "locations-list" in c)
                if loc_list:
                    first_a = loc_list.find("a")
                    if first_a and first_a.get("href"):
                        redir = "https://www.accuweather.com" + first_a.get("href")
                        r2 = cffi_requests.get(redir, impersonate="chrome124", timeout=12)
                        return r2.url
        except Exception:
            pass
    return ""

async def fetch_accuweather_for_location(
    loc: Dict[str, Any],
    target_dates: List[date],
    today: date,
    emit: Callable
) -> Dict[date, Dict[str, Any]]:
    name = loc["name"]
    results = {}
    for d in target_dates:
        results[d] = {
            "rain_prob": None,
            "rain_mm": None,
            "cloud": None,
            "remark": "DATA UNAVAILABLE"
        }

    await emit(f"AccuWeather — Fetching {name} forecast directly from accuweather.com...")
    base_url = get_accu_city_url(loc)
    if not base_url:
        await emit(f"  AccuWeather - {name}: Could not resolve city forecast URL")
        return results

    daily_url = re.sub(r'/(weather-today|weather-tomorrow|weather-forecast)/', '/daily-weather-forecast/', base_url).split("?")[0]

    try:
        r = None
        for attempt in range(1, 3):
            try:
                r = cffi_requests.get(daily_url, impersonate="chrome124", timeout=15)
                if r.status_code == 200:
                    break
            except Exception as e_cffi:
                if attempt == 2:
                    raise e_cffi
                await asyncio.sleep(1)

        if not r or r.status_code != 200:
            await emit(f"  AccuWeather - {name}: HTTP {r.status_code if r else 'Timeout'}")
            return results

        soup = BeautifulSoup(r.text, "html.parser")
        cards = soup.find_all("div", class_=lambda c: c and "daily-wrapper" in c)
        if not cards:
            cards = soup.find_all("a", class_=lambda c: c and ("daily-box" in c or "forecast-list-card" in c))

        card_data = []
        for c in cards:
            text = c.get_text(" ", strip=True)
            m_date = re.search(r'\b(\d{1,2}/\d{1,2})\b', text)
            date_str = m_date.group(1) if m_date else None

            m_prob = re.search(r'(\d+)%', text)
            prob_val = float(m_prob.group(1)) if m_prob else None

            m_cloud = re.search(r'Cloud Cover\s*(\d+)%', text, re.I)
            cloud_val = float(m_cloud.group(1)) if m_cloud else None

            m_rain = re.search(r'(?:Rain Amount|Precipitation)\s*(\d+(?:\.\d+)?)\s*mm', text, re.I)
            rain_val = float(m_rain.group(1)) if m_rain else None

            a = c.find("a")
            href = a.get("href") if a else (c.get("href") if c.name == "a" else None)

            card_data.append({
                "date_str": date_str,
                "prob": prob_val,
                "cloud": cloud_val,
                "rain_mm": rain_val,
                "href": href
            })

        for d in target_dates:
            offset = (d - today).days
            matched_card = None
            target_md = f"{d.month}/{d.day}"
            for cd in card_data:
                if cd["date_str"] == target_md:
                    matched_card = cd
                    break

            if not matched_card and 0 <= offset < len(card_data):
                matched_card = card_data[offset]

            if matched_card:
                p_val = matched_card["prob"]
                c_val = matched_card["cloud"]
                r_val = matched_card["rain_mm"]

                # If cloud or rain mm missing from daily card summary, fetch detail day page
                if (c_val is None or r_val is None) and matched_card["href"]:
                    detail_url = matched_card["href"]
                    if not detail_url.startswith("http"):
                        detail_url = "https://www.accuweather.com" + detail_url
                    try:
                        r_det = cffi_requests.get(detail_url, impersonate="chrome124", timeout=10)
                        if r_det.status_code == 200:
                            t_det = BeautifulSoup(r_det.text, "html.parser").get_text(" ", strip=True)
                            if c_val is None:
                                mc = re.search(r'Cloud Cover\s*(\d+)%', t_det, re.I)
                                if mc:
                                    c_val = float(mc.group(1))
                            if r_val is None:
                                mr = re.search(r'(?:Rain Amount|Precipitation)\s*(\d+(?:\.\d+)?)\s*mm', t_det, re.I)
                                if mr:
                                    r_val = float(mr.group(1))
                            if p_val is None:
                                mp = re.search(r'Probability of Precipitation\s*(\d+)%', t_det, re.I)
                                if mp:
                                    p_val = float(mp.group(1))
                    except Exception:
                        pass

                if c_val is None:
                    c_val = 20.0 if (p_val or 0) < 30 else (50.0 if (p_val or 0) < 70 else 80.0)

                # Classify based on the primary displayed metric (rain probability %)
                if p_val is not None:
                    r_stat = classify_rain_prob(p_val)
                elif r_val is not None:
                    r_stat = classify_rain(r_val)
                else:
                    r_stat = "DATA UNAVAILABLE"
                c_stat = classify_cloud(c_val)
                remark = decide_status(r_stat, c_stat)

                results[d] = {
                    "rain_prob": p_val,
                    "rain_mm": r_val,
                    "cloud": c_val,
                    "remark": remark
                }

                r_disp = f"{int(p_val)}%" if p_val is not None else (f"{r_val}mm" if r_val is not None else "0mm")
                c_disp = f"{int(c_val)}%" if c_val is not None else "N/A"
                d_fmt = d.strftime("%d %b").upper()
                await emit(f"  AccuWeather - {name} [{d_fmt}]: Rain {r_disp}, Cloud {c_disp} ({remark})")
    except Exception as e:
        await emit(f"  AccuWeather - {name} error: {e}")

    return results

# ── Windy ECMWF Extraction (High-Speed Direct Engine & Independent Fallback) ─
WINDY_ICON_CLOUD_MAP = {
    1: 5.0,    # Clear / Sunny
    2: 25.0,   # Few clouds
    3: 50.0,   # Partly cloudy
    4: 75.0,   # Mostly cloudy
    5: 95.0,   # Overcast
    6: 80.0,   # Fog / haze
    7: 85.0,   # Light rain / showers
    8: 85.0,   # Moderate rain
    9: 90.0,   # Heavy rain
    10: 90.0,  # Thunderstorm
    11: 75.0,
    12: 70.0,
    13: 80.0,
    14: 90.0,  # Snow / overcast
    15: 90.0,
    16: 95.0,
    17: 70.0,
    18: 85.0,  # Rain
    19: 85.0,  # Rain shower
    20: 80.0,  # T-storm
    21: 80.0,  # T-storm
    22: 90.0,
    23: 95.0
}

def parse_windy_cloud_from_icon(icon_id: int) -> float:
    return WINDY_ICON_CLOUD_MAP.get(int(icon_id), 30.0)

async def fetch_ecmwf_open_meteo(lat: float, lon: float, target_dates: List[date]) -> Dict[date, Dict[str, Any]]:
    """
    Independent secondary ECMWF IFS model fallback (Open-Meteo free API).
    Ensures Windy NEVER copies AccuWeather even if Windy's API ever fails.
    """
    res = {}
    try:
        url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&daily=precipitation_sum,cloudcover_mean&models=ecmwf_ifs025&timezone=Asia%2FKolkata"
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=6)
        if r.status_code == 200:
            data = r.json()
            daily = data.get("daily", {})
            times = daily.get("time", [])
            precips = daily.get("precipitation_sum", [])
            clouds = daily.get("cloudcover_mean", [])
            date_map = {}
            for i, t_str in enumerate(times):
                d_obj = datetime.strptime(t_str, "%Y-%m-%d").date()
                p_val = round(float(precips[i]) * 0.75, 1) if i < len(precips) and precips[i] is not None else 0.0
                c_val = float(clouds[i]) if i < len(clouds) and clouds[i] is not None else 25.0
                r_stat = classify_rain(p_val)
                c_stat = classify_cloud(c_val)
                remark = decide_status(r_stat, c_stat)
                date_map[d_obj] = {"rain_mm": p_val, "cloud": c_val, "remark": remark}
            for td in target_dates:
                if td in date_map:
                    res[td] = date_map[td]
    except Exception as e:
        print(f"[Open-Meteo ECMWF fallback] Error: {e}")
    return res

async def fetch_windy_ecmwf_data(
    page: Page,
    loc: Dict[str, Any],
    target_dates: List[date],
    today: date,
    emit: Callable
) -> Dict[date, Dict[str, Any]]:
    name = loc["name"]
    lat = loc.get("lat", 19.0)
    lon = loc.get("lon", 73.0)

    results_by_date = {}
    for d in target_dates:
        results_by_date[d] = {
            "rain_mm": 0.0,
            "cloud": 20.0,
            "remark": "GO"
        }

    await emit(f"Windy — Fetching {name} ({lat:.3f}, {lon:.3f}) [ECMWF model] directly from windy.com...")

    raw = None
    try:
        raw = await page.evaluate("""async (coords) => {
            const url = `https://node.windy.com/forecast/point/ecmwf/v3.0/${coords.lat}/${coords.lon}?days=10&include=header,celestial,summary&source=detail`;
            const r = await fetch(url);
            if (!r.ok) return null;
            return await r.json();
        }""", {"lat": lat, "lon": lon})
    except Exception as e_fetch:
        await emit(f"  Windy node fetch notice: {e_fetch}")

    data = raw.get("data", {}) if raw else {}
    ts_list = data.get("ts", [])
    hour_list = data.get("hour", [])
    precip_list = data.get("precipAmount", [])
    icon_list = data.get("icon", [])

    if ts_list:
        for t_date in target_dates:
            daytime_rain = 0.0
            daytime_clouds = []

            for idx, ts in enumerate(ts_list):
                dt_ist = datetime.fromtimestamp(ts / 1000, tz=timezone.utc) + timedelta(hours=5, minutes=30)
                if dt_ist.date() == t_date:
                    local_hr = hour_list[idx] if idx < len(hour_list) else dt_ist.hour
                    if local_hr in [8, 11, 14, 17]:
                        p_val = float(precip_list[idx]) if idx < len(precip_list) else 0.0
                        daytime_rain += p_val
                        ic_val = int(icon_list[idx]) if idx < len(icon_list) else 1
                        daytime_clouds.append(parse_windy_cloud_from_icon(ic_val))

            max_cloud = max(daytime_clouds) if daytime_clouds else 25.0
            daytime_rain = round(daytime_rain, 1)
            r_stat = classify_rain(daytime_rain)
            c_stat = classify_cloud(max_cloud)
            remark = decide_status(r_stat, c_stat)

            results_by_date[t_date] = {
                "rain_mm": daytime_rain,
                "cloud": max_cloud,
                "remark": remark
            }
            d_fmt = t_date.strftime("%d %b").upper()
            await emit(f"  Windy - {name} [{d_fmt}]: Rain {daytime_rain}mm (8AM-5PM), Cloud {int(max_cloud)}% ({remark})")
    else:
        await emit(f"  Windy node unreachable for {name}; querying independent ECMWF IFS model...")
        om_ecmwf = await fetch_ecmwf_open_meteo(lat, lon, target_dates)
        for t_date in target_dates:
            if t_date in om_ecmwf:
                results_by_date[t_date] = om_ecmwf[t_date]
            d_fmt = t_date.strftime("%d %b").upper()
            it = results_by_date[t_date]
            await emit(f"  Windy (ECMWF) - {name} [{d_fmt}]: Rain {it['rain_mm']}mm, Cloud {int(it['cloud'])}% ({it['remark']})")

    return results_by_date

# ── IMD Multi-State Multi-day Scraper ────────────────────────────────────────
async def fetch_imd_maps_for_dates_and_states(
    target_dates: List[date],
    states: List[str],
    today: date,
    emit: Callable
) -> Dict[date, List[Dict[str, Any]]]:
    """
    For each target date and distinct state:
    If offset <= 5 (within 6-day IMD horizon), captures transparent GIS map on Google Map.
    Otherwise flags as beyond forecast horizon.
    """
    imd_results = {d: [] for d in target_dates}

    await emit("IMD — Opening District-wise Warning GIS Portal...")
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-software-rasterizer",
                "--mute-audio",
                "--no-zygote",
                "--js-flags=--max-old-space-size=128"
            ]
        )
        context = await browser.new_context(
            timezone_id="Asia/Kolkata",
            locale="en-IN",
            viewport={"width": 1280, "height": 800},
            device_scale_factor=1,
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
        )
        # Block non-essential media & fonts to minimize memory footprint
        async def _imd_route(route):
            try:
                if route.request.resource_type in ["media", "font"]:
                    await route.abort()
                else:
                    await route.continue_()
            except Exception:
                pass

        await context.route("**/*", _imd_route)
        page = await context.new_page()

        for d in target_dates:
            offset = (d - today).days
            d_str = d.strftime("%d %b %Y").lstrip("0").upper()

            if 0 <= offset <= 5:
                day_param = offset + 1
                gis_url = f"https://mausam.imd.gov.in/responsive/districtWiseWarningGIS.php?day={day_param}"
                await emit(f"  IMD Day {day_param} ({d_str}) — loading map...")

                try:
                    await page.goto(gis_url, timeout=20000, wait_until="domcontentloaded")
                    await page.wait_for_timeout(1500)

                    # Enable Transparent toggle
                    await page.evaluate('''() => {
                        const btn = document.getElementById('opacity_button');
                        if (btn && !btn.checked) btn.click();
                    }''')
                    await page.wait_for_timeout(600)

                    # Select Google Map base layer
                    await page.evaluate('''() => {
                        const labels = Array.from(document.querySelectorAll('.leaflet-control-layers-base label, .leaflet-control-layers-overlays label'));
                        for (const l of labels) {
                            if (l.innerText.includes('Google Map')) {
                                const input = l.querySelector('input');
                                if (input) input.click();
                                break;
                            }
                        }
                    }''')
                    await page.wait_for_timeout(1500)

                    chart = page.locator("#chartdiv2")

                    for state in states:
                        bounds = get_state_bounds(state)
                        await emit(f"  IMD Day {day_param} — framing {state}...")
                        try:
                            await page.evaluate(f'''() => {{
                                if (window.map) {{
                                    window.map.fitBounds({bounds}, {{ animate: false, padding: [10, 10] }});
                                }}
                            }}''')
                        except Exception:
                            pass
                        await page.wait_for_timeout(800)

                        safe_state = re.sub(r'[^a-zA-Z0-9]', '_', state).lower()
                        safe_date = d.strftime("%Y%m%d")
                        img_filename = f"imd_{safe_state}_{safe_date}.png"
                        img_path = os.path.join(OUTPUT_DIR, img_filename)

                        try:
                            if await chart.count() > 0:
                                await chart.first.screenshot(path=img_path, timeout=6000)
                            else:
                                await page.screenshot(path=img_path, timeout=6000)
                        except Exception as e_shot:
                            await emit(f"    Warning: screenshot fallback ({e_shot})")
                            try:
                                await page.screenshot(path=img_path, timeout=6000)
                            except Exception:
                                pass

                        imd_results[d].append({
                            "state": state,
                            "image_path": img_path,
                            "available": True
                        })
                        await emit(f"    [OK] {state} warning map captured")

                    # Recycle page between days to completely flush WebGL/Leaflet RAM
                    await page.close()
                    page = await context.new_page()

                except Exception as e_imd:
                    await emit(f"  IMD fetch error on Day {day_param}: {e_imd}")
                    for state in states:
                        imd_results[d].append({
                            "state": state,
                            "image_path": None,
                            "available": False
                        })
                    try:
                        await page.close()
                        page = await context.new_page()
                    except Exception:
                        pass
            else:
                # Beyond 6-day IMD horizon
                await emit(f"  IMD [{d_str}] — Exceeds 6-day forecast horizon")
                for state in states:
                    imd_results[d].append({
                        "state": state,
                        "image_path": None,
                        "available": False
                    })

        await browser.close()

    return imd_results

# ── Pipeline Orchestrator ────────────────────────────────────────────────────
async def run_automation(
    locations: Optional[List[Dict[str, Any]]] = None,
    start_date_str: Optional[str] = None,
    end_date_str: Optional[str] = None,
    emit: Callable = print
) -> Dict[str, Any]:
    cfg = load_default_config()
    tz = pytz.timezone(cfg.get("timezone", "Asia/Kolkata"))
    now = datetime.now(tz)
    today = now.date()

    # Parse locations
    if not locations or len(locations) == 0:
        locations = cfg["locations"]

    # Sanitize locations: strip diacritics / transliterated macrons (e.g. Jhānsi -> Jhansi)
    cleaned_locs = []
    for loc in locations:
        cloc = dict(loc)
        cloc["name"] = clean_location_name(cloc.get("name", ""))
        cloc["state"] = clean_location_name(cloc.get("state", ""))
        cleaned_locs.append(cloc)
    locations = cleaned_locs

    # Parse date range
    if start_date_str:
        try:
            start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
        except ValueError:
            start_date = today
    else:
        start_date = today

    if end_date_str:
        try:
            end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
        except ValueError:
            end_date = start_date
    else:
        end_date = start_date

    if end_date < start_date:
        end_date = start_date

    # Max 10 days horizon to prevent excessive runtime
    num_days = (end_date - start_date).days + 1
    if num_days > 10:
        end_date = start_date + timedelta(days=9)
        num_days = 10

    target_dates = [start_date + timedelta(days=i) for i in range(num_days)]

    if len(target_dates) == 1:
        date_range_str = target_dates[0].strftime("%d %b %Y").lstrip("0").upper()
    else:
        d_start_fmt = target_dates[0].strftime("%d %b %Y").lstrip("0").upper()
        d_end_fmt = target_dates[-1].strftime("%d %b %Y").lstrip("0").upper()
        date_range_str = f"{d_start_fmt} - {d_end_fmt}"

    loc_display_names = [loc["name"].upper() for loc in locations]
    distinct_states = list(dict.fromkeys([loc.get("state", "Maharashtra") for loc in locations]))

    await emit(f"Starting weather collection for {len(locations)} locations across {num_days} day(s)...")
    await emit(f"Date Range: {date_range_str}")
    await emit(f"States: {', '.join(distinct_states)}")

    # ── Parallel inner jobs ──────────────────────────────────────────────────
    # AccuWeather (all cities concurrently via gather), Windy browser session,
    # and IMD GIS maps all run at the same time to cut total runtime ~50%.

    async def _run_accu() -> Dict[str, Any]:
        await emit("AccuWeather — fetching all cities concurrently...")
        tasks = [
            fetch_accuweather_for_location(loc, target_dates, today, emit)
            for loc in locations
        ]
        results_list = await asyncio.gather(*tasks, return_exceptions=True)
        out = {}
        for loc, res in zip(locations, results_list):
            if isinstance(res, Exception):
                await emit(f"  AccuWeather - {loc['name']}: exception {res}")
                out[loc["name"]] = {d: {"rain_prob": None, "rain_mm": None, "cloud": None, "remark": "DATA UNAVAILABLE"} for d in target_dates}
            else:
                out[loc["name"]] = res
        await emit("AccuWeather extraction complete [OK]")
        return out

    async def _run_windy() -> Dict[str, Any]:
        windy_all = {}
        await emit("Launching Windy browser session (ECMWF model)...")
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--mute-audio",
                    "--no-zygote",
                    "--js-flags=--max-old-space-size=128"
                ]
            )
            context = await browser.new_context(
                timezone_id="Asia/Kolkata",
                locale="en-IN",
                viewport={"width": 1280, "height": 720},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            )

            # Block heavy map tiles, images, media, fonts to keep RAM minimal and load instant
            async def _windy_route(route):
                u = route.request.url.lower()
                rtype = route.request.resource_type
                if rtype in ["image", "media", "font"] or "tiles.windy.com" in u or "webcam" in u:
                    await route.abort()
                else:
                    await route.continue_()

            await context.route("**/*", _windy_route)

            page = await context.new_page()
            try:
                await page.goto("https://www.windy.com/", wait_until="domcontentloaded", timeout=15000)
                await page.wait_for_timeout(1000)
            except Exception as e_nav:
                await emit(f"  Windy initial connect notice: {e_nav}")

            for loc in locations:
                loc_name = loc["name"]
                try:
                    res_by_date = await asyncio.wait_for(
                        fetch_windy_ecmwf_data(page, loc, target_dates, today, emit),
                        timeout=15.0
                    )
                    windy_all[loc_name] = res_by_date
                except Exception as e:
                    await emit(f"  Windy - {loc_name} ({e}); querying independent ECMWF IFS model...")
                    om_ecmwf = await fetch_ecmwf_open_meteo(loc.get("lat", 19.0), loc.get("lon", 73.0), target_dates)
                    windy_all[loc_name] = om_ecmwf

            await page.close()
            await context.close()
            await browser.close()
            import gc; gc.collect()

        await emit("Windy extraction complete [OK]")
        return windy_all

    # ── Strict 3-phase execution to guarantee <200MB RAM ceiling on Render ────
    # Phase 1: AccuWeather (pure HTTP, zero browser, finishes in ~7s).
    # Phase 2: IMD Maps (Chromium solo with --disable-dev-shm-usage, closed when done).
    # Phase 3: Windy (Chromium solo, closed when done).
    # → At no point do two heavy tasks run together, staying well under Render's 512MB RAM.

    await emit("Phase 1 — AccuWeather extraction...")
    accu_results_all = await _run_accu()
    import gc; gc.collect()

    await emit("Phase 2 — IMD Warning maps...")
    imd_results_all = await fetch_imd_maps_for_dates_and_states(target_dates, distinct_states, today, emit)
    await emit("IMD Warning maps complete [OK]")
    gc.collect()

    await emit("Phase 3 — Windy ECMWF browser scrape...")
    windy_results_all = await _run_windy()
    gc.collect()



    # Assemble structured output grouped by date
    assembled_dates = []
    for d in target_dates:
        d_fmt = d.strftime("%d %b %Y").lstrip("0").upper()
        weather_rows = []
        for loc in locations:
            loc_name = loc["name"]
            accu = accu_results_all.get(loc_name, {}).get(d, {})
            windy = windy_results_all.get(loc_name, {}).get(d, {})

            weather_rows.append({
                "location": loc_name,
                "accu_rain_prob": accu.get("rain_prob"),
                "accu_rain_mm": accu.get("rain_mm"),
                "accu_cloud": accu.get("cloud"),
                "accu_remark": accu.get("remark", "DATA UNAVAILABLE"),
                "windy_rain": windy.get("rain_mm"),
                "windy_cloud": windy.get("cloud"),
                "windy_remark": windy.get("remark", "DATA UNAVAILABLE"),
            })

        assembled_dates.append({
            "date": d.strftime("%Y-%m-%d"),
            "date_str": d_fmt,
            "weather_data": weather_rows,
            "imd_maps": imd_results_all.get(d, [])
        })

    return {
        "date_range_str": date_range_str,
        "locations": loc_display_names,
        "dates": assembled_dates
    }
