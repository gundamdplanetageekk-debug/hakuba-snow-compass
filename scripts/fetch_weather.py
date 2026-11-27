#!/usr/bin/env python3
"""Fetch one Open-Meteo forecast for all Hakuba resorts (top + base each) and save it as weather.json.

The browser reads this file, so traffic to your site never multiplies API calls:
one hourly run = 24 requests/day no matter how many visitors you have.

Usage:  python scripts/fetch_weather.py --resorts data/resorts.json --out _data/weather.json
Env:    OPEN_METEO_API_KEY  (optional; required for commercial use -> customer-api.open-meteo.com)
"""
import argparse, json, os, sys, time, urllib.parse, urllib.request
from datetime import datetime, timezone

HOURLY_VARS = [
    "temperature_2m", "snowfall", "precipitation", "weather_code",
    "wind_speed_10m", "wind_gusts_10m", "wind_direction_10m",
    "visibility", "freezing_level_height",
    "wind_speed_850hPa", "wind_direction_850hPa", "temperature_850hPa",
    "wind_speed_700hPa", "wind_direction_700hPa", "temperature_700hPa",
]  # keep in sync with engine.js


def build_url(resorts, api_key=None):
    lat, lon, ele = [], [], []
    for r in resorts:
        for key, elev in (("top", r["top"]), ("base", r["base"])):
            lat.append(r["coords"][key][0]); lon.append(r["coords"][key][1]); ele.append(elev)
    params = {
        "latitude": ",".join(map(str, lat)), "longitude": ",".join(map(str, lon)),
        "elevation": ",".join(map(str, ele)), "hourly": ",".join(HOURLY_VARS),
        "timezone": "Asia/Tokyo", "past_days": 3, "forecast_days": 3, "wind_speed_unit": "kmh",
    }
    host = "https://api.open-meteo.com"
    if api_key:
        host = "https://customer-api.open-meteo.com"
        params["apikey"] = api_key
    return f"{host}/v1/forecast?{urllib.parse.urlencode(params)}"


def fetch(url, tries=4):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "hakuba-snow-compass/1.0"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.load(resp)
        except Exception as exc:  # network blips are common on shared CI runners
            if i == tries - 1:
                raise
            print(f"retry {i + 1}: {exc}", file=sys.stderr)
            time.sleep(15 * (i + 1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--resorts", default="data/resorts.json")
    ap.add_argument("--out", default="data/weather.json")
    a = ap.parse_args()

    resorts = json.load(open(a.resorts, encoding="utf-8"))["resorts"]
    key = os.environ.get("OPEN_METEO_API_KEY") or None
    data = fetch(build_url(resorts, key))
    if not isinstance(data, list) or len(data) != len(resorts) * 2:
        sys.exit(f"Unexpected payload: expected {len(resorts) * 2} locations")

    payload = {
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "open-meteo" + (" (commercial)" if key else " (free)"),
        "order": [f"{r['id']}:{k}" for r in resorts for k in ("top", "base")],
        "data": data,
    }
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, separators=(",", ":"))
    print(f"wrote {a.out}: {len(data)} locations")


if __name__ == "__main__":
    main()
