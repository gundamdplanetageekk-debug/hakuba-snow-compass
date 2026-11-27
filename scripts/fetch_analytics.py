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
query($acct: String!, $start: Time!, $end: Time!, $site: String!) {
  viewer { accounts(filter: {accountTag: $acct}) {
    byDay: rumPageloadEventsAdaptiveGroups(limit: 100, filter: {datetime_geq: $start, datetime_leq: $end, siteTag: $site}, orderBy: [date_ASC]) {
      count sum { visits } dimensions { date } }
    byPath: rumPageloadEventsAdaptiveGroups(limit: 50, filter: {datetime_geq: $start, datetime_leq: $end, siteTag: $site}, orderBy: [count_DESC]) {
      count sum { visits } dimensions { requestPath } }
    byReferer: rumPageloadEventsAdaptiveGroups(limit: 25, filter: {datetime_geq: $start, datetime_leq: $end, siteTag: $site}, orderBy: [count_DESC]) {
      count sum { visits } dimensions { refererHost } }
    byCountry: rumPageloadEventsAdaptiveGroups(limit: 25, filter: {datetime_geq: $start, datetime_leq: $end, siteTag: $site}, orderBy: [count_DESC]) {
      count sum { visits } dimensions { countryName } }
    byDevice: rumPageloadEventsAdaptiveGroups(limit: 10, filter: {datetime_geq: $start, datetime_leq: $end, siteTag: $site}, orderBy: [count_DESC]) {
      count sum { visits } dimensions { deviceType } }
    clicksByDay: rumPageloadEventsAdaptiveGroups(limit: 500, filter: {datetime_geq: $start, datetime_leq: $end, siteTag: $site, requestPath_like: "/go/%"}, orderBy: [date_ASC]) {
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


def find_site_tag(token, acct):
    """Look up the Web Analytics site tag for HOST (REST: /accounts/{id}/rum/site_info/list)."""
    url = f"https://api.cloudflare.com/client/v4/accounts/{acct}/rum/site_info/list?per_page=100"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.load(r)
    except Exception as e:
        print("site list failed:", e, "- falling back to the beacon token on the live page")
        try:
            import re
            html = urllib.request.urlopen(urllib.request.Request(f"https://{HOST}/", headers={"User-Agent": "Mozilla/5.0 (compatible; hakuba-analytics/1.0)", "Accept": "text/html"}), timeout=30).read().decode("utf-8", "replace")
            m = re.search(r'data-cf-beacon=.{0,5}\{[^}]*"token"\s*:\s*"([0-9a-f]{32})"', html)
            print("beacon token found" if m else "no beacon on page")
            return m.group(1) if m else None
        except Exception as e2:
            print("page fetch failed:", e2)
            return None
    sites = data.get("result") or []
    print("web analytics sites:", [(x.get("host"), x.get("site_tag")) for x in sites])
    for x in sites:
        if (x.get("host") or "").lower() in (HOST, "hakuba-snow-compass.pages.dev"):
            return x.get("site_tag")
    return sites[0].get("site_tag") if len(sites) == 1 else None


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

    end = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    start = end - dt.timedelta(days=a.days)
    site = os.environ.get("CF_SITE_TAG") or find_site_tag(token, acct)
    if not site:
        print("No Web Analytics site found for", HOST)
        return 1
    res = post(token, {"query": QUERY, "variables": {"acct": acct, "site": site,
                                                     "start": start.isoformat().replace("+00:00", "Z"),
                                                     "end": end.isoformat().replace("+00:00", "Z")}})
    if res.get("errors"):
        print("GraphQL errors:", json.dumps(res["errors"])[:800], file=sys.stderr)
        return 1
    acc = res["data"]["viewer"]["accounts"][0]

    if not acc["byDay"]:
        # Diagnostics: last 24 h for this site, with and without the account filter's id prefix shown.
        s24 = (end - dt.timedelta(hours=24)).isoformat().replace("+00:00", "Z")
        q = """query($acct: String!, $start: Time!, $end: Time!, $site: String!, $s30: Time!, $s7: Time!) { viewer { accounts(filter: {accountTag: $acct}) {
              a: rumPageloadEventsAdaptiveGroups(limit: 5, filter: {datetime_geq: $start, datetime_leq: $end, siteTag: $site}) { count dimensions { date } }
              b: rumPageloadEventsAdaptiveGroups(limit: 5, filter: {datetime_geq: $start, datetime_leq: $end, siteTag: $site}, orderBy: [date_ASC]) { count sum { visits } dimensions { date } }
              c: rumPageloadEventsAdaptiveGroups(limit: 5, filter: {datetime_geq: $s30, datetime_leq: $end, siteTag: $site}) { count dimensions { date } }
              d: rumPageloadEventsAdaptiveGroups(limit: 5, filter: {datetime_geq: $s7, datetime_leq: $end, siteTag: $site}) { count dimensions { date } }
            } } }"""
        dbg = post(token, {"query": q, "variables": {"acct": acct, "site": site, "start": s24, "end": end.isoformat().replace("+00:00", "Z"),
                     "s30": start.isoformat().replace("+00:00", "Z"), "s7": (end - dt.timedelta(days=7)).isoformat().replace("+00:00", "Z")}})
        print("diag acct", acct[:6], "site", site[:6], json.dumps(dbg)[:900])

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
