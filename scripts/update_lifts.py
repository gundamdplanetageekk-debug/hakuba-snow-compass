#!/usr/bin/env python3
"""Scrape live lift status from the official Hakuba Valley site into lifts.json.

hakubavalley.com publishes one "WEATHER & LIFT STATUS" page per resort, e.g.
  https://www.hakubavalley.com/en/weather_en/detail_hakuba47_en
with a table: lift name | current status | remarks.

The script discovers the per-resort pages from the index page (so URL changes don't break it),
parses every table row and counts OPEN vs total. Unknown layouts are skipped, never guessed.

Usage: python scripts/update_lifts.py --out _data/lifts.json
"""
import argparse, json, os, re, sys, urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser

INDEX = "https://www.hakubavalley.com/en/weather_en/"
UA = {"User-Agent": "hakuba-snow-compass/1.0 (+lift status cache, 1 req/resort/hour)"}

# slug fragment in the detail URL -> resort id in resorts.json. Extend if the site adds new slugs.
SLUG_MAP = {
    "happo": "happo", "goryu": "goryu", "hakuba47": "hakuba47", "iwatake": "iwatake",
    "tsugaike": "tsugaike", "norikura": "norikura", "cortina": "cortina",
    "kashimayari": "kashimayari", "jigatake": "jigatake", "jiigatake": "jigatake", "sanosaka": "sanosaka",
}
OPEN_WORDS = ("open", "operating", "運行中", "営業中")
CLOSED_WORDS = ("closed", "suspended", "stop", "hold", "運休", "停止", "準備中")


class TableParser(HTMLParser):
    def __init__(self):
        super().__init__(); self.rows, self._row, self._cell, self._in = [], None, None, False
    def handle_starttag(self, tag, attrs):
        if tag == "tr": self._row = []
        elif tag in ("td", "th") and self._row is not None: self._cell, self._in = [], True
    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._in:
            self._row.append(" ".join("".join(self._cell).split())); self._in = False
        elif tag == "tr" and self._row is not None:
            if self._row: self.rows.append(self._row)
            self._row = None
    def handle_data(self, data):
        if self._in: self._cell.append(data)


def get(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def classify(status):
    s = status.lower()
    if any(w in s for w in CLOSED_WORDS): return "closed"
    if any(w in s for w in OPEN_WORDS): return "open"
    return "unknown"


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--out", default="data/lifts.json"); a = ap.parse_args()
    html = get(INDEX)
    links = sorted(set(re.findall(r'href="([^"]*weather_en/detail_([a-z0-9]+)_en/?)"', html)))
    if not links:
        sys.exit("No resort detail links found on index page — layout changed, check INDEX/regex.")

    out = {"fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "source": INDEX, "resorts": {}}
    for href, slug in links:
        rid = next((v for k, v in SLUG_MAP.items() if k in slug), None)
        if not rid: print(f"skip unknown slug {slug}", file=sys.stderr); continue
        url = href if href.startswith("http") else "https://www.hakubavalley.com" + href
        try:
            p = TableParser(); p.feed(get(url))
        except Exception as exc:
            print(f"{rid}: {exc}", file=sys.stderr); continue
        lifts = []
        for row in p.rows:
            if len(row) < 2 or row[0].lower() in ("lift name", "リフト名"): continue
            lifts.append({"name": row[0], "status": classify(row[1]), "raw": row[1], "remarks": row[2] if len(row) > 2 else ""})
        if lifts:
            prev = out["resorts"].get(rid, {"lifts": []})["lifts"]
            all_lifts = prev + lifts
            out["resorts"][rid] = {
                "url": url, "lifts": all_lifts,
                "open": sum(1 for l in all_lifts if l["status"] == "open"),
                "total": sum(1 for l in all_lifts if l["status"] != "unknown"),
            }
            print(f"{rid}: {out['resorts'][rid]['open']}/{out['resorts'][rid]['total']} open")

    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    json.dump(out, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
