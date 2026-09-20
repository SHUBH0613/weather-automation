"""
weather_automation.py
Accurate, live browser-based weather automation using Playwright + Chrome.
Scrapes AccuWeather, Windy, and IMD — zero paid services.
"""

import asyncio
import json
import os
import re
import urllib.request
from datetime import datetime
from typing import Optional, Callable

import pytz
from playwright.async_api import async_playwright, Page, Browser
from bs4 import BeautifulSoup

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")
IMD_IMAGE_PATH = os.path.join(os.path.dirname(__file__), "imd_warning.png")

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

def load_config():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)

# ── AccuWeather Scraping ─────────────────────────────────────────────────────
async def fetch_accuweather(page: Page, loc: dict, emit: Callable) -> dict:
    name = loc["name"]
    accu_url = loc.get("accu_url")
    accu_query = loc.get("accu_query", f"{name} Maharashtra India")
    result = {
        "rain_prob": None,
        "rain_mm": None,
        "cloud": None,
        "remark": "DATA UNAVAILABLE"
    }

    await emit(f"AccuWeather — {name}...")
    try:
        today_html = None
        # Try direct URL first
        if accu_url:
            try:
                await page.goto(accu_url, timeout=25000, wait_until="domcontentloaded")
                await page.wait_for_timeout(3000)
                today_html = await page.content()
            except Exception as e_direct:
                today_html = None

        # If direct URL wasn't available or didn't load weather, search
        if not today_html or "weather-today" not in page.url:
            search_url = f"https://www.accuweather.com/en/search-locations?query={accu_query}"
            await page.goto(search_url, timeout=25000, wait_until="domcontentloaded")
            await page.wait_for_timeout(3000)

            content = await page.content()
            soup = BeautifulSoup(content, "html.parser")
            loc_list = soup.find(class_=lambda c: c and "locations-list" in c)
            redirect_url = None
            if loc_list:
                first_a = loc_list.find("a")
                if first_a and first_a.get("href"):
                    redirect_url = "https://www.accuweather.com" + first_a.get("href")

            if redirect_url:
                await page.goto(redirect_url, timeout=25000, wait_until="domcontentloaded")
                await page.wait_for_timeout(3000)
                today_url = page.url.split("?")[0].replace("weather-forecast", "weather-today")
                await page.goto(today_url, timeout=25000, wait_until="domcontentloaded")
                await page.wait_for_timeout(3000)
                today_html = await page.content()

        if today_html:
            today_soup = BeautifulSoup(today_html, "html.parser")
            text_all = today_soup.get_text(" ", strip=True)

            # 1. Probability of Precipitation %
            m_prob = re.search(r'Probability of Precipitation\s*(\d+)%', text_all, re.I)
            if m_prob:
                result["rain_prob"] = float(m_prob.group(1))

            # 2. Rain Amount / Precipitation mm
            m_rain = re.search(r'(?:Rain Amount|Precipitation)\s*(\d+(?:\.\d+)?)\s*mm', text_all, re.I)
            if m_rain:
                result["rain_mm"] = float(m_rain.group(1))

            # 3. Cloud Cover %
            m_cloud = re.search(r'Cloud Cover\s*(\d+)%', text_all, re.I)
            if m_cloud:
                result["cloud"] = float(m_cloud.group(1))

            # Calculate remark
            if result["rain_mm"] is not None:
                r_stat = classify_rain(result["rain_mm"])
            elif result["rain_prob"] is not None:
                r_stat = classify_rain_prob(result["rain_prob"])
            else:
                r_stat = "DATA UNAVAILABLE"

            c_stat = classify_cloud(result["cloud"])
            result["remark"] = decide_status(r_stat, c_stat)

            r_disp = f"{int(result['rain_prob'])}%" if result["rain_prob"] is not None else (f"{result['rain_mm']}mm" if result["rain_mm"] is not None else "N/A")
            c_disp = f"{int(result['cloud'])}%" if result["cloud"] is not None else "N/A"
            await emit(f"  AccuWeather - {name} [OK] Rain: {r_disp}, Cloud: {c_disp} ({result['remark']})")
        else:
            await emit(f"  AccuWeather - {name}: DATA UNAVAILABLE")

    except Exception as e:
        await emit(f"  AccuWeather - {name} error: {e}")

    return result

