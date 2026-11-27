#!/usr/bin/env python3
"""Pull Cloudflare Web Analytics (cookieless RUM) numbers for the site into JSON.

Needs two repository secrets:
  CF_API_TOKEN   - API token with "Account Analytics: Read"
  CF_ACCOUNT_ID  - the Cloudflare account ID

Writes:
  <out>/analytics.json          last 30 days: totals, by day, top paths/referrers/countries/devices,
                                and outbound offer clicks (/go/<name>.html page views)
  <out>/analytics_history.json  one row per day, kept for ~400 days (merged with what is already there)
Standard library only. Exits 0 without writing when the secrets are missing.
"""
import argparse, datetime as dt, json, os, sys, time, urllib.request

API = "https://api.cloudflare.com/client/v4/graphql"
HOST = "hakuba-snow-compass.pages.dev"

QUERY = """
query($acct: String!, $start: Time!, $end: Time!, $host: String!) {
  viewer { accounts(filter: {accountTag: $acct}) {
    byDay: rumPageloadEventsAdaptiveGroups(limit: 100, filter: {datetime_geq: $start, datetime_lt: $end, requestHost: $host}, orderBy: [date_ASC]) {
      count sum { visits } dimensions { date } }
    byPath: rumPageloadEventsAdaptiveGroups(limit: 50, filter: {datetime_geq: $start, datetime_lt: $end, requestHost: $host}, orderBy: [count_DESC]) {
      count sum { visits } dimensions { requestPath } }
    byReferer: rumPageloadEventsAdaptiveGroups(limit: 25, filter: {datetime_geq: $start, datetime_lt: $end, requestHost: $host}, orderBy: [count_DESC]) {
      count sum { visits } dimensions { refererHost } }
    byCountry: rumPageloadEventsAdaptiveGroups(limit: 25, filter: {datetime_geq: $start, datetime_lt: $end, requestHost: $host}, orderBy: [count_DESC]) {
      count sum { visits } dimensions { countryName } }
    byDevice: rumPageloadEventsAdaptiveGroups(limit: 10, filter: {datetime_geq: $start, datetime_lt: $end, requestHost: $host}, orderBy: [count_DESC]) {
      count sum { visits } dimensions { deviceType } }
    clicksByDay: rumPageloadEventsAdaptiveGroups(limit: 500, filter: {datetime_geq: $start, datetime_lt: $end, requestHost: $host, requestPath_like: "/go/%"}, orderBy: [date_ASC]) {
      count dimensions { date requestPath } }
  } }
}
"""


def post(token, payload):
    req = urllib.request.Request(API, data=json.dumps(payload).encode(), method="POST",
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    last = None
    for i in range(4):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except Exception as e:  # network blip: back off and retry
            last = e
            time.sleep(10 * (i + 1))
    raise last


def rows(groups, dim):
    return [{"key": g["dimensions"].get(dim) or "(none)", "views": g["count"], "visits": (g.get("sum") or {}).get("visits")} for g in groups]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="_data")
    ap.add_argument("--days", type=int, default=30)
    a = ap.parse_args()
    token, acct = os.environ.get("CF_API_TOKEN"), os.environ.get("CF_ACCOUNT_ID")
    if not token or not acct:
        print("CF_API_TOKEN / CF_ACCOUNT_ID not set - skipping analytics")
        return 0

    end = dt.datetime.now(dt.timezone.utc).replace(minute=0, second=0, microsecond=0)
    start = end - dt.timedelta(days=a.days)
    res = post(token, {"query": QUERY, "variables": {"acct": acct, "host": HOST,
                                                     "start": start.isoformat().replace("+00:00", "Z"),
                                                     "end": end.isoformat().replace("+00:00", "Z")}})
    if res.get("errors"):
        print("GraphQL errors:", json.dumps(res["errors"])[:800], file=sys.stderr)
        return 1
    acc = res["data"]["viewer"]["accounts"][0]

    clicks = {}
    for g in acc["clicksByDay"]:
        name = g["dimensions"]["requestPath"].rsplit("/", 1)[-1].replace(".html", "")
        clicks.setdefault(name, 0)
        clicks[name] += g["count"]

    by_day = rows(acc["byDay"], "date")
    out = {
        "fetched_at": end.isoformat(), "source": "cloudflare-web-analytics", "host": HOST,
        "window_days": a.days,
        "totals": {"views": sum(r["views"] for r in by_day), "visits": sum(r["visits"] or 0 for r in by_day),
                   "offer_clicks": sum(clicks.values())},
        "by_day": by_day,
        "top_paths": [r for r in rows(acc["byPath"], "requestPath") if not r["key"].startswith("/go/")],
        "top_referrers": rows(acc["byReferer"], "refererHost"),
        "top_countries": rows(acc["byCountry"], "countryName"),
        "devices": rows(acc["byDevice"], "deviceType"),
        "offer_clicks": clicks,
        "offer_clicks_by_day": [{"date": g["dimensions"]["date"], "offer": g["dimensions"]["requestPath"].rsplit("/", 1)[-1].replace(".html", ""), "clicks": g["count"]} for g in acc["clicksByDay"]],
    }
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "analytics.json"), "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    # Daily history: merge, newest wins, keep ~400 days.
    hp = os.path.join(a.out, "analytics_history.json")
    hist = {}
    if os.path.exists(hp):
        try:
            hist = {r["date"]: r for r in json.load(open(hp)).get("days", [])}
        except Exception:
            hist = {}
    cday = {}
    for c in out["offer_clicks_by_day"]:
        cday.setdefault(c["date"], {})[c["offer"]] = c["clicks"]
    for r in by_day:
        hist[r["key"]] = {"date": r["key"], "views": r["views"], "visits": r["visits"], "offer_clicks": cday.get(r["key"], {})}
    days = sorted(hist.values(), key=lambda r: r["date"])[-400:]
    with open(hp, "w") as f:
        json.dump({"updated_at": end.isoformat(), "days": days}, f, ensure_ascii=False, indent=1)
    print(f"analytics: {out['totals']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
