"""Agency news: RSS/Atom/JSON newsroom feeds plus an optional hand-kept file, matched to projects by route.

Region config:
  "news": {
    "feeds": [{"agency": "IDOT", "url": "https://.../feed.rss",
               "keywords": ["Madison", "St. Clair", "District 8"]}],   # optional: keep only items that mention one
    "file": "config/news/<region>.json",  # optional: {"items": [...]} written by hand or by another tool
    "maxAgeDays": 30, "maxItems": 30
  }
A hand-kept item uses the page's NewsItem shape: kind, agency, date, title, url, summary, topic, projectIds,
vsFeed. Hand-kept items win over feed items with the same url.
"""
import datetime as dt
import html
import json
import re
import sys
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

from .common import clean, expand_env, http_get, jload, parse_date, route_family, today

ROADWORK = re.compile(r"lane|closur|closed|ramp|detour|work zone|restriction|construction|resurfac|repair|"
                      r"paving|bridge|shoulder|traffic shift|flagger", re.I)
OTHER = re.compile(r"meeting|ribbon|celebrat|comment period|hearing|open house|award|advisory|invit|survey|hiring|"
                   r"job fair|scholarship", re.I)
ROUTE_TOKENS = re.compile(r"\b(I|US|U\.S\.|IL|MO|SR|State Route|Route|Hwy|Highway|IA|IN|KY|KS|OH|TX|CA)[-\s]?(\d{1,3}[A-Z]?)\b")
TAG = re.compile(r"<[^>]+>")


def _text(el, *names):
    for n in names:
        x = el.find(n)
        if x is not None:
            return (x.text or x.get("href") or "").strip()
    return ""


def parse_feed(body):
    """RSS 2.0, Atom or a JSON feed (Prezly-style {"stories": [...]} or JSON Feed {"items": [...]})."""
    body = body.lstrip("﻿ \n\r\t")
    if body.startswith("{"):
        d = json.loads(body)
        rows = d.get("stories") or d.get("items") or d.get("data") or []
        return [{"title": clean(r.get("title")), "url": r.get("url") or r.get("links", {}).get("newsroom_view") or r.get("link"),
                 "date": r.get("published_at") or r.get("date_published") or r.get("date"),
                 "summary": r.get("subtitle") or r.get("intro") or r.get("summary") or r.get("content_text") or ""}
                for r in rows]
    root = ET.fromstring(body)
    ns = {"a": "http://www.w3.org/2005/Atom"}
    out = []
    for it in root.iter("item"):
        out.append({"title": _text(it, "title"), "url": _text(it, "link", "guid"), "date": _text(it, "pubDate", "date"),
                    "summary": _text(it, "description")})
    for it in root.iter("{http://www.w3.org/2005/Atom}entry"):
        link = it.find("a:link", ns)
        out.append({"title": _text(it, "{http://www.w3.org/2005/Atom}title"),
                    "url": link.get("href") if link is not None else "",
                    "date": _text(it, "{http://www.w3.org/2005/Atom}published", "{http://www.w3.org/2005/Atom}updated"),
                    "summary": _text(it, "{http://www.w3.org/2005/Atom}summary", "{http://www.w3.org/2005/Atom}content")})
    return out


def _date(v):
    if not v:
        return None
    d = parse_date(v)
    if d:
        return d
    try:
        return parsedate_to_datetime(v).date()
    except (TypeError, ValueError, IndexError):
        return None


def _summary(s, words=40):
    s = clean(html.unescape(TAG.sub(" ", s or "")))
    w = s.split(" ")
    return " ".join(w[:words]) + ("…" if len(w) > words else "")


def route_tokens(text):
    out = set()
    for kind, num in ROUTE_TOKENS.findall(text or ""):
        k = {"U.S.": "US", "State Route": "SR", "Hwy": "Route", "Highway": "Route"}.get(kind, kind)
        out.add((k if k == "I" else "R", re.sub(r"\D", "", num)))
    return out