# ── Windy Scraping ───────────────────────────────────────────────────────────
async def fetch_windy(page: Page, loc: dict, emit: Callable) -> dict:
    name = loc["name"]
    lat = loc["lat"]
    lon = loc["lon"]
    result = {
        "rain_mm": None,
        "cloud": None,
        "remark": "DATA UNAVAILABLE"
    }

    await emit(f"Windy - {name}...")
    # Open Windy with clouds overlay so both forecast table and cloud interpolator are active
    url = f"https://www.windy.com/{lat}/{lon}?clouds,{lat},{lon},11"

    try:
        await page.goto(url, timeout=35000, wait_until="domcontentloaded")
        await page.wait_for_timeout(5000)

        # 1. Extract authentic Rain (mm) from the forecast table (11 AM / morning briefing slot)
        table_el = page.locator(".forecast-table__table")
        if await table_el.count() > 0:
            rain_val = await page.evaluate(r'''() => {
                const table = document.querySelector('.forecast-table__table');
                if (!table) return 0.0;
                const daysTr = table.querySelector('.tr--days');
                const hourTr = table.querySelector('.tr--hour');
                const rainTr = table.querySelector('.tr--rain');
                
                const firstDayTd = daysTr ? daysTr.querySelector('td') : null;
                const colspan = firstDayTd ? parseInt(firstDayTd.getAttribute('colspan') || '6') : 6;
                
                const hours = Array.from(hourTr ? hourTr.querySelectorAll('td') : []).slice(0, colspan).map(td => td.innerText.trim());
                const rains = Array.from(rainTr ? rainTr.querySelectorAll('td') : []).slice(0, colspan).map(td => td.innerText.trim());
                
                // Locate 11AM slot (closest to 10AM morning briefing); fallback to 2nd daytime slot
                let idx = hours.indexOf("11AM");
                if (idx === -1) idx = 1;
                
                const rText = rains[idx] || "";
                const m = rText.match(/(\d+(?:\.\d+)?)/);
                return m ? parseFloat(m[1]) : 0.0;
            }''')
            result["rain_mm"] = rain_val
        else:
            result["rain_mm"] = 0.0

        # 2. Extract authentic Cloud Cover % directly from Windy's WebGL map interpolator
        cloud_val = await page.evaluate(r'''async (coord) => {
            try {
                if (window.W && window.W.interpolator) {
                    const interp = await window.W.interpolator.getLatLonInterpolator();
                    if (interp) {
                        const raw = await interp({lat: coord.lat, lon: coord.lon});
                        if (Array.isArray(raw) && raw.length > 0 && typeof raw[0] === 'number') {
                            return Math.round(raw[0]);
                        }
                    }
                }
            } catch (e) {
                console.error(e);
            }
            return null;
        }''', {"lat": lat, "lon": lon})
        
        result["cloud"] = cloud_val

        r_stat = classify_rain(result["rain_mm"])
        c_stat = classify_cloud(result["cloud"])
        result["remark"] = decide_status(r_stat, c_stat)

        r_disp = f"{result['rain_mm']}mm" if result["rain_mm"] is not None else "N/A"
        c_disp = f"{int(result['cloud'])}%" if result["cloud"] is not None else "N/A"
        await emit(f"  Windy - {name} [OK] 11AM Rain: {r_disp}, Cloud: {c_disp} ({result['remark']})")

    except Exception as e:
        await emit(f"  Windy - {name} error: {e}")

    return result

