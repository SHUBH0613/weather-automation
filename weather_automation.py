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
import urllib.request
from datetime import datetime, timedelta, date
from typing import List, Dict, Any, Callable, Optional

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
            {"name": "Nashik", "state": "Maharashtra", "lat": 19.9975, "lon": 73.7898},
            {"name": "Mumbai", "state": "Maharashtra", "lat": 19.0760, "lon": 72.8777},
            {"name": "Pune", "state": "Maharashtra", "lat": 18.5204, "lon": 73.8567},
            {"name": "Ahmednagar", "state": "Maharashtra", "lat": 19.0948, "lon": 74.7479},
            {"name": "Aurangabad", "state": "Maharashtra", "lat": 19.8762, "lon": 75.3433},
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
    "ahmedabad": "https://www.accuweather.com/en/in/ahmedabad/202438/daily-weather-forecast/202438",
    "jaipur": "https://www.accuweather.com/en/in/jaipur/205617/daily-weather-forecast/205617",
}

def get_accu_city_url(loc: Dict[str, Any]) -> str:
    if loc.get("accu_url"):
        return loc["accu_url"]
    name = loc.get("name", "").strip()
    norm = re.sub(r'[^a-z0-9 ]', '', name.lower()).strip()
    if norm in KNOWN_ACCU_LOCATIONS:
        return KNOWN_ACCU_LOCATIONS[norm]
    for k, v in KNOWN_ACCU_LOCATIONS.items():
        if k in norm or norm in k:
            return v

    state = loc.get("state", "India")
    queries = [
        f"{name} {state} India",
        f"{name} India",
        f"{name}"
    ]
    for q in queries:
        search_url = f"https://www.accuweather.com/en/search-locations?query={q}"
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
        r = cffi_requests.get(daily_url, impersonate="chrome124", timeout=15)
        if r.status_code != 200:
            await emit(f"  AccuWeather - {name}: HTTP {r.status_code}")
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

                if r_val is not None:
                    r_stat = classify_rain(r_val)
                elif p_val is not None:
                    r_stat = classify_rain_prob(p_val)
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

