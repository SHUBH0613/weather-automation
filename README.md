# Weather App — Quick Start Guide

## What was built

A local web tool that:
1. Opens AccuWeather, Windy, and IMD in a headless browser (invisible)
2. Collects rain (mm) and cloud cover (%) for 5 Maharashtra locations
3. Applies GO / LTD GO / NO GO rules
4. Fills your existing PowerPoint template (3 slides)
5. Lets you download `WX UPDATE DD MMM YYYY.pptx`

---

## Project layout

```
weather-app/
├── index.html             ← The web page (open in browser)
├── app.py                 ← Flask server (backend)
├── weather_automation.py  ← Playwright browser automation
├── ppt_generator.py       ← PowerPoint fill logic
├── config.json            ← Locations (edit to change cities)
├── requirements.txt
├── run.bat                ← Double-click to start on Windows
└── template/
    └── weather_template.pptx  ← Your original PPT goes here
```

---

## Setup (first time only)

```bash
# 1. Install Python packages
pip install -r requirements.txt

# 2. Install Playwright browser
playwright install chromium
```

---

## How to run

**Option A — double-click**  
`run.bat`

**Option B — command line**  
```bash
python app.py
```

Then open **http://127.0.0.1:5000** in your browser.

---

## Using the app

1. Open `http://127.0.0.1:5000`
2. Click **GET DATA**
3. Watch the live progress log
4. When complete, click **DOWNLOAD POWERPOINT**

The automation takes **3–6 minutes** (headless browser visits 3 websites).

---

## Changing locations

Edit `config.json`:

```json
{
  "locations": [
    { "name": "Nashik",     "lat": 19.9975, "lon": 73.7898 },
    { "name": "Mumbai",     "lat": 19.0760, "lon": 72.8777 },
    ...
  ]
}
```

---

## Template

Your PPT template must be at:  
`template/weather_template.pptx`

The file must have exactly 3 slides:
- Slide 1: Title with "WX UPDATE" text box
- Slide 2: 6-column weather table + 3-column GO rules table
- Slide 3: IMD map picture + city annotation text boxes

---

## Known limitations

| Website | Status | Notes |
|---------|--------|-------|
| **AccuWeather** | ⚠ May block scrapers | Shows bot detection on some requests; values marked N/A if blocked |
| **Windy** | ⚠ Heavy JS app | DOM selectors may need tuning if Windy redesigns; fallback tries page source |
| **IMD** | ✓ Works | Takes a full-page screenshot of the Maharashtra warning page |

If AccuWeather or Windy block the scraper, the cell shows **N/A** in the PPT — the run continues.  
The PPT is always generated even if some values are unavailable.

---

## Output file

`WX UPDATE 20 SEP 2026.pptx` (date updates automatically)
