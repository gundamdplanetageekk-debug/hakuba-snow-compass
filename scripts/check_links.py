#!/usr/bin/env python3
"""Daily health check of the safety links shown in Backcountry Mode (data/safety.json).

Writes <out>/link_status.json: one entry per link with HTTP status or error. Standard library only.
A link that fails on two runs in a row is worth replacing; the weekly report reads this file.
"""
import argparse, datetime as dt, json, os, urllib.request

UA = {"User-Agent": "Mozilla/5.0 (compatible; hakuba-snow-compass-linkcheck/1.0)", "Accept": "text/html,application/pdf,*/*"}


def check(url):
    for method in ("HEAD", "GET"):
        try:
            req = urllib.request.Request(url, headers=UA, method=method)
            with urllib.request.urlopen(req, timeout=25) as r:
                return {"ok": 200 <= r.status < 400, "status": r.status}
        except urllib.error.HTTPError as e:
            if method == "HEAD" and e.code in (403, 405, 501):
                continue  # some servers refuse HEAD; retry with GET
            return {"ok": False, "status": e.code}
        except Exception as e:
            if method == "HEAD":
                continue
            return {"ok": False, "error": type(e).__name__ + ": " + str(e)[:120]}
    return {"ok": False, "error": "unknown"}


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--out", default="_data"); a = ap.parse_args()
    safety = json.load(open("data/safety.json"))
    prev = {}
    p = os.path.join(a.out, "link_status.json")
    if os.path.exists(p):
        try: prev = {x["url"]: x for x in json.load(open(p)).get("links", [])}
        except Exception: prev = {}
    out = []
    for l in safety.get("links", []):
        r = check(l["url"])
        fails = 0 if r["ok"] else prev.get(l["url"], {}).get("consecutive_failures", 0) + 1
        out.append({"label": l["label"], "url": l["url"], **r, "consecutive_failures": fails})
        print(("OK  " if r["ok"] else "FAIL"), l["url"], r)
    os.makedirs(a.out, exist_ok=True)
    json.dump({"checked_at": dt.datetime.now(dt.timezone.utc).isoformat(), "links": out}, open(p, "w"), indent=1)


if __name__ == "__main__":
    main()
