#!/usr/bin/env python3
"""Watch the official shuttle-bus pages, detect new timetable PDFs, and extract departures.

Flow (runs daily in GitHub Actions):
 1. Download each source page in SOURCES and collect every linked PDF.
 2. SHA-256 each PDF. If nothing changed since the last run -> exit 0 (no commit).
 3. For changed PDFs: extract text with pdfplumber, pull "stop name + HH:MM times" rows,
    write them into shuttle.json["timetable"], and set _meta.needsReview = true.
 4. The workflow sees needsReview and opens a GitHub Issue so a human glances at it once.
    (Timetables are safety-relevant for tourists; a 1-minute check per season is worth it.)

Usage: python scripts/update_shuttle.py --shuttle _data/shuttle.json
"""
import argparse, hashlib, io, json, os, re, sys, urllib.parse, urllib.request
from datetime import datetime, timezone

SOURCES = {  # system id -> official page that links the current timetable PDF(s)
    "hv-shuttle": "https://www.hakubavalley.com/en/access_en/shuttlebus_en/",
    "goryu-47": "https://hakubaescal.com/winter-en/access/",
    "gurutto": "https://kanko-omachi.gr.jp/en/gurutto-bus",
}
UA = {"User-Agent": "hakuba-snow-compass/1.0 (+timetable watcher, daily)"}
TIME = re.compile(r"\b([0-2]?\d)[:：]([0-5]\d)\b")


def get(url, binary=False):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
        data = r.read()
    return data if binary else data.decode("utf-8", "replace")


def pdf_links(page_url):
    html = get(page_url)
    hrefs = re.findall(r'href="([^"]+\.pdf)"', html, flags=re.I)
    return sorted({urllib.parse.urljoin(page_url, h) for h in hrefs})


def extract_rows(pdf_bytes):
    """Return [(stop, [HH:MM, ...])] from a timetable PDF. Heuristic: a row = text label + >=2 times."""
    import pdfplumber  # installed in the workflow
    rows = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables() or []:
                for cells in table:
                    cells = [c or "" for c in cells]
                    label = next((c.strip() for c in cells if c and not TIME.search(c)), "")
                    times = [f"{int(h):02d}:{m}" for c in cells for h, m in TIME.findall(c)]
                    if label and len(times) >= 2:
                        rows.append((" ".join(label.split())[:60], sorted(set(times))))
            if not rows:  # fall back to plain text lines
                for line in (page.extract_text() or "").splitlines():
                    times = [f"{int(h):02d}:{m}" for h, m in TIME.findall(line)]
                    label = TIME.sub("", line).strip(" -→↓|")
                    if label and len(times) >= 2:
                        rows.append((" ".join(label.split())[:60], sorted(set(times))))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shuttle", default="data/shuttle.json")
    a = ap.parse_args()

    doc = json.load(open(a.shuttle, encoding="utf-8"))
    known = {s["url"]: s["sha256"] for s in doc["_meta"].get("sources", [])}
    sources, timetable, changed = [], [], []

    for system, page in SOURCES.items():
        try:
            links = pdf_links(page)
        except Exception as exc:
            print(f"{system}: page error {exc}", file=sys.stderr); continue
        for url in links:
            try:
                blob = get(url, binary=True)
            except Exception as exc:
                print(f"{url}: {exc}", file=sys.stderr); continue
            digest = hashlib.sha256(blob).hexdigest()
            sources.append({"system": system, "url": url, "sha256": digest})
            if known.get(url) != digest:
                changed.append(url)
            try:
                rows = extract_rows(blob)
            except Exception as exc:
                print(f"parse failed {url}: {exc}", file=sys.stderr); rows = []
            for stop, times in rows:
                timetable.append({"system": system, "stop": stop, "times": times, "source": url})

    if not changed:
        print("No timetable changes."); return
    doc["_meta"].update({
        "sources": sources,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": "parsed" if timetable else "structure-only",
        "needsReview": True,
        "changed": changed,
    })
    if timetable:
        doc["timetable"] = timetable
    json.dump(doc, open(a.shuttle, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"Changed PDFs: {len(changed)}; extracted rows: {len(timetable)}")


if __name__ == "__main__":
    main()