# ── IMD Warning ──────────────────────────────────────────────────────────────
async def fetch_imd_warning(emit: Callable) -> str:
    await emit("IMD - opening District-wise Warning GIS map...")
    gis_url = "https://mausam.imd.gov.in/responsive/districtWiseWarningGIS.php"

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"]
            )
            context = await browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
            )
            page = await context.new_page()

            await page.goto(gis_url, timeout=45000, wait_until="domcontentloaded")
            await page.wait_for_timeout(5000)

            # 1. Enable Transparent toggle
            await emit("  IMD - enabling Transparent layer...")
            await page.evaluate('''() => {
                const btn = document.getElementById('opacity_button');
                if (btn && !btn.checked) {
                    btn.click();
                }
            }''')
            await page.wait_for_timeout(1200)

            # 2. Select "Google Map" layer
            await emit("  IMD - selecting Google Map base layer...")
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
            await page.wait_for_timeout(3000)

            # 3. Focus view strictly on Maharashtra
            await emit("  IMD - focusing view on Maharashtra portion...")
            await page.evaluate('''() => {
                if (window.map) {
                    window.map.fitBounds([[15.8, 72.6], [22.0, 80.9]], {
                        animate: false,
                        padding: [10, 10]
                    });
                }
            }''')
            await page.wait_for_timeout(4000)

            # Capture screenshot of the map element #chartdiv2
            chart = page.locator("#chartdiv2")
            if await chart.count() > 0:
                await chart.first.screenshot(path=IMD_IMAGE_PATH)
            else:
                sdww = page.locator(".sdww-block")
                if await sdww.count() > 0:
                    await sdww.first.screenshot(path=IMD_IMAGE_PATH)
                else:
                    await page.screenshot(path=IMD_IMAGE_PATH)

            await browser.close()
            await emit("  IMD Maharashtra district-wise warning map captured [OK]")
            return IMD_IMAGE_PATH

    except Exception as e:
        await emit(f"  IMD error: {e}")

    return ""

# ── Pipeline Orchestrator ────────────────────────────────────────────────────
async def run_automation(emit: Callable) -> dict:
    cfg = load_config()
    locations = cfg["locations"]
    tz = pytz.timezone(cfg.get("timezone", "Asia/Kolkata"))
    now = datetime.now(tz)
    date_str = now.strftime("%d %b %Y").lstrip("0").upper()

    all_data = []

    # 1. AccuWeather using Chrome or bundled Chromium
    await emit("Opening AccuWeather...")
    async with async_playwright() as p:
        launch_args = [
            "--disable-blink-features=AutomationControlled",
            "--disable-http2",
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
        ]
        try:
            browser = await p.chromium.launch(
                channel="chrome",
                headless=True,
                args=launch_args
            )
        except Exception:
            browser = await p.chromium.launch(
                headless=True,
                args=launch_args
            )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            viewport={"width": 1366, "height": 768},
            locale="en-US"
        )
        page = await context.new_page()
        await page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

        for loc in locations:
            accu = await fetch_accuweather(page, loc, emit)
            all_data.append({
                "location": loc["name"],
                "accu_rain_prob": accu["rain_prob"],
                "accu_rain_mm": accu["rain_mm"],
                "accu_cloud": accu["cloud"],
                "accu_remark": accu["remark"],
                "windy_rain": None,
                "windy_cloud": None,
                "windy_remark": "DATA UNAVAILABLE",
            })

        await browser.close()

    await emit("AccuWeather complete [OK]")

    # 2. Windy
    await emit("Opening Windy...")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1366, "height": 768})
        page = await context.new_page()

        for i, loc in enumerate(locations):
            w = await fetch_windy(page, loc, emit)
            all_data[i]["windy_rain"] = w["rain_mm"]
            all_data[i]["windy_cloud"] = w["cloud"]
            all_data[i]["windy_remark"] = w["remark"]

        await browser.close()

    await emit("Windy complete [OK]")

    # 3. IMD
    imd_image = await fetch_imd_warning(emit)

    return {
        "date_str": date_str,
        "weather_data": all_data,
        "imd_image": imd_image,
    }