def _project_tokens(p):
    toks = set()
    for r in [p.get("route", "")] + [z.get("route", "") for z in p.get("zones") or []]:
        kind = "I" if r.strip().startswith("I-") else "R"
        toks |= {(kind, n) for n in route_family(r)}
    return toks


def match(item, projects, limit=6):
    """Ids of current projects on a route the item names; narrowed by place names when there are many."""
    text = f"{item.get('title', '')} {item.get('summary', '')}"
    toks = route_tokens(text)
    if not toks:
        return []
    hits = [p for p in projects if p.get("status") != "ended" and not p.get("reportOnly") and _project_tokens(p) & toks]
    if len(hits) > limit:
        low = text.lower()
        near = [p for p in hits if any(x and x.lower() in low for x in
                                       [p.get("nearTown")] + list(p.get("counties") or []))]
        hits = near or hits
    return [p["id"] for p in hits[:limit]]


def collect(cfg, projects, stored_items):
    """News items for status.json: feed items, hand-kept items and still-fresh stored items, newest first."""
    ncfg = cfg.get("news") or {}
    max_age, max_items = ncfg.get("maxAgeDays", 30), ncfg.get("maxItems", 30)
    cutoff = (today() - dt.timedelta(days=max_age)).isoformat()
    items = {it.get("url") or it.get("title"): it for it in stored_items or [] if (it.get("date") or "") >= cutoff}
    for feed in ncfg.get("feeds") or []:
        try:
            rows = parse_feed(http_get(expand_env(feed["url"]), text=True))
        except Exception as e:  # noqa: BLE001
            print("news feed failed:", feed.get("url"), e, file=sys.stderr)
            continue
        kws = [k.lower() for k in feed.get("keywords") or []]
        for r in rows:
            d = _date(r.get("date"))
            if not r.get("url") or not d or d.isoformat() < cutoff:
                continue
            text = f"{r['title']} {r['summary']}"
            if kws and not any(k in text.lower() for k in kws):
                continue
            title = clean(html.unescape(r["title"]))
            topic = "other" if OTHER.search(title) else ("roadwork" if ROADWORK.search(text) else "other")
            old = items.get(r["url"], {})
            items[r["url"]] = {**{"kind": "news", "agency": feed.get("agency", ""), "date": d.isoformat(),
                                  "title": title, "url": r["url"], "summary": _summary(r["summary"]), "topic": topic},
                               **{k: old[k] for k in ("vsFeed",) if old.get(k)}}
    for it in (jload(ncfg["file"], {}) or {}).get("items", []) if ncfg.get("file") else []:
        items[it.get("url") or it.get("title")] = it
    out = []
    for it in items.values():
        if "projectIds" not in it or it.get("_auto"):
            it["projectIds"] = match(it, projects)
            it["_auto"] = True
            if it["projectIds"] and not it.get("vsFeed"):
                n = len(it["projectIds"])
                it["vsFeed"] = f"Matched by route to {n} project{'s' if n > 1 else ''} in the agency data feeds."
        out.append(it)
    out.sort(key=lambda x: x.get("date") or "", reverse=True)
    return out[:max_items]


def attach(projects, items):
    """Copy each news item onto the projects it names (newest first, at most 8 per project)."""
    by = {}
    for it in items:
        for pid in it.get("projectIds") or []:
            by.setdefault(pid, []).append({k: it[k] for k in ("date", "agency", "title", "url", "summary", "kind", "vsFeed")
                                           if it.get(k)})
    for p in projects:
        if p["id"] in by:
            key = lambda u: (u.get("url"), u.get("date"), u.get("title")) if u.get("kind") == "social" else u.get("url")  # noqa: E731
            ups = {key(u): u for u in (p.get("updates") or []) + by[p["id"]]}
            p["updates"] = sorted(ups.values(), key=lambda u: u.get("date") or "", reverse=True)[:8]