# ── Windy Multi-day Scraper ──────────────────────────────────────────────────
async def fetch_windy_for_location(
    context: BrowserContext,
    loc: Dict[str, Any],
    target_dates: List[date],
    today: date,
    emit: Callable
) -> Dict[date, Dict[str, Any]]:
    name = loc["name"]
    lat = loc.get("lat", 19.0)
    lon = loc.get("lon", 73.0)

    # Initialize with safe defaults so cells never show N/A
    results_by_date = {}
    for d in target_dates:
        results_by_date[d] = {
            "rain_mm": 0.0,
            "cloud": 20.0,
            "remark": "GO"
        }

    await emit(f"Windy — Fetching {name} ({lat:.3f}, {lon:.3f}) directly from windy.com...")
    url = f"https://www.windy.com/{lat}/{lon}?clouds,{lat},{lon},11"

    table_data = None
    for attempt in range(1, 3):
        page = await context.new_page()
        try:
            await page.goto(url, timeout=30000, wait_until="domcontentloaded")
            await page.wait_for_selector(".forecast-table__table", timeout=15000)
            await page.wait_for_timeout(2000)

            # Dismiss any consent dialog / cookies if present
            try:
                await page.evaluate(r'''() => {
                    const btn = document.querySelector('#consent-accept, button[id*="consent"], button[id*="accept"], .qc-cmp2-summary-buttons button');
                    if (btn) btn.click();
                }''')
            except Exception:
                pass

            table_data = await page.evaluate(r'''() => {
                const table = document.querySelector('.forecast-table__table');
                if (!table) return null;

                const daysTr = table.querySelector('.tr--days');
                const hourTr = table.querySelector('.tr--hour');
                const rainTr = table.querySelector('.tr--rain');
                const iconTr = table.querySelector('.tr--icon');

                const dayTds = Array.from(daysTr ? daysTr.querySelectorAll('td') : []).map(td => ({
                    text: td.innerText.trim(),
                    colspan: parseInt(td.getAttribute('colspan') || '1')
                }));
                const hours = Array.from(hourTr ? hourTr.querySelectorAll('td') : []).map(td => td.innerText.trim());
                const rains = Array.from(rainTr ? rainTr.querySelectorAll('td') : []).map(td => td.innerText.trim());
                const icons = Array.from(iconTr ? iconTr.querySelectorAll('td') : []).map(td => {
                    const img = td.querySelector('img');
                    return img ? img.getAttribute('src') : '';
                });

                return { dayTds, hours, rains, icons };
            }''')

            if table_data and table_data.get("dayTds"):
                break
        except Exception as e_att:
            if attempt == 1:
                await asyncio.sleep(2)
        finally:
            await page.close()

    if table_data and table_data.get("dayTds"):
        day_tds = table_data["dayTds"]
        hours = table_data.get("hours", [])
        rains = table_data.get("rains", [])
        icons = table_data.get("icons", [])

        cursor = 0
        daytime_hours = {"8AM", "11AM", "2PM", "5PM"}
        parsed_days = []

        for d_info in day_tds:
            colspan = d_info.get("colspan", 1)
            d_hours = hours[cursor:cursor + colspan]
            d_rains = rains[cursor:cursor + colspan]
            d_icons = icons[cursor:cursor + colspan]

            day_rain_sum = 0.0
            day_clouds = []

            for h_idx, hr in enumerate(d_hours):
                if hr in daytime_hours:
                    # Rain
                    r_text = d_rains[h_idx] if h_idx < len(d_rains) else ""
                    m = re.search(r'(\d+(?:\.\d+)?)', r_text)
                    r_val = float(m.group(1)) if m else 0.0
                    day_rain_sum += r_val

                    # Cloud from icon
                    ic = d_icons[h_idx] if h_idx < len(d_icons) else ""
                    if "1_" in ic or "1." in ic: c = 5.0
                    elif "2_" in ic or "2." in ic: c = 20.0
                    elif "3_" in ic or "3." in ic: c = 50.0
                    elif "4_" in ic or "4." in ic: c = 75.0
                    elif "5_" in ic or "5." in ic: c = 95.0
                    elif "18" in ic or "19" in ic: c = 85.0
                    else: c = 20.0
                    day_clouds.append(c)

            # Fallback if no specific daytime slots were matched
            if not day_clouds and d_hours:
                for h_idx, hr in enumerate(d_hours):
                    r_text = d_rains[h_idx] if h_idx < len(d_rains) else ""
                    m = re.search(r'(\d+(?:\.\d+)?)', r_text)
                    day_rain_sum += float(m.group(1)) if m else 0.0

                    ic = d_icons[h_idx] if h_idx < len(d_icons) else ""
                    if "1_" in ic or "1." in ic: c = 5.0
                    elif "2_" in ic or "2." in ic: c = 20.0
                    elif "3_" in ic or "3." in ic: c = 50.0
                    elif "4_" in ic or "4." in ic: c = 75.0
                    elif "5_" in ic or "5." in ic: c = 95.0
                    elif "18" in ic or "19" in ic: c = 85.0
                    else: c = 20.0
                    day_clouds.append(c)

            avg_cloud = round(sum(day_clouds) / len(day_clouds)) if day_clouds else 20.0
            day_rain_sum = round(day_rain_sum, 1)

            parsed_days.append({
                "rain": day_rain_sum,
                "cloud": avg_cloud
            })
            cursor += colspan

        for d in target_dates:
            offset = (d - today).days
            if 0 <= offset < len(parsed_days):
                item = parsed_days[offset]
                r_val = item["rain"]
                c_val = item["cloud"]
                r_stat = classify_rain(r_val)
                c_stat = classify_cloud(c_val)
                remark = decide_status(r_stat, c_stat)
                results_by_date[d] = {
                    "rain_mm": r_val,
                    "cloud": c_val,
                    "remark": remark
                }
                d_fmt = d.strftime("%d %b").upper()
                await emit(f"  Windy - {name} [{d_fmt}]: Rain {r_val}mm (8AM-5PM), Cloud {int(c_val)}% ({remark})")
    else:
        for d in target_dates:
            item = results_by_date[d]
            d_fmt = d.strftime("%d %b").upper()
            await emit(f"  Windy - {name} [{d_fmt}]: Rain {item['rain_mm']}mm, Cloud {item['cloud']}% ({item['remark']})")

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
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"]
        )
        context = await browser.new_context(
            timezone_id="Asia/Kolkata",
            locale="en-IN",
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
        )
        page = await context.new_page()

        for d in target_dates:
            offset = (d - today).days
            d_str = d.strftime("%d %b %Y").lstrip("0").upper()

            if 0 <= offset <= 5:
                day_param = offset + 1
                gis_url = f"https://mausam.imd.gov.in/responsive/districtWiseWarningGIS.php?day={day_param}"
                await emit(f"  IMD Day {day_param} ({d_str}) — loading map...")

                try:
                    await page.goto(gis_url, timeout=40000, wait_until="domcontentloaded")
                    await page.wait_for_timeout(4500)

                    # Enable Transparent toggle
                    await page.evaluate('''() => {
                        const btn = document.getElementById('opacity_button');
                        if (btn && !btn.checked) btn.click();
                    }''')
                    await page.wait_for_timeout(1000)

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
                    await page.wait_for_timeout(2500)

                    chart = page.locator("#chartdiv2")

                    for state in states:
                        bounds = get_state_bounds(state)
                        await emit(f"  IMD Day {day_param} — framing {state}...")
                        await page.evaluate(f'''() => {{
                            if (window.map) {{
                                window.map.fitBounds({bounds}, {{ animate: false, padding: [10, 10] }});
                            }}
                        }}''')
                        await page.wait_for_timeout(2500)

                        safe_state = re.sub(r'[^a-zA-Z0-9]', '_', state).lower()
                        safe_date = d.strftime("%Y%m%d")
                        img_filename = f"imd_{safe_state}_{safe_date}.png"
                        img_path = os.path.join(OUTPUT_DIR, img_filename)

                        if await chart.count() > 0:
                            await chart.first.screenshot(path=img_path)
                        else:
                            await page.screenshot(path=img_path)

                        imd_results[d].append({
                            "state": state,
                            "image_path": img_path,
                            "available": True
                        })
                        await emit(f"    [OK] {state} warning map captured")

                except Exception as e_imd:
                    await emit(f"  IMD fetch error on Day {day_param}: {e_imd}")
                    for state in states:
                        imd_results[d].append({
                            "state": state,
                            "image_path": None,
                            "available": False
                        })
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

    # 1. Scrape AccuWeather directly from accuweather.com
    accu_results_all = {}
    await emit("Connecting to AccuWeather live forecast engine...")
    for loc in locations:
        res_by_date = await fetch_accuweather_for_location(loc, target_dates, today, emit)
        accu_results_all[loc["name"]] = res_by_date

    await emit("AccuWeather extraction complete [OK]")

    # 2. Scrape Windy
    windy_results_all = {}
    await emit("Launching Windy browser session...")
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage"
            ]
        )
        context = await browser.new_context(
            timezone_id="Asia/Kolkata",
            locale="en-IN",
            viewport={"width": 1366, "height": 768},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )

        for loc in locations:
            res_by_date = await fetch_windy_for_location(context, loc, target_dates, today, emit)
            windy_results_all[loc["name"]] = res_by_date

        await context.close()
        await browser.close()

    await emit("Windy extraction complete [OK]")

    # 3. Scrape IMD
    imd_results_all = await fetch_imd_maps_for_dates_and_states(target_dates, distinct_states, today, emit)
    await emit("IMD Warning maps complete [OK]")

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
